"""Issue #58 actual UCI-game runner contract using deterministic legal fake engines."""
from __future__ import annotations

from types import SimpleNamespace

import chess

from chesssim import cup_games
from chesssim.cup_rules import normalize, stage_game


def config():
    return normalize({"name":"Copa Teste","preset":"custom",
                      "participants":["fischer","tal"],
                      "execution_mode":"engine_full","depth":4})


def game_spec(cfg, stage=0):
    return stage_game({"id":"r01-m01","round":1,
                       "players":["fischer","tal"]},cfg,stage,0)


def resources():
    return {"engine_sha256":"a"*64,
            "profile_sha256":{"fischer":"b"*64,"tal":"c"*64}}


class FourPlyEngine:
    def __init__(self):
        self.calls=[]
        self.closed=False
    def play(self,board,limit,game,info):
        self.calls.append((board.ply(),limit.depth,limit.white_clock,
                           limit.black_clock,limit.white_inc,limit.black_inc))
        uci=("f2f3","e7e5","g2g4","d8h4")[board.ply()]
        move=chess.Move.from_uci(uci)
        assert move in board.legal_moves
        return SimpleNamespace(move=move)
    def quit(self):
        pass
    def close(self):
        self.closed=True


def test_real_uci_fide_classical_metadata_and_checkmate(tmp_path):
    created=[]
    def factory(root,exe,p,book):
        engine=FourPlyEngine()
        created.append(engine)
        return engine
    cfg=config()
    spec=game_spec(cfg)
    game,meta=cup_games.play_cup_game(
        tmp_path,spec,cfg,resources(),lambda:False,factory=factory)
    assert meta["status"]=="COMPLETED"
    assert meta["result"]=="0-1"
    assert meta["termination"]=="CHECKMATE"
    assert len(list(game.mainline_moves()))==4
    assert game.end().board().is_checkmate()
    assert game.headers["TimeControl"]=="40/5400:1800+30"
    assert game.headers["CupStage"]=="classical"
    assert game.headers["CupMatch"]=="r01-m01"
    assert game.headers["CupGame"]=="r01-m01-s0-g1"
    assert game.headers["WhiteProfileSHA256"]==resources()["profile_sha256"][spec["white"]]
    assert game.headers["BlackProfileSHA256"]==resources()["profile_sha256"][spec["black"]]
    assert game.headers["ColorDrawSHA256"]==spec["color_draw"]["sha256"]
    assert game.headers["ColorDrawPolicy"]=="independent-seeded-stage-lottery-v1"
    assert all(eng.closed for eng in created)
    assert sorted(len(eng.calls) for eng in created)==[2,2]
    assert all(c[1]==4 and c[4]==30 and c[5]==30
               for eng in created for c in eng.calls)
    assert all(node.clock() is not None for node in game.mainline())


def test_real_armageddon_time_is_asymmetric_and_bid_is_labeled(tmp_path):
    cfg=config()
    spec=game_spec(cfg,5)
    observed=[]
    def factory(root,exe,p,book):
        eng=FourPlyEngine()
        observed.append(eng)
        return eng
    game,meta=cup_games.play_cup_game(
        tmp_path,spec,cfg,resources(),lambda:False,factory=factory)
    assert meta["status"]=="COMPLETED"
    assert game.headers["ArmageddonBlackDrawOdds"]=="true"
    assert game.headers["ArmageddonBlackBid"]==str(
        spec["bid_proof"]["winning_black_seconds"])
    assert game.headers["BidPolicy"]=="synthetic-deterministic-no-UCI-bid"
    expected_inc=cfg["armageddon"]["increment_seconds"]
    all_calls=[call for eng in observed for call in eng.calls]
    assert all(call[4]==expected_inc and call[5]==expected_inc
               for call in all_calls)
    assert all_calls[0][2]==cfg["armageddon"]["white_seconds"]
    assert all_calls[0][3]==spec["bid_proof"]["winning_black_seconds"]
    assert all(eng.closed for eng in observed)


def test_uci_interruption_produces_no_scored_outcome(tmp_path):
    cfg=config()
    games=[]
    def factory(root,exe,p,book):
        eng=FourPlyEngine()
        games.append(eng)
        return eng
    game,meta=cup_games.play_cup_game(
        tmp_path,game_spec(cfg),cfg,resources(),
        lambda:True,factory=factory)
    assert meta["status"]=="INCOMPLETE"
    assert meta["result"]=="*"
    assert meta["termination"]=="OperatorStop"
    assert game.headers["Result"]=="*"
    assert list(game.mainline_moves())==[]
    assert all(eng.closed for eng in games)


def test_uci_time_forfeit_not_treated_as_completed_move(tmp_path,monkeypatch):
    cfg=config()
    spec=game_spec(cfg,5)
    tick=iter((0.0,0.0,0.0,9999.0))
    monkeypatch.setattr(cup_games,"monotonic",lambda:next(tick))
    engines=[]
    def factory(root,exe,p,book):
        engine=FourPlyEngine()
        engines.append(engine)
        return engine
    game,meta=cup_games.play_cup_game(
        tmp_path,spec,cfg,resources(),
        lambda:False,factory=factory)
    assert meta["status"]=="COMPLETED"
    assert meta["termination"]=="TimeForfeit"
    assert meta["result"]=="0-1"
    assert meta["plies"]==0
    assert len(list(game.mainline_moves()))==0
    assert meta["clock_left"][0]<=0
    assert all(eng.closed for eng in engines)
