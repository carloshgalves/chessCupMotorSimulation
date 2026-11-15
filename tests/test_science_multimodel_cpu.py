"""Smoke of comparable metric domain, not scientific synthetic evidence."""
import chess
import pytest
from chesssim.science_multimodel_cpu import _metrics, MAIA23_HF_REVISION, DSC_SOURCE_COMMIT


def test_ranks_share_identical_rows_and_distinguish_top1_top3():
    fen = chess.Board().fen()
    rows = [{"fen": fen, "move": "g1f3", "game_sha256": "1"*64}]
    good = _metrics(rows, [["e2e4", "g1f3", "d2d4"]])
    assert good["exact_match"] == 0
    assert good["top3_correct"] == 1
    assert good["top3"] == 1
    with pytest.raises(ValueError, match="position counts"):
        _metrics(rows, [])
    assert len(MAIA23_HF_REVISION) == 40
    assert len(DSC_SOURCE_COMMIT) == 40
