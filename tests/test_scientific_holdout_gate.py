"""Engineering-only checks for frozen final protocol; no historical holdout."""
import json
from pathlib import Path
import pytest
from chesssim import scientific_holdout_gate as gate


def _protocol(**kwargs):
    data = {
        "schema": gate.SCHEMA,
        "candidate_model_sha256": "1"*64,
        "train_manifest_sha256": "2"*64,
        "source_snapshot_commit": "a"*40,
        "source_pgn_commitment_sha256": "3"*64,
        "blind_identity_protocol_sha256": "4"*64,
        "tactical_safety_protocol_sha256": "5"*64,
        "hypotheses_and_multiplicity_sha256": "6"*64,
        "seed": 2027, "per_player": 64, "per_game": 2,
        "rodent_depth": 3, "rodent_seconds": 0.15, "use_rodent": False,
    }
    data.update(kwargs)
    return {**data, "frozen_protocol_sha256": gate._digest(data)}


def _verify(path: Path):
    return gate.authorize_final_holdout(
        path, model_sha256="1"*64, train_manifest_sha256="2"*64,
        source_pgn_commitment_sha256="3"*64,
        seed=2027, per_player=64, per_game=2, rodent_depth=3,
        rodent_seconds=0.15, use_rodent=False,
    )


def test_operator_self_approval_never_opens_real_holdout(tmp_path):
    p = tmp_path / "protocol.json"
    p.write_text(json.dumps(_protocol()))
    assert not gate.TRUSTED_FROZEN_PREREGISTRATIONS
    with pytest.raises(PermissionError, match="separately reviewed"):
        _verify(p)


def test_tampering_and_parameter_drift_refused(tmp_path, monkeypatch):
    p = tmp_path / "protocol.json"
    document = _protocol()
    p.write_text(json.dumps(document))
    monkeypatch.setattr(gate, "TRUSTED_FROZEN_PREREGISTRATIONS",
                        frozenset({document["frozen_protocol_sha256"]}))
    assert _verify(p) == document["frozen_protocol_sha256"]
    p.write_text(json.dumps({**document, "per_player": 66}))
    with pytest.raises(ValueError, match="modified"):
        _verify(p)
    p.write_text(json.dumps(_protocol(candidate_model_sha256="7"*64)))
    with pytest.raises(PermissionError, match="does not match"):
        _verify(p)
    p.write_text(json.dumps(_protocol()).replace('"seed": 2027', '"seed": 2026, "seed": 2027'))
    with pytest.raises(ValueError, match="Duplicate"):
        _verify(p)


def test_symlinked_preregistration_denied(tmp_path):
    raw = tmp_path / "real.json"
    raw.write_text(json.dumps(_protocol()))
    alias = tmp_path / "alias.json"
    alias.symlink_to(raw)
    with pytest.raises(ValueError, match="symlink"):
        _verify(alias)
