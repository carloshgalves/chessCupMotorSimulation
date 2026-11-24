import pytest

from chesssim.roster_product_wave import (
    FROZEN, WAVE, SHARDS, remaining, in_period, parameters, eligible_quality,
)


def test_remaining_roster_covered_once_and_frozen_preserved():
    players = remaining()
    assert len(players) == len(set(players)) == 60
    assert not set(players) & FROZEN
    assert set(WAVE) <= set(players)
    assert sum(len(players[i::SHARDS]) for i in range(SHARDS)) == 60


@pytest.mark.parametrize("year,expected", [("1927", True), ("1934", True),
                                           ("1926", False), ("1935", False), (None, False)])
def test_period_year_string(year, expected):
    assert in_period({"date": year}, WAVE["alekhine"]) is expected


def test_candidate_edits_are_small_and_based_on_original():
    keys = ["OwnAttack", "PiecePressure", "OwnMobility", "OppMobility", "Outposts", "PawnStructure"]
    raw = "\n".join(f"setoption name {k} value 100" for k in keys)
    variants = parameters(raw)
    assert variants["original"] == {}
    assert variants["initiative"] == {"OwnAttack": 115, "PiecePressure": 110, "OwnMobility": 110}
    assert variants["restriction"] == {"OppMobility": 115, "Outposts": 110, "PawnStructure": 110}
    with pytest.raises(ValueError):
        parameters(raw + "\nsetoption name OwnAttack value 99")


def test_quality_gate_requires_numeric_support():
    assert eligible_quality({"numeric": 20, "excess_cp": 35, "severe_excess": .15})
    assert not eligible_quality({"numeric": 19, "excess_cp": 0, "severe_excess": 0})
    assert not eligible_quality({"numeric": 36, "excess_cp": 36, "severe_excess": 0})
