"""No synthetic claims of real player fidelity: only regression fixtures."""
import chess
import pytest

from chesssim.dsc_conditional_residual_v2 import (
    ALPHAS, EPOCHS, FIT_PER_PLAYER, SEED, _arm,
    _encode, _prediction, _fit, _blind, _scores, _quality,
)


def _example(target="e2e4"):
    board=chess.Board()
    legal=sorted(m.uci() for m in board.legal_moves)
    base={move:1/len(legal) for move in legal}
    row={"fen":board.fen(),"move":target,
         "player":"fischer","game_sha256":"a"*64,"ply":21}
    return row,_encode(row,base)


def test_split_by_global_game_is_identical_across_player_aliases():
    assert _arm("a"*64) in ("fit","tune","audit")
    assert _arm("a"*64)==_arm("a"*64)
    with pytest.raises(ValueError,match="trajectory"):
        _arm("not-a-digest")
    assert EPOCHS==12 and SEED==4261 and FIT_PER_PLAYER==512
    assert ALPHAS==(0.0,0.25,0.5,1.0,2.0)


def test_true_softmax_legal_and_head_without_target_leakage():
    row, example=_example()
    assert len(example["features"])==chess.Board().legal_moves.count()
    assert _prediction(example,[0]*256,[0]*256,0) in example["moves"]
    assert len(_scores(example,[0]*256))==len(example["moves"])
    with pytest.raises(ValueError,match="complete legal-move"):
        _encode(row,{"e2e4":1.0})
    modified={**row,"move":"e2e5"}
    with pytest.raises(ValueError,match="illegal"):
        _encode(modified,{m:1/20 for m in example["moves"]})


def test_trainable_residual_uses_fit_human_labels_and_is_deterministic():
    cases=[_example("e2e4")[1] for _ in range(300)]
    first=_fit(cases,42)
    again=_fit(cases,42)
    assert first==again
    assert len(first)==256
    assert any(abs(v)>0.001 for v in first)
    with pytest.raises(ValueError,match="Insufficient"):
        _fit(cases[:10],42)


def test_blind_identity_does_not_claim_success_from_tie_break():
    rows=[]; examples=[]
    for slug in ("fischer","karpov","kasparov","tal"):
        for i in range(30):
            row,example=_example()
            row={**row,"player":slug,
                 "game_sha256":f"{len(rows)+1:064x}"}
            rows.append(row);examples.append(example)
    same=[0.0]*256
    data=_blind(rows,examples,{s:same for s in
                 ("fischer","karpov","kasparov","tal")},same,20)
    assert data["status"]=="BLIND_REAL_HISTORICAL_AUDIT_DEVELOPMENT"
    assert data["balanced_accuracy"]==pytest.approx(.25)
    assert data["balanced_game_bootstrap_95_ci"]==pytest.approx([.25,.25])
    assert sum(data["confusion_matrix"]["fischer"].values())==30



def test_human_target_does_not_change_policy_input_features():
    a,example=_example("e2e4")
    b,changed=_example("g1f3")
    assert a["fen"]==b["fen"]
    assert example["features"]==changed["features"]
    assert example["offsets"]==changed["offsets"]
    assert example["target"]!=changed["target"]
    assert _scores(example,[0.0]*256)==_scores(changed,[0.0]*256)


def test_proper_log_loss_and_top3_keep_historical_denominators():
    row,e=_example("e2e4")
    quality=_quality([row],[e],[0.0]*256,[0.0]*256,0.0)
    assert quality["positions"]==1
    assert quality["historical_games"]==1
    assert quality["mean_log_loss_nats"]==pytest.approx(
        __import__("math").log(20),abs=1e-7)
    assert 0 <= quality["top3_correct"] <= 1
