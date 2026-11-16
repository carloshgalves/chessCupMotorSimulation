"""Blind wrong-name controls must not invent historical style differences."""
import json

import chess
import pytest

from chesssim import real85_blind_controls as blind
from chesssim import real85_online_residual as net
from chesssim.real85_neural_stream import GAME_SCHEMA


def test_bootstrap_treats_games_not_lances_as_independent_units():
    same=blind.paired_interval([0.0]*9,reps=200,seed=32)
    assert same["n_games"]==9
    assert same["mean_difference"]==0
    assert same["confidence_95"]==[0.0,0.0]
    negative=blind.paired_interval([-0.9,-0.8,-0.1],reps=300,seed=44)
    assert negative["mean_difference"] < 0
    assert negative["confidence_95"][1] < 0
    assert blind.paired_interval([],reps=100)["status"]=="NO_COMPARABLE_GAMES"
    for invalid in (0,True,99,5001):
        with pytest.raises(ValueError,match="bootstrap"):
            blind.paired_interval([0.1],reps=invalid)


def test_same_historical_fen_and_negative_for_both_personalities(monkeypatch):
    monkeypatch.setattr(blind,"neural_arm",lambda digest:"tune")
    w=net._initial(445)
    other=net._initial(445)
    generic=net._initial(445)
    board=chess.Board()
    game={"schema":GAME_SCHEMA,"player":"fischer","arm":"tune",
          "game_sha256":"f"*64,"initial_fen":board.fen(),
          "focal_color":"white",
          "uci_moves":["e2e4","e7e5","g1f3","b8c6"],
          "focal_decisions":2}
    m={"fischer":w,"tal":other}
    before=json.dumps(m,sort_keys=True)
    result=blind._game(game,m,slug="fischer",generic=generic)
    assert result["decisions"]==2
    assert result["comparisons"]==2
    assert result["losses"]["fischer"]==pytest.approx(result["losses"]["tal"])
    assert result["losses"]["fischer"]==pytest.approx(result["losses"]["generic-untrained"])
    assert json.dumps(m,sort_keys=True)==before
    invalid=dict(game,arm="fit")
    with pytest.raises(ValueError,match="FIT/TEST"):
        blind._game(invalid,m,slug="fischer",generic=generic)
    illegal=dict(game,uci_moves=["e2e5"])
    with pytest.raises(ValueError,match="Illegal historical"):
        blind._game(illegal,m,slug="fischer",generic=generic)


def test_untrained_equal_styles_remain_equal_and_ties_reportable():
    m=net._initial(445)
    board=chess.Board()
    p=chess.Move.from_uci("e2e4")
    q=chess.Move.from_uci("d2d4")
    f,_=net._pair(m,board,p,q,update=False)
    g,_=net._pair(net._initial(445),board,p,q,update=False)
    assert f==g
