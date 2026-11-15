"""Engineering fixtures only; no synthetic data used as scientific evidence."""
import chess
import pytest
from chesssim.dsc_four_player_identity import (
    PLAYERS, _centered_weights, _policy,
    _blind_accuracy, _independent_split,
)


def test_centering_removes_shared_elite_bias_without_name_leakage():
    profiles = {s: {"weights":{"capture": 2.0, "unique": i}}
                for i,s in enumerate(PLAYERS)}
    w = _centered_weights(profiles)
    assert all(w[s]["capture"] == 0 for s in PLAYERS)
    assert sum(w[s]["unique"] for s in PLAYERS) == 0
    with pytest.raises(ValueError, match="fixed"):
        _centered_weights({"tal": profiles["tal"]})


def test_top20_style_is_legal_and_ordinal_not_claimed_calibration():
    b=chess.Board()
    selected, probs = _policy(b, ["e2e4","g1f3"], {}, 0.0)
    assert selected == "e2e4"
    assert set(probs) == {"e2e4","g1f3"}
    assert sum(probs.values()) == pytest.approx(1)
    with pytest.raises(ValueError, match="Duplicate"):
        _policy(b, ["e2e4","e2e4"], {}, 0.2)


def test_audit_excludes_any_same_game_or_canonical_position_globally():
    b = chess.Board().fen()
    row = {"game_sha256": "a"*64, "ply": 21, "fen": b}
    tune = {s: [] for s in PLAYERS}
    audit = {s: [] for s in PLAYERS}
    tune["fischer"]=[row]
    audit["tal"]=[{"game_sha256": "b"*64, "ply": 23, "fen": b}]
    audit["kasparov"]=[row]
    _t,a = _independent_split(tune,audit)
    assert a["tal"] == [] and a["kasparov"] == []


def test_blind_classifier_abstains_when_true_move_outside_top_candidates():
    row = {"fen": chess.Board().fen(), "move":"a2a3",
           "game_sha256":"a"*64,"player":"fischer"}
    result = _blind_accuracy([row],[["e2e4","d2d4"]],
                              {s:{} for s in PLAYERS}, 0.2, 10)
    assert result["status"] == "UNDERPOWERED_CANDIDATE_COVERAGE"
    assert result["covered"] == 0
