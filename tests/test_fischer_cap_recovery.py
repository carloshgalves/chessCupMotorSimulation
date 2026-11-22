"""No original ChessCup case is reclassified solely by reaching a ply cap."""
import chess
import pytest

from chesssim import fischer_cap_recovery as rec


@pytest.fixture
def initial(monkeypatch):
    monkeypatch.setattr(rec, "reconstruct", lambda *a: chess.Board())
    return {"case":{"id":"a"*64,"color":"white","mode":"book",
                    "root":"b"*64,"arm":"fit","opponent_depth":16},
            "moves":["a2a3"]*rec.ORIGINAL_CAP}


def patch(initial,moves,status,result,termination):
    return {"schema":rec.SCHEMA,"source_run_id":rec.SOURCE_RUN_ID,
            "case":initial["case"],"original_sha256":"f"*64,
            "original_plies":rec.ORIGINAL_CAP,"extended_cap":rec.EXTENDED_CAP,
            "extra_moves":moves,"status":status,"result":result,
            "termination":termination,"reached":{},"missing":{},"nodes_exceeded":{}}


def test_scholars_mate_is_terminal_only_if_legal(initial):
    moves=["f2f3","e7e5","g2g4","d8h4"]
    p=patch(initial,moves,"COMPLETE","0-1","CHECKMATE")
    assert rec.validate_patch(initial,p,{},"f"*64)==p
    with pytest.raises(ValueError,match="Incorrect"):
        rec.validate_patch(initial,{**p,"result":"1/2-1/2"},{},"f"*64)
    with pytest.raises(ValueError,match="Moves after terminal"):
        rec.validate_patch(initial,{**p,"extra_moves":moves+["a2a3"]},{},"f"*64)


def test_ongoing_game_is_not_an_assumed_draw(initial):
    moves=["e2e4","e7e5"]
    progress=patch(initial,moves,"IN_PROGRESS","*","RECOVERY_IN_PROGRESS")
    assert rec.validate_patch(initial,progress,{},"f"*64)==progress
    with pytest.raises(ValueError,match="Incorrect"):
        rec.validate_patch(initial,{**progress,"status":"COMPLETE","result":"1/2-1/2",
                                     "termination":"FIFTY_MOVES"},{},"f"*64)
    with pytest.raises(ValueError,match="Incorrect"):
        rec.validate_patch(initial,{**progress,"status":"PARTIAL",
                                     "termination":"EXTENDED_CAP_INCOMPLETE"},{},"f"*64)
    assert rec.points(initial["case"],"1/2-1/2")==.5


def test_resume_exact_legal_prefix_after_interruption(monkeypatch,initial):
    monkeypatch.setattr(rec,"EXTENDED_CAP",rec.ORIGINAL_CAP+20)
    moves=["e2e4","e7e5","g1f3","b8c6","f1c4","f8c5",
           "d2d3","d7d6","b1c3","g8f6",
           "c1e3","c8e6","d1d2","d8d7","a2a3","a7a6",
           "h2h3","h7h6","e1g1","e8g8"]
    # The completed ten-move prefix stays immutable across the retry.
    checkpoints=[]
    def interrupted(engine,board,depth):
        if board.ply()>=10:
            raise RuntimeError("simulated runner interruption")
        return chess.Move.from_uci(moves[board.ply()]),{
            "reached_requested":True,"reported_depth":depth,"reported_nodes":100}
    monkeypatch.setattr(rec,"choose",interrupted)
    with pytest.raises(RuntimeError,match="interruption"):
        rec.extend_case(initial,{}, "f"*64,object(),object(),on_progress=checkpoints.append)
    assert len(checkpoints)==1
    assert checkpoints[0]["status"]=="IN_PROGRESS"
    assert len(checkpoints[0]["extra_moves"])==10
    def finish(engine,board,depth):
        return chess.Move.from_uci(moves[board.ply()]),{
            "reached_requested":True,"reported_depth":depth,"reported_nodes":100}
    monkeypatch.setattr(rec,"choose",finish)
    result=rec.extend_case(initial,{},"f"*64,object(),object(),
                           prior=checkpoints[0])
    assert result["extra_moves"]==moves
    assert result["status"]=="PARTIAL" and result["result"]=="*"
    assert len(result["extra_moves"])==20


def test_recovery_checkpoint_atomic_replacement(tmp_path):
    target=tmp_path/"rec.json"
    rec.atomic_replace(target,{"n":1})
    rec.atomic_replace(target,{"n":2})
    assert rec.original.load_json(target)=={"n":2}
    assert {p.name for p in tmp_path.iterdir()}=={"rec.json"}
    with pytest.raises(ValueError):
        rec.atomic_replace(tmp_path/"rec.json.tmp",{"n":1})
