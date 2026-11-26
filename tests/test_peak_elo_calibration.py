import pytest

np = pytest.importorskip("numpy")
from chesssim.peak_elo_calibration import band, partition, fit_model, inferred, qualification
from chesssim.peak_elo_targets import TARGETS


def test_reference_inventory():
    assert len(TARGETS) == 64
    assert TARGETS["philidor"]["rating"] is None
    assert band(2882) == 2800
    assert partition("00000000") == "test"
    assert partition("00000001") == "fit"


def test_inverse_on_synthetic_data():
    rows = []
    for i in range(20):
        for rating in (1800, 2200, 2600, 2800):
            features = [1., (i % 3) / 10, (i % 5) / 10, i / 20]
            loss = .18 + .1 * features[1] + .2 * features[2] + .03 * features[3] - .04 * (rating - 2400) / 400
            rows.append({"rating": rating, "features": features, "losses": {"human": loss}})
    beta = fit_model(rows)
    assert np.isclose(inferred(rows, "human", beta), np.mean([r["rating"] for r in rows]))


def test_bilateral_gate_rejects_both_directions():
    model = {"status": "VALIDATED_PROXY"}
    target = {"rating": 2700, "verified_scale": True}
    games = [{"game": {"status": "COMPLETE", "time_forfeit": None}}] * 24
    for ci in ([51, 90], [-90, -51], [-80, 10], [-10, 80]):
        row = {"n": 60, "difference_ci95": ci}
        assert qualification(model, target, row, row, games) == "BILATERAL_EQUIVALENCE_NOT_ESTABLISHED"
    row = {"n": 60, "difference_ci95": [-40, 40], "in_support": True,
           "coverage": 1, "ci95": [2660, 2740]}
    assert qualification(model, target, row, row, games) == "REVIEW_REQUIRED_NOT_FROZEN"
    assert qualification(model, TARGETS["carlsen"], row, row, games) == "HUMAN_REFERENCE_NOT_VERIFIED"


def test_nominal_target_and_coverage_are_required():
    model = {"status": "VALIDATED_PROXY"}
    target = {"rating": 2700, "verified_scale": True}
    games = [{"game": {"status": "COMPLETE", "time_forfeit": None}}] * 24
    row = {"n": 60, "difference_ci95": [-10, 10], "in_support": True,
           "coverage": 1, "ci95": [2770, 2810]}
    assert qualification(model, target, row, row, games) == "NOMINAL_TARGET_EQUIVALENCE_NOT_ESTABLISHED"
    assert qualification(model, target, row, {**row, "in_support": False}, games) == "OUTSIDE_MODEL_SUPPORT"
    assert qualification(model, target, row, row, games[:2]) == "COMPETITIVE_VALIDATION_FAILED"
