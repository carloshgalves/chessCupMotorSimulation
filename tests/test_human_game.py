"""Issue #57: HTTP, validation, durability and actual synthetic UCI subprocess."""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import arena, human_game


@pytest.fixture
def synthetic_uci(tmp_path, monkeypatch):
    root = tmp_path / "engine"
    root.mkdir()
    executable = root / "rodent-iv-plain.exe"
    executable.write_text(
        r'''#!/usr/bin/env python3
import sys
import chess
board = chess.Board()
for raw in sys.stdin:
    line = raw.strip()
    if line == "uci":
        print("id name ChessSim synthetic UCI", flush=True)
        print("option name Personality type combo default Fischer var Fischer var Tal", flush=True)
        print("uciok", flush=True)
    elif line == "isready":
        print("readyok", flush=True)
    elif line == "ucinewgame":
        board.reset()
    elif line.startswith("position startpos"):
        board.reset()
        if " moves " in line:
            for uci in line.split(" moves ", 1)[1].split():
                board.push_uci(uci)
    elif line.startswith("position fen "):
        payload = line[len("position fen "):]
        fen, sep, moves = payload.partition(" moves ")
        board = chess.Board(fen)
        if sep:
            for uci in moves.split():
                board.push_uci(uci)
    elif line.startswith("go "):
        move = next(iter(board.legal_moves), None)
        print("bestmove " + (move.uci() if move else "(none)"), flush=True)
    elif line == "quit":
        break
''', encoding="utf-8")
    executable.chmod(0o700)
    profile = root / "Fischer.txt"
    profile.write_text("synthetic UCI profile\n", encoding="utf-8")

    def synthetic_validate(root_arg, a, b):
        assert root_arg == root
        assert a.slug == b.slug
        if not a.rodent_alias:
            raise ValueError("Perfil não disponível")
        return executable, {a.slug: profile}

    monkeypatch.setattr(human_game, "validate", synthetic_validate)
    monkeypatch.setattr(human_game, "official_book_hashes", lambda *args: {})
    workspace=tmp_path / "arena"
    workspace.mkdir()
    return root, workspace


@contextmanager
def server_context(synthetic_uci):
    root, workspace = synthetic_uci
    server = arena.create_server(root, workspace, port=0)
    runner = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01},daemon=True)
    runner.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        runner.join(timeout=1)


def get(base, path="/api/arena/human"):
    with urlopen(base + path,timeout=10) as response:
        return json.load(response)


def post(base, path, data, token, *, origin=None, mime="application/json"):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    req = Request(base + path, data=raw, method="POST",
                  headers={"Content-Type":mime, "X-Chesssim-Token":token,
                           "Origin": origin or base})
    return urlopen(req,timeout=15)


GOOD = {"opponent":"fischer","color":"white","depth":3,
        "preset":"blitz","opening_mode":"none"}


def test_human_config_rejects_invalid_values_and_untrusted_params():
    valid = human_game.normalize_human(GOOD)
    assert valid["color"] == "white"
    assert valid["clock"] == [180,2]
    for changes in (
        {"opponent":"bogus"}, {"opponent":"fischer/../../etc"},
        {"depth":0}, {"depth":17}, {"depth":True}, {"depth":"3"},
        {"color":"green"}, {"preset":"bad"}, {"preset":"custom"},
        {"preset":"blitz","initial_seconds":180},
        {"preset":"custom","initial_seconds":30,"increment_seconds":3},
        {"preset":"custom","initial_seconds":600,"increment_seconds":False},
        {"opening_mode":"historical"}, {"exe":"/etc/passwd"},
        {"depth":3.5},
    ):
        with pytest.raises(human_game.HumanInput):
            human_game.normalize_human({**GOOD,**changes})
    custom=human_game.normalize_human({
        **GOOD,"color":"black","preset":"custom",
        "initial_seconds":1800,"increment_seconds":20})
    assert custom["clock"]==[1800,20]


def test_human_http_security_and_denial_paths(synthetic_uci):
    with server_context(synthetic_uci) as (server,base):
        token = server.arena_token
        assert get(base)["game"] is None
        with urlopen(base+"/human-play.js",timeout=10) as page:
            assert b"Human vs UCI" in page.read()
        for wrong,origin in (("wrong-token",base),
                             (token,"https://attacker.test"),("",base)):
            with pytest.raises(HTTPError) as error:
                post(base,"/api/arena/human/start",GOOD,wrong,origin=origin)
            assert error.value.code==403
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/start",GOOD,token,mime="text/plain")
        assert error.value.code==415
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/start",b"x"*4097,token)
        assert error.value.code==413
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/start",
                 b'{"depth":3,"depth":4}',token)
        assert error.value.code==400
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/nope",{},token)
        assert error.value.code==404
        assert get(base)["game"] is None


def test_human_uci_subprocess_legal_move_pgn_clock_and_resignation(synthetic_uci):
    with server_context(synthetic_uci) as (server,base):
        token=server.arena_token
        with post(base,"/api/arena/human/start",GOOD,token) as response:
            assert response.status==201
            ident=json.load(response)["id"]
        state=get(base)["game"]
        assert state["id"]==ident and state["revision"]==0
        assert state["human_turn"] is True
        assert state["clocks"]==pytest.approx([180,180],abs=1)
        assert "e2e4" in state["legal_moves"]
        for bad in ("e2e5","a1a8","e2e4q","../../../etc"):
            with pytest.raises(HTTPError) as error:
                post(base,"/api/arena/human/move",{
                    "id":ident,"revision":0,"uci":bad},token)
            assert error.value.code==400
        with post(base,"/api/arena/human/move",{
            "id":ident,"revision":0,"uci":"e2e4"},token) as response:
            assert response.status==200
            assert json.load(response)["revision"]==1
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/move",{
                "id":ident,"revision":0,"uci":"e2e4"},token)
        assert error.value.code==409
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/start",GOOD,token)
        assert error.value.code==409
        arena_status=get(base,"/api/arena")
        assert arena_status["busy"] is True
        assert arena_status["human_busy"] is True
        assert arena_status["duel_busy"] is False
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/launch",{
                "white":"fischer","black":"tal","mode":"depth",
                "depth":3,"reverse":True,"opening_mode":"none",
                "max_plies":80},token)
        assert error.value.code==409
        end=time.monotonic()+12
        while time.monotonic()<end and get(base)["game"]["revision"]<2:
            time.sleep(.05)
        state=get(base)["game"]
        assert state["revision"]==2
        assert state["human_turn"] is True
        assert state["moves"][0]["uci"]=="e2e4"
        assert len(state["moves"])==2
        assert state["clocks"][0]>0 and state["clocks"][1]>0
        folder=synthetic_uci[1]/"human-games"/ident
        with (folder/"game.pgn").open(encoding="utf-8") as stream:
            game=chess.pgn.read_game(stream)
        assert game.headers["EngineSlug"]=="fischer"
        assert game.headers["SourceKind"]=="human_vs_engine"
        assert game.headers["TimeControl"]=="180+2"
        assert len(list(game.mainline_moves()))==2
        assert all(n.clock() is not None for n in game.mainline())
        with post(base,"/api/arena/human/action",{
            "id":ident,"action":"offer_draw"},token) as response:
            assert response.status==200
        assert "não negocia" in get(base)["game"]["draw_offer"]
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/action",{
                "id":ident,"action":"claim_draw"},token)
        assert error.value.code==400
        with post(base,"/api/arena/human/action",{
            "id":ident,"action":"resign"},token) as response:
            assert json.load(response)["status"]=="COMPLETED"
        state=get(base)["game"]
        assert state["result"]=="0-1"
        assert state["termination"]=="HumanResignation"
        with (folder/"game.pgn").open(encoding="utf-8") as stream:
            final=chess.pgn.read_game(stream)
        assert final.headers["Result"]=="0-1"


def test_human_time_forfeit_is_server_authoritative(synthetic_uci):
    with server_context(synthetic_uci) as (server,base):
        token=server.arena_token
        with post(base,"/api/arena/human/start",{
            **GOOD,"preset":"bullet"},token) as response:
            ident=json.load(response)["id"]
        human=server.arena_manager.human
        with human._lock:
            human._turn_started-=65
        state=get(base)["game"]
        assert state["id"]==ident
        assert state["status"]=="COMPLETED"
        assert state["result"]=="0-1"
        assert state["termination"]=="TimeForfeit"
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/move",{
                "id":ident,"revision":0,"uci":"e2e4"},token)
        assert error.value.code==409


def test_human_interrupt_restart_and_resume_only_same_engine(synthetic_uci):
    root,workspace=synthetic_uci
    with server_context(synthetic_uci) as (server,base):
        token=server.arena_token
        with post(base,"/api/arena/human/start",GOOD,token) as response:
            ident=json.load(response)["id"]
        with post(base,"/api/arena/human/action",{
            "id":ident,"action":"interrupt"},token) as response:
            assert json.load(response)["status"]=="INTERRUPTED"
    with server_context(synthetic_uci) as (server,base):
        old=get(base)["game"]
        assert old["id"]==ident and old["status"]=="INTERRUPTED"
        assert old["revision"]==0
        with post(base,"/api/arena/human/resume",{"id":ident},
                  server.arena_token) as response:
            assert json.load(response)["status"]=="RUNNING"
        with post(base,"/api/arena/human/move",{
            "id":ident,"revision":0,"uci":"e2e4"},server.arena_token) as response:
            assert response.status==200
        with post(base,"/api/arena/human/action",{
            "id":ident,"action":"interrupt"},server.arena_token) as response:
            assert json.load(response)["status"]=="INTERRUPTED"
    path=root/"rodent-iv-plain.exe"
    path.write_text(path.read_text()+"\n# changed\n",encoding="utf-8")
    with server_context(synthetic_uci) as (server,base):
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/resume",{"id":ident},server.arena_token)
        assert error.value.code==400


def test_black_human_engine_moves_first_and_rejects_out_of_turn(synthetic_uci):
    with server_context(synthetic_uci) as (server,base):
        token=server.arena_token
        with post(base,"/api/arena/human/start",{
            **GOOD,"color":"black","preset":"none"},token) as response:
            ident=json.load(response)["id"]
        with pytest.raises(HTTPError) as error:
            post(base,"/api/arena/human/move",{
                "id":ident,"revision":0,"uci":"e7e5"},token)
        assert error.value.code==409
        end=time.monotonic()+10
        while time.monotonic()<end and get(base)["game"]["revision"]<1:
            time.sleep(.05)
        state=get(base)["game"]
        assert state["revision"]==1
        assert state["human_turn"] is True
        assert state["clocks"] is None
        assert len(state["legal_moves"])>0


def test_synthetic_uci_states_are_replayable_and_server_owned(synthetic_uci):
    with server_context(synthetic_uci) as (server,base):
        token=server.arena_token
        with post(base,"/api/arena/human/start",GOOD,token) as response:
            ident=json.load(response)["id"]
        with post(base,"/api/arena/human/move",{
            "id":ident,"revision":0,"uci":"g1f3"},token):
            pass
        until=time.monotonic()+8
        while time.monotonic()<until and get(base)["game"]["revision"]<2:
            time.sleep(.05)
        game=get(base)["game"]
        assert len(game["states"])==game["revision"]+1
        assert game["states"][0]["fen"]==chess.Board().fen()
        assert game["states"][-1]["fen"]==game["fen"]
        previous=chess.Board()
        for ply,uci in enumerate(m["uci"] for m in game["moves"]):
            previous.push_uci(uci)
            assert game["states"][ply+1]["fen"]==previous.fen()


def test_interrupt_resume_drops_stale_engine_reply_and_closes_transport(
        synthetic_uci):
    from types import SimpleNamespace

    started=threading.Event()
    release=threading.Event()
    opened=[]
    class BlockingEngine:
        def __init__(self):
            self.closed=False
            self.number=len(opened)+1
        def play(self,board,limit,game,info):
            if self.number==1:
                started.set()
                assert release.wait(timeout=10)
            return SimpleNamespace(move=next(iter(board.legal_moves)))
        def quit(self):
            pass
        def close(self):
            self.closed=True
    def factory(root,exe,p,*,book):
        engine=BlockingEngine()
        opened.append(engine)
        return engine

    root,workspace=synthetic_uci
    game=human_game.HumanGame(root,workspace,engine_factory=factory)
    try:
        started_game=game.start({**GOOD,"color":"black"})
        ident=started_game["id"]
        assert started.wait(timeout=4)
        interrupted=game.action({"id":ident,"action":"interrupt"})
        assert interrupted["status"]=="INTERRUPTED"
        assert opened[0].closed
        assert game.resume({"id":ident})["status"]=="RUNNING"
        release.set()
        until=time.monotonic()+8
        while time.monotonic()<until and game.state()["game"]["revision"]<1:
            time.sleep(.05)
        current=game.state()["game"]
        assert current["revision"]==1
        assert len(opened)>=2, "Only the fresh epoch may produce a move"
        assert current["moves"][0]["ply"]==1
    finally:
        release.set()
        game.shutdown()


def test_classical_session_not_cut_off_by_duel_one_hour_guard(synthetic_uci):
    root,workspace=synthetic_uci
    game=human_game.HumanGame(root,workspace,max_wall_seconds=3600)
    try:
        ident=game.start({**GOOD,"preset":"classical"})["id"]
        with game._lock:
            assert game._wall_budget>7200
            game._session_started-=3700
            game._turn_started-=1
        state=game.state()["game"]
        assert state["id"]==ident
        assert state["status"]=="RUNNING"
        assert state["clocks"][0]>0
    finally:
        game.shutdown()


def test_pgndata_ahead_of_manifest_never_resumes_unsafe_clock(synthetic_uci):
    root,workspace=synthetic_uci
    game=human_game.HumanGame(root,workspace)
    ident=game.start(GOOD)["id"]
    game.move({"id":ident,"revision":0,"uci":"e2e4"})
    game.shutdown()
    folder=workspace/"human-games"/ident
    state_path=folder/"state.json"
    damaged=json.loads(state_path.read_text(encoding="utf-8"))
    damaged["revision"]=0  # stale state after successful PGN checkpoint
    state_path.write_text(json.dumps(damaged),encoding="utf-8")
    recovered=human_game.HumanGame(root,workspace)
    try:
        assert recovered.state()["game"]["status"]=="FAILED"
        assert recovered.state()["game"]["termination"]=="CheckpointMismatch"
        with pytest.raises(human_game.HumanBusy):
            recovered.resume({"id":ident})
    finally:
        recovered.shutdown()


def test_corrupt_nonfinite_persisted_clocks_are_rejected(synthetic_uci):
    root,workspace=synthetic_uci
    game=human_game.HumanGame(root,workspace)
    ident=game.start(GOOD)["id"]
    game.shutdown()
    state_file=workspace/"human-games"/ident/"state.json"
    manifest=json.loads(state_file.read_text(encoding="utf-8"))
    manifest["clocks"]=[float("nan"),180.0]
    state_file.write_text(json.dumps(manifest),encoding="utf-8")
    with pytest.raises(ValueError,match="Checkpoint humano"):
        human_game.HumanGame(root,workspace)


def test_threefold_draw_is_claimable_not_auto_adjudicated(synthetic_uci):
    root,workspace=synthetic_uci
    human=human_game.HumanGame(root,workspace)
    ident=human.start({**GOOD,"preset":"none"})["id"]
    try:
        # Two knight shuffles return to the initial position three times.
        repeated=("g1f3","g8f6","f3g1","f6g8")*2
        with human._lock:
            for uci in repeated:
                move=chess.Move.from_uci(uci)
                assert move in human._board.legal_moves
                human._append_locked(move)
            assert human._board.can_claim_threefold_repetition()
            assert human._data["status"]=="RUNNING"
            assert human._data["result"]=="*"
        response=human.action({"id":ident,"action":"claim_draw"})
        assert response["status"]=="COMPLETED"
        result=human.state()["game"]
        assert result["result"]=="1/2-1/2"
        assert result["termination"]=="HumanDrawClaim"
    finally:
        human.shutdown()
