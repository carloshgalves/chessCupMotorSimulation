from types import SimpleNamespace

import chess
import chess.pgn

from chesssim import fischer_position_calibration as cal


def test_cohort_rejects_test_and_fit_outside_peak(monkeypatch):
    row = {"mainline_sha256": "a", "date": "1971"}
    monkeypatch.setattr(cal, "neural_arm", lambda _: "fit")
    assert cal.cohort(row) == "peak_fit"
    assert cal.cohort({**row, "date": "1960"}) is None
    monkeypatch.setattr(cal, "neural_arm", lambda _: "tune")
    assert cal.cohort(row) == "tune"
    monkeypatch.setattr(cal, "neural_arm", lambda _: "excluded_test")
    assert cal.cohort(row) is None
    import pytest
    with pytest.raises(ValueError):
        cal.sample(chess.pgn.Game(), row)


def test_matched_root_and_duplicate_move_scored_once(monkeypatch):
    board = chess.Board()
    human = chess.Move.from_uci("e2e4")
    alternative = chess.Move.from_uci("d2d4")
    class Engine:
        def __init__(self, move):
            self.move = move
        def play(self, position, limit, game):
            assert position is board and limit.depth in cal.DEPTHS
            return SimpleNamespace(move=self.move)
    calls = []
    def score(engine, position, move, depth):
        assert position is board and depth == 16
        calls.append(move)
        return {"move": (move or human).uci(),
                "cp": 20 if move in (None, human) else -10, "mate": None}
    monkeypatch.setattr(cal, "_score", score)
    contexts, values = cal.evaluate(board, human, object(),
                                    {"fischer": Engine(human), "simple": Engine(alternative)})
    assert len(calls) == 3
    assert values["human"]["signed_gap_cp"] == 0
    assert all(values[f"fischer_d{d}"]["human_match"] for d in cal.DEPTHS)
    assert all(values[f"simple_d{d}"]["signed_gap_cp"] == 30 for d in cal.DEPTHS)
    assert "balanced_not_in_check_proxy" in contexts
    row = {"game": "a", "fen": board.fen(), "scores": values}
    summary = cal.summarize([row])
    assert summary["policies"]["simple_d8"]["mean_paired_excess_gap_vs_human_cp"] == 30
    assert summary["policies"]["simple_d8"]["paired_game_cluster_ci95_cp"] is None


def test_cluster_interval_uses_games_not_positions():
    assert cal.game_cluster_interval([("one", 50)] * 100) is None
    assert cal.game_cluster_interval([(str(i), 30) for i in range(20)]) == [30, 30]
