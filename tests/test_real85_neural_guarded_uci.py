"""Fail-closed original Rodent + frozen neural + shallow Stockfish safety guard."""
from types import SimpleNamespace

import chess
import chess.engine
import pytest

from chesssim import real85_neural_guarded_uci as hybrid
from chesssim import real85_online_residual as residual


class FakeStockfish:
    def __init__(self, *, worse=False, mate=False, fails=False):
        self.worse=worse
        self.mate=mate
        self.fails=fails
        self.calls=0

    def analyse(self, board, limit, multipv, info):
        self.calls+=1
        assert limit.depth==8
        assert multipv==4
        assert info==chess.engine.INFO_SCORE | chess.engine.INFO_PV
        if self.fails:
            raise OSError("UCI safety engine died")
        mover=board.turn
        e4=chess.Move.from_uci("e2e4")
        d4=chess.Move.from_uci("d2d4")
        g3=chess.Move.from_uci("g2g3")
        f4=chess.Move.from_uci("f2f4")
        def x(move,value):
            score=(chess.engine.Mate(3) if self.mate
                   else chess.engine.Cp(value))
            return {"pv":[move],"score":chess.engine.PovScore(score,mover)}
        return [x(e4,30),x(d4,-200 if self.worse else 19),
                x(g3,-150),x(f4,-160)]


def _policy(sf, *, mode=100, cp=25, min_ply=1):
    checkpoint={"schema":residual.SCHEMA,"player":"fischer",
                "gradient_steps":1,"model":residual._initial()}
    return hybrid.NeuralGuardedPolicy(
        "fischer",checkpoint,sf,depth=8,cp_budget=cp,
        min_ply=min_ply,override_percent=mode)


def test_safe_move_overrides_only_when_stockfish_restricts_to_near_equal(monkeypatch):
    board=chess.Board()
    base=chess.Move.from_uci("e2e4")
    d4=chess.Move.from_uci("d2d4")
    def style(_model,_ctx,features):
        return 10.0 if "to:27" in features else 0.0
    monkeypatch.setattr(hybrid,"_score",style)
    sf=FakeStockfish()
    policy=_policy(sf)
    unchanged=str(policy.checkpoint["model"])
    chosen=policy.select(board,slug="fischer",engine_move=base,
                         round_number=1,ply=1)
    assert chosen.move==d4
    assert chosen.audit["outcome"]=="EXTERNAL_POLICY_OVERRIDE"
    assert chosen.audit["proposed_cp_gap_from_best"]==11
    assert chosen.audit["safe_candidate_count"]==2
    assert str(policy.checkpoint["model"])==unchanged
    assert sf.calls==1
    shadow=_policy(FakeStockfish(),mode=0)
    traced=shadow.select(board,slug="fischer",engine_move=base,
                         round_number=1,ply=1)
    assert traced.move==base and traced.audit["outcome"]=="SHADOW_ONLY"
    assert traced.audit["policy_move"]=="d2d4"
    assert traced.audit["sampled"] is False


def test_unsafe_or_mating_alternative_never_overrides(monkeypatch):
    monkeypatch.setattr(hybrid,"_score",
                        lambda _model,_ctx,features:
                        10 if "to:27" in features else 0)
    board=chess.Board()
    base=chess.Move.from_uci("e2e4")
    bad=_policy(FakeStockfish(worse=True))
    returned=bad.select(board,slug="fischer",engine_move=base,
                        round_number=1,ply=1)
    assert returned.move==base
    assert returned.audit["outcome"]=="NO_SAFE_ALTERNATIVE"
    mated=_policy(FakeStockfish(mate=True))
    returned=mated.select(board,slug="fischer",engine_move=base,
                          round_number=1,ply=1)
    assert returned.move==base
    assert returned.audit["outcome"]=="MATE_SENSITIVE_RODENT_RETAINED"
    sf=FakeStockfish()
    control=_policy(sf)
    untouched=control.select(board,slug="control-simple",engine_move=base,
                             round_number=1,ply=1)
    assert untouched.move==base
    assert untouched.audit["outcome"]=="CONTROL_RODENT_UNMODIFIED"
    assert sf.calls==0
    early=_policy(sf,min_ply=41)
    assert early.select(board,slug="fischer",engine_move=base,
                        round_number=1,ply=1).move==base
    assert sf.calls==0


def test_failed_evaluator_blocks_game_and_bad_config_denied():
    board=chess.Board()
    with pytest.raises(RuntimeError,match="Stockfish safety engine failed"):
        _policy(FakeStockfish(fails=True)).select(
            board,slug="fischer",engine_move=chess.Move.from_uci("e2e4"),
            round_number=1,ply=1)
    for bad in (True,-1,101):
        with pytest.raises(ValueError,match="safety parameters"):
            _policy(FakeStockfish(),cp=bad)
    with pytest.raises(ValueError,match="safety parameters"):
        _policy(FakeStockfish(),mode=30)
    with pytest.raises(ValueError,match="UCI turn"):
        _policy(FakeStockfish()).select(
            board,slug="fischer",engine_move=chess.Move.from_uci("e2e5"),
            round_number=1,ply=1)


def test_real_executor_accepts_safe_policy_contract_without_official_clock(monkeypatch,tmp_path):
    from chesssim import matches
    from chesssim.catalog import Player
    base=chess.Move.from_uci("e2e4")
    actors=[]
    class FakeRodent:
        def play(self, board, limit, **kwargs):
            return SimpleNamespace(move=next(iter(board.legal_moves)))
        def quit(self):
            pass
        def close(self):
            pass
    monkeypatch.setattr(matches,"_open_engine",
                        lambda *a,**k:FakeRodent())
    w=Player(1,"Fischer","fischer","baseline")
    b=Player(2,"Rodent Simple","control-simple","baseline")
    policy=_policy(FakeStockfish(),min_ply=41)
    with pytest.raises(ValueError,match="development-only"):
        matches._play_one(tmp_path,tmp_path/"not-run",w,b,4,None,100,1,
                          {"exe":"a","fischer":"b","control-simple":"c"},
                          use_books=False,clock=(600,2),
                          experimental_policy=policy)
    assert actors==[]
