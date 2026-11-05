"""Execução UCI real, partidas legais e PGNs atômicos. Importa python-chess sob demanda."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import uuid
import re
import time
from typing import Callable

from .protocol import OFFICIAL

import chess
import chess.engine
import chess.pgn

from .catalog import Player
from .game_diversity import audit_mainlines, mainline_identity
from .registry import registered_aliases


def file_hash(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate(root: Path, white: Player, black: Player) -> tuple[Path, dict[str, Path]]:
    exe = root / "rodent-iv-plain.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Motor não encontrado: {exe}")
    aliases = registered_aliases(root / "personalities" / "basic.ini")
    paths = {}
    for p in (white, black):
        if p.rodent_alias is None:
            raise ValueError(f"{p.name} ainda NÃO tem perfil histórico calibrado. Não será simulado.")
        registered = aliases.get(p.rodent_alias)
        expected = p.profile_filename
        if registered != expected:
            raise ValueError(f"{p.name}: Personality {p.rodent_alias} não registrado como {expected} no basic.ini")
        path = root / "personalities" / expected
        if not path.is_file():
            raise FileNotFoundError(f"Perfil não encontrado: {path}")
        paths[p.slug] = path
    return exe, paths


def official_book_hashes(root: Path, profile_paths: dict[str, Path]) -> dict[str, str]:
    """Recusa torneio oficial com livros faltantes ou vazios."""
    books = {}
    rx = re.compile(r"(?m)^setoption name (?:GuideBookFile|MainBookFile) value ((?:players|hist)/[\w.-]+\.bin)\s*$")
    for path in profile_paths.values():
        for rel in rx.findall(path.read_text(encoding="utf-8-sig", errors="replace")):
            p = root / "books" / rel
            if not p.is_file() or p.stat().st_size == 0:
                raise FileNotFoundError(f"Livro histórico exigido não encontrado: {p}")
            books[rel] = file_hash(p)
    return books


def _open_engine(root: Path, executable: Path, p: Player, *, book: bool | None = None):
    e = chess.engine.SimpleEngine.popen_uci(str(executable), cwd=str(root), timeout=120.0)
    try:
        if "Personality" not in e.options:
            raise RuntimeError("Motor não anunciou opção UCI 'Personality'")
        personality = e.options["Personality"]
        if personality.var and p.rodent_alias not in personality.var:
            raise RuntimeError(f"{p.rodent_alias} não anunciado na opção Personality")
        # IMPORTANTE: Personality pode redefinir os parâmetros de força.
        e.configure({"Personality": p.rodent_alias})
        settings = {}
        for key, val in (("Threads", OFFICIAL.threads_per_engine), ("Hash", OFFICIAL.hash_mb_per_engine), ("UCI_LimitStrength", False), ("UseBook", book)):
            if key in e.options and val is not None:
                settings[key] = val
        if settings:
            e.configure(settings)
        return e
    except BaseException:
        try:
            e.quit()
        except Exception:
            e.close()
        raise


def _atomic_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as f:
            f.write(contents)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _play_one(root: Path, exe: Path, white: Player, black: Player,
              depth: int | None, movetime: float | None, max_plies: int, round_number: int,
              hashes: dict[str, str], *, clock: tuple[int, int] | None = None,
              use_books: bool | None = None,
              historical_openings: dict | None = None, opening_seed: int = 2029,
              opening_min_count: int = 1,
              stop_check: Callable[[], bool] | None = None,
              on_progress: Callable[[chess.pgn.Game], None] | None = None,
              max_wall_seconds: float | None = None) -> tuple[chess.pgn.Game, dict]:
    engines = []
    try:
        w = _open_engine(root, exe, white, book=use_books)
        engines.append(w)
        b = _open_engine(root, exe, black, book=use_books)
        engines.append(b)
        board = chess.Board()
        game = chess.pgn.Game()
        game.headers.update({
            "Event": ("Copa dos Imortais - Oficial" if clock == (600, 2) else
                      "Copa dos Imortais - Ritmo Experimental" if clock else
                      "Copa dos Imortais - Duelo de Desenvolvimento"),
            "Site": "Local WSL",
            "Date": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
            "Round": str(round_number),
            "White": white.name,
            "Black": black.name,
            "WhiteModel": f"RodentIV:{white.rodent_alias}",
            "BlackModel": f"RodentIV:{black.rodent_alias}",
            "EngineSHA256": hashes["exe"],
            "WhiteProfileSHA256": hashes[white.slug],
            "BlackProfileSHA256": hashes[black.slug],
            "SearchLimit": (f"{clock[0]//60}+{clock[1]}" if clock else
                            f"depth={depth}" if depth is not None else f"time={movetime}s"),
            "ProtocolSHA256": (OFFICIAL.json()["sha256"] if clock == (600, 2) else
                                "v06-experimental-clock" if clock else "development"),
            "TimeControl": f"{clock[0]}+{clock[1]}" if clock else "-",
            "BookMode": "historical-v09-exploratory" if historical_openings else "historical" if use_books else "disabled" if use_books is not None else "legacy",
        })
        if historical_openings:
            game.headers["OpeningSeed"] = str(opening_seed)
            game.headers["OpeningMinCount"] = str(opening_min_count)
        opening_trace = []
        node = game
        limit = (chess.engine.Limit(depth=depth) if depth is not None
                 else chess.engine.Limit(time=movetime) if movetime is not None
                 else None)
        clocks = [float(clock[0]), float(clock[0])] if clock else None
        elapsed = [0.0, 0.0]
        opening_moves = {'white': 0, 'black': 0}
        started = time.monotonic()
        interrupted = None
        if on_progress is not None:
            on_progress(game)
        for ply in range(max_plies):
            if board.is_game_over(claim_draw=True):
                break
            if stop_check is not None and stop_check():
                interrupted = "OperatorStop"
                break
            if max_wall_seconds is not None and time.monotonic() - started >= max_wall_seconds:
                interrupted = "WallTimeLimit"
                break
            actor = w if board.turn == chess.WHITE else b
            idx = 0 if board.turn else 1
            turn_limit = (chess.engine.Limit(white_clock=max(0.001, clocks[0]),
                         black_clock=max(0.001, clocks[1]),
                         white_inc=clock[1], black_inc=clock[1]) if clock else limit)
            opening_move = None
            opening_source = None
            if historical_openings:
                from .opening_books import select_book_move
                owner = white.slug if board.turn == chess.WHITE else black.slug
                selected = historical_openings.get(owner)
                if selected is not None:
                    opening_move = select_book_move(selected, board, seed=opening_seed,
                                                    game_number=round_number,
                                                    min_count=opening_min_count)
                    if opening_move is not None:
                        opening_source = owner
            start = time.monotonic()
            if opening_move is not None:
                actual_move = opening_move
            else:
                result = actor.play(board, turn_limit, game=game, info=chess.engine.INFO_NONE)
                actual_move = result.move
            spent = time.monotonic() - start
            elapsed[idx] += spent
            if clocks is not None:
                clocks[idx] -= spent
                if clocks[idx] <= 0:
                    # O último lance não conta; vitória por tempo se o adversário
                    # possui material suficiente para dar mate em princípio.
                    loser_is_white = board.turn == chess.WHITE
                    res = "0-1" if loser_is_white else "1-0"
                    if board.has_insufficient_material(not board.turn):
                        res = "1/2-1/2"
                    game.headers["Result"] = res
                    game.headers["Termination"] = "TimeForfeit"
                    meta = {"round": round_number, "white": white.slug, "black": black.slug,
                            "result": res, "status": "COMPLETED", "termination": "TimeForfeit",
                            "plies": board.ply(), "elapsed_seconds": elapsed, "clock_left": clocks,
                            "opening_moves": opening_moves, "opening_trace": opening_trace,
                            "opening_seed": opening_seed if historical_openings else None,
                            "opening_mode": game.headers["BookMode"],
                            "color_order": f"{white.slug}|{black.slug}"}
                    return game, meta
                clocks[idx] += clock[1]
            if actual_move is None or actual_move not in board.legal_moves:
                raise RuntimeError("Motor/repertório devolveu um lance ausente ou ilegal")
            san = board.san(actual_move)
            prefix = f"{board.fullmove_number}." if board.turn else f"{board.fullmove_number}..."
            print(f"{prefix} {san}", flush=True)
            node = node.add_variation(actual_move)
            if opening_source is not None:
                node.comment = 'ChessSimulation64 historical opening policy (exploratory)'
                opening_moves['white' if board.turn == chess.WHITE else 'black'] += 1
                opening_trace.append({
                    "ply": board.ply() + 1,
                    "player": opening_source,
                    "move": actual_move.uci(),
                    "seed": opening_seed,
                    "round": round_number,
                    "book_source_sha256": historical_openings[opening_source].get("source", {}).get("pgn_sha256"),
                })
            board.push(actual_move)
            if on_progress is not None:
                on_progress(game)
            if clocks is not None:
                print(f"Relógios: brancas={clocks[0]:.1f}s | pretas={clocks[1]:.1f}s",
                      flush=True)

        outcome = board.outcome(claim_draw=True)
        if interrupted is not None:
            res, term, status = "*", interrupted, "INCOMPLETE"
        elif outcome is None:
            # Não se inventam empates: partida interrompida fica como inconclusiva.
            res, term, status = "*", "InterruptedAtPlyLimit", "INCOMPLETE"
        else:
            res, term, status = outcome.result(), outcome.termination.name, "COMPLETED"
        game.headers["Result"] = res
        game.headers["Termination"] = term
        meta = {"round": round_number, "white": white.slug, "black": black.slug,
                "result": res, "status": status, "termination": term, "plies": board.ply(),
                "elapsed_seconds": elapsed, "clock_left": clocks, "opening_moves": opening_moves,
                "opening_trace": opening_trace,
                "opening_seed": opening_seed if historical_openings else None,
                "opening_mode": game.headers["BookMode"],
                "color_order": f"{white.slug}|{black.slug}"}
        return game, meta
    finally:
        for e in reversed(engines):
            try:
                e.quit()
            except BaseException:
                # SimpleEngine.close encerra o transporte, mesmo se o UCI quit falhar.
                pass
            finally:
                try:
                    e.close()
                except BaseException:
                    pass


def play_duel(root: Path, output_dir: Path, white: Player, black: Player,
              *, depth: int | None = 6, movetime: float | None = None,
              reverse: bool = True, max_plies: int = 400,
              clock: tuple[int, int] | None = None, use_books: bool | None = None,
              historical_openings: dict | None = None, opening_seed: int = 2029,
              opening_min_count: int = 1,
              stop_check: Callable[[], bool] | None = None,
              max_wall_seconds: float | None = None) -> Path:
    root = root.expanduser().resolve()
    exe, profile_paths = validate(root, white, black)
    if sum(x is not None for x in (depth, movetime, clock)) != 1:
        raise ValueError("Informe exatamente um limite: --depth, --movetime ou --preset")
    if clock and (clock[0] <= 0 or clock[1] < 0):
        raise ValueError("Relógio inválido")
    if depth is not None and not 1 <= depth <= 99:
        raise ValueError("Depth fora do intervalo 1..99")
    if movetime is not None and movetime <= 0:
        raise ValueError("Movetime deve ser positivo")
    if max_plies < 1:
        raise ValueError("Max-plies deve ser >= 1")
    if max_wall_seconds is not None and max_wall_seconds <= 0:
        raise ValueError("max_wall_seconds deve ser positivo")
    if not 1 <= opening_min_count <= 100:
        raise ValueError('Opening min_count must be 1..100')
    if historical_openings and (use_books is True or clock == (600, 2)):
        raise ValueError('Aberturas condicionadas não integram protocolo oficial; execute como desenvolvimento')
    if historical_openings and not all(k in (white.slug, black.slug) for k in historical_openings):
        raise ValueError('Opening book is not bound to a participating player')

    attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = output_dir.expanduser().resolve() / f"{white.slug}-vs-{black.slug}" / attempt
    folder.mkdir(parents=True, exist_ok=False)
    hashes = {"exe": file_hash(exe)}
    book_hashes = official_book_hashes(root, profile_paths) if use_books else {}
    for slug, path in profile_paths.items():
        hashes[slug] = file_hash(path)
    games: list[chess.pgn.Game] = []
    summary = {"status": "RUNNING", "protocol": (OFFICIAL.json() if clock == (600, 2) else
               {"version":"v06-experimental-clock","time_control":f"{clock[0]}+{clock[1]}",
                "historical_mode_strength_verified":False} if clock else "development"), "attempt": attempt, "players": [white.slug, black.slug],
               "engine_path": str(exe), "file_hashes": hashes,
               "book_hashes": book_hashes,
               "historical_opening_policy": ("EXPLORATORY_POSITION_CONDITIONED" if historical_openings else "DISABLED"),
               "opening_seed": opening_seed if historical_openings else None,
               "variation_control": {
                   "historical_opening_seed_effective": bool(historical_openings),
                   "internal_uci_book": use_books,
                   "colors_reversed": reverse,
                   "warning": ("An opening seed has no effect without the external historical opening policy; "
                               "internal engine book/engine search randomness is not controlled by this seed."),
               },
               "games": [], "diversity": audit_mainlines([])[1]}
    _atomic_text(folder / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lineups = [(white, black)] + ([(black, white)] if reverse else [])
    try:
        for i, (w, b) in enumerate(lineups, 1):
            if stop_check is not None and stop_check():
                break
            print(f"\n=== Jogo {i}: {w.name} (brancas) x {b.name} (pretas) ===", flush=True)

            def checkpoint(partial_game: chess.pgn.Game) -> None:
                # O arquivo .partial é uma evidência, nunca um resultado válido.
                _atomic_text(folder / f"jogo_{i}.partial.pgn",
                             str(partial_game) + "\n\n")

            game, meta = _play_one(root, exe, w, b, depth, movetime, max_plies, i, hashes,
                                   clock=clock, use_books=use_books,
                                   historical_openings=historical_openings,
                                   opening_seed=opening_seed, opening_min_count=opening_min_count,
                                   stop_check=stop_check, on_progress=checkpoint,
                                   max_wall_seconds=max_wall_seconds)
            digest, plies = mainline_identity(game)
            if plies != meta["plies"]:
                raise RuntimeError("PGN/mainline ply count disagrees with engine record")
            meta.update({"mainline_sha256": digest, "source": f"game_{i}"})
            games.append(game)
            _atomic_text(folder / f"jogo_{i}.pgn", str(game) + "\n\n")
            _atomic_text(folder / "duelo.pgn", "\n\n".join(str(g) for g in games) + "\n\n")
            summary["games"].append(meta)
            summary["games"], summary["diversity"] = audit_mainlines(summary["games"])
            _atomic_text(folder / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
            print(f"Resultado: {meta['result']} ({meta['termination']})", flush=True)
            # Partidas limitadas por plies continuam úteis para auditoria de
            # diversidade; somente parada/timeout operacional cancela o par.
            if meta["termination"] in {"OperatorStop", "WallTimeLimit"}:
                break
        summary["status"] = ("COMPLETED" if len(summary["games"]) == len(lineups)
                             and all(g["status"] == "COMPLETED" for g in summary["games"])
                             else "INCOMPLETE")
    except BaseException as ex:
        summary["status"] = "FAILED"
        summary["error"] = f"{type(ex).__name__}: {ex}"
        raise
    finally:
        _atomic_text(folder / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(f"\nArquivos do confronto: {folder}", flush=True)
    return folder
