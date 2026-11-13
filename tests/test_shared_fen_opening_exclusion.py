"""Issue #11: predeclared exact-FEN exclusion, never inferred historical fidelity."""
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
    rows = [
        {"game": "A1", "player": "A", "fen": fw, "move": "e2e4", "ply": 17},
        {"game": "A2", "player": "A", "fen": fb, "move": "e7e5", "ply": 18},
        {"game": "B1", "player": "B", "fen": fw, "move": "d2d4", "ply": 17},
        {"game": "B2", "player": "B", "fen": fb, "move": "c7c5", "ply": 18},
    ]
    choices = {
        "profile_A": {fw: "d2d4", fb: "e7e5"},
        "profile_B": {fw: "d2d4", fb: "c7c5"},
        "generic": {fw: "e2e4", fb: "c7c5"},
    }
    return rows, choices, fw, fb


def _opening(*fens):
    return {"fens": list(fens), "declared_source_sha256": "a" * 64}


def _compare(rows, predictions, opening=None, *, generic=True):
    return compare_shared_fen_assignments(
        rows, predictions,
        player_a="A", player_b="B",
        profile_a="profile_A", profile_b="profile_B",
        generic_profile="generic" if generic else None,
        opening_exclusion=opening,
    )


def test_declared_opening_fen_exclusion_uses_same_remaining_fens_for_all_profiles():
    rows, choices, fw, _ = _case()
    original = _compare(rows, choices)
    result = _compare(rows, choices, _opening(fw))
    assert original["matched_fens"] == 2
    assert original["declared_assignment_score"] == 0.75
    assert result["matched_fens"] == 1
    assert result["declared_assignment_score"] == 1.0
    assert result["swapped_assignment_score"] == 0.0
    assert result["contrast"] == 1.0
    assert result["opening_exclusion"] == {
        "method": "EXACT_FULL_FEN_CATALOG",
        "provenance": "DECLARED_NOT_VERIFIED",
        "declared_source_sha256": "a" * 64,
        "catalog_fens": 1,
        "matched_fens_before_exclusion": 2,
        "matched_fens_excluded": 1,
        "remaining_unlisted_fens": 1,
    }
    assert result["accuracy_by_profile_and_player"]["generic"] == {"A": 0.0, "B": 1.0}
    assert result["limitations"]["opening_exclusion"] == "UNLISTED_IS_NOT_PROOF_OF_OUT_OF_REPERTOIRE"
    assert result["input_evidence_sha256"] != original["input_evidence_sha256"]


def test_excluded_fens_do_not_need_predictions_and_extras_still_rejected():
    rows, choices, fw, fb = _case()
    for model in choices.values():
        del model[fw]
    result = _compare(rows, choices, _opening(fw))
    assert result["matched_fens"] == 1
    assert result["opening_exclusion"]["matched_fens_excluded"] == 1

    choices["profile_A"][fw] = "e2e4"
    with pytest.raises(ValueError, match="predictions must match"):
        _compare(rows, choices, _opening(fw))
    # Predictions for the retained FEN remain mandatory for all profiles.
    del choices["profile_A"][fw]
    del choices["profile_B"][fb]
    with pytest.raises(ValueError, match="predictions must match"):
        _compare(rows, choices, _opening(fw))


def test_explicit_exclusion_all_fens_fails_closed_without_statistics():
    rows, choices, fw, fb = _case()
    result = _compare(rows, {k: {} for k in choices}, _opening(fw, fb))
    assert result["status"] == "NO_SHARED_FENS_AFTER_OPENING_EXCLUSION"
    assert result["opening_exclusion"]["matched_fens_before_exclusion"] == 2
    assert result["opening_exclusion"]["matched_fens_excluded"] == 2
    assert result["matched_fens"] == 0
    assert result["declared_assignment_score"] is None
    assert result["swapped_assignment_score"] is None
    assert result["generic_control"]["mean_assigned_minus_generic"] is None


def test_unmatched_catalog_entry_does_not_false_label_positions_as_outside_repertoire():
    rows, choices, fw, _ = _case()
    board = chess.Board(fw)
    board.fullmove_number = 10
    another_valid_fen = board.fen()  # Same pieces but different full FEN.
    result = _compare(rows, choices, _opening(another_valid_fen))
    assert result["opening_exclusion"]["matched_fens_excluded"] == 0
    assert result["opening_exclusion"]["remaining_unlisted_fens"] == 2
    assert result["status"] == "DESCRIPTIVE_ONLY_NOT_HISTORICALLY_VALIDATED"
    assert result["limitations"]["opening_exclusion"] == "UNLISTED_IS_NOT_PROOF_OF_OUT_OF_REPERTOIRE"


def test_backward_compatibility_with_optional_none_and_without_generic_control():
    rows, choices, fw, _ = _case()
    base_predictions = {k: v for k, v in choices.items() if k != "generic"}
    baseline = compare_shared_fen_assignments(
        rows, base_predictions, player_a="A", player_b="B",
        profile_a="profile_A", profile_b="profile_B",
    )
    explicit_none = _compare(rows, base_predictions, opening=None, generic=False)
    assert baseline == explicit_none
    assert "opening_exclusion" not in baseline
    filtered = _compare(rows, {k: {fen: move for fen, move in v.items() if fen != fw}
                              for k, v in base_predictions.items()},
                        _opening(fw), generic=False)
    assert filtered["matched_fens"] == 1
    assert "generic_control" not in filtered


@pytest.mark.parametrize("invalid", [
    None, True, [], "bad", {},
    {"fens": []},
    {"fens": [], "declared_source_sha256": "a" * 64},
    {"fens": ["bad FEN"], "declared_source_sha256": "a" * 64},
    {"fens": ["bad FEN"], "declared_source_sha256": "g" * 64},
    {"fens": ["bad FEN"], "declared_source_sha256": "A" * 64},
    {"fens": ["bad FEN"], "declared_source_sha256": "a" * 64, "untrusted": 1},
])
def test_invalid_exclusion_catalogs_fail_closed(invalid):
    rows, choices, _, _ = _case()
    if invalid is None:
        invalid = {"fens": [42], "declared_source_sha256": "a" * 64}
    with pytest.raises(ValueError):
        _compare(rows, choices, invalid)


def test_duplicate_and_noncanonical_fens_rejected_without_silent_deduplication():
    rows, choices, fw, _ = _case()
    with pytest.raises(ValueError):
        _compare(rows, choices, _opening(fw, fw))
    noncanonical = fw.replace(" w KQkq - ", " w qkQK - ")
    assert noncanonical != fw
    with pytest.raises(ValueError):
        _compare(rows, choices, _opening(noncanonical))


def test_report_is_order_invariant_and_catalog_integrity_affects_digest():
    rows, choices, fw, fb = _case()
    for model in choices.values():
        del model[fw]
    before = _compare(rows, choices, _opening(fw))
    inverted = _compare(
        list(reversed(rows)),
        {name: dict(reversed(list(pred.items())))
         for name, pred in reversed(list(choices.items()))},
        _opening(fw),
    )
    assert before == inverted
    changed = _opening(fw)
    changed["declared_source_sha256"] = "b" * 64
    assert before["input_evidence_sha256"] != _compare(rows, choices, changed)["input_evidence_sha256"]
    serialized = json.dumps(before, sort_keys=True)
    assert fw not in serialized
    assert fb not in serialized
    assert "e2e4" not in serialized


def test_cli_opening_exclusion_is_optional_and_output_is_safe(tmp_path):
    rows, choices, fw, _ = _case()
    for profile in choices.values():
        del profile[fw]
    payload = {
        "positions": rows, "predictions": choices,
        "player_a": "A", "player_b": "B",
        "profile_a": "profile_A", "profile_b": "profile_B",
        "generic_profile": "generic", "opening_exclusion": _opening(fw),
    }
    source, output = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    report = run_shared_fen_file(source, output, ack_unsealed=True)
    assert report["opening_exclusion"]["matched_fens_excluded"] == 1
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert "e2e4" not in output.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_shared_fen_file(source, output, ack_unsealed=True)


def test_cli_explicit_null_exclusion_is_rejected(tmp_path):
    rows, choices, _, _ = _case()
    payload = {
        "positions": rows, "predictions": choices,
        "player_a": "A", "player_b": "B",
        "profile_a": "profile_A", "profile_b": "profile_B",
        "generic_profile": "generic", "opening_exclusion": None,
    }
    source, output = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="opening_exclusion"):
        run_shared_fen_file(source, output, ack_unsealed=True)
    assert not output.exists()
