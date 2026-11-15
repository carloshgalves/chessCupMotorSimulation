"""Real promotion is forbidden until a reviewed allowlist commit exists."""
import pytest
from chesssim.real_policy_experiment import _digest
from chesssim.scientific_promotion_guard import SCHEMA, verify_promotion, TRUSTED_FINAL_REPORT_SHA256


def _manifest(**changes):
    m = {
        "schema": SCHEMA, "state": "APPROVED_FOR_EXPERIMENTAL_PLAY",
        "player": "fischer", "candidate_model_sha256": "1"*64,
        "training_manifest_sha256": "2"*64, "final_protocol_sha256": "3"*64,
        "independent_final_report_sha256": "4"*64,
        "blind_identity_passed": True, "tactical_safety_passed": True,
        "baseline_comparison_passed": True, "final_holdout_opened_once": True,
    }
    m.update(changes)
    return {**m, "manifest_sha256": _digest(m)}


def test_no_self_signed_forgery_can_promote():
    assert not TRUSTED_FINAL_REPORT_SHA256
    m = _manifest()
    with pytest.raises(PermissionError, match="not explicitly reviewed"):
        verify_promotion(m)


def test_negative_evidence_and_invalid_fields_fail_closed():
    with pytest.raises(PermissionError, match="requirements not met"):
        verify_promotion(_manifest(blind_identity_passed=False))
    with pytest.raises(ValueError, match="Tampered"):
        verify_promotion({**_manifest(), "player": "tal"})
    with pytest.raises(ValueError, match="commitment"):
        verify_promotion(_manifest(final_protocol_sha256="not-sha"))
