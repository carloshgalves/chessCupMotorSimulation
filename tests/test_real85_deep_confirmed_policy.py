"""Adversarial before-play depth12 root guard on actual neural UCI candidates."""
import copy

import chess
import chess.engine
import pytest

from chesssim import real85_neural_guarded_uci as hybrid
from chesssim import real85_online_residual as residual


class ControlledStockfish:
    def __init__(self, *, delta=-15, mate=False, invalid_depth=False,
                 invalid_pv=False, dies=False):
        self.delta=delta
        self.mate=mate
        self.invalid_depth=invalid_depth
        self.invalid_pv=invalid_pv
        self.dies=dies
        self.shallow_calls=0
        self.deep_calls=[]

    def analyse(self, board, limit, *, info, multipv=None,
                root_moves=None, game=None):
        mover=board.turn
        if self.dies:
            raise OSError("Stockfish unavailable")
        def info_for(uci, value, *, depth=None):
            move=chess.Move.from_uci(uci)
            score=(chess.engine.Mate(3) if self.mate and root_moves is not None and uci=="d2d4"
                   else chess.engine.Cp(value))
            result={"pv":[move],"score":chess.engine.PovScore(score,mover)}
            if depth is not None:
                result["depth"]=depth
            return result
        if root_moves is None:
            assert limit.depth==8 and multipv==4
            self.shallow_calls+=1
            return [info_for("e2e4",30),info_for("d2d4",20),
                    info_for("g2g3",-120),info_for("f2f4",-130)]
        assert len(root_moves)==1
        assert limit.depth==12
        assert game is not None
        move=root_moves[0]
        self.deep_calls.append((board.fen(),move.uci()))
        score=100 if move.uci()=="e2e4" else 100+self.delta
        correct=move.uci()
        result=info_for("g1f3" if self.invalid_pv else correct,
                        score,depth=4 if self.invalid_depth else 12)
        return result


def _make_policy(sf, *, mode=100, confirm=12):
    checkpoint={"schema":residual.SCHEMA,"player":"fischer",
                "gradient_steps":1500,"model":residual._initial()}
    return hybrid.NeuralGuardedPolicy(
        "fischer",checkpoint,sf,depth=8,cp_budget=25,
        min_ply=1,override_percent=mode,confirmation_depth=confirm)


def _choose(policy):
    board=chess.Board()
    move=chess.Move.from_uci("e2e4")
    return policy.select(board,slug="fischer",engine_move=move,
                         round_number=1,ply=1)


def _style(monkeypatch):
    monkeypatch.setattr(hybrid,"_score",
                        lambda model,context,action:
                        10 if "to:27" in action else 0)


def test_harmful_deeper_regression_stays_with_original_rodent(monkeypatch):
    _style(monkeypatch)
    sf=ControlledStockfish(delta=-135)
    policy=_make_policy(sf)
    frozen=copy.deepcopy(policy.checkpoint["model"])
    selected=_choose(policy)
    assert selected.move.uci()=="e2e4"
    assert selected.audit["policy_move"]=="d2d4"
    assert selected.audit["outcome"]=="DEEP_CP_GUARD_RETAINED_RODENT"
    assert selected.audit["confirmation_neural_minus_rodent_cp"]==-135
    assert selected.audit["confirmation_depth"]==12
    assert selected.audit["sampled"] is False
    assert len(sf.deep_calls)==2
    assert sf.deep_calls[0][0]==sf.deep_calls[1][0]==chess.STARTING_FEN
    assert policy.checkpoint["model"]==frozen


def test_acceptable_deep_difference_can_make_actual_override(monkeypatch):
    _style(monkeypatch)
    sf=ControlledStockfish(delta=-25)
    selected=_choose(_make_policy(sf))
    assert selected.move.uci()=="d2d4"
    assert selected.audit["outcome"]=="EXTERNAL_POLICY_OVERRIDE"
    assert selected.audit["confirmation_neural_minus_rodent_cp"]==-25
    assert selected.audit["played_move"]=="d2d4"
    assert selected.audit["sampled"] is True
    shadow=_choose(_make_policy(ControlledStockfish(delta=-25),mode=0))
    assert shadow.move.uci()=="e2e4"
    assert shadow.audit["outcome"]=="SHADOW_ONLY"
    assert shadow.audit["confirmation_neural_minus_rodent_cp"]==-25


def test_unsafe_mate_and_broken_stockfish_abort_or_retain(monkeypatch):
    _style(monkeypatch)
    mate=_choose(_make_policy(ControlledStockfish(mate=True)))
    assert mate.move.uci()=="e2e4"
    assert mate.audit["outcome"]=="DEEP_MATE_GUARD_RETAINED_RODENT"
    assert mate.audit["confirmation_proposed_mate"]==3
    for kwargs,diagnostic in [
        ({"dies":True},"Stockfish safety engine failed"),
        ({"invalid_depth":True},"did not complete required deep root"),
        ({"invalid_pv":True},"incorrect forced PV"),
    ]:
        # Dies at shallow comparison; incorrect depth/PV only at the
        # second candidate confirmation stage, never silently bypassed.
        with pytest.raises(RuntimeError,match=diagnostic):
            _choose(_make_policy(ControlledStockfish(**kwargs)))


def test_legacy_gate_unchanged_and_invalid_depths_denied(monkeypatch):
    _style(monkeypatch)
    sf=ControlledStockfish(delta=-1000)
    legacy=_choose(_make_policy(sf,confirm=None))
    assert legacy.move.uci()=="d2d4"
    assert legacy.audit["outcome"]=="EXTERNAL_POLICY_OVERRIDE"
    assert not sf.deep_calls
    for bad in (True,0,8,9,21):
        with pytest.raises(ValueError,match="neural safety parameters"):
            _make_policy(ControlledStockfish(),confirm=bad)
