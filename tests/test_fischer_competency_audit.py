"""Six-dimensional Fischer protocol: exhaustive coverage, true controls and safety."""
import chess
import chess.pgn
import pytest

from chesssim import fischer_competency_audit as audit
from chesssim import fischer_strength_calibration as strength


def _game(moves=("e4","e5","Nf3","Nc6","Bc4","Bc5")):
    game=chess.pgn.Game()
    node=game
    for san in moves:
        node=node.add_variation(node.board().parse_san(san))
    return game


def test_every_historical_decision_is_kept_without_phase_sampling():
    game=_game()
    entries,forced=audit.game_decisions(game,{"color":"white"})
    assert len(entries)==3 and forced==0
    assert [b.ply() for b,m,stage,options in entries]==[0,2,4]
    assert all(stage=="opening" and options>1 for _,_,stage,options in entries)
    black,forced_black=audit.game_decisions(game,{"color":"black"})
    assert len(black)==3 and forced_black==0
    assert [b.ply() for b,_,_,_ in black]==[1,3,5]
    assert len(entries)+len(black)==6


def test_plan_uses_every_game_and_all_shards(monkeypatch):
    # Synthetic 2 per shard, fully populated source metadata. This is only
    # a unit fixture; not evidence about Fischer's corpus.
    rows=[{"mainline_sha256":f"{i:016x}"+("0"*48)} for i in range(128)]
    ids={r["mainline_sha256"]:("fit" if i<90 else "tune" if i<100 else
                              "excluded_test") for i,r in enumerate(rows)}
    monkeypatch.setattr(audit,"_source",lambda _: (b"fixture",rows,{},"LOCAL","Fischer"))
    monkeypatch.setattr(audit,"neural_arm",lambda g:ids[g])
    out=audit.plan()
    assert out["games"]==128 and out["arms"]=={"fit":90,"tune":10,"excluded_test":28}
    assert out["shards"]==64
    assert set(out["shard_game_counts"].values())=={2}
    assert out["calibrated_0_110"] is False
    assert audit.expected_row(rows[-1])==63
    with pytest.raises(ValueError,match="Invalid shard"):
        audit.run_shard(None,None,None,64)


def test_real_root_scored_same_depth_and_mates_not_converted(monkeypatch):
    board=chess.Board()
    human=chess.Move.from_uci("d2d4")
    e4=chess.Move.from_uci("e2e4")
    monkeypatch.setattr(audit,"_reference_top2",
        lambda *a:([{"move":e4,"cp":50,"mate":None},
                   {"move":human,"cp":0,"mate":None}]))
    monkeypatch.setattr(audit,"choose",lambda engine,board,depth:
                        (e4 if depth==18 else human,
                         {"requested_depth":depth,"reported_depth":depth,
                          "reported_nodes":100,"reached_requested":True}))
    seen=[]
    def fake_score(engine,board,move,depth):
        seen.append((move,depth))
        return {"move":move.uci(),"cp":60 if move==e4 else 20,"mate":None}
    monkeypatch.setattr(audit,"_score",fake_score)
    doc=audit.audit_decision(board,human,"opening",20,None,None)
    assert set(seen)=={(e4,20),(human,20)}
    assert doc["scores"]["human"]["cp_loss"]==40
    assert doc["scores"]["d16"]["cp_loss"]==40
    assert doc["scores"]["d18"]["cp_loss"]==0
    assert doc["scores"]["d18"]["reference_match"] is True
    assert doc["competencies"]==["opening"]
    assert doc["root_margin_cp"]==50
    assert doc["stratum"]=="medium|balanced|moderate"


def _policy(excess,over=0,mates=0,negative=0):
    return {"decisions":400,"paired_numeric":400,
            "mean_excess_vs_human_cp":excess,"over_100cp":over,
            "lost_positive_mate":mates,"negative_mate_selected":negative,
            "attained_requested_depth":400,"overshot_node_cap":0,
            "game_cluster_ci95_cp":[-3,3]}


def _fit_group(a,b):
    return {"decisions":400,"distinct_games":100,"policies":{
        "d16":_policy(a),"d18":_policy(b),"human":{}}}


def test_phase_budget_only_fit_and_risk_gates():
    fit={"by_phase":{"opening":_fit_group(5,2),
                     "middlegame":_fit_group(0.2,0.1),
                     "endgame":_fit_group(5,0)}}
    fit["by_phase"]["endgame"]["policies"]["d18"]["over_100cp"]=1
    selected=strength.choose_fit_depths(fit)
    assert {k:v["depth"] for k,v in selected["depths"].items()}=={
        "opening":18,"middlegame":16,"endgame":16}
    assert selected["eligible_for_arena"] is False
    board=chess.Board()
    assert strength.choice_depth(board,selected)==18
    assert strength.summarize_out_of_training(selected,{
        "tune":fit,"excluded_test":fit})["tune"]["opening"]["selected_depth"]==18
    with pytest.raises(ValueError):
        strength.summarize_out_of_training(selected,{"fit":fit})
    with pytest.raises(ValueError):
        strength.choice_depth(board,{**selected,"eligible_for_arena":True})


def test_no_score_invented_from_low_support():
    empty={"by_phase":{x:{"decisions":0,"distinct_games":0,"policies":{
        "d16":{"decisions":0,"paired_numeric":0,"mean_excess_vs_human_cp":None},
        "d18":{"decisions":0,"paired_numeric":0,"mean_excess_vs_human_cp":None}}}
        for x in strength.PHASES}}
    chosen=strength.choose_fit_depths(empty)
    assert all(x["depth"]==16 and x["reason"]=="INSUFFICIENT_SUPPORT"
               for x in chosen["depths"].values())
    assert chosen["six_competency_values"] is None
