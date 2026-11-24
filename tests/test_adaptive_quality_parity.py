"""Contracts for the adaptive reference and held-out protocol."""
import chess
import pytest

from chesssim import adaptive_quality_parity as q


def test_adaptive_escalation_all_roots_and_stop(monkeypatch):
    board = chess.Board()
    calls = []
    moves = {"human": "e2e4", "fischer": "d2d4", "tal_balanced": "g1f3",
             "tal_original": "b1c3", "simple": "e2e3"}

    def rank(sf, b, depth):
        calls.append(depth)
        return [{"move": "e2e4", "cp": 40, "mate": None},
                {"move": "d2d4", "cp": 30, "mate": None}]

    def score(sf, b, move, depth):
        m = move.uci()
        cp = 40 if m == "e2e4" else 30
        if m == "g1f3" and depth < 32:
            cp = 90
        return {"move": m, "cp": cp, "mate": None}

    monkeypatch.setattr(q, "_reference_top2", rank)
    monkeypatch.setattr(q, "_score", score)
    history = q.adaptive_reference(None, board, moves)
    assert calls == [24, 28, 32]
    assert [r["inconsistent"] for r in history] == [True, True, False]
    assert all(set(r["evals"]) == set(moves.values()) for r in history)
    calls.clear()
    monkeypatch.setattr(q, "_score", lambda sf, b, m, d: {
        "move": m.uci(), "cp": 40 if m.uci() == "e2e4" else 30, "mate": None})
    assert len(q.adaptive_reference(None, board, moves)) == 1
    assert calls == [24]


@pytest.mark.parametrize("difference, expected", [(35, False), (36, True)])
def test_locked_threshold(difference, expected):
    rank = [{"move": "e2e4", "cp": 20, "mate": None},
            {"move": "d2d4", "cp": 10, "mate": None}]
    raw = {x["move"]: dict(x) for x in rank}
    raw["g1f3"] = {"move": "g1f3", "cp": 20 + difference, "mate": None}
    assert q._disagree(rank, raw) is expected


def test_mate_sign_conflict_is_not_hidden():
    rank = [{"move": "e2e4", "cp": None, "mate": -3},
            {"move": "d2d4", "cp": None, "mate": -2}]
    raw = {x["move"]: dict(x) for x in rank}
    raw["g1f3"] = {"move": "g1f3", "cp": -900, "mate": None}
    assert q._disagree(rank, raw)


def test_reserved_selection_never_uses_fit_or_tune(monkeypatch):
    monkeypatch.setattr(q, "SOURCE_ARM", "excluded_test")
    monkeypatch.setattr(q, "neural_arm", lambda gid: gid.split("-")[0])
    sources = {}
    for player, peak in (("fischer", 1971), ("tal", 1959)):
        rows = [
            {"mainline_sha256": f"{arm}-{player}-{year}-{i}",
             "date": year, "plies": 60, "color": "white"}
            for arm in ("fit", "tune", "excluded_test")
            for year in (peak, 1980) for i in range(4)
        ]
        sources[player] = (None, rows, None, None, None)
    selected = q.choose_games(sources)
    assert all(len(rows) == 8 for rows in selected.values())
    assert all(r["mainline_sha256"].startswith("excluded_test-")
               for rows in selected.values() for r in rows)
    assert q.NONINFERIORITY_CP == 15
    assert q.TACTICAL_NONINFERIORITY_CP == 20
    assert q.SEVERE_NONINFERIORITY_RATE == .05
    assert q.MAX_REFERENCE_INCONSISTENCY == .02
