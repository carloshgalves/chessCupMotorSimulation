"""Bounded execution of independent games; coordinator owns scheduling."""
from concurrent.futures import ThreadPoolExecutor


def options(raw, default):
    if not isinstance(raw, dict) or set(raw) - {"scope", "workers"}:
        raise ValueError("Opções de execução inválidas")
    scope = raw.get("scope", default)
    workers = raw.get("workers", 1)
    if scope not in ("game", "round", "competition") or type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Selecione partida, rodada ou competição e 1 a 4 motores simultâneos")
    return scope, workers


def batch(games, workers):
    """Select games with distinct participants; never parallelize dependent ties."""
    selected, used = [], set()
    for game in games:
        pair = {game["white"], game["black"]}
        if not pair & used:
            selected.append(game)
            used.update(pair)
        if len(selected) == workers:
            break
    return selected


def execute(games, runner):
    if len(games) == 1:
        runner(games[0])
    elif games:
        # Every worker finishes (or fails) before the coordinator releases the
        # workspace reservation. Successful receipts survive another failure.
        with ThreadPoolExecutor(max_workers=len(games), thread_name_prefix="chesssim-game") as pool:
            futures = [pool.submit(runner, game) for game in games]
            for future in futures:
                future.result()
