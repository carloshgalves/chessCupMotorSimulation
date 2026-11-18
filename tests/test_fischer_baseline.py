"""Regression tests: no manufactured ratings, FIT-only choices and correct perspective."""
from collections import Counter, defaultdict

import chess
import chess.pgn
import pytest

from chesssim import fischer_baseline as baseline
from chesssim.game_diversity import mainline_identity
from chesssim.real85_neural_stream import neural_arm


def make_game():
    game = chess.pgn.Game()
    game.headers.update({"White":"Fischer, Robert James","Black":"Opponent",
                         "Date":"1971.01.01","Result":"*"})
    node = game
    for san in ("e4","e5","Nf3","Nc6","Bb5","a6","Ba4","Nf6"):
        node = node.add_variation(node.board().parse_san(san))
    return game


def test_career_bands_and_material_not_ply():
    assert [baseline.period(y) for y in (1957,1958,1962,1963,1969,1970,1972,1973,1991,1992,None)] == [
        "through_1957","1958_1962","1958_1962","1963_1969","1963_1969",
        "1970_1972","1970_1972","1973_1991","1973_1991","1992_plus","unknown"]
    assert baseline.period("1971") == "1970_1972"
    with pytest.raises(ValueError,match="Invalid known"):
        baseline.period("197?")
    assert baseline.phase(chess.Board()) == "opening"
    assert baseline.phase(chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")) == "endgame"


def test_full_fit_style_and_book_sample_reservation(monkeypatch):
    game=make_game()
    digest,_=mainline_identity(game)
    row={"mainline_sha256":digest,"color":"white"}
    style=defaultdict(Counter)
    votes={side:defaultdict(Counter) for side in ("white","black")}
    monkeypatch.setattr(baseline,"neural_arm",lambda _: "fit")
    one=baseline.fit_observations(game,row,style,votes)
    assert len(one)==1
    stage,board,move=one[0]
    assert stage=="opening" and board.turn and move in board.legal_moves
    assert style["opening"]["decisions"]==4
    assert sum(sum(v.values()) for v in votes["white"].values())==4
    assert not votes["black"]
    two=baseline.fit_observations(game,row,defaultdict(Counter))
    assert one[0][1].fen()==two[0][1].fen()
    for arm in ("tune","excluded_test"):
        monkeypatch.setattr(baseline,"neural_arm",lambda _,a=arm:a)
        with pytest.raises(ValueError,match="Only globally"):
            baseline.fit_observations(game,row,defaultdict(Counter))


def test_signed_gaps_and_mates_remain_distinct(monkeypatch):
    board=chess.Board()
    move=chess.Move.from_uci("d2d4")
    def score(engine,board,forced,depth):
        assert depth==12
        return {"move":"e2e4","cp":25,"mate":None} if forced is None else {
            "move":forced.uci(),"cp":35,"mate":None}
    monkeypatch.setattr(baseline,"_score",score)
    result=baseline.quality(None,board,move,12)
    assert result["signed_gap_cp"]==-10 and not result["top1"]
    def mate(engine,board,forced,depth):
        return {"move":"e2e4","cp":None,"mate":3} if forced is None else {
            "move":forced.uci(),"cp":20,"mate":None}
    monkeypatch.setattr(baseline,"_score",mate)
    m=baseline.quality(None,board,move,12)
    rows=[dict(result,game="a",fen="x"),dict(m,game="b",fen="x")]
    summary=baseline.summarize(rows)
    assert summary["nonmate_count"]==1 and summary["mate_sensitive_count"]==1
    assert summary["negative_gap_count"]==1 and summary["distinct_fens"]==1
    assert summary["mean_signed_gap_cp"]==-10
    assert baseline.summarize([])["top1_rate"] is None


def test_census_counts_global_arms_without_rating_inference():
    ids={}
    for i in range(1000):
        digest=f"{i:064x}"
        ids[neural_arm(digest)]=digest
        if len(ids)==3:
            break
    assert set(ids)=={"fit","tune","excluded_test"}
    rows=[{"mainline_sha256":d,"date":"1971","color":"black",
           "focal_elo":"","time_control":"?"} for d in ids.values()]
    inv=baseline.inventory(rows)
    assert inv["distinct_games"]==3
    cell=inv["by_period"]["1970_1972"]
    assert cell["fit"]==cell["tune"]==cell["excluded_test"]==1
    assert cell["with_focal_elo_header"]==cell["with_time_control_header"]==0


def test_forced_lance_counts_but_never_measures_choice(monkeypatch):
    board=chess.Board("8/8/8/8/8/8/r1k5/K7 w - - 0 1")
    assert board.is_valid() and board.legal_moves.count()==1
    game=chess.pgn.Game()
    game.setup(board)
    game.add_variation(next(iter(board.legal_moves)))
    digest,_=mainline_identity(game)
    monkeypatch.setattr(baseline,"neural_arm",lambda _: "fit")
    style=defaultdict(Counter)
    result=baseline.fit_observations(game,{"mainline_sha256":digest,"color":"white"},style)
    assert result==[]
    assert style["endgame"]["decisions"]==style["endgame"]["forced_only_move"]==1
