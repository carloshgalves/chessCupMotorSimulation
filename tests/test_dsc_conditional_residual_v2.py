"""No synthetic claims of real player fidelity: only regression fixtures."""
from collections import Counter

import chess
import pytest

from chesssim.dsc_conditional_residual_v2 import (
    ALPHAS, EPOCHS, FIT_PER_PLAYER, SEED, _arm,
    _encode, _prediction, _fit, _blind, _scores,
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
