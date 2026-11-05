"""End-to-end loopback gates for opt-in arena launch, provenance and sessions."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import arena
from chesssim.catalog import find_player


FOOLS_MATE = ("f2f3", "e7e5", "g2g4", "d8h4")


def fake_duel(root, destination, white, black, **kwargs):
    del root
    out = destination / f"{white.slug}-vs-{black.slug}" / "20261105T000000Z-test"
    out.mkdir(parents=True)
    results = []
    for i, pair in enumerate(((white, black), (black, white)) if kwargs["reverse"]
                             else ((white, black),), 1):
        game = chess.pgn.Game()
        game.headers["White"] = pair[0].name
        game.headers["Black"] = pair[1].name
        node = game
        board = game.board()
        for item in FOOLS_MATE:
            move = chess.Move.from_uci(item)
            node = node.add_variation(move)
            board.push(move)
        game.headers["Result"] = "0-1"
        assert board.outcome().result() == "0-1"
        (out / f"jogo_{i}.pgn").write_text(str(game) + "\n", encoding="utf-8")
        results.append({"status": "COMPLETED", "result": "0-1"})
    (out / "summary.json").write_text(
        json.dumps({"status": "COMPLETED", "games": results}), encoding="utf-8")
    return out


@pytest.fixture
def rodent_stub(tmp_path, monkeypatch):
    root = tmp_path / "rodent"
    root.mkdir()
    (root / "rodent-iv-plain.exe").write_bytes(b"fake binary")
    def validate(root_arg, a, b):
        assert root_arg == root
        if a.rodent_alias is None or b.rodent_alias is None:
            raise ValueError("Indisponível")
        return (root / "rodent-iv-plain.exe",
                {a.slug: root / "fischer.txt", b.slug: root / "tal.txt"})
    monkeypatch.setattr(arena, "validate", validate)
    monkeypatch.setattr(arena, "official_book_hashes", lambda *args: {})
    return root, tmp_path / "runs"


@contextmanager
def arena_server(rodent_stub, runner=fake_duel, **opts):
    root, workspace = rodent_stub
    server = arena.create_server(root, workspace, port=0, runner=runner, **opts)
    task = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                            daemon=True)
    task.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        task.join(timeout=1)


def post(base, path, payload, token, *, origin=None, content_type="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": content_type, "X-Chesssim-Token": token,
               "Origin": origin or base}
    return urlopen(Request(base + path, data=body, method="POST", headers=headers), timeout=5)


def api(base, path="/api/arena"):
    with urlopen(base + path, timeout=5) as response:
        assert response.status == 200
        return json.load(response)


def wait_for(manager, status, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = manager.status()["session"]
        if value and value["status"] == status:
            return value
        time.sleep(0.01)
    raise AssertionError(f"Status {status} não alcançado")


GOOD = {"white":"fischer", "black":"tal", "mode":"depth", "depth":3,
        "reverse":True, "opening_mode":"none", "max_plies":80}


def test_profile_catalog_provenance_not_historical_authentication(rodent_stub):
    with arena_server(rodent_stub) as (_, base):
        data = api(base)
        assert data["busy"] is False
        assert len(data["players"]) == 64
        fischer = next(p for p in data["players"] if p["slug"] == "fischer")
        mecking = next(p for p in data["players"] if p["slug"] == "mequinho")
        assert fischer["installed"] is True
        assert fischer["historically_validated"] is False
        assert fischer["reference_status"] == "RESEARCH_HYPOTHESIS"
        assert fischer["model_status"].endswith("NOT_VALIDATED")
        assert mecking["installed"] is False
        assert mecking["reference_period"] == "1973-1978"
        assert mecking["reference_status"] == "CATALOG_REFERENCE_UNVERIFIED"


@pytest.mark.parametrize("changes", [
    {"white":"fischer", "black":"fischer"},
    {"black":"mequinho"},
    {"white":"nonexistent"},
    {"mode":"not-a-mode"},
    {"mode":"depth", "depth":0},
    {"mode":"depth", "depth":True},
    {"mode":"movetime", "movetime":float("nan")},
    {"mode":"movetime", "movetime":100},
    {"mode":"preset", "preset":"official", "opening_mode":"none"},
    {"mode":"preset", "preset":"official", "opening_mode":"internal", "reverse":False},
    {"mode":"preset", "preset":"official", "opening_mode":"internal", "max_plies":40},
    {"mode":"preset", "preset":[]},
    {"reverse":1},
    {"max_plies":9000},
    {"seed":2029},
    {"opening_mode":"historical"},
    {"unknown":"anything"}
])
def test_invalid_configs_fail_without_creating_sessions(rodent_stub, changes):
    config = {**GOOD, **changes}
    with pytest.raises(arena.BadLaunch):
        arena.normalize_config(config)
    assert not rodent_stub[1].exists()


def test_depth_movetime_official_and_seed_semantics():
    depth = arena.normalize_config(GOOD)
    assert depth["depth"] == 3 and depth["seed"] is None
    data = {k:v for k,v in GOOD.items() if k != "depth"}
    data.update({"mode":"movetime","movetime":0.4})
    assert arena.normalize_config(data)["movetime"] == 0.4
    data = {k:v for k,v in GOOD.items() if k != "depth"}
    data.update({"mode":"preset","preset":"official","opening_mode":"internal",
                 "max_plies":600})
    assert arena.normalize_config(data)["preset"] == "official"
    data = {**GOOD, "opening_mode":"historical","seed":2029}
    assert arena.normalize_config(data)["seed"] == 2029


def test_http_never_allows_launch_without_local_capability(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        for wrong, origin in (("bad-token", base),
                              (server.arena_token, "https://attacker.invalid"),
                              ("", base)):
            with pytest.raises(HTTPError) as exc:
                post(base, "/api/arena/launch", GOOD, wrong, origin=origin)
            assert exc.value.code == 403
        with pytest.raises(HTTPError) as exc:
            post(base, "/api/arena/launch", GOOD, server.arena_token,
                 content_type="text/plain")
        assert exc.value.code == 415
        with pytest.raises(HTTPError) as exc:
            post(base, "/api/arena/launch", b"x" * 4097, server.arena_token)
        assert exc.value.code == 413
        with pytest.raises(HTTPError) as exc:
            post(base, "/api/arena/launch", b'{"mode":"depth","mode":"preset"}',
                 server.arena_token)
        assert exc.value.code == 400
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + "/api/arena", headers={"Host": "bad.example"}))
        assert exc.value.code == 403
        assert not list(rodent_stub[1].glob("session-*"))


def test_launch_persists_configuration_and_replay_after_reload(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as response:
            assert response.status == 202
            launch = json.load(response)
        done = wait_for(server.arena_manager, "COMPLETED")
        assert launch["id"] == done["id"]
        assert done["config"]["seed"] is None
        assert done["config"]["white"] == "fischer"
        assert done["duel"]["results"] == ["0-1", "0-1"]
        board = api(base, "/api/state")
        assert board["duel"]["games"][0]["white"] == "Bobby Fischer"
        assert board["duel"]["games"][1]["white"] == "Mikhail Tal"
        assert board["duel"]["games"][0]["states"][-1]["san"] == "Qh4#"
        folder = rodent_stub[1] / done["id"]
        assert folder.is_dir()
        metadata = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        assert metadata["status"] == "COMPLETED"
    with arena_server(rodent_stub) as (_, base):
        historical = api(base)
        assert historical["session"]["id"] == done["id"]
        assert historical["session"]["status"] == "COMPLETED"
        assert api(base, "/api/state")["duel"]["games"][0]["white"] == "Bobby Fischer"


def test_one_active_match_and_stop_are_cooperative(rodent_stub):
    started = threading.Event()
    def waiting_runner(root, output, white, black, **kwargs):
        started.set()
        assert kwargs["stop_check"] is not None
        assert kwargs["stop_check"].__self__.wait(timeout=4)
        out = fake_duel(root, output, white, black, **kwargs)
        state = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        state["status"] = "INCOMPLETE"
        (out / "summary.json").write_text(json.dumps(state), encoding="utf-8")
        return out
    with arena_server(rodent_stub, runner=waiting_runner) as (server, base):
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as r:
            assert r.status == 202
        assert started.wait(timeout=2)
        with pytest.raises(HTTPError) as duplicate:
            post(base, "/api/arena/launch", GOOD, server.arena_token)
        assert duplicate.value.code == 409
        with post(base, "/api/arena/stop", {}, server.arena_token) as r:
            assert json.load(r)["stop_requested"] is True
        done = wait_for(server.arena_manager, "INCOMPLETE")
        assert done["status"] == "INCOMPLETE"
        assert done["duel"]["status"] == "INCOMPLETE"


def test_strict_http_security_and_local_viewer_frame(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        for uri in ("/", "/arena.js", "/arena.css", "/viewer", "/app.js", "/app.css"):
            with urlopen(base + uri) as response:
                assert response.status == 200
                assert len(response.read()) > 100
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Referrer-Policy"] == "no-referrer"
                if uri == "/":
                    assert "frame-src 'self'" in response.headers["Content-Security-Policy"]
                if uri == "/viewer":
                    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(base+"/api/state", data=b"{}", method="POST",
                            headers={"Origin":base,"X-Chesssim-Token":server.arena_token,
                                     "Content-Type":"application/json"}))
        assert denied.value.code == 404
        with pytest.raises(HTTPError) as missing:
            urlopen(base + "/private/pgn")
        assert missing.value.code == 404
        assert api(base,"/api/state")["duel"] is None


def test_bad_request_schema_is_not_executed(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        with pytest.raises(HTTPError) as exc:
            post(base, "/api/arena/launch", {**GOOD, "black":"mequinho"},
                 server.arena_token)
        assert exc.value.code == 400
        assert not list(rodent_stub[1].glob("session-*"))


def test_unexpected_aborted_session_is_never_replayed_automatically(rodent_stub):
    root, workspace = rodent_stub
    workspace.mkdir()
    path = workspace / "session-20261105T100000Z-abcdef"
    path.mkdir()
    (path / "session.json").write_text(json.dumps({
        "schema":"chesssim-arena-session-v1","id":path.name,
        "status":"RUNNING","config":GOOD,"created_utc":"2026-11-05T10:00:00Z"
    }),encoding="utf-8")
    manager = arena.ArenaManager(root, workspace, runner=fake_duel)
    assert manager.status()["session"]["status"] == "INTERRUPTED"
    assert manager.status()["busy"] is False


def test_official_launch_requires_internal_books_and_reverse(rodent_stub):
    data = {k:v for k,v in GOOD.items() if k!="depth"}
    data.update({"mode":"preset", "preset":"official", "opening_mode":"internal",
                 "max_plies":600})
    with arena_server(rodent_stub) as (server, base):
        with post(base,"/api/arena/launch",data,server.arena_token) as response:
            assert response.status == 202
        finished = wait_for(server.arena_manager,"COMPLETED")
        assert finished["config"]["preset"] == "official"
        assert finished["config"]["reverse"] is True


def test_external_historical_books_are_explicit_and_seeded(rodent_stub, tmp_path):
    book_dir=tmp_path/"opening-books"
    book_dir.mkdir()
    for slug in ("fischer","tal"):
        (book_dir / f"{slug}.json").write_text(json.dumps({
            "schema":"chesssim-opening-v9", "player":slug,
            "policy":{"max_plies":16}, "positions":{"white":{},"black":{}}
        }),encoding="utf-8")
    calls=[]
    def capture(root, output, white, black, **kwargs):
        calls.append(kwargs)
        return fake_duel(root,output,white,black,**kwargs)
    data={**GOOD,"opening_mode":"historical","seed":2030}
    with arena_server(rodent_stub,runner=capture,opening_books=book_dir) as (server,base):
        with post(base,"/api/arena/launch",data,server.arena_token) as response:
            assert response.status == 202
        wait_for(server.arena_manager,"COMPLETED")
    assert calls[0]["opening_seed"]==2030
    assert set(calls[0]["historical_openings"])=={"fischer","tal"}
    assert calls[0]["use_books"] is False
