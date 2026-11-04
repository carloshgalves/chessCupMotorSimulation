"""Strength proxy: fixed-opening paired games at fixed depth; never estimate Elo.

Does not replace official tournament protocol. Unfinished games are failures,
not fabricated draws. Exactly two Rodent processes are reused.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .calibration import file_hash
from .research_v3 import staged_profile
from .uci_raw import RodentUci

OPENINGS = (
    ("Open game", ("e2e4", "e7e5", "g1f3", "b8c6")),
    ("Queen pawn", ("d2d4", "d7d5", "c2c4", "e7e6")),
    ("Sicilian", ("e2e4", "c7c5", "g1f3", "d7d6")),
    ("English", ("c2c4", "e7e5", "b1c3", "g8f6")),
)


def run_benchmark(root: Path, profile: Path, output: Path, *,
                  opponent: str = "karpov.txt", depth: int = 5,
                  openings: int = 3, max_plies: int = 450) -> dict:
    import chess
    import chess.pgn
    if not (1 <= depth <= 20 and 1 <= openings <= len(OPENINGS) and max_plies >= 20):
        raise ValueError("Parâmetros benchmark inválidos")
    root = root.resolve()
    profile = profile.resolve()
    exe = root / "rodent-iv-plain.exe"
    against = root / "personalities" / opponent
    if against.resolve().parent != (root / "personalities").resolve() or not against.is_file():
        raise ValueError("Oponente deve ser perfil oficial direto em personalities")
    if not profile.is_file() or not exe.is_file():
        raise FileNotFoundError("Perfil candidato ou motor ausente")
    if output.exists():
        raise FileExistsError(output)
    results, pgns = [], []
    with staged_profile(root, profile.read_text(encoding="utf-8-sig")) as candidate_rel:
        with RodentUci(exe, root, profile=candidate_rel, book=False) as candidate_engine:
            with RodentUci(exe, root, profile=opponent, book=False) as opponent_engine:
                for opening_name, opening_moves in OPENINGS[:openings]:
                    for candidate_white in (True, False):
                        candidate_engine.send("ucinewgame")
                        candidate_engine.send("isready")
                        candidate_engine.wait_for("readyok")
                        opponent_engine.send("ucinewgame")
                        opponent_engine.send("isready")
                        opponent_engine.wait_for("readyok")
                        board = chess.Board()
                        game = chess.pgn.Game()
                        game.headers.update({"Event": "ChessSim v0.3 strength proxy (not Elo)",
                                             "White": "Candidate" if candidate_white else opponent,
                                             "Black": opponent if candidate_white else "Candidate",
                                             "Opening": opening_name,
                                             "Depth": str(depth)})
                        node = game
                        for uci in opening_moves:
                            move = chess.Move.from_uci(uci)
                            if move not in board.legal_moves:
                                raise ValueError(f"Abertura inválida: {uci}")
                            node = node.add_variation(move)
                            board.push(move)
                        for ply in range(max_plies - len(opening_moves)):
                            if board.is_game_over(claim_draw=True):
                                break
                            is_candidate_turn = (board.turn == chess.WHITE) == candidate_white
                            engine = candidate_engine if is_candidate_turn else opponent_engine
                            move_uci, _ = engine.bestmove(board.fen(), depth)
                            move = chess.Move.from_uci(move_uci)
                            if move not in board.legal_moves:
                                raise RuntimeError(f"Lance ilegal no benchmark: {move_uci}")
                            node = node.add_variation(move)
                            board.push(move)
                        outcome = board.outcome(claim_draw=True)
                        result = outcome.result() if outcome is not None else "*"
                        end = outcome.termination.name if outcome is not None else "PLY_LIMIT_INCOMPLETE"
                        game.headers["Result"] = result
                        game.headers["Termination"] = end
                        if result == "*":
                            score = None
                        elif result == "1/2-1/2":
                            score = .5
                        else:
                            score = float((result == "1-0") == candidate_white)
                        results.append({"opening": opening_name, "candidate_color": "white" if candidate_white else "black",
                                        "result": result, "candidate_score": score,
                                        "termination": end, "plies": board.ply()})
                        pgns.append(str(game))
                        print(f"{opening_name:<15} {'W' if candidate_white else 'B'}: {result} {end}", flush=True)
    completed = [r for r in results if r["candidate_score"] is not None]
    report = {"status": "COMPLETE" if len(completed) == len(results) else "INCOMPLETE",
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "candidate_sha256": file_hash(profile), "opponent_sha256": file_hash(against),
              "engine_sha256": file_hash(exe), "depth": depth,
              "opening_policy": "frozen UCI positions; reversed colors; no books",
              "results": results, "completed": len(completed), "scheduled": len(results),
              "candidate_points": sum(r["candidate_score"] for r in completed),
              "note": "Small paired-depth benchmark is NOT Elo, does not establish historical strength."}
    output.mkdir(parents=True, exist_ok=False)
    (output / "benchmark.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    (output / "benchmark.pgn").write_text("\n\n".join(pgns)+"\n", encoding="utf-8")
    return report
