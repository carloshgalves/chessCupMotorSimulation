"""Issue #11: optional generic-control profile on the same complete FENs."""
from __future__ import annotations

from copy import deepcopy
import json

import chess
import pytest

from chesssim.shared_fen_controls import (
    compare_shared_fen_assignments,
    run_shared_fen_file,
)


def _case():
    white = chess.Board()
    white.fullmove_number = 9
    black = chess.Board()
    black.fullmove_number = 9
    black.push_uci("e2e4")
    fw, fb = white.fen(), black.fen()
    positions = [
        {"game": "a-1", "player": "A", "fen": fw, "move": "e2e4", "ply": 17},
        {"game": "a-2", "player": "A", "fen": fb, "move": "e7e5", "ply": 18},
        {"game": "b-1", "player": "B", "fen": fw, "move": "d2d4", "ply": 17},
        {"game": "b-2", "player": "B", "fen": fb, "move": "c7c5", "ply": 18},
    ]
    predictions = {
        "target_A": {fw: "e2e4", fb: "e7e5"},
        "target_B": {fw: "d2d4", fb: "c7c5"},
        "generic": {fw: "e2e4", fb: "c7c5"},
    }
    return positions, predictions


def _compare(positions, predictions, *, generic="generic", **kwargs):
    return compare_shared_fen_assignments(
        positions, predictions, player_a="A", player_b="B",
        profile_a="target_A", profile_b="target_B",
        generic_profile=generic, **kwargs,
    )


def test_generic_control_is_evaluated_on_identical_shared_fens():
    positions, predictions = _case()
    result = _compare(positions, predictions)
    assert result["matched_fens"] == 2
    assert result["declared_assignment_score"] == 1.0
    assert result["swapped_assignment_score"] == 0.0
    assert result["contrast"] == 1.0
    assert result["accuracy_by_profile_and_player"]["generic"] == {
        "A": 0.5, "B": 0.5,
    }
    assert result["generic_control"] == {
        "declared_profile": "generic",
        "provenance": "DECLARED_NOT_VERIFIED",
        "accuracy_by_player": {"A": 0.5, "B": 0.5},
        "assigned_minus_generic_by_player": {"A": 0.5, "B": 0.5},
        "mean_assigned_minus_generic": 0.5,
    }
    assert result["limitations"]["generic_control"] == "DECLARED_NOT_VERIFIED"
    assert "fidelity" not in str(result["generic_control"]).lower()


def test_generic_equal_to_target_profile_does_not_generate_artificial_gain():
    positions, predictions = _case()
    predictions["generic"] = deepcopy(predictions["target_A"])
    result = _compare(positions, predictions)
    assert result["generic_control"]["accuracy_by_player"] == {
        "A": 1.0, "B": 0.0,
    }
    assert result["generic_control"]["assigned_minus_generic_by_player"] == {
        "A": 0.0, "B": 1.0,
    }
    assert result["generic_control"]["mean_assigned_minus_generic"] == 0.5


def test_generic_control_is_optional_and_legacy_contract_stays_identical():
    positions, predictions = _case()
    original = compare_shared_fen_assignments(
        positions, {k: v for k, v in predictions.items() if k != "generic"},
        player_a="A", player_b="B",
        profile_a="target_A", profile_b="target_B",
    )
    optional = compare_shared_fen_assignments(
        positions, {k: v for k, v in predictions.items() if k != "generic"},
        player_a="A", player_b="B",
        profile_a="target_A", profile_b="target_B",
        generic_profile=None,
    )
    assert original == optional
    assert "generic_control" not in original
    assert "generic_control" not in original["limitations"]


def test_generic_control_no_exact_shared_fens_has_no_false_claim():
    positions, predictions = _case()
    positions = positions[:2]  # Player B has no eligible decisions.
    result = _compare(positions, {k: {} for k in predictions})
    assert result["status"] == "NO_EXACT_SHARED_FENS"
    assert result["matched_fens"] == 0
    assert result["generic_control"]["accuracy_by_player"] == {
        "A": None, "B": None,
    }
    assert result["generic_control"]["assigned_minus_generic_by_player"] == {
        "A": None, "B": None,
    }
    assert result["generic_control"]["mean_assigned_minus_generic"] is None


@pytest.mark.parametrize("mutation", [
    "missing_generic", "extra_generic", "missing_fen", "extra_fen",
    "illegal_move", "non_dict", "profile_collision", "empty_label",
])
def test_generic_control_fails_closed_on_invalid_input(mutation):
    positions, predictions = _case()
    control_name = "generic"
    if mutation == "missing_generic":
        del predictions["generic"]
    elif mutation == "extra_generic":
        predictions["unused"] = {}
    elif mutation == "missing_fen":
        predictions["generic"].pop(next(iter(predictions["generic"])))
    elif mutation == "extra_fen":
        predictions["generic"][chess.Board().fen()] = "e2e4"
    elif mutation == "illegal_move":
        fen = next(iter(predictions["generic"]))
        predictions["generic"][fen] = "e7e5"
    elif mutation == "non_dict":
        predictions["generic"] = []
    elif mutation == "profile_collision":
        control_name = "target_A"
        del predictions["generic"]
    elif mutation == "empty_label":
        control_name = ""
    with pytest.raises(ValueError):
        _compare(positions, predictions, generic=control_name)


def test_generic_control_is_order_invariant_and_hash_binds_control_identity():
    positions, predictions = _case()
    result = _compare(positions, predictions)
    shuffled = _compare(
        list(reversed(positions)),
        {k: dict(reversed(list(v.items())))
         for k, v in reversed(list(predictions.items()))},
    )
    assert result == shuffled

    changed = deepcopy(predictions)
    changed["generic"][next(iter(changed["generic"]))] = "d2d4"
    other = _compare(positions, changed)
    assert result["input_evidence_sha256"] != other["input_evidence_sha256"]
    assert result["generic_control"] != other["generic_control"]


def test_generic_is_not_allowed_when_the_two_profile_contract_was_requested():
    positions, predictions = _case()
    with pytest.raises(ValueError, match="prediction profiles"):
        compare_shared_fen_assignments(
            positions, predictions, player_a="A", player_b="B",
            profile_a="target_A", profile_b="target_B")


def test_cli_optional_generic_control_and_existing_safety_checks(tmp_path):
    positions, predictions = _case()
    inp, out = tmp_path / "input.json", tmp_path / "report.json"
    payload = {
        "positions": positions, "predictions": predictions,
        "player_a": "A", "player_b": "B",
        "profile_a": "target_A", "profile_b": "target_B",
        "generic_profile": "generic",
        "min_ply": 17,
    }
    inp.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        run_shared_fen_file(inp, out, ack_unsealed=False)
    assert not out.exists()
    report = run_shared_fen_file(inp, out, ack_unsealed=True)
    assert report["generic_control"]["mean_assigned_minus_generic"] == 0.5
    assert json.loads(out.read_text()) == report
    with pytest.raises(FileExistsError):
        run_shared_fen_file(inp, out, ack_unsealed=True)


def test_cli_rejects_duplicate_nested_control_prediction_key(tmp_path):
    positions, predictions = _case()
    inp, out = tmp_path / "input.json", tmp_path / "report.json"
    payload = {
        "positions": positions, "predictions": predictions,
        "player_a": "A", "player_b": "B",
        "profile_a": "target_A", "profile_b": "target_B",
        "generic_profile": "generic",
    }
    raw = json.dumps(payload)
    needle = '"generic": {'
    assert needle in raw
    # Duplicate JSON map key inside the declared generic profile.
    fen, move = next(iter(predictions["generic"].items()))
    mutated = raw.replace(needle, needle + json.dumps(fen) + ': ' +
                          json.dumps(move) + ', ', 1)
    inp.write_text(mutated)
    with pytest.raises(ValueError, match="Invalid JSON input"):
        run_shared_fen_file(inp, out, ack_unsealed=True)
    assert not out.exists()



def test_cli_explicit_null_generic_profile_fails_closed(tmp_path):
    positions, predictions = _case()
    data = {
        "positions": positions,
        "predictions": {k: v for k, v in predictions.items() if k != "generic"},
        "player_a": "A", "player_b": "B",
        "profile_a": "target_A", "profile_b": "target_B",
        "generic_profile": None,
    }
    inp, out = tmp_path / "source.json", tmp_path / "report.json"
    inp.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be null"):
        run_shared_fen_file(inp, out, ack_unsealed=True)
    assert not out.exists()


def test_generic_label_changes_digest_without_changing_scores():
    positions, predictions = _case()
    first = _compare(positions, predictions)
    renamed = deepcopy(predictions)
    renamed["generic-renamed"] = renamed.pop("generic")
    second = _compare(positions, renamed, generic="generic-renamed")
    assert first["input_evidence_sha256"] != second["input_evidence_sha256"]
    assert first["generic_control"]["accuracy_by_player"] == (
        second["generic_control"]["accuracy_by_player"])
    assert first["generic_control"]["mean_assigned_minus_generic"] == (
        second["generic_control"]["mean_assigned_minus_generic"])


def test_generic_control_report_does_not_contain_raw_decision_traces():
    positions, predictions = _case()
    result = _compare(positions, predictions)
    rendered = json.dumps(result)
    for row in positions:
        assert row["fen"] not in rendered
        assert row["move"] not in rendered
