from types import SimpleNamespace

import chess

from chesssim import fischer_fit_ablation as ab


def test_book_guard_rejects_unsafe_and_mate(monkeypatch):
    board=chess.Board()
    base=chess.Move.from_uci("e2e4")
    alt=chess.Move.from_uci("d2d4")
    monkeypatch.setattr(ab,"select_book_move",lambda *a,**k:alt)
    def score(engine,board,move,depth):
        assert depth==14
        return {"cp":0 if move==alt else 30,"mate":None}
    monkeypatch.setattr(ab,"_score",score)
    assert ab.guard_book(board,base,{},None)==(base,"book_cp_guard")
    monkeypatch.setattr(ab,"_score",lambda *a:{"cp":None,"mate":1})
    assert ab.guard_book(board,base,{},None)==(base,"book_mate_guard")
    monkeypatch.setattr(ab,"_score",lambda *a:{"cp":10,"mate":None})
    assert ab.guard_book(board,base,{},None)==(alt,"book_override")


def test_mutually_exclusive_interventions_and_deep_audit(monkeypatch):
    board=chess.Board()
    base=chess.Move.from_uci("e2e4")
    alt=chess.Move.from_uci("d2d4")
    monkeypatch.setattr(ab,"choose",lambda *a:(base,{"reached_requested":True,"reported_depth":16}))
    monkeypatch.setattr(ab,"guard_book",lambda *a:(alt,"book_override"))
    monkeypatch.setattr(ab,"reference_choices",lambda *a:(
        {"move":"e2e4","cp":100,"mate":None},{"move":"d2d4","cp":0,"mate":None}))
    monkeypatch.setattr(ab,"_score",lambda engine,board,m,d:
                        {"move":m.uci(),"cp":100 if m==base else -10,"mate":None})
    class Policy:
        def select(self,*a,**kw):
            raise AssertionError("Residual in opening")
    tags,values,events,searches=ab.evaluate(board,alt,"opening",None,None,None,{},Policy())
    assert values["d16_book"]["human_match"] and values["d16_both"]["changed"]
    assert not values["d16_residual"]["changed"]
    c=ab.cell([{"game":"example","fen":board.fen(),"scores":values,
                "outcomes":events,"searches":searches}])
    assert c["interventions"]["d16_both"]["worse_over_100cp"]==1
    assert c["interventions"]["d16_base"]["changed"]==0
    board.fullmove_number=21
    monkeypatch.setattr(ab,"guard_book",lambda *a: (_ for _ in ()).throw(AssertionError("late book")))
    class Later:
        def select(self,*a,**kw):
            assert kw["ply"]==41
            return SimpleNamespace(move=alt,event={"outcome":"EXTERNAL_POLICY_OVERRIDE"})
    _,v,e,_=ab.evaluate(board,alt,"middlegame",None,None,None,{},Later())
    assert v["d16_residual"]["changed"] and v["d16_both"]["changed"]
    assert not v["d16_book"]["changed"]
    assert e["16"]["book"]=="outside_book"


def test_guard_analyses_use_fresh_game_identity():
    received=[]
    class Engine:
        def analyse(self,board,limit,**kwargs):
            received.append(kwargs)
            return {}
    engine=ab.FreshAnalysis(Engine())
    board=chess.Board()
    limit=chess.engine.Limit(depth=10)
    engine.analyse(board,limit,multipv=4)
    engine.analyse(board,limit,multipv=4)
    assert received[0]["game"] is not received[1]["game"]
    assert received[0]["multipv"]==received[1]["multipv"]==4
