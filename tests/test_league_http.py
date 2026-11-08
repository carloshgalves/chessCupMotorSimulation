"""Issue #59 local-HTTP contracts and hostile request gates."""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chesssim import arena
from test_leagues import config


@contextmanager
def live_arena(tmp_path):
    root=tmp_path/"engine";root.mkdir()
    output=tmp_path/"workspace"
    server=arena.create_server(root,output,port=0)
    running=threading.Thread(target=server.serve_forever,
                             kwargs={"poll_interval":.01},daemon=True)
    running.start()
    try:
        yield server,"http://127.0.0.1:"+str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        running.join(timeout=2)


def get(base,path="/api/leagues",**kwargs):
    with urlopen(Request(base+path,**kwargs),timeout=10) as response:
        return response.status,json.load(response)


def post(base,path,body,token,origin=None,ctype="application/json"):
    payload=body if isinstance(body,bytes) else json.dumps(body).encode("utf-8")
    return urlopen(Request(base+path,method="POST",data=payload,headers={
        "Content-Type":ctype,"X-Chesssim-Token":token,
        "Origin":origin or base,
    }),timeout=10)


def test_season_http_create_run_rounds_history_and_exports(tmp_path):
    with live_arena(tmp_path) as (server,base):
        assert get(base)[1]["leagues"]==[]
        for path in ("/league.js","/league.css"):
            with urlopen(base+path,timeout=10) as response:
                assert response.status==200 and response.read()
        with post(base,"/api/leagues/create",config(),server.arena_token) as response:
            assert response.status==201
            ident=json.load(response)["id"]
        listing=get(base)[1]["leagues"]
        assert len(listing)==1 and listing[0]["id"]==ident
        detail=get(base,"/api/leagues/"+ident)[1]
        assert detail["total_games"]==12 and len(detail["standings"])==4
        assert len(detail["round_progress"])==6
        with post(base,"/api/leagues/"+ident+"/run",{},server.arena_token) as response:
            assert response.status==202
        until=time.monotonic()+8
        while time.monotonic()<until:
            state=get(base,"/api/leagues/"+ident)[1]
            if state["status"]=="PAUSED":break
            time.sleep(.05)
        assert state["completed_games"]==2
        round_one=get(base,"/api/leagues/"+ident+"/round/1")[1]
        assert len(round_one["games"])==2
        assert all(g["status"]=="COMPLETED" for g in round_one["games"])
        history=get(base,"/api/leagues/"+ident+"/games?offset=0&limit=1")[1]
        assert history["total"]==2 and len(history["games"])==1
        with urlopen(base+"/api/leagues/"+ident+"/export/json",timeout=10) as response:
            assert response.headers["Content-Disposition"].endswith(".json"+'"')
            exported=json.load(response)
            assert len(exported["completed_games"])==2
        with pytest.raises(HTTPError) as error:
            urlopen(base+"/api/leagues/"+ident+"/export/pgn",timeout=10)
        assert error.value.code==404
        status=get(base,"/api/arena")[1]
        assert status["league_busy"] is False
        with pytest.raises(HTTPError) as error:
            get(base,"/api/leagues/"+ident+"/round/99")
        assert error.value.code==404


def test_security_rejects_untrusted_origins_tokens_malformed_ids_and_filters(tmp_path):
    with live_arena(tmp_path) as (server,base):
        token=server.arena_token
        for wrong,origin in (("bad-token",base),
                             (token,"https://attacker.invalid"),
                             ("",base)):
            with pytest.raises(HTTPError) as error:
                post(base,"/api/leagues/create",config(),wrong,origin=origin)
            assert error.value.code==403
        with pytest.raises(HTTPError) as error:
            post(base,"/api/leagues/create",config(),token,ctype="text/plain")
        assert error.value.code==415
        with pytest.raises(HTTPError) as error:
            post(base,"/api/leagues/create",b"q"*4097,token)
        assert error.value.code==413
        with pytest.raises(HTTPError) as error:
            post(base,"/api/leagues/create",b'{"name":"x","name":"y"}',token)
        assert error.value.code==400
        with pytest.raises(HTTPError) as error:
            post(base,"/api/leagues/create",config(path="../../etc"),token)
        assert error.value.code==400
        with post(base,"/api/leagues/create",config(),token) as response:
            ident=json.load(response)["id"]
        for route in (
            "/api/leagues/"+ident+"/games?offset=-1",
            "/api/leagues/"+ident+"/games?limit=101",
            "/api/leagues/"+ident+"/games?offset=0&offset=2",
            "/api/leagues/"+ident+"/games?evil=1",
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(base+route)
            assert error.value.code==400
        with pytest.raises(HTTPError) as error:
            post(base,"/api/leagues/"+ident+"/run",{"exe":"evil"},token)
        assert error.value.code==400
        with pytest.raises(HTTPError) as error:
            post(base,"/api/leagues/league-../../etc/run",{},token)
        assert error.value.code==404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+"/api/leagues",headers={"Host":"evil.example"}))
        assert error.value.code==403
        assert get(base)[1]["leagues"][0]["completed_games"]==0
