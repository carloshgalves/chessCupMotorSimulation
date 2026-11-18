import chess
import chess.pgn
import pytest

from chesssim import fischer_fit_ablation as ab
from chesssim.experimental_policy_adapter import PolicyDecision
from chesssim.game_diversity import mainline_identity


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
            return PolicyDecision(alt,{"outcome":"EXTERNAL_POLICY_OVERRIDE"})
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


def test_all_development_games_not_just_peak(monkeypatch):
    rows=[{"mainline_sha256":f"{n:064x}", "date":"1955","arm":"fit"} for n in range(683)]
    rows += [{"mainline_sha256":f"{n:064x}","date":"1992","arm":"tune"} for n in range(683,706)]
    rows += [{"mainline_sha256":f"{n:064x}","arm":"excluded_test"} for n in range(706,827)]
    arms={r["mainline_sha256"]:r["arm"] for r in rows}
    monkeypatch.setattr(ab,"neural_arm",lambda digest:arms[digest])
    chosen=ab.select_all_development(rows)
    assert len(chosen)==706
    assert {r["mainline_sha256"] for r in chosen}==set(arms)-{
        r["mainline_sha256"] for r in rows if r["arm"]=="excluded_test"}
    with pytest.raises(ValueError,match="Duplicate"):
        ab.select_all_development(rows+[rows[0]])


def test_every_focal_decision_counted_and_reserved_game_rejected(monkeypatch):
    game=chess.pgn.Game()
    node=game
    for san in ("e4","e5","Nf3","Nc6","Bb5","a6","Ba4","Nf6"):
        node=node.add_variation(node.board().parse_san(san))
    digest,_=mainline_identity(game)
    row={"mainline_sha256":digest,"color":"white","date":"1955"}
    monkeypatch.setattr(ab,"neural_arm",lambda _: "fit")
    obs,counts=ab.sample_game(game,row)
    assert counts["focal_decisions"]==counts["nonforced"]==4
    assert counts["eligible_opening"]==4
    assert len(obs)==1 and obs[0][0]=="opening"
    obs2,_=ab.sample_game(game,row)
    assert obs[0][1].fen()==obs2[0][1].fen()
    monkeypatch.setattr(ab,"neural_arm",lambda _: "excluded_test")
    with pytest.raises(ValueError,match="Reserved"):
        ab.sample_game(game,row)
