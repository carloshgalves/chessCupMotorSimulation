from chesssim.bilateral_cp import (
    PHASES, MARGIN, PREFERRED, gate, stats, select, fit_eligible, baseline_inventory, recipes, partition, variant_alias, refine_cases, STRICT_MARGIN, safe_alias,
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


def test_rodent_alias_limit_and_stable_existing_names():
    assert variant_alias("TalBalanced", "toward") == "TalBalancedCpToward"
    names = [variant_alias(base, label) for base in ("CapablancaWaveIndividual", "BotvinnikWaveRestriction", "DingLirenWaveRestriction") for label in ("toward", "away")]
    assert len(set(names)) == 6
    assert all(len(n.encode("utf-8")) <= 31 for n in names)
    assert variant_alias("CapablancaWaveIndividual", "toward") == names[0]


def test_strict_one_cp_boundary_and_uncertainty():
    assert STRICT_MARGIN == 1
    for delta in (-1, 0, 1):
        rows = [observation(i, p, 30, 30-delta) for i in range(50) for p in PHASES]
        s = stats(rows, "motor", bootstrap=True)
        assert fit_eligible(s, strict=True)
        assert gate(s, strict=True) == "EXCELLENT"
        assert gate({**s, "ci95": [-1.01, 1]}, strict=True) == "UNCERTAIN_EQUIVALENCE"
    for delta in (-2, 2):
        s = stats([observation(i, p, 30, 30-delta) for i in range(50) for p in PHASES], "motor", bootstrap=True)
        assert not fit_eligible(s, strict=True)
        assert gate(s, strict=True) in ("MOTOR_TOO_WEAK", "MOTOR_TOO_STRONG")
        assert gate(s) == "EXCELLENT"


def test_fine_grid_is_deterministic_and_preserves_original_recipes():
    coarse = recipes({"frozen": None, "toward": None, "away": None}, ("frozen", 16, 5_000_000))
    fine = refine_cases(coarse, "nodes-750000")
    assert fine == refine_cases(coarse, "nodes-750000")
    assert all(fine[k] == v for k, v in coarse.items())
    assert len(set(fine.values())) == len(fine)
    assert ("frozen", 15, 750_000) in fine.values()
    assert ("toward", 16, 750_000) in fine.values()
    assert all(6 <= v[1] <= 24 and 10_000 <= v[2] <= 5_000_000 for v in fine.values())


def test_alias_normalization_and_fine_profile_recipes():
    import pytest
    name = "VachierLagraveWaveIndividualCpToward"
    alias = safe_alias(name)
    assert len(alias.encode("ascii")) <= 31
    assert alias == safe_alias(name)
    assert safe_alias("Simple") == "Simple"
    assert safe_alias(name + "2") != alias
    with pytest.raises(ValueError):
        safe_alias("invalid alias")
    coarse = recipes({"frozen": None, "toward": None}, ("frozen", 16, 5_000_000))
    fine = refine_cases(coarse, "baseline", ("mid-toward-50",))
    assert ("mid-toward-50", 16, 5_000_000) in fine.values()
    assert all(fine[k] == v for k, v in coarse.items())
