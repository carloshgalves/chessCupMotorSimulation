"""Issue #10: externally pinned preflight commitments, checked before train PGN I/O."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from chesssim.cli import main
from chesssim import historical_train_verify as module
from chesssim.historical_split_index import build_historical_split_manifest
from chesssim.historical_train_verify import (
    run_historical_train_verification, verify_historical_training_pgn,
)
from test_historical_train_verify import _fixture


def _pins(index):
    manifest = build_historical_split_manifest(index)
    return {
        "expected_index_evidence_sha256": manifest["input_evidence_sha256"],
        "expected_train_partition_sha256":
            manifest["partitions"]["train"]["game_commitment_sha256"],
    }


def test_pin_pair_matches_and_binds_approved_index(tmp_path):
    index, _, inp, train, out = _fixture(tmp_path)
    pins = _pins(index)
    result = run_historical_train_verification(
        inp, train, out, ack_train_only=True, **pins)
    assert result["status"] == "TRAIN_PARTITION_MATCHED_SOURCE_NOT_VERIFIED"
    assert result["pin_verification"] == {
        "status": "MATCHED_OPERATOR_SUPPLIED_PINS_NOT_AUTHENTICATED",
        "index_evidence_sha256": pins["expected_index_evidence_sha256"],
        "train_partition_sha256": pins["expected_train_partition_sha256"],
        "checked_before_training_pgn_parsing": True,
    }
    assert json.loads(out.read_text()) == result
    assert result["source_rights_status"] == (
        "OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED")
    assert result["embargo_status"] == (
        "RESERVED_GAMES_NOT_OPENED_EMBARGO_NOT_PROVED")


def test_legacy_unpinned_api_is_bitwise_compatible(tmp_path):
    index, _, _, train, _ = _fixture(tmp_path)
    baseline = verify_historical_training_pgn(index, train.read_bytes())
    empty = verify_historical_training_pgn(
        index, train.read_bytes(),
        expected_index_evidence_sha256=None,
        expected_train_partition_sha256=None)
    assert baseline == empty
    assert "pin_verification" not in baseline


@pytest.mark.parametrize("mutation", [
    "holdout_hash_changed", "license_claim_changed",
    "train_hash_changed", "train_date_changed", "train_metadata_changed",
])
def test_post_freeze_index_mutation_fails_even_if_train_data_is_unchanged(
        tmp_path, mutation):
    index, _, inp, train, out = _fixture(tmp_path)
    pins = _pins(index)
    updated = deepcopy(index)
    if mutation == "holdout_hash_changed":
        updated["games"][9]["mainline_sha256"] = sha256(
            b"modified holdout claim").hexdigest()
    elif mutation == "license_claim_changed":
        updated["sources"][0]["license_id"] = "OTHER_OPERATOR_ATTESTATION"
    elif mutation == "train_hash_changed":
        updated["games"][0]["mainline_sha256"] = sha256(b"new train").hexdigest()
    elif mutation == "train_date_changed":
        updated["games"][0]["date"] = "1980-12-21"
    else:
        updated["games"][0]["metadata"]["time_control_known"] = False
    inp.write_text(json.dumps(updated), encoding="utf-8")
    with pytest.raises(ValueError, match="pinned"):
        run_historical_train_verification(
            inp, train, out, ack_train_only=True, **pins)
    assert not out.exists()


def test_holdout_pin_mismatch_is_rejected_before_training_pgn_is_read(
        tmp_path, monkeypatch):
    index, _, inp, train, out = _fixture(tmp_path)
    pins = _pins(index)
    index["games"][9]["mainline_sha256"] = sha256(
        b"invalidated-after-approval").hexdigest()
    inp.write_text(json.dumps(index), encoding="utf-8")

    def forbidden_read(_):
        raise AssertionError("Training PGN must not be read after failed pins")

    monkeypatch.setattr(module, "_read_training_pgn", forbidden_read)
    with pytest.raises(ValueError, match="pinned"):
        run_historical_train_verification(
            inp, train, out, ack_train_only=True, **pins)
    assert not out.exists()


@pytest.mark.parametrize("mutation", [
    "missing_index", "missing_train", "wrong_index",
    "wrong_train", "uppercase_index", "truncated_train",
    "boolean", "numeric",
])
def test_invalid_or_partial_commitment_pins_fail_closed(tmp_path, mutation):
    index, _, inp, train, out = _fixture(tmp_path)
    pins = _pins(index)
    if mutation == "missing_index":
        pins["expected_index_evidence_sha256"] = None
    elif mutation == "missing_train":
        pins["expected_train_partition_sha256"] = None
    elif mutation == "wrong_index":
        pins["expected_index_evidence_sha256"] = "0" * 64
    elif mutation == "wrong_train":
        pins["expected_train_partition_sha256"] = "0" * 64
    elif mutation == "uppercase_index":
        pins["expected_index_evidence_sha256"] = (
            pins["expected_index_evidence_sha256"].upper())
    elif mutation == "truncated_train":
        pins["expected_train_partition_sha256"] = "a" * 63
    elif mutation == "boolean":
        pins["expected_index_evidence_sha256"] = True
    else:
        pins["expected_train_partition_sha256"] = 12
    with pytest.raises(ValueError, match="pinned|pin"):
        run_historical_train_verification(
            inp, train, out, ack_train_only=True, **pins)
    assert not out.exists()


def test_public_api_and_runner_use_same_pin_comparison(tmp_path):
    index, _, inp, train, out = _fixture(tmp_path)
    pins = _pins(index)
    public = verify_historical_training_pgn(index, train.read_bytes(), **pins)
    runner = run_historical_train_verification(
        inp, train, out, ack_train_only=True, **pins)
    assert public["pin_verification"] == runner["pin_verification"]
    assert public["train_pgn_sha256"] == runner["train_pgn_sha256"]


def test_pinned_cli_success_and_drift_failure(tmp_path, capsys):
    index, _, inp, train, out = _fixture(tmp_path)
    pins = _pins(index)
    command = [
        "historical-train-verify", "--index", str(inp),
        "--train-pgn", str(train), "--out", str(out), "--ack-train-only",
        "--expect-index-evidence-sha256",
        pins["expected_index_evidence_sha256"],
        "--expect-train-partition-sha256",
        pins["expected_train_partition_sha256"],
    ]
    assert main(command) == 0
    assert json.loads(out.read_text())["pin_verification"]["status"] == (
        "MATCHED_OPERATOR_SUPPLIED_PINS_NOT_AUTHENTICATED")
    out2 = tmp_path / "drifted.json"
    index["games"][9]["mainline_sha256"] = sha256(b"tampered").hexdigest()
    inp.write_text(json.dumps(index))
    command[command.index(str(out))] = str(out2)
    assert main(command) == 2
    assert not out2.exists()
    assert "pinned" in capsys.readouterr().err


def test_pins_never_disclose_individual_reserved_identities(tmp_path):
    index, _, inp, train, out = _fixture(tmp_path)
    result = run_historical_train_verification(
        inp, train, out, ack_train_only=True, **_pins(index))
    raw = json.dumps(result)
    for row in index["games"]:
        assert row["mainline_sha256"] not in raw



def test_insufficient_period_index_is_rejected_before_train_pgn_read(
        tmp_path, monkeypatch):
    index, _, inp, train, out = _fixture(tmp_path)
    index["games"] = index["games"][:2]
    pins = _pins(index)
    inp.write_text(json.dumps(index))

    def forbid_train_read(_):
        raise AssertionError("Must reject insufficient pinned periods first")

    monkeypatch.setattr(module, "_read_training_pgn", forbid_train_read)
    with pytest.raises(ValueError, match="insufficient period"):
        run_historical_train_verification(
            inp, train, out, ack_train_only=True, **pins)
    assert not out.exists()
