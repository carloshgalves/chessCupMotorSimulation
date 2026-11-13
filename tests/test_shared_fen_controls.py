"""Issue #11: exact-FEN style controls, with no engine or sealed-test access."""
from __future__ import annotations

import chess
import pytest

from chesssim.shared_fen_controls import compare_shared_fen_assignments


def _fen_white():
    board = chess.Board()
    board.fullmove_number = 9
    return board.fen()


def _fen_black():
    board = chess.Board()
    board.fullmove_number = 9
    board.push_uci("e2e4")
    return board.fen()


def _row(player, game, fen, move):
    return {"player": player, "game": game, "fen": fen,
            "move": move, "ply": chess.Board(fen).ply() + 1}


def _case():
    white, black = _fen_white(), _fen_black()
    rows = [
        _row("A", "A1", white, "e2e4"),
        _row("A", "A2", black, "e7e5"),
        _row("B", "B1", white, "d2d4"),
        _row("B", "B2", black, "c7c5"),
    ]
    choices = {
        "profile_A": {white: "e2e4", black: "e7e5"},
        "profile_B": {white: "d2d4", black: "c7c5"},
    }
    return rows, choices


def _compare(rows, choices, **kw):
    return compare_shared_fen_assignments(
        rows, choices, player_a="A", player_b="B",
        profile_a="profile_A", profile_b="profile_B", **kw,
    )


def test_positive_and_swapped_profile_assignment_are_measured_on_same_fens():
    rows, choices = _case()
    result = _compare(rows, choices)
    assert result["schema"] == "chesssim-shared-fen-negative-control-v1"
    assert result["status"] == "DESCRIPTIVE_ONLY_NOT_HISTORICALLY_VALIDATED"
    assert result["matched_fens"] == 2
    assert result["distinct_games_by_player"] == {"A": 2, "B": 2}
    assert result["observations_by_player"] == {"A": 2, "B": 2}
    assert result["correct_assignment_score"] == 1.0
    assert result["swapped_assignment_score"] == 0.0
    assert result["contrast"] == 1.0
    assert result["accuracy_by_profile_and_player"] == {
        "profile_A": {"A": 1.0, "B": 0.0},
        "profile_B": {"A": 0.0, "B": 1.0},
    }
    assert "fidelity_percent" not in result
    assert "historical_fidelity" not in result
    assert all("fen" not in k.lower() for k in result["accuracy_by_profile_and_player"])

    inverted = compare_shared_fen_assignments(
        rows, choices, player_a="A", player_b="B",
        profile_a="profile_B", profile_b="profile_A")
    assert inverted["correct_assignment_score"] == 0.0
    assert inverted["swapped_assignment_score"] == 1.0
    assert inverted["contrast"] == -1.0


def test_identical_profiles_cannot_show_discriminative_signal():
    rows, choices = _case()
    choices["profile_B"] = dict(choices["profile_A"])
    result = _compare(rows, choices)
    assert result["correct_assignment_score"] == 0.5
    assert result["swapped_assignment_score"] == 0.5
    assert result["contrast"] == 0.0


def test_duplicate_records_do_not_inflate_independent_games_or_score():
    rows, choices = _case()
    expected = _compare(rows, choices)
    result = _compare(rows + [dict(rows[0]), dict(rows[0])], choices)
    assert result["duplicate_records_ignored"] == 2
    for key in ("matched_fens", "correct_assignment_score",
                "swapped_assignment_score", "contrast", "observations_by_player"):
        assert result[key] == expected[key]
    assert result["input_evidence_sha256"] == expected["input_evidence_sha256"]


def test_distinct_games_with_different_choices_are_not_collapsed():
    rows, choices = _case()
    white = _fen_white()
    rows.append(_row("A", "A3", white, "d2d4"))
    result = _compare(rows, choices)
    assert result["distinct_games_by_player"] == {"A": 3, "B": 2}
    assert result["matched_fens"] == 2
    assert result["accuracy_by_profile_and_player"]["profile_A"]["A"] == 0.75
    assert result["accuracy_by_profile_and_player"]["profile_B"]["A"] == 0.25
    assert result["correct_assignment_score"] == 0.875
    assert result["swapped_assignment_score"] == 0.125


def test_outside_opening_proxy_excludes_early_ply_and_requires_exact_fen():
    rows, choices = _case()
    beginning = chess.Board().fen()
    rows += [_row("A", "earlyA", beginning, "e2e4"),
             _row("B", "earlyB", beginning, "d2d4")]
    result = _compare(rows, choices)
    assert result["early_rows_excluded"] == 2
    assert result["matched_fens"] == 2
    assert "chronological" in result["limitations"]["opening_filter"].lower()


def test_no_shared_fens_fails_closed_without_claiming_discrimination():
    rows, _ = _case()
    rows = [r for r in rows if r["player"] == "A"]
    choices = {"profile_A": {}, "profile_B": {}}
    result = _compare(rows, choices)
    assert result["status"] == "NO_EXACT_SHARED_FENS"
    assert result["matched_fens"] == 0
    assert result["correct_assignment_score"] is None
    assert result["swapped_assignment_score"] is None
    assert result["contrast"] is None


@pytest.mark.parametrize("mutation", [
    "invalid_fen", "illegal_historical_move", "wrong_ply",
    "conflicting_duplicate", "missing_prediction", "extra_prediction",
    "illegal_prediction", "wrong_model_type", "unknown_player",
    "invalid_game", "invalid_threshold",
])
def test_malformed_or_noncomparable_evidence_is_rejected(mutation):
    rows, choices = _case()
    white = _fen_white()
    if mutation == "invalid_fen":
        rows[0]["fen"] = "not a FEN"
    elif mutation == "illegal_historical_move":
        rows[0]["move"] = "e7e5"
    elif mutation == "wrong_ply":
        rows[0]["ply"] = 1
    elif mutation == "conflicting_duplicate":
        rows.append(dict(rows[0], move="d2d4"))
    elif mutation == "missing_prediction":
        del choices["profile_A"][white]
    elif mutation == "extra_prediction":
        choices["profile_A"][chess.Board().fen()] = "e2e4"
    elif mutation == "illegal_prediction":
        choices["profile_A"][white] = "e7e5"
    elif mutation == "wrong_model_type":
        choices["profile_B"] = []
    elif mutation == "unknown_player":
        rows[0]["player"] = "unexpected"
    elif mutation == "invalid_game":
        rows[0]["game"] = ""
    elif mutation == "invalid_threshold":
        with pytest.raises(ValueError):
            _compare(rows, choices, min_ply=True)
        return
    with pytest.raises(ValueError):
        _compare(rows, choices)


def test_order_and_dict_insertion_do_not_affect_provenance_or_statistics():
    rows, choices = _case()
    first = _compare(rows, choices)
    reverse_predictions = {
        "profile_B": dict(reversed(list(choices["profile_B"].items()))),
        "profile_A": dict(reversed(list(choices["profile_A"].items()))),
    }
    second = _compare(list(reversed(rows)), reverse_predictions)
    assert first == second


def test_name_swap_is_only_a_label_control_not_scientific_proof():
    rows, choices = _case()
    result = _compare(rows, choices)
    assert result["limitations"]["split_provenance"] == "NOT_VERIFIED"
    assert result["limitations"]["inference"] == "DESCRIPTIVE_NO_INDEPENDENCE_CLAIM"
    assert "independent historical validation" in result["limitations"]["interpretation"].lower()
