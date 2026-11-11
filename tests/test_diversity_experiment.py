"""#69: controlled trials are plans, not evidence of engine diversity."""
from __future__ import annotations

import json

import pytest

from chesssim.diversity_experiment import design, export_plan, SCHEMA


def test_pairing_preserves_negative_control_and_distinct_alternate_seeds():
    plan = design(["fischer", "tal"], seed=2029, trials=5,
                  declared_external_repertoire=False)
    assert plan["schema"] == SCHEMA
    assert plan["status"] == "PLAN_ONLY_NOT_RUN_NOT_CALIBRATED"
    assert plan["seed_effectiveness"] == "NO_EFFECT_EXPECTED_WITHOUT_EXTERNAL_REPERTOIRE"
    assert len(plan["blocks"]) == 5
    assert all([r["seed"] for r in b["runs"]] ==
               [2029+i, 2029+i, 2034+i] for i, b in enumerate(plan["blocks"]))
    assert all("book_hash" in b["fixed_variables"] for b in plan["blocks"])
    assert plan == design(["fischer", "tal"], seed=2029, trials=5,
                          declared_external_repertoire=False)
    assert plan != design(["fischer", "tal"], seed=2030, trials=5,
                          declared_external_repertoire=False)
    assert plan["evaluation"]["planned_only"] is True


def test_declared_repertoire_is_explicitly_unverified_not_certified():
    p = design(["fischer", "tal"], seed=1, trials=2,
               declared_external_repertoire=True)
    assert "UNVERIFIED" in p["seed_effectiveness"]
    assert p["status"] == "PLAN_ONLY_NOT_RUN_NOT_CALIBRATED"
    assert p["blocks"][-1]["runs"][-1]["seed"] == 4


@pytest.mark.parametrize("players,seed,trials", [
    (["fischer"], 0, 2),
    (["fischer", "fischer"], 0, 2),
    (["fischer", "../../secret"], 0, 2),
    (["nonexistent", "tal"], 0, 2),
    (["fischer", "tal"], -1, 2),
    (["fischer", "tal"], True, 2),
    (["fischer", "tal"], 0, 1),
    (["fischer", "tal"], 0, 41),
    (["fischer", "tal"], 2**32-1, 2),
])
def test_invalid_design_fails_closed(players, seed, trials):
    with pytest.raises(ValueError):
        design(players, seed=seed, trials=trials,
               declared_external_repertoire=False)


def test_uint32_last_safe_seed_inclusive():
    p = design(["fischer", "tal"], seed=2**32-4, trials=2,
               declared_external_repertoire=False)
    assert p["blocks"][-1]["runs"][-1]["seed"] == 2**32-1


def test_export_is_exclusive_and_does_not_touch_runs(tmp_path):
    target = tmp_path / "out" / "experiment.json"
    plan = export_plan(["fischer", "tal"], target, seed=2029, trials=2)
    assert json.loads(target.read_text(encoding="utf-8")) == plan
    with pytest.raises(FileExistsError):
        export_plan(["fischer", "tal"], target, seed=2029, trials=2)
    link = tmp_path / "alias.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        export_plan(["fischer", "tal"], link, seed=2029, trials=2)
    assert not list(tmp_path.rglob("*.pgn"))
