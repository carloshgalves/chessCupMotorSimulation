"""Hermetic native UCI integration for analyze-quality (no Stockfish download).

The stub reports deterministic legal PVs and accepts both normal and
root_moves-restricted search over the actual position passed by python-chess.
"""
from __future__ import annotations

import sys

import pytest

from chesssim import quality_audit as quality


UCI_STUB = r'''#!/usr/bin/env python3
import chess
import sys

board = chess.Board()
for raw in sys.stdin:
    command = raw.strip()
    if command == "uci":
        print("id name Quality Audit UCI Stub")
        print("id author ChessSim regression fixture")
        print("option name Threads type spin default 1 min 1 max 8")
        print("option name Hash type spin default 64 min 1 max 512")
        print("uciok", flush=True)
    elif command == "isready":
        print("readyok", flush=True)
    elif command.startswith("position "):
        pos = command[len("position "):]
        state, sep, moves = pos.partition(" moves ")
        if state == "startpos":
            board = chess.Board()
        elif state.startswith("fen "):
            board = chess.Board(state[4:])
        else:
            raise RuntimeError(f"Invalid position: {state}")
        if sep:
            for move in moves.split():
                board.push_uci(move)
    elif command.startswith("go "):
        choices = sorted(board.legal_moves, key=lambda m: m.uci())
        if not choices:
            print("bestmove 0000", flush=True)
            continue
        if " searchmoves " in command:
            forced = command.split(" searchmoves ", 1)[1].split()
            forced = [chess.Move.from_uci(text) for text in forced]
            choices = [move for move in choices if move in forced]
            if not choices:
                print("bestmove 0000", flush=True)
                continue
            cp = 40
        else:
            cp = 80
        chosen = choices[0]
        print(f"info depth 5 nodes 15 score cp {cp} pv {chosen.uci()}")
        print(f"bestmove {chosen.uci()}", flush=True)
    elif command == "quit":
        break
'''


@pytest.mark.skipif(sys.platform != "linux", reason="Uses native executable bit and UCI process")
def test_quality_report_with_real_uci_subprocess(tmp_path):
    import chess
    import chess.pgn

    exe = tmp_path / "stockfish-uci-stub"
    exe.write_text(UCI_STUB, encoding="utf-8")
    exe.chmod(0o755)
    pgn = tmp_path / "small.pgn"
    game = chess.pgn.Game()
    game.headers["White"] = "Profile Alpha"
    game.headers["Black"] = "Profile Beta"
    position = game.board()
    node = game
    for move in ("e2e4", "e7e5", "g1f3", "b8c6"):
        selected = chess.Move.from_uci(move)
        assert selected in position.legal_moves
        node = node.add_variation(selected)
        position.push(selected)
    pgn.write_text(str(game) + "\n", encoding="utf-8")
    output = tmp_path / "quality.json"

    report = quality.analyze_games_v91(
        pgn, exe, output, depth=5, max_positions=4, min_fullmove=1,
        pv_plies=2,
    )
    assert report["inputs"]["engine_uci_id"]["name"] == "Quality Audit UCI Stub"
    assert report["inputs"]["engine_configured_options"] == {"Threads":1, "Hash":64}
    assert len(report["decisions"]) == 4
    assert report["reconciliation"]["status"] == "CONSISTENT"
    assert sum(row["positions_analyzed"] for row in report["games"]) == 4
    assert all(row["pv_availability"] == {"best": "PRESENT", "forced": "PRESENT"}
               for row in report["decisions"])
    for row in report["decisions"]:
        assert row["played_line_uci"][0] == row["played"]
        assert chess.Move.from_uci(row["best_move"]) in chess.Board(row["fen"]).legal_moves
