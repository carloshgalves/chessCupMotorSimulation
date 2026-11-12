"""Mata-mata persistente: duelos pareados, checkpoint, retomada e desempates explícitos."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import random
import time

import chess.pgn

from .catalog import find_player
from .matches import _atomic_text, _fsync_directory_tree, _publish_exclusive_text, file_hash, official_book_hashes, play_duel, validate
from .game_diversity import mainline_identity
from .peak_windows import CLOCK_PRESETS
from .protocol import OFFICIAL

SCHEMA = "chesssim-tournament-v1"
RESULTS = {"1-0", "0-1", "1/2-1/2"}
CLOCKS = {"official": (600, 2), **CLOCK_PRESETS}
STOP_FILE = ".stop-requested.json"


def _save(directory: Path, state: dict) -> None:
    _atomic_text(directory / "tournament.json", json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                 durable=os.name == "posix")


def load_tournament(directory: Path) -> dict:
    path = directory.expanduser().resolve() / "tournament.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"Manifesto de torneio incompatível: {path}")
    _check_bracket(data)
    return data


def _stop_file(directory: Path) -> Path:
    return directory.expanduser().resolve() / STOP_FILE


def request_stop(directory: Path, *, reason: str = "Solicitado pelo operador") -> dict:
    """Pedido idempotente, sem esperar o lock do orquestrador."""
    directory = directory.expanduser().resolve()
    load_tournament(directory)
    reason = reason.strip()
    if not reason or len(reason) > 256:
        raise ValueError("Motivo deve conter de 1 a 256 caracteres")
    path = _stop_file(directory)
    payload = json.dumps({"requested_utc": datetime.now(timezone.utc).isoformat(),
                          "reason": reason}, ensure_ascii=False) + "\n"
    try:
        if os.name == "posix":
            # Hard-link publication makes the completed, synced flag visible
            # atomically and never overwrites a concurrent stop request.
            _publish_exclusive_text(path, payload)
        else:
            # Preserve existing portable semantics without a durability claim.
            with path.open("x", encoding="utf-8") as stream:
                stream.write(payload)
    except FileExistsError:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Existing stop flag is not a regular file")
        if os.name == "posix":
            _fsync_directory_tree(path.parent)
    return tournament_status(directory)


def clear_stop(directory: Path) -> dict:
    """A liberação só acontece sem outra execução usando a chave do torneio."""
    directory = directory.expanduser().resolve()
    with _lock(directory):
        load_tournament(directory)
        stop_path = _stop_file(directory)
        stop_path.unlink(missing_ok=True)
        if os.name == "posix":
            _fsync_directory_tree(stop_path.parent)
        return tournament_status(directory)



def _check_bracket(state: dict) -> None:
    """Não permite que um estado editado mude adversários ou a progressão da chave."""
    players = state["participants"]
    if len(players) < 2 or len(players) & (len(players) - 1) or len(set(players)) != len(players):
        raise ValueError("Participantes inconsistentes no manifesto")
    expected = list(players)
    random.Random(state["seed"]).shuffle(expected)
    if state["initial_bracket"] != expected:
        raise ValueError("Sorteio persistido diverge da semente")
    rounds = state["rounds"]
    if not rounds:
        raise ValueError("Torneio sem rodada")
    wall = state.get("max_game_wall_seconds")
    if wall is not None and (not isinstance(wall, (int, float)) or
                             not math.isfinite(wall) or wall <= 0):
        raise ValueError("Limite de tempo inválido no manifesto")
    for index, rd in enumerate(rounds, 1):
        matches = rd["matches"]
        if rd["number"] != index or len(matches) != len(expected) // 2:
            raise ValueError("Estrutura de rodadas inconsistente")
        for number, match in enumerate(matches, 1):
            if (match["id"] != f"r{index:02d}-m{number:02d}" or
                    match["players"] != expected[2 * number - 2:2 * number] or
                    len(match["attempts"]) > state["max_tiebreak_pairs"]):
                raise ValueError(f"Chaveamento inconsistente: {match['id']}")
            if (match["winner"] is not None and
                    (match["winner"] not in match["players"] or
                     match["status"] != "COMPLETED")):
                raise ValueError(f"Vencedor inconsistente: {match['id']}")
        if index < len(rounds):
            if any(m["winner"] is None for m in matches):
                raise ValueError("Rodada posterior existe antes da conclusão da anterior")
            expected = [m["winner"] for m in matches]
    if state["champion"] is not None:
        if len(rounds[-1]["matches"]) != 1 or (
                state["champion"] != rounds[-1]["matches"][0]["winner"]):
            raise ValueError("Campeão incompatível com a final")



@contextmanager
def _lock(directory: Path):
    """Lock de execução local; o SO libera-o se o processo morrer."""
    with (directory / ".tournament.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.write(b".")
            stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Outra execução do torneio está ativa") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Outra execução do torneio está ativa") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _match(round_number: int, number: int, a: str, b: str) -> dict:
    return {"id": f"r{round_number:02d}-m{number:02d}",
            "players": [a, b], "status": "PENDING",
            "winner": None, "attempts": [], "excluded_attempts": []}


def _round(number: int, players: list[str]) -> dict:
    return {"number": number,
            "matches": [_match(number, i // 2 + 1, players[i], players[i + 1])
                        for i in range(0, len(players), 2)]}


def _preflight(root: Path, slugs: list[str], preset: str) -> dict:
    engine = root / "rodent-iv-plain.exe"
    profiles = {}
    books = {}
    per_player_books = {}
    for slug in slugs:
        player = find_player(slug)
        exe, paths = validate(root, player, player)
        if exe != engine:
            raise RuntimeError("Executable inconsistente no pré-voo")
        profiles[slug] = file_hash(paths[slug])
        required = official_book_hashes(root, paths) if preset == "official" else {}
        books.update(required)
        per_player_books[slug] = sorted(required)
    return {"engine_sha256": file_hash(engine), "profile_sha256": profiles,
            "book_sha256": books, "books_by_player": per_player_books}


def create_tournament(root: Path, directory: Path, *, participants: list[str],
                      seed: int, preset: str = "official",
                      max_tiebreak_pairs: int = 3, max_plies: int = 600,
                      max_game_wall_seconds: float | None = None) -> dict:
    directory = directory.expanduser().resolve()
    root = root.expanduser().resolve()
    if directory.exists():
        raise FileExistsError(f"Diretório já existe: {directory}")
    if preset not in CLOCKS:
        raise ValueError(f"Preset inválido: {preset}")
    if not 1 <= max_tiebreak_pairs <= 20:
        raise ValueError("max_tiebreak_pairs deve estar entre 1 e 20")
    if max_plies < 2:
        raise ValueError("max_plies deve ser >= 2")
    if max_game_wall_seconds is not None and (
            not math.isfinite(max_game_wall_seconds) or max_game_wall_seconds <= 0):
        raise ValueError("max_game_wall_seconds deve ser finito e positivo")
    selected = [find_player(p) for p in participants]
    if len(selected) < 2 or len(selected) > 64 or len(selected) & (len(selected) - 1):
        raise ValueError("Informe 2, 4, 8, 16, 32 ou 64 participantes")
    slugs = [p.slug for p in selected]
    if len(set(slugs)) != len(slugs):
        raise ValueError("Participantes repetidos")
    if any(p.rodent_alias is None for p in selected):
        missing = [p.name for p in selected if p.rodent_alias is None]
        raise ValueError(f"Perfis ainda indisponíveis: {', '.join(missing)}")
    resources = _preflight(root, slugs, preset)
    shuffled = slugs.copy()
    random.Random(seed).shuffle(shuffled)
    state = {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(),
             "status": "READY", "seed": seed, "participants": slugs,
             "initial_bracket": shuffled, "preset": preset,
             "clock": list(CLOCKS[preset]), "max_plies": max_plies,
             "max_game_wall_seconds": max_game_wall_seconds,
             "max_tiebreak_pairs": max_tiebreak_pairs,
             "engine": "Rodent IV", "historically_validated": False,
             "resources": resources, "rounds": [_round(1, shuffled)],
             "champion": None, "resolution_log": []}
    directory.mkdir(parents=True, exist_ok=False)
    _save(directory, state)
    return state


def _verify_resources(root: Path, state: dict) -> None:
    now = _preflight(root, state["participants"], state["preset"])
    if now != state["resources"]:
        raise RuntimeError("Executável, perfis ou livros divergiram da versão congelada do torneio")


def _duel_dir(directory: Path, match: dict) -> Path:
    return directory / "matches" / match["id"] / "duels"


def _valid_completed_attempt(directory: Path, state: dict, match: dict,
                             summary_file: Path) -> dict:
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    a, b = match["players"]
    if summary.get("status") != "COMPLETED":
        raise ValueError("Confronto ainda não concluído")
    games = summary.get("games", [])
    if summary.get("players") != [a, b] or len(games) != 2:
        raise RuntimeError(f"Resumo de confronto incompatível: {summary_file}")
    expected_resources = {"exe": state["resources"]["engine_sha256"],
                          a: state["resources"]["profile_sha256"][a],
                          b: state["resources"]["profile_sha256"][b]}
    if summary.get("file_hashes") != expected_resources:
        raise RuntimeError(f"Hashes do motor/perfis divergentes: {summary_file}")
    book_keys = set(state["resources"]["books_by_player"][a])
    book_keys.update(state["resources"]["books_by_player"][b])
    expected_books = {key: state["resources"]["book_sha256"][key] for key in book_keys}
    if summary.get("book_hashes") != expected_books:
        raise RuntimeError(f"Hashes dos livros divergentes: {summary_file}")
    protocol = summary.get("protocol", {})
    if state["preset"] == "official":
        if not isinstance(protocol, dict) or protocol.get("sha256") != OFFICIAL.json()["sha256"]:
            raise RuntimeError("Protocolo oficial incorreto")
    elif not isinstance(protocol, dict) or protocol.get("time_control") != (
        f"{state['clock'][0]}+{state['clock'][1]}"):
        raise RuntimeError("Ritmo do confronto divergente")
    pgn_file = summary_file.with_name("duelo.pgn")
    results = []
    digests = []
    with pgn_file.open(encoding="utf-8") as stream:
        for i in range(2):
            pgn = chess.pgn.read_game(stream)
            if pgn is None or pgn.errors:
                raise RuntimeError(f"PGN incompleto ou inválido: {pgn_file}")
            expected_white, expected_black = ((a, b) if i == 0 else (b, a))
            if (pgn.headers.get("White") != find_player(expected_white).name
                    or pgn.headers.get("Black") != find_player(expected_black).name):
                raise RuntimeError(f"Cores/identidade inconsistentes: {pgn_file}")
            result = games[i].get("result")
            digest, plies = mainline_identity(pgn)
            if (result not in RESULTS or games[i].get("status") != "COMPLETED"
                    or pgn.headers.get("Result") != result or
                    games[i].get("mainline_sha256") != digest
                    or games[i].get("plies") != plies):
                raise RuntimeError(f"Resultados ou trajetórias inconsistentes: {pgn_file}")
            if pgn.headers.get("Termination") != games[i].get("termination"):
                raise RuntimeError(f"Terminação do PGN diverge do resumo: {pgn_file}")
            if (pgn.headers.get("EngineSHA256") != state["resources"]["engine_sha256"]
                    or pgn.headers.get("WhiteProfileSHA256") !=
                    state["resources"]["profile_sha256"][expected_white]
                    or pgn.headers.get("BlackProfileSHA256") !=
                    state["resources"]["profile_sha256"][expected_black]
                    or pgn.headers.get("TimeControl") !=
                    f"{state['clock'][0]}+{state['clock'][1]}"):
                raise RuntimeError(f"Cabeçalhos de proveniência divergentes: {pgn_file}")
            if games[i]["termination"] == "TimeForfeit":
                remaining = games[i].get("clock_left")
                board = pgn.end().board()
                loser_index = 0 if board.turn else 1
                if (not isinstance(remaining, list) or len(remaining) != 2 or
                        remaining[loser_index] > 0):
                    raise RuntimeError(f"Tempo não comprovado no resumo: {pgn_file}")
            else:
                outcome = pgn.end().board().outcome(claim_draw=True)
                if outcome is None or outcome.result() != result:
                    raise RuntimeError(f"Resultado não comprovado pelo tabuleiro: {pgn_file}")
            results.append(result)
            digests.append(digest)
        if chess.pgn.read_game(stream) is not None:
            raise RuntimeError(f"PGN contém partidas extras: {pgn_file}")
    return {"folder": str(summary_file.parent.relative_to(directory)),
            "results": results, "mainline_sha256": digests,
            "summary_sha256": file_hash(summary_file), "pgn_sha256": file_hash(pgn_file)}


def _score(match: dict) -> dict:
    a, b = match["players"]
    scores = {a: 0.0, b: 0.0}
    for attempt in match["attempts"]:
        for i, result in enumerate(attempt["results"]):
            w, k = (a, b) if i == 0 else (b, a)
            if result == "1-0":
                scores[w] += 1
            elif result == "0-1":
                scores[k] += 1
            else:
                scores[w] += 0.5
                scores[k] += 0.5
    return scores


def _refresh_match(match: dict, max_pairs: int) -> None:
    if match.get("resolution"):
        match["winner"] = match["resolution"]["winner"]
        match["status"] = "COMPLETED"
        return
    scores = _score(match)
    a, b = match["players"]
    if scores[a] != scores[b]:
        match["winner"] = a if scores[a] > scores[b] else b
        match["status"] = "COMPLETED"
    elif len(match["attempts"]) >= max_pairs:
        match["winner"] = None
        match["status"] = "TIE_PENDING"
    elif match["excluded_attempts"] and (
            not match["attempts"] or
            match["excluded_attempts"][-1]["folder"] >
            match["attempts"][-1]["folder"]):
        match["winner"] = None
        match["status"] = "RETRY_REQUIRED"
    else:
        match["winner"] = None
        match["status"] = "PENDING"


def _reconcile(directory: Path, state: dict, match: dict) -> None:
    """Rebuild attempts after crashes, including pre-manifest SIGKILL windows.

    A created attempt directory is evidence even if summary.json was never
    written. It must not be silently ignored or promoted to a scored result.
    """
    a, b = match["players"]
    duel_dir = _duel_dir(directory, match)
    base = duel_dir / f"{a}-vs-{b}"
    # A symlink in any intermediate component can redirect the scan to files
    # outside the tournament; fail closed rather than traverse it.
    for component in (directory / "matches",
                      directory / "matches" / match["id"], duel_dir, base):
        if component.is_symlink():
            raise RuntimeError("Diretório de torneio redirecionado por symlink")
    if base.exists() and not base.is_dir():
        raise RuntimeError("Diretório de tentativas inválido")
    attempt_dirs = sorted(base.iterdir()) if base.exists() else []
    if len(attempt_dirs) > 1000:
        raise RuntimeError("Limite de tentativas examinadas excedido")
    present = set()
    seen_failures: dict[str, str] = {}
    previous = {x["folder"]: x for x in match["attempts"]}
    previous_winner = match.get("winner")
    was_final = previous_winner is not None
    for attempt_dir in attempt_dirs:
        if attempt_dir.is_symlink() or not attempt_dir.is_dir():
            raise RuntimeError("Tentativa de torneio redirecionada ou inválida")
        folder = str(attempt_dir.relative_to(directory))
        present.add(folder)
        summary_file = attempt_dir / "summary.json"
        if summary_file.is_symlink():
            raise RuntimeError("Resumo da tentativa redirecionado por symlink")
        if not summary_file.exists():
            if folder in previous:
                raise RuntimeError("Tentativa registrada perdeu o resumo")
            # SIGKILL/power loss may happen between mkdir and the first
            # atomic summary write. This is NOT a game result.
            seen_failures[folder] = "MISSING_SUMMARY"
            continue
        if not summary_file.is_file() or summary_file.stat().st_size > 1048576:
            raise RuntimeError("Resumo da tentativa inválido ou excessivo")
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("Resumo da tentativa não é objeto JSON")
        if summary.get("status") != "COMPLETED":
            if folder in previous:
                raise RuntimeError(f"Confronto contabilizado deixou de estar concluído: {folder}")
            seen_failures[folder] = "NOT_COMPLETED"
            continue
        attempt = _valid_completed_attempt(directory, state, match, summary_file)
        if folder in previous:
            if previous[folder] != attempt:
                raise RuntimeError(f"Artefato de partida alterado após registro: {folder}")
        else:
            if was_final:
                raise RuntimeError(f"Nova tentativa após encerramento do confronto: {folder}")
            match["attempts"].append(attempt)
    if set(previous) - present:
        raise RuntimeError("Artefato de confronto já registrado foi apagado")
    match["attempts"].sort(key=lambda item: item["folder"])
    match["excluded_attempts"] = [
        {"folder": folder, "reason": reason}
        for folder, reason in sorted(seen_failures.items())
    ]
    _refresh_match(match, state["max_tiebreak_pairs"])
    if was_final and match["winner"] != previous_winner:
        raise RuntimeError(f"Vencedor previamente confirmado foi alterado: {match['id']}")


def _advance(state: dict) -> None:
    while True:
        current = state["rounds"][-1]
        matches = current["matches"]
        if not all(m["winner"] is not None for m in matches):
            return
        if len(matches) == 1:
            state["champion"] = matches[0]["winner"]
            state["status"] = "COMPLETED"
            return
        players = [m["winner"] for m in matches]
        state["rounds"].append(_round(current["number"] + 1, players))


def _summary(state: dict) -> dict:
    matches = [m for rd in state["rounds"] for m in rd["matches"]]
    bracket = [{
        "round": rd["number"],
        "matches": [{"id": m["id"], "players": m["players"],
                     "score": _score(m), "status": m["status"],
                     "winner": m["winner"], "pairs_played": len(m["attempts"]),
                     "duels": [a["folder"] for a in m["attempts"]]}
                    for m in rd["matches"]]
    } for rd in state["rounds"]]
    return {"status": state["status"], "champion": state["champion"],
            "participants": len(state["participants"]), "bracket": bracket,
            "rounds": len(state["rounds"]),
            "matches_completed": sum(m["winner"] is not None for m in matches),
            "matches_total_generated": len(matches),
            "matches_pending_tiebreak": [m["id"] for m in matches
                                         if m["status"] == "TIE_PENDING"],
            "matches_retry_required": [m["id"] for m in matches
                                       if m["status"] == "RETRY_REQUIRED"]}


def tournament_status(directory: Path) -> dict:
    report = _summary(load_tournament(directory))
    report["stop_requested"] = _stop_file(directory).exists()
    return report


def tournament_recovery_preview(directory: Path) -> dict:
    """Read-only advisory: simulate reconciliation; do not persist or start UCI.

    A live runner may update the files while this snapshot is read. This
    function intentionally does not create/acquire the tournament lock.
    """
    directory = directory.expanduser().resolve()
    state = load_tournament(directory)
    candidate = deepcopy(state)
    recovered = []
    excluded = []
    for rd in candidate["rounds"]:
        for match in rd["matches"]:
            known = {attempt["folder"] for attempt in match["attempts"]}
            _reconcile(directory, candidate, match)
            recovered.extend({
                "match": match["id"], "folder": item["folder"],
            } for item in match["attempts"] if item["folder"] not in known)
            excluded.extend({
                "match": match["id"], "folder": item["folder"],
                "reason": item["reason"],
            } for item in match["excluded_attempts"])
    _advance(candidate)
    preview = _summary(candidate)
    stop = _stop_file(directory).exists()
    if candidate["champion"] is not None:
        recovery_gate = "COMPETITION_COMPLETE"
    elif stop:
        recovery_gate = "STOP_REQUESTED"
    elif preview["matches_retry_required"]:
        recovery_gate = "EXPLICIT_RETRY_REQUIRED"
    elif preview["matches_pending_tiebreak"]:
        recovery_gate = "TIEBREAK_DECISION_REQUIRED"
    else:
        recovery_gate = "NO_INCOMPLETE_ATTEMPTS_DETECTED"
    return {
        "schema": "chesssim-tournament-recovery-preview-v1",
        "mode": "READ_ONLY_ADVISORY",
        "checkpoint_status": state["status"],
        "projected_status": candidate["status"],
        "recovery_gate": recovery_gate,
        "stop_requested": stop,
        "recovered_completed_attempts": recovered,
        "excluded_incomplete_attempts": excluded,
        "matches_retry_required": preview["matches_retry_required"],
        "matches_pending_tiebreak": preview["matches_pending_tiebreak"],
        "projected_champion": preview["champion"],
        "projected_matches_completed": preview["matches_completed"],
        "warnings": [
            "Snapshot may race a running tournament; inspect only while idle.",
            "No game, process, UCI command, checkpoint or score was changed.",
            "An incomplete attempt requires explicit --retry-incomplete.",
            "Checkpoint/projected status is not an execution authorization; inspect recovery_gate.",
            "This does not test kernel resource quotas, SIGKILL or power-loss recovery on a real engine.",
        ],
    }


def run_tournament(root: Path, directory: Path, *, limit_pairs: int | None = None,
                   retry_incomplete: bool = False,
                   max_run_seconds: float | None = None) -> dict:
    directory = directory.expanduser().resolve()
    root = root.expanduser().resolve()
    if limit_pairs is not None and limit_pairs < 1:
        raise ValueError("limit_pairs deve ser positivo")
    if max_run_seconds is not None and (
            not math.isfinite(max_run_seconds) or max_run_seconds <= 0):
        raise ValueError("max_run_seconds deve ser finito e positivo")
    with _lock(directory):
        state = load_tournament(directory)
        _verify_resources(root, state)
        started = time.monotonic()

        def should_stop() -> bool:
            return (_stop_file(directory).exists() or
                    (max_run_seconds is not None and
                     time.monotonic() - started >= max_run_seconds))

        played = 0
        failed_this_run = set()
        while True:
            # Valida todos os artefatos já incorporados, inclusive de rodadas anteriores.
            for rd in state["rounds"]:
                for match in rd["matches"]:
                    _reconcile(directory, state, match)
            _advance(state)
            _save(directory, state)
            if state["champion"] is not None:
                break
            if should_stop():
                state["status"] = "PAUSED"
                _save(directory, state)
                return _summary(state)
            current = state["rounds"][-1]
            pending = [m for m in current["matches"]
                       if m["id"] not in failed_this_run and
                       (m["status"] == "PENDING" or
                        (retry_incomplete and m["status"] == "RETRY_REQUIRED"))]
            if not pending:
                state["status"] = "BLOCKED"
                break
            for match in pending:
                if should_stop():
                    state["status"] = "PAUSED"
                    _save(directory, state)
                    return _summary(state)
                if limit_pairs is not None and played >= limit_pairs:
                    state["status"] = "PAUSED"
                    _save(directory, state)
                    return _summary(state)
                a, b = (find_player(slug) for slug in match["players"])
                match["status"] = "RUNNING"
                state["status"] = "RUNNING"
                _save(directory, state)
                try:
                    play_duel(root, _duel_dir(directory, match), a, b,
                              depth=None, clock=tuple(state["clock"]),
                              reverse=True, max_plies=state["max_plies"],
                              use_books=state["preset"] == "official",
                              stop_check=should_stop,
                              max_wall_seconds=state.get("max_game_wall_seconds"))
                except BaseException:
                    state["status"] = "BLOCKED"
                    raise
                finally:
                    _reconcile(directory, state, match)
                    if match["status"] == "RETRY_REQUIRED":
                        failed_this_run.add(match["id"])
                    _save(directory, state)
                played += 1
            _advance(state)
            if state["champion"] is not None:
                break
            if should_stop():
                state["status"] = "PAUSED"
                break
            if limit_pairs is not None and played >= limit_pairs:
                state["status"] = "PAUSED"
                break
        if state["champion"] is None and state["status"] not in {"PAUSED", "BLOCKED"}:
            state["status"] = "READY"
        _save(directory, state)
        return _summary(state)


def resolve_tiebreak(directory: Path, match_id: str, winner: str, reason: str) -> dict:
    directory = directory.expanduser().resolve()
    if len(reason.strip()) < 10:
        raise ValueError("Justificativa de arbitragem insuficiente")
    winner = find_player(winner).slug
    with _lock(directory):
        state = load_tournament(directory)
        matches = [m for rd in state["rounds"] for m in rd["matches"]
                   if m["id"] == match_id]
        if len(matches) != 1:
            raise ValueError(f"Confronto inexistente: {match_id}")
        match = matches[0]
        _reconcile(directory, state, match)
        if match["status"] != "TIE_PENDING" or winner not in match["players"]:
            raise ValueError("Somente empate esgotado pode ser resolvido por arbitragem")
        decision = {"winner": winner, "reason": reason.strip(),
                    "method": "EXPLICIT_EXTERNAL_ARBITRATION_NOT_ENGINE_RESULT",
                    "utc": datetime.now(timezone.utc).isoformat()}
        match["resolution"] = decision
        state["resolution_log"].append({"match": match_id, **decision})
        _refresh_match(match, state["max_tiebreak_pairs"])
        _advance(state)
        state["status"] = "COMPLETED" if state["champion"] else "READY"
        _save(directory, state)
        return _summary(state)
