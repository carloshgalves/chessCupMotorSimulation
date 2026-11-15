"""Smoke of comparable metric domain, not scientific synthetic evidence."""
import chess
import pytest
from chesssim.science_multimodel_cpu import _metrics, _peak_rss_kib, MAIA23_HF_REVISION, MAIA23_CHECKPOINT_SHA256, DSC_SOURCE_COMMIT


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


def test_diagnostic_one_move_does_not_fabricate_top3():
    row = {"fen": chess.Board().fen(), "move": "e2e4", "game_sha256": "2"*64}
    metric = _metrics([row], [["e2e4"]], full_ranking=False)
    assert metric["top1"] == 1
    assert metric["top3"] is None
    assert metric["top3_correct"] is None
    with pytest.raises(ValueError, match="Top-3"):
        _metrics([row], [["e2e4"]])
    assert MAIA23_CHECKPOINT_SHA256 == (
        "bce6cd1af5f0399ac7eed33fabb7a6a2ef6193662c2740f262bf93af7bfb3569"
    )


def test_linux_memory_reader_is_observational():
    import os
    class Transport:
        def get_pid(self):
            return os.getpid()
    class Engine:
        transport = Transport()
    memory = _peak_rss_kib(Engine())
    assert memory is None or memory > 0
