"""Adversarial tests for opt-in post-move FEN position embargo (#63)."""
from __future__ import annotations

import json

import chess
import chess.pgn
import pytest

from chesssim import custom_position_embargo as embargo


def _game(moves: str) -> chess.pgn.Game:
    game = chess.pgn.Game()
    board = game.board()
    node = game
    for token in moves.split():
        move = chess.Move.from_uci(token)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    return game


def _row(moves: str, idx: int) -> dict:
    return {
        "_game": _game(moves),
        "date": f"2026-02-{idx:02d}",
        "side": "white",
        "mainline_sha256": f"{idx:064x}",
    }


A = "g1f3 g8f6 g2g3 g7g6"
A_TRANSPOSED = "g2g3 g7g6 g1f3 g8f6"
B = "e2e4 e7e5 g1f3 b8c6"
C = "d2d4 d7d5 c2c4 e7e6"


def test_transposition_quarantines_whole_training_game_even_with_distinct_moves():
    original = {
        "train": [_row(A, 1)],
        "validation": [_row(A_TRANSPOSED, 2)],
        "holdout": [_row(B, 3)],
    }
    pieces, report = embargo.apply_embargo(original, min_ply=4)
    assert pieces["train"] == []
    assert len(pieces["validation"]) == 1
    assert len(pieces["holdout"]) == 1
    assert report["status"] == "AUDITED_DISJOINT"
    assert report["pre_quarantine_overlap"]["train_vs_validation_unique_positions"] == 1
    assert report["quarantined_games"]["train_overlap_reserved"] == 1
    assert report["post_quarantine_cross_partition_overlap_unique_positions"] == 0
    assert report["output_partition_games"]["train"] == 0
    assert report["original_partition_games"]["train"] == 1
    assert "g1f3" not in json.dumps(report)
    assert "rnbq" not in json.dumps(report)


def test_validation_transposition_is_quarantined_before_holdout_and_never_exposed():
    original = {
        "train": [_row(B, 1)],
        "validation": [_row(A_TRANSPOSED, 2)],
        "holdout": [_row(A, 3)],
    }
    pieces, report = embargo.apply_embargo(original, min_ply=4)
    assert len(pieces["train"]) == 1
    assert pieces["validation"] == []
    assert len(pieces["holdout"]) == 1
    assert report["quarantined_games"]["validation_overlap_holdout"] == 1
    assert report["pre_quarantine_overlap"]["validation_vs_holdout_unique_positions"] == 1
    assert report["status"] == "AUDITED_DISJOINT"
    assert "mainline_sha256" not in json.dumps(report)


def test_train_checked_against_original_reserved_even_if_validation_quarantined():
    original = {
        "train": [_row(A_TRANSPOSED, 1)],
        "validation": [_row(A_TRANSPOSED, 2)],
        "holdout": [_row(A, 3)],
    }
    pieces, report = embargo.apply_embargo(original, min_ply=4)
    assert pieces["train"] == []
    assert pieces["validation"] == []
    assert len(pieces["holdout"]) == 1
    assert report["quarantined_games"]["train_overlap_reserved"] == 1


def test_common_first_position_ignored_only_with_explicit_min_ply():
    original = {
        "train": [_row(A, 1)],
        "validation": [_row(A_TRANSPOSED, 2)],
        "holdout": [_row(B, 3)],
    }
    pieces, report = embargo.apply_embargo(original, min_ply=5)
    # No post-move state at ply 5 for a four-ply PGN: unassessed is never PASS.
    assert report["status"] == "INCOMPLETE_RESERVED_POSITION_COVERAGE"
    assert report["quarantined_games"]["train_unassessed_short"] == 1
    assert report["reserved_games_with_no_audited_position"] == 2
    assert pieces["train"] == []
    assert len(pieces["holdout"]) == 1


def test_early_common_opening_is_out_of_scope_after_explicit_cutoff():
    original = {
        "train": [_row("g1f3 g8f6 g2g3 g7g6", 1)],
        "validation": [_row("g1f3 g8f6 e2e4 e7e5", 2)],
        "holdout": [_row(B, 3)],
    }
    pieces, report = embargo.apply_embargo(original, min_ply=4)
    assert report["status"] == "AUDITED_DISJOINT"
    assert report["pre_quarantine_overlap"]["train_vs_validation_unique_positions"] == 0
    assert len(pieces["train"]) == 1
    assert len(pieces["validation"]) == 1
    assert len(pieces["holdout"]) == 1


def test_position_key_canonicalizes_away_clocks_but_preserves_castling_rights():
    one = chess.pgn.Game()
    board = chess.Board()
    one.setup(board)
    move = chess.Move.from_uci("e2e4")
    one.add_variation(move)
    without_castling = chess.pgn.Game()
    board2 = chess.Board(board.fen())
    board2.set_castling_fen("-")
    without_castling.setup(board2)
    without_castling.add_variation(move)
    count = [0]
    key1 = embargo._positions({"_game": one}, 1, count)
    key2 = embargo._positions({"_game": without_castling}, 1, count)
    assert key1 != key2
    assert all(len(key.split()) == 4 for key in key1 | key2)
    assert count[0] == 2


@pytest.mark.parametrize("value", [True, False, 0, 81, -1, 1.0, "1", None])
def test_invalid_min_ply_is_rejected_without_work(value):
    with pytest.raises(ValueError, match="1 a 80"):
        embargo.apply_embargo({
            "train": [], "validation": [], "holdout": []
        }, min_ply=value)


def test_global_position_quota_rejects_not_truncates(monkeypatch):
    monkeypatch.setattr(embargo, "MAX_REPLAYED_STATES", 1)
    with pytest.raises(ValueError, match="Orçamento"):
        embargo.apply_embargo({
            "train": [_row(B, 1)],
            "validation": [], "holdout": []
        }, min_ply=1)


def test_reserved_partition_absent_cannot_be_certified():
    final, report = embargo.apply_embargo({
        "train": [_row(A, 1)], "validation": [], "holdout": []
    }, min_ply=4)
    assert len(final["train"]) == 1
    assert report["status"] == "INCOMPLETE_RESERVED_POSITION_COVERAGE"


def test_unchanged_source_input_and_deterministic_output():
    original = {
        "train": [_row(A, 1), _row(B, 2)],
        "validation": [_row(A_TRANSPOSED, 3)],
        "holdout": [_row(C, 4)],
    }
    out1, report1 = embargo.apply_embargo(original, min_ply=4)
    out2, report2 = embargo.apply_embargo(original, min_ply=4)
    assert [len(out1[k]) for k in original] == [len(out2[k]) for k in original]
    assert report1 == report2
    assert [len(original[k]) for k in original] == [2, 1, 1]
    assert "train_overlap_reserved" in report1["quarantined_games"]
