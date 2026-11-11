"""End-to-end loopback gates for opt-in arena launch, provenance and sessions."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import arena, matches


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
    # Mirror the real match writer: never expose a half-written JSON file
    # to a concurrent arena status poll.
    from chesssim.matches import _atomic_text
    _atomic_text(out / "summary.json",
                 json.dumps({"status": "COMPLETED", "games": results}))
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
        # Production checkpoints use atomic replacement. Avoid test-only
        # torn JSON reads when the status thread polls this checkpoint.
        from chesssim.matches import _atomic_text
        _atomic_text(out / "summary.json", json.dumps(state))
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


def test_arena_viewer_events_share_read_only_contract(rodent_stub):
    """The same-origin iframe receives change hints without a POST capability."""
    with arena_server(rodent_stub) as (server, base):
        # No running duel is an explicit event checkpoint, not a missing route.
        with urlopen(base + "/api/events", timeout=3) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/event-stream")
            collected = []
            for _ in range(12):
                line = response.readline().decode("utf-8")
                collected.append(line)
                if line == "\\n" and any(x.startswith("event: state") for x in collected):
                    break
            payload_line = next(x for x in collected if x.startswith("data: "))
            data = json.loads(payload_line.removeprefix("data: "))
            assert data["schema"] == "chesssim-game-events-v1"
            assert data["duel"]["status"] is None
            assert data["games"] == []
            assert len(data["revision"]) == 64
        with pytest.raises(HTTPError) as wrong:
            urlopen(base + "/api/events?match=r01-m01", timeout=3)
        assert wrong.value.code == 400
        with pytest.raises(HTTPError) as forbidden:
            urlopen(Request(base + "/api/events",
                            headers={"Origin": "https://attacker.test"}), timeout=3)
        assert forbidden.value.code == 403
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as launched:
            assert launched.status == 202
        wait_for(server.arena_manager, "COMPLETED")
        with urlopen(base + "/api/events", timeout=3) as response:
            found = None
            for _ in range(15):
                line = response.readline().decode("utf-8")
                if line.startswith("data: "):
                    found = json.loads(line.removeprefix("data: "))
                    break
            assert found is not None
            assert found["duel"]["status"] == "COMPLETED"
            assert found["games"][0]["ply"] == len(FOOLS_MATE)
        assert api(base, "/api/state")["duel"]["status"] == "COMPLETED"


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


def test_workspace_lock_rejects_second_server_even_on_other_port(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        with pytest.raises(RuntimeError, match="Outra execução"):
            arena.create_server(rodent_stub[0], rodent_stub[1], port=0,
                                runner=fake_duel)
        assert api(base)["busy"] is False
    with arena_server(rodent_stub) as (_, base):
        assert api(base)["session"] is None


def test_worker_exception_is_recorded_and_slot_released(rodent_stub):
    def broken(*args, **kwargs):
        raise RuntimeError("sensitive local engine path")
    with arena_server(rodent_stub, runner=broken) as (server, base):
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as r:
            assert r.status == 202
        state = wait_for(server.arena_manager, "FAILED")
        assert state["error"] == "ENGINE_OR_ARTIFACT_FAILURE"
        assert "sensitive" not in json.dumps(api(base))
        assert api(base)["busy"] is False
        assert state["id"].startswith("session-")


def test_bad_persisted_state_returns_sanitized_http_error(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        manager = server.arena_manager
        chosen = manager.workspace / "session-20261105T100000Z-dead"
        chosen.mkdir()
        (chosen / "session.json").write_text(
            '{"schema":"chesssim-arena-session-v1","status":null}',
            encoding="utf-8")
        manager._selected = chosen
        with pytest.raises(HTTPError) as err:
            urlopen(base + "/api/arena", timeout=5)
        assert err.value.code == 503
        assert b"Estado da arena indispon" in err.value.read()


def test_arena_markup_never_includes_remote_scripts_or_inline_secrets():
    root = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"
    html = (root / "arena.html").read_text(encoding="utf-8")
    script = (root / "arena.js").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html
    assert 'src="/arena.js"' in html
    assert 'src="/viewer"' in html
    assert 'id="duel-form"' in html
    assert "innerHTML" not in script and "eval(" not in script
    assert "textContent" in script
    assert "X-Chesssim-Token" in script
    assert "fetch(\"/api/arena\"" in script


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_arena_browser_form_contract_and_javascript_syntax():
    root = Path(__file__).resolve().parents[1]
    script = root / "src" / "chesssim" / "data" / "arena.js"
    harness = root / "tests" / "assets" / "arena_browser_harness.cjs"
    syntax = subprocess.run(["node", "--check", str(script)],
                            capture_output=True, text=True, timeout=8, check=False)
    assert syntax.returncode == 0, syntax.stderr
    checked = subprocess.run(["node", str(harness), str(script)],
                             capture_output=True, text=True, timeout=12, check=False)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "Arena browser controls: PASS" in checked.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="Native process smoke is Linux-only")
def test_real_uci_process_launches_via_arena_without_external_engine(rodent_stub, monkeypatch):
    root, _ = rodent_stub
    engine = root / "rodent-iv-plain.exe"
    engine.write_text("""#!/usr/bin/env python3
import os
from pathlib import Path
import sys

with Path("arena-uci-pids.txt").open("a", encoding="ascii") as file:
    file.write(str(os.getpid()) + "\\n")

sequence = ["f2f3", "e7e5", "g2g4", "d8h4"]
moves = []
for raw in sys.stdin:
    cmd = raw.strip()
    if cmd == "uci":
        print("id name Arena Test UCI")
        print("option name Personality type combo default Fischer var Fischer var Tal")
        print("option name Threads type spin default 1 min 1 max 8")
        print("option name Hash type spin default 64 min 1 max 1024")
        print("option name UseBook type check default false")
        print("uciok", flush=True)
    elif cmd == "isready":
        print("readyok", flush=True)
    elif cmd.startswith("position "):
        moves = cmd.split(" moves ", 1)[1].split() if " moves " in cmd else []
    elif cmd.startswith("go"):
        print("bestmove " + sequence[len(moves) % 4], flush=True)
    elif cmd == "quit":
        break
""", encoding="utf-8")
    engine.chmod(0o755)
    for name in ("fischer", "tal"):
        (root / f"{name}.txt").write_text("profile " + name, encoding="utf-8")

    def preflight(root_arg, white, black):
        assert root_arg == root
        return engine, {
            white.slug: root / f"{white.slug}.txt",
            black.slug: root / f"{black.slug}.txt",
        }

    monkeypatch.setattr(arena, "validate", preflight)
    monkeypatch.setattr(matches, "validate", preflight)
    with arena_server(rodent_stub, runner=matches.play_duel) as (server, base):
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as response:
            assert response.status == 202
        done = wait_for(server.arena_manager, "COMPLETED", seconds=10)
        assert done["duel"]["results"] == ["0-1", "0-1"]
        game_state = api(base, "/api/state")
        assert [g["states"][-1]["san"] for g in game_state["duel"]["games"]] == [
            "Qh4#", "Qh4#",
        ]
        session_id = done["id"]
        assert (rodent_stub[1] / session_id / "session.json").is_file()

    pids = [int(pid) for pid in (root / "arena-uci-pids.txt")
            .read_text(encoding="ascii").splitlines()]
    assert len(pids) == 4  # two subprocesses per game, two color orders
    for _ in range(100):
        if not any(Path(f"/proc/{pid}").exists() for pid in pids):
            break
        time.sleep(0.02)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)


def test_deep_json_and_duplicate_keys_are_rejected(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        deeply_nested = (b"[" * 1500) + b"1" + (b"]" * 1500)
        with pytest.raises(HTTPError) as err:
            post(base, "/api/arena/launch", deeply_nested, server.arena_token)
        assert err.value.code == 400
        assert json.loads(err.value.read())["error"] in {
            "Corpo JSON inválido", "Corpo deve ser um objeto JSON"
        }
        duplicate = b'{"white":"fischer","white":"tal"}'
        with pytest.raises(HTTPError) as err:
            post(base, "/api/arena/launch", duplicate, server.arena_token)
        assert err.value.code == 400
        assert json.loads(err.value.read())["error"] == "Campo JSON duplicado"


def test_session_metadata_id_and_state_are_strictly_validated(rodent_stub):
    root, workspace = rodent_stub
    workspace.mkdir()
    location = workspace / "session-20261105T110000Z-bad"
    location.mkdir()
    path = location / "session.json"
    content = {"schema": "chesssim-arena-session-v1", "id": "session-other",
               "status": "COMPLETED", "config": GOOD,
               "created_utc": "2026-11-05T11:00:00Z"}
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError, match="incompatível"):
        arena.ArenaManager(root, workspace, runner=fake_duel)
    # Failed preflight cannot lock out the operator's next valid session.
    content["id"] = location.name
    path.write_text(json.dumps(content), encoding="utf-8")
    with arena_server(rodent_stub) as (_, base):
        assert api(base)["session"]["id"] == location.name



def test_terminal_checkpoint_and_busy_are_atomically_published(rodent_stub, monkeypatch):
    """A terminal status must never be readable while busy still says True."""
    entered = threading.Event()
    release = threading.Event()
    reader_started = threading.Event()
    reader_done = threading.Event()
    result = []

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic crash")

    with arena_server(rodent_stub, runner=broken) as (server, base):
        manager = server.arena_manager
        original_save = manager._save

        def delay_terminal_save(folder, state):
            if state["status"] == "FAILED":
                entered.set()
                assert release.wait(timeout=4)
            return original_save(folder, state)

        monkeypatch.setattr(manager, "_save", delay_terminal_save)
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as response:
            assert response.status == 202
        assert entered.wait(timeout=3)

        def observe():
            reader_started.set()
            result.append(manager.status())
            reader_done.set()

        reader = threading.Thread(target=observe, daemon=True)
        reader.start()
        try:
            assert reader_started.wait(timeout=2)
            assert not reader_done.wait(timeout=0.05), (
                "A terminal checkpoint cannot be observed before releasing busy"
            )
        finally:
            release.set()
            reader.join(timeout=3)
        assert reader_done.is_set()
        assert result[0]["busy"] is False
        assert result[0]["session"]["status"] == "FAILED"



def test_partial_live_checkpoint_is_not_reported_as_corrupt_completed_game(rodent_stub):
    """A legacy writer may expose zero-byte JSON during active execution."""
    entered = threading.Event()
    release = threading.Event()

    def legacy_runner(root, output, white, black, **kwargs):
        folder = output / f"{white.slug}-vs-{black.slug}" / "test-run"
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text("", encoding="utf-8")
        entered.set()
        assert release.wait(timeout=4)
        from chesssim.matches import _atomic_text
        _atomic_text(folder / "summary.json",
                     json.dumps({"status": "COMPLETED", "games": []}))
        return folder

    with arena_server(rodent_stub, runner=legacy_runner) as (server, base):
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as response:
            assert response.status == 202
        try:
            assert entered.wait(timeout=3)
            for _ in range(5):
                state = api(base)
                assert state["busy"] is True
                assert state["session"]["status"] == "RUNNING"
                assert state["session"]["duel"] is None
        finally:
            release.set()
        finished = wait_for(server.arena_manager, "COMPLETED")
        assert finished["duel"] == {"status": "COMPLETED", "results": []}


def test_corrupt_completed_duel_summary_fails_closed_in_api(rodent_stub):
    with arena_server(rodent_stub) as (server, base):
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as response:
            assert response.status == 202
        done = wait_for(server.arena_manager, "COMPLETED")
        folder = (rodent_stub[1] / done["id"] / "duels" /
                  "fischer-vs-tal" / "20261105T000000Z-test")
        (folder / "summary.json").write_text("", encoding="utf-8")
        with pytest.raises(HTTPError) as failure:
            urlopen(base + "/api/arena", timeout=5)
        assert failure.value.code == 503
        message = failure.value.read().decode("utf-8")
        assert "Estado da arena indisponível" in message
        assert str(rodent_stub[1]) not in message


def test_custom_clock_validation_and_runner_receives_exact_values(rodent_stub):
    raw={k:v for k,v in GOOD.items() if k != "depth"}
    raw.update({"mode":"preset","preset":"custom",
                "initial_seconds":1800,"increment_seconds":20})
    normalized=arena.normalize_config(raw)
    assert normalized["initial_seconds"] == 1800
    assert normalized["increment_seconds"] == 20
    for changes in (
        {"increment_seconds":-1}, {"increment_seconds":121},
        {"increment_seconds":True}, {"initial_seconds":0},
        {"initial_seconds":14401}, {"initial_seconds":1800.5},
    ):
        with pytest.raises(arena.BadLaunch):
            arena.normalize_config({**raw,**changes})
    with pytest.raises(arena.BadLaunch):
        arena.normalize_config({**raw,"preset":"rapid"})
    seen=[]
    def capture(root,destination,white,black,**kwargs):
        seen.append(kwargs["clock"])
        return fake_duel(root,destination,white,black,**kwargs)
    with arena_server(rodent_stub,runner=capture) as (server,base):
        with post(base,"/api/arena/launch",raw,server.arena_token) as response:
            assert response.status == 202
        finished=wait_for(server.arena_manager,"COMPLETED")
        assert finished["config"]["preset"] == "custom"
        assert finished["config"]["initial_seconds"] == 1800
    assert seen == [(1800,20)]


@pytest.mark.parametrize(("config","depth","clock"), [
    ({"preset":"bullet","depth":16},16,"bullet"),
    ({"preset":"blitz","depth":8},8,"blitz"),
    ({"preset":"custom","depth":16,"initial_seconds":1800,
      "increment_seconds":20},16,"custom"),
])
def test_depth_cap_and_clock_are_independent(config,depth,clock):
    normalized=arena.normalize_config({**GOOD,**config})
    assert normalized["mode"] == "depth"
    assert normalized["depth"] == depth
    assert normalized["preset"] == clock
    assert normalized["movetime"] is None


def test_official_protocol_remains_depth_free_and_movetime_clock_is_rejected():
    with pytest.raises(arena.BadLaunch, match="official"):
        arena.normalize_config({**GOOD,"preset":"official",
                                "opening_mode":"internal","max_plies":600})
    with pytest.raises(arena.BadLaunch, match="Tempo fixo"):
        arena.normalize_config({
            **{k:v for k,v in GOOD.items() if k != "depth"},
            "mode":"movetime","movetime":0.5,"preset":"blitz",
        })


def test_depth_plus_bullet_is_forwarded_to_duel_without_losing_clock(rodent_stub):
    observed=[]
    def capture(root,destination,white,black,**kwargs):
        observed.append(kwargs.copy())
        return fake_duel(root,destination,white,black,**kwargs)
    with arena_server(rodent_stub,runner=capture) as (server,base):
        config={**GOOD,"preset":"bullet","depth":16}
        with post(base,"/api/arena/launch",config,server.arena_token) as response:
            assert response.status == 202
        finished=wait_for(server.arena_manager,"COMPLETED")
        assert finished["config"]["mode"]=="depth"
        assert finished["config"]["depth"]==16
        assert finished["config"]["preset"]=="bullet"
    assert len(observed)==1
    assert observed[0]["depth"]==16
    assert observed[0]["clock"]==(60,0)
    assert observed[0]["movetime"] is None



def test_arena_v2_event_reconnect_is_read_only_and_backward_compatible(
        rodent_stub, monkeypatch):
    from chesssim import live_events

    monkeypatch.setattr(live_events, "EVENT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(live_events, "EVENT_STREAM_SECONDS", 0.1)
    with arena_server(rodent_stub) as (server, base):
        def receive_v2(headers=None):
            with urlopen(Request(base + "/api/events/v2",
                                 headers=headers or {}), timeout=4) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                for _ in range(30):
                    line = response.readline().decode("utf-8")
                    if line.startswith("data: "):
                        return json.loads(line.removeprefix("data: "))
            raise AssertionError("SSE v2 event missing")

        first = receive_v2()
        assert first["schema"] == "chesssim-game-events-v2"
        assert first["resync_required"] is True
        assert first["duel"]["status"] is None
        assert first["games"] == []
        with pytest.raises(HTTPError) as invalid:
            urlopen(base + "/api/events/v2?match=r01-m01", timeout=3)
        assert invalid.value.code == 400
        with pytest.raises(HTTPError) as invalid:
            urlopen(Request(base + "/api/events/v2",
                            headers={"Origin": "https://untrusted.example"}),
                    timeout=3)
        assert invalid.value.code == 403
        with post(base, "/api/arena/launch", GOOD, server.arena_token) as launched:
            assert launched.status == 202
        wait_for(server.arena_manager, "COMPLETED")
        second = receive_v2({"Last-Event-ID": first["event_id"]})
        assert second["schema"] == "chesssim-game-events-v2"
        assert second["sequence"] > first["sequence"]
        assert second["resync_required"] is False
        assert second["duel"]["status"] == "COMPLETED"
        assert second["games"][0]["ply"] == len(FOOLS_MATE)
        assert api(base, "/api/state")["duel"]["status"] == "COMPLETED"
