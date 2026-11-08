"""#58 local HTTP API: authorize mutations and keep read-only routes safe."""
from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chesssim import arena


@contextmanager
def server(tmp_path):
    root=tmp_path/"engine";root.mkdir()
    workspace=tmp_path/"workspace"
    service=arena.create_server(root,workspace,port=0)
    thread=threading.Thread(target=service.serve_forever,
                            kwargs={"poll_interval":.01},daemon=True)
    thread.start()
    try:
        yield service,"http://127.0.0.1:"+str(service.server_port)
    finally:
        service.shutdown()
        service.server_close()
        service.arena_manager.shutdown()
        thread.join(timeout=2)


def get(base,route):
    with urlopen(base+route,timeout=12) as response:
        return response.status,json.load(response)


def post(base,route,body,token,*,origin=None,ctype="application/json"):
    payload=body if isinstance(body,bytes) else json.dumps(body).encode()
    req=Request(base+route,method="POST",data=payload,headers={
        "Content-Type":ctype,"X-Chesssim-Token":token,
        "Origin":origin or base,
    })
    return urlopen(req,timeout=12)


DATA={
    "name":"Copa dos Imortais","edition":"Edição 1",
    "preset":"custom","participants":["fischer","tal"],
    "execution_mode":"fast_statistical","seed":19,"depth":6,
}


def test_cup_endpoints_with_real_HTTP_roundtrip(tmp_path):
    with server(tmp_path) as (service,base):
        assert get(base,"/api/cups")[1]["cups"]==[]
        p=get(base,"/api/cups/presets")[1]
        assert len(p["standard"]["participants"])==64
        assert len(p["world_champions"]["participants"])==18
        assert p["women"]["format"]=="double_round_robin"
        with post(base,"/api/cups/create",DATA,service.arena_token) as response:
            assert response.status==201
            ident=json.load(response)["id"]
        assert len(get(base,"/api/cups")[1]["cups"])==1
        first=get(base,"/api/cups/"+ident)[1]
        assert first["completed_games"]==0
        assert first["next_game"]["stage"]=="classical"
        with post(base,"/api/cups/"+ident+"/run",{},service.arena_token) as response:
            assert response.status==202
        until=time.monotonic()+8
        while time.monotonic()<until:
            info=get(base,"/api/cups/"+ident)[1]
            if info["status"]!="RUNNING":
                break
            time.sleep(.05)
        assert info["completed_games"]==1
        assert info["recent"][0]["source"]=="fast_statistical"
        assert info["recent"][0]["pgn"] is False
        assert get(base,"/api/cups")[1]["cups"][0]["completed_games"]==1
        with urlopen(base+"/api/cups/"+ident+"/export/json") as response:
            assert response.headers["Content-Disposition"].endswith('.json"')
            assert len(json.load(response)["completed_games"])==1
        for path in (
            "/api/cups/"+ident+"/export/pgn",
            "/api/cups/"+ident+"/games/"+info["recent"][0]["id"]+"/replay",
        ):
            with pytest.raises(HTTPError) as e:
                urlopen(base+path)
            assert e.value.code==404
        for path in ("/cup.js","/cup.css"):
            with urlopen(base+path) as response:
                assert response.status==200 and response.read()
        assert get(base,"/api/arena")[1]["cup_busy"] is False


def test_cup_http_rejects_untrusted_requests(tmp_path):
    with server(tmp_path) as (service,base):
        token=service.arena_token
        for wrong,origin in ((token,"https://evil.example"),
                             ("wrong-token",base),
                             ("",base)):
            with pytest.raises(HTTPError) as e:
                post(base,"/api/cups/create",DATA,wrong,origin=origin)
            assert e.value.code==403
        with pytest.raises(HTTPError) as e:
            post(base,"/api/cups/create",DATA,token,ctype="text/plain")
        assert e.value.code==415
        with pytest.raises(HTTPError) as e:
            post(base,"/api/cups/create",b"x"*4097,token)
        assert e.value.code==413
        with pytest.raises(HTTPError) as e:
            post(base,"/api/cups/create",b'{"name":"a","name":"b"}',token)
        assert e.value.code==400
        with pytest.raises(HTTPError) as e:
            post(base,"/api/cups/create",{**DATA,"path":"../../etc"},token)
        assert e.value.code==400
        with post(base,"/api/cups/create",DATA,token) as response:
            ident=json.load(response)["id"]
        with pytest.raises(HTTPError) as e:
            post(base,"/api/cups/"+ident+"/run",{"exe":"/etc/passwd"},token)
        assert e.value.code==400
        with pytest.raises(HTTPError) as e:
            post(base,"/api/cups/cup-../../etc/run",{},token)
        assert e.value.code==404
        with pytest.raises(HTTPError) as e:
            urlopen(Request(base+"/api/cups",headers={"Host":"evil.example"}))
        assert e.value.code==403
        assert get(base,"/api/cups/"+ident)[1]["completed_games"]==0


def test_cup_human_duel_and_league_mutual_exclusion(tmp_path):
    with server(tmp_path) as (service,base):
        cup=service.arena_manager.cups
        entered=threading.Event()
        release=threading.Event()
        def blocked(root,spec,config,resources,stop_check):
            entered.set()
            if not release.wait(timeout=8):
                raise RuntimeError("Timeout test")
            return None,{"status":"INCOMPLETE","result":"*",
                         "termination":"OperatorStop"}
        cup.runner=blocked
        # Worker blocks only with engine mode; replace preflight to synthetic
        # resource snapshot without depending on Rodent IV files.
        cup._preflight=lambda cfg:{
            "engine_sha256":"a"*64,
            "profile_sha256":{p:"b"*64 for p in cfg["participants"]},
        }
        raw={**DATA,"execution_mode":"engine_full"}
        with post(base,"/api/cups/create",raw,service.arena_token) as response:
            ident=json.load(response)["id"]
        with post(base,"/api/cups/"+ident+"/run",{},service.arena_token):
            pass
        try:
            assert entered.wait(timeout=4)
            assert get(base,"/api/arena")[1]["busy"] is True
            assert get(base,"/api/arena")[1]["cup_busy"] is True
            with pytest.raises(HTTPError) as e:
                post(base,"/api/cups/"+ident+"/run",{},service.arena_token)
            assert e.value.code==409
            with pytest.raises(HTTPError) as e:
                post(base,"/api/arena/launch",{
                    "white":"fischer","black":"tal","mode":"depth","depth":3,
                    "reverse":True,"opening_mode":"none","max_plies":80},
                    service.arena_token)
            assert e.value.code==409
            with post(base,"/api/cups/"+ident+"/pause",{},service.arena_token) as response:
                assert json.load(response)["pause_requested"]
        finally:
            release.set()
