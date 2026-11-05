"""Adversarial regression tests for Issue #15 game diversity."""
from __future__ import annotations

import json

import pytest

from chesssim.game_diversity import audit_mainlines, mainline_identity


class FakeMove:
    def __init__(self, text):
        self.text = text

    def uci(self):
        return self.text


class FakeBoard:
    def __init__(self, fen):
        self._fen = fen

    def fen(self):
        return self._fen


class FakeGame:
    def __init__(self, moves, fen="standard-start", **headers):
        self.moves = moves
        self._fen = fen
        self.headers = headers

    def board(self):
        return FakeBoard(self._fen)

    def mainline_moves(self):
        return (FakeMove(text) for text in self.moves)


def _row(game, *, source, **attrs):
    digest, plies = mainline_identity(game)
    return {"source": source, "mainline_sha256": digest, "plies": plies, **attrs}


def test_identity_ignores_headers_but_respects_position_and_full_move_sequence():
    a = FakeGame(["e2e4", "e7e5"], White="A", Black="B", Event="One")
    b = FakeGame(["e2e4", "e7e5"], White="X", Black="Y", Event="Two")
    different_start = FakeGame(["e2e4", "e7e5"], fen="another legal starting FEN")
    different_suffix = FakeGame(["e2e4", "e7e5", "g1f3"])
    assert mainline_identity(a) == mainline_identity(b)
    assert mainline_identity(a)[0] != mainline_identity(different_start)[0]
    assert mainline_identity(a)[0] != mainline_identity(different_suffix)[0]


def test_duplicate_does_not_inflate_evidence_and_preserves_raw_games():
    first = _row(FakeGame(["e2e4", "e7e5"]), source="pair1/game1", opening_seed=2029,
                 opening_mode="EXPLORATORY", color_order="A-B")
    second = _row(FakeGame(["e2e4", "e7e5"], Event="Changed"), source="pair2/game1",
                  opening_seed=2030, opening_mode="EXPLORATORY", color_order="A-B")
    third = _row(FakeGame(["d2d4", "d7d5"]), source="pair2/game2", opening_seed=2030,
                 opening_mode="EXPLORATORY", color_order="B-A")
    raw = [first, second, third]
    annotated, report = audit_mainlines(raw)
    assert raw[1].get("duplicate_mainline") is None  # no mutation
    assert report["games_total"] == 3
    assert report["games_distinct"] == report["effective_independent_games"] == 2
    assert report["games_duplicate"] == 1
    assert annotated[1]["first_occurrence"] == "pair1/game1"
    assert not annotated[1]["eligible_for_independent_evidence"]
    assert annotated[2]["eligible_for_independent_evidence"]
    assert report["variation_sources"]["historical_opening_seeds"] == [2029, 2030]
    assert report["variation_sources"]["color_orders"] == ["A-B", "B-A"]


def test_zero_moves_never_cause_evidence_or_suppress_later_valid_games():
    empty = FakeGame([])
    game = FakeGame(["e2e4"])
    rows, report = audit_mainlines([
        _row(empty, source=1), _row(empty, source=2),
        _row(game, source=3), _row(game, source=4),
    ])
    assert report["games_total"] == 4
    assert report["games_without_moves"] == 2
    assert report["games_distinct"] == 1
    assert report["games_duplicate"] == 1
    assert rows[0]["independence_reason"] == "NO_MOVES"
    assert rows[1]["duplicate_mainline"] is False
    assert rows[3]["first_occurrence"] == 3


@pytest.mark.parametrize("broken", [
    {"mainline_sha256": "x", "plies": 2},
    {"mainline_sha256": "0" * 64, "plies": -1},
    {"mainline_sha256": "0" * 64, "plies": True},
    {"mainline_sha256": "0" * 64, "plies": 2, "opening_seed": "not-int"},
])
def test_invalid_input_fails_closed(broken):
    with pytest.raises(ValueError):
        audit_mainlines([broken])


def test_report_is_stable_and_json_serializable():
    entries = [_row(FakeGame(["e2e4", "e7e5"]), source="a")]
    assert audit_mainlines(entries) == audit_mainlines(entries)
    json.dumps(audit_mainlines(entries))


def test_opening_seed_provenance_parsing_is_strict():
    from chesssim.game_diversity import opening_seed_from_headers

    assert opening_seed_from_headers({"OpeningSeed": "2029"}) == 2029
    assert opening_seed_from_headers({"OpeningSeed": "-2"}) == -2
    assert opening_seed_from_headers({}) is None
    with pytest.raises(ValueError, match="OpeningSeed"):
        opening_seed_from_headers({"OpeningSeed": "unknown"})
