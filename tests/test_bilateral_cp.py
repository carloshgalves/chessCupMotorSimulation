from chesssim.bilateral_cp import (
    PHASES, MARGIN, PREFERRED, gate, stats, select, fit_eligible, baseline_inventory, recipes, partition,
)
from chesssim.catalog import load_roster
from chesssim.qualified_runtime import assets


def observation(i, phase, human, motor):
    def score(cp):
        return {"numeric": True, "cp_loss": cp, "over100": cp > 100,
                "missed_winning_mate": False, "selected_losing_mate": False}
    return {"game": str(i), "phase": phase, "stable": True,
            "scores": {"human": score(human), "motor": score(motor)}}


def sample(delta):
    return [observation(i, p, 100, 100 - delta) for i in range(50) for p in PHASES]


def test_signed_difference_and_equivalence():
    assert MARGIN == 5 and PREFERRED == 3
    for delta, wanted in ((0, "EXCELLENT"), (3, "EXCELLENT"), (-3, "EXCELLENT"),
                          (5, "COMPATIBLE"), (-5, "COMPATIBLE"),
                          (6, "MOTOR_TOO_STRONG"), (-6, "MOTOR_TOO_WEAK")):
        # Avoid crossing the 100cp severe-loss boundary just to test delta.
        rows = [observation(i, p, 30, 30-delta) for i in range(50) for p in PHASES]
        s = stats(rows, "motor", bootstrap=True)
        assert s["delta_cp"] == delta
        assert s["ci95"] == [delta, delta]
        assert gate(s) == wanted


def test_uncertainty_and_imbalance():
    s = stats(sample(0), "motor", bootstrap=True)
    assert gate({**s, "ci95": [-6, 2]}) == "UNCERTAIN_EQUIVALENCE"
    assert gate({**s, "ci95": [-2, 6]}) == "UNCERTAIN_EQUIVALENCE"
    assert gate({**s, "severe_gap": -.06}) == "SEVERE_OR_MATE_IMBALANCE"
    assert gate({**s, "severe_gap": .06}) == "SEVERE_OR_MATE_IMBALANCE"
    assert gate({**s, "phase_delta": {"opening": 12, "middlegame": -12, "endgame": 0}}) == "PHASE_IMBALANCE"
    assert gate({**s, "games": 1}) == "INSUFFICIENT_RESERVED_EVIDENCE"
    assert gate({**s, "coverage": .8}) == "INSUFFICIENT_RESERVED_EVIDENCE"
    assert gate(stats([], "motor", bootstrap=True)) == "INSUFFICIENT_RESERVED_EVIDENCE"


def test_phase_balanced_and_game_clustered():
    rows = [observation(i, "opening", 30, 27) for i in range(100)]
    rows += [observation(i, "middlegame", 30, 33) for i in range(50)]
    rows += [observation(i, "endgame", 30, 30) for i in range(50)]
    result = stats(rows, "motor", bootstrap=True)
    assert result["delta_cp"] == 0
    assert result["games"] == 100
    assert result["numeric"] == 200


def test_selection_minimizes_signed_absolute_error():
    candidates = {}
    for label, delta in (("baseline", 18), ("weak", -2), ("strong", 8)):
        rows = [observation(i, p, 30, 30-delta) for i in range(50) for p in PHASES]
        candidates[label] = stats(rows, "motor")
    assert select(candidates) == "weak"


def test_baseline_inventory_and_recipes():
    old = set(assets())
    pending = set(baseline_inventory())
    assert len(pending) == 44
    assert old | pending == {p.slug for p in load_roster()}
    cases = recipes({"frozen": None, "toward": None, "away": None}, ("frozen", 16, 5_000_000))
    assert cases["baseline"] == ("frozen", 16, 5_000_000)
    assert len(set(cases.values())) == len(cases)
    assert any(r[1] < 16 for r in cases.values())
    assert any(r[1] > 16 for r in cases.values())
    assert any(r[2] < 5_000_000 for r in cases.values())
    assert any(r[0] == "toward" for r in cases.values())


def test_global_test_preserved_for_every_player():
    for i in range(100):
        gid = f"{i:064x}"
        arms = {partition(p.slug, gid) == "excluded_test" for p in load_roster()}
        assert len(arms) == 1


def test_fit_requires_bilateral_phase_and_severe_support():
    base = stats(sample(0), "motor")
    assert fit_eligible(base)
    assert not fit_eligible({**base, "delta_cp": -6})
    assert not fit_eligible({**base, "delta_cp": 6})
    assert not fit_eligible({**base, "phase_delta": {"opening": 12, "middlegame": -12, "endgame": 0}})
    assert not fit_eligible({**base, "coverage": .4})
