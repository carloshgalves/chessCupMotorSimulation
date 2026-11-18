import chess
import pytest

from chesssim import fischer_full_games as fg


def root(arm="fit"):
    board=chess.Board()
    prefix=[]
    for san in ("e4","e5","Nf3","Nc6","Bb5","a6","Ba4","Nf6","O-O","Be7","Re1","b5"):
        move=board.parse_san(san)
        prefix.append(move.uci())
        board.push(move)
    return {"arm":arm,"id":fg.digest([arm,prefix]),"prefix":prefix,
            "source_games":["a"*64]}


def test_every_root_color_depth_mode_allocated_once():
    panel=[root("fit"),root("tune")]
    cases=list(fg.cases(panel))
    assert len(cases)==16 and len({c["id"] for c in cases})==16
    sharded=[c for index in range(2) for c in fg.shard_cases(panel,index,2)]
    assert {c["id"] for c in sharded}=={c["id"] for c in cases}
    assert len(sharded)==len(cases)
    assert fg.shard_cases(panel,0,2)==fg.shard_cases(list(reversed(panel)),0,2)
    assert len(fg.initial_board(panel[0]).move_stack)==12
    with pytest.raises(ValueError):
        fg.shard_cases(panel,2,2)


def test_capped_continuation_not_scored_and_trace_verified(monkeypatch):
    opening=root()
    case=next(c for c in fg.cases([opening]) if c["color"]=="white" and c["mode"]=="book")
    monkeypatch.setattr(fg,"MAX_CONTINUATION_PLIES",1)
    monkeypatch.setattr(fg,"choose",lambda engine,board,depth:
                        (board.parse_san("Bb3"),{"reached_requested":True,"reported_depth":depth,"reported_nodes":100}))
    monkeypatch.setattr(fg,"guard_book",lambda board,*args:(board.parse_san("c3"),"book_override"))
    result=fg.play_case(case,opening,None,None,None,{})
    assert result["status"]=="INCOMPLETE" and result["result"]=="*"
    assert result["interventions"]=={"book_override":1}
    assert fg.validate_case(result,case,opening)==result
    with pytest.raises(ValueError,match="False terminal"):
        fg.validate_case({**result,"status":"COMPLETE","result":"1-0"},case,opening)
    bad={**result,"book_trace":[{**result["book_trace"][0],"reason":"book_agreement"}]}
    with pytest.raises(ValueError,match="Invalid recorded book"):
        fg.validate_case(bad,case,opening)


def test_atomic_checkpoint_never_overwrites(tmp_path):
    path=tmp_path/"case.json"
    fg.atomic_json(path,{"value":1})
    assert fg.load_json(path)=={"value":1}
    with pytest.raises(ValueError,match="overwrite"):
        fg.atomic_json(path,{"value":2})
    assert fg.load_json(path)=={"value":1}


def test_no_clobber_leaves_no_stale_temporary(tmp_path):
    path=tmp_path/"case.json"
    fg.atomic_json(path,{"value":1})
    with pytest.raises(ValueError,match="overwrite"):
        fg.atomic_json(path,{"value":2})
    assert set(p.name for p in tmp_path.iterdir())=={"case.json"}
