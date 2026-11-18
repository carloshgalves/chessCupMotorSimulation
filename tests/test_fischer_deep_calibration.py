from types import SimpleNamespace

import chess
import pytest

from chesssim import fischer_deep_calibration as cal


def test_peak_panel_is_deterministic_and_excludes_test(monkeypatch):
    rows=[{"mainline_sha256":f"{i:064x}","arm":"peak_fit"} for i in range(60)]
    rows += [{"mainline_sha256":f"{i:064x}","arm":"tune"} for i in range(60,83)]
    rows += [{"mainline_sha256":f"{i:064x}","arm":None} for i in range(83,100)]
    monkeypatch.setattr(cal,"cohort",lambda row:row["arm"])
    select=lambda entries:{r["mainline_sha256"] for r in cal.select_panel(entries)}
    chosen=cal.select_panel(rows)
    assert sum(r["arm"]=="peak_fit" for r in chosen)==24
    assert sum(r["arm"]=="tune" for r in chosen)==23
    assert select(rows)==select(list(reversed(rows)))
    assert not any(r["arm"] is None for r in chosen)
    with pytest.raises(ValueError):
        cal.select_panel(rows[:23])


def test_depth_cap_is_not_depth_attainment():
    board=chess.Board()
    class Engine:
        def play(self,position,limit,game,info):
            assert limit.depth==16 and limit.nodes==cal.NODE_CAP
            return SimpleNamespace(move=chess.Move.from_uci("e2e4"),
                                   info={"depth":13,"nodes":cal.NODE_CAP,"time":2.0})
    move,meta=cal.choose(Engine(),board,16)
    assert move in board.legal_moves
    assert meta["reported_depth"]==13 and not meta["reached_requested"]
    row={"telemetry":{label:meta for label in cal.CANDIDATES}}
    stats=cal.telemetry_summary([row])
    assert stats["fischer_d16"]["below_requested"]==1
    assert stats["fischer_d16"]["reached_requested"]==0
    assert cal.telemetry_summary([])["fischer_d16"]["median_reported_depth"] is None


def test_contexts_overlap_and_never_require_observed_move():
    board=chess.Board()
    balanced={"cp":10,"mate":None}
    close={"cp":0,"mate":None}
    worse={"cp":-100,"mate":None}
    assert cal.tags(board,"opening",balanced,close)==["opening"]
    assert cal.tags(board,"middlegame",balanced,close)==["positional"]
    assert cal.tags(board,"middlegame",balanced,worse)==["tactics"]
    assert "defense" in cal.tags(board,"middlegame",worse,{"cp":-190,"mate":None})
    assert "endgames" in cal.tags(board,"endgame",balanced,close)
    assert "tactics" in cal.tags(board,"middlegame",{"cp":None,"mate":3},close)


def test_reference_fails_closed_for_incomplete_or_duplicate_pv():
    board=chess.Board()
    pv=[chess.Move.from_uci("e2e4")]
    complete={"pv":pv,"depth":cal.REFERENCE_DEPTH,"score":chess.engine.PovScore(chess.engine.Cp(10),True)}
    class Engine:
        def analyse(self,*args,**kwargs):
            return [complete,{**complete,"depth":cal.REFERENCE_DEPTH-1}]
    with pytest.raises(RuntimeError,match="Incomplete"):
        cal.reference_choices(Engine(),board)
    class Duplicated:
        def analyse(self,*args,**kwargs):
            return [complete,complete]
    with pytest.raises(RuntimeError,match="Duplicate"):
        cal.reference_choices(Duplicated(),board)
