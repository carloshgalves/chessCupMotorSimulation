"""Auditoria sequencial: smoke test isolado e duelos completos (cores invertidas).

Quick abre apenas um processo por perfil e testa busca real em duas posições,
uma para cada cor. Full abre um par por perfil, reutilizando-o para os dois
jogos e evitando dezenas de inicializações simultâneas de .exe via WSL.

Um resultado incompleto nunca equivale a empate nem conta como aprovado.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from .catalog import load_roster
from .matches import validate, _open_engine, _atomic_text, file_hash, official_book_hashes


def _safe_quit(engine) -> None:
    if engine is None:
        return
    try:
        engine.quit()
    except Exception:
        try:
            engine.close()
        except Exception:
            pass


def _failure(color: str, exc: Exception) -> dict:
    return {"color": color, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}


def _quick_probe(engine, *, color: str, depth: int) -> dict:
    board = chess.Board()
    if color == "black":
        board.push_uci("e2e4")  # posição legal, comum, com pretas a jogar
    reply = engine.play(board, chess.engine.Limit(depth=depth),
                        game=object(), info=chess.engine.INFO_NONE, ponder=False)
    if reply.move is None or reply.move not in board.legal_moves:
        raise RuntimeError(f"Motor não devolveu lance legal ({color}): {reply.move}")
    san = board.san(reply.move)
    return {"color": color, "status": "PASS", "plies": 1,
            "fen": board.fen(), "move": reply.move.uci(), "san": san,
            "termination": "SMOKE_LEGAL_MOVE"}


def _full_game(root: Path, white, black, white_engine, black_engine,
               *, depth: int, max_plies: int, use_books: bool, output: Path) -> dict:
    board = chess.Board()
    game = chess.pgn.Game()
    game.headers.update({
        "Event": "Copa 64 - Auditoria completa", "White": white.name,
        "Black": black.name, "WhiteModel": str(white.rodent_alias),
        "BlackModel": str(black.rodent_alias), "SearchLimit": f"depth={depth}",
        "BookMode": str(use_books), "Result": "*"})
    node = game
    for _ in range(max_plies):
        if board.is_game_over(claim_draw=True):
            break
        engine = white_engine if board.turn == chess.WHITE else black_engine
        reply = engine.play(board, chess.engine.Limit(depth=depth),
                            game=game, info=chess.engine.INFO_NONE, ponder=False)
        if reply.move is None or reply.move not in board.legal_moves:
            raise RuntimeError(f"Lance ilegal/ausente: {reply.move}")
        node = node.add_variation(reply.move)
        board.push(reply.move)
    outcome = board.outcome(claim_draw=True)
    status = "PASS" if outcome is not None else "INCOMPLETE"
    game.headers["Result"] = outcome.result() if outcome else "*"
    game.headers["Termination"] = outcome.termination.name if outcome else "PLY_LIMIT"
    _atomic_text(output, str(game) + "\n\n")
    return {"status": status, "plies": board.ply(),
            "result": game.headers["Result"], "termination": game.headers["Termination"],
            "pgn": str(output)}


def audit_roster(root: Path, out: Path, *, depth: int = 4, full: bool = False,
                 both_colors: bool = True, max_plies: int = 600,
                 use_books: bool = False) -> dict:
    if not 1 <= depth <= 20:
        raise ValueError("depth deve ser 1..20")
    if max_plies < 8:
        raise ValueError("max_plies muito baixo (<8)")
    players = [p for p in load_roster() if p.rodent_alias]
    if not players:
        raise ValueError("Nenhum perfil oficial no elenco")
    baseline = next(p for p in players if p.slug == "fischer")
    root = root.resolve()
    exe = root / "rodent-iv-plain.exe"
    if not exe.is_file():
        raise FileNotFoundError(exe)
    out.mkdir(parents=True, exist_ok=False)
    report = {"status": "RUNNING", "date_utc": datetime.now(timezone.utc).isoformat(),
              "engine_sha256": file_hash(exe), "depth": depth, "full": full,
              "both_colors": both_colors, "use_books": use_books,
              "max_plies": max_plies, "entries": []}
    _atomic_text(out / "audit.json", json.dumps(report, indent=2, ensure_ascii=False))

    for idx, player in enumerate(players, 1):
        colors = ["white", "black"] if both_colors else ["white"]
        entries = []
        subject_engine = None
        baseline_engine = None
        try:
            # Verificar arquivos e livros ANTES de abrir processos; erro por perfil
            # não interrompe a auditoria dos demais.
            _, profiles = validate(root, player, baseline)
            if use_books:
                official_book_hashes(root, profiles)
            subject_engine = _open_engine(root, exe, player, book=use_books)
            if full:
                baseline_engine = _open_engine(root, exe, baseline, book=use_books)
            for color in colors:
                try:
                    if full:
                        if color == "white":
                            white, black = player, baseline
                            white_engine, black_engine = subject_engine, baseline_engine
                        else:
                            white, black = baseline, player
                            white_engine, black_engine = baseline_engine, subject_engine
                        label = f"{player.slug}_{'w' if color=='white' else 'b'}"
                        result = _full_game(root, white, black, white_engine, black_engine,
                                            depth=depth, max_plies=max_plies,
                                            use_books=use_books, output=out / f"{label}.pgn")
                        entries.append({"color": color, **result})
                    else:
                        entries.append(_quick_probe(subject_engine, color=color, depth=depth))
                except Exception as exc:
                    entries.append(_failure(color, exc))
                    # Uma falha de motor pode deixar o processo sem comunicação.
                    # Não insistir na segunda cor sem saber se o motor está vivo.
                    if isinstance(exc, (chess.engine.EngineTerminatedError, BrokenPipeError, OSError)):
                        for remaining in colors[len(entries):]:
                            entries.append({"color": remaining, "status": "NOT_RUN",
                                            "error": "Motor falhou na etapa anterior; nova inicialização necessária"})
                        break
        except Exception as exc:
            for color in colors:
                entries.append(_failure(color, exc) if not entries else {
                    "color": color, "status": "NOT_RUN",
                    "error": f"Falha inicial antes das partidas: {type(exc).__name__}: {exc}"})
        finally:
            _safe_quit(baseline_engine)
            _safe_quit(subject_engine)
        ok = len(entries) == len(colors) and all(x["status"] == "PASS" for x in entries)
        profile_path = root / "personalities" / player.profile_filename
        row = {"player": player.slug, "alias": player.rodent_alias,
               "profile_sha256": file_hash(profile_path) if profile_path.is_file() else None,
               "status": "PASS" if ok else "FAIL_OR_INCOMPLETE", "games": entries}
        report["entries"].append(row)
        _atomic_text(out / "audit.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        first_error = next((entry["error"] for entry in entries if "error" in entry), "")
        print(f"{idx:02}/{len(players)} {player.rodent_alias:<14} {row['status']}"
              + (f" | {first_error}" if first_error else ""), flush=True)
    report["passed"] = sum(row["status"] == "PASS" for row in report["entries"])
    report["status"] = "PASS" if report["passed"] == len(players) else "FAIL_OR_INCOMPLETE"
    _atomic_text(out / "audit.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report
