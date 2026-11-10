"""Operational contract for read-only SSE checkpoint notifications (#17)."""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPResponse
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import time

import chess
import chess.pgn
import pytest

from chesssim import live_board, live_events


def duel(folder: Path, moves=("e2e4",), *, status="RUNNING") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    summary = folder / "summary.json"
    staging_summary = folder / ".summary.tmp"
    staging_summary.write_text(
        json.dumps({"status": status, "games": []}), encoding="utf-8")
    staging_summary.replace(summary)
    game = chess.pgn.Game()
    game.headers["White"] = "Bobby Fischer"
    game.headers["Black"] = "Mikhail Tal"
    game.headers["Result"] = "*"
    board = game.board()
    node = game
    for uci in moves:
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    staging_pgn = folder / ".game.tmp"
    staging_pgn.write_text(str(game) + "\n", encoding="utf-8")
    staging_pgn.replace(folder / "jogo_1.partial.pgn")


@contextmanager
def monitor(folder: Path):
    server = live_board.create_server(folder, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def read_state_event(response: HTTPResponse) -> dict:
    data = {}
    # The first SSE frame can be 'retry', and later ones comments.
    for _ in range(100):
        line = response.readline()
        if not line:
            raise AssertionError("SSE connection ended before state event")
        decoded = line.decode("utf-8").strip()
        if not decoded:
            if data.get("event") == "state":
                assert "id" in data and "data" in data
                payload = json.loads(data["data"])
                assert payload["revision"] == data["id"]
                return payload
            data = {}
            continue
        if decoded.startswith(":"):
            continue
        field, _, value = decoded.partition(":")
        data[field] = value.strip()
    raise AssertionError("SSE event did not arrive")


def test_checkpoint_ignores_server_time_but_tracks_moves_clocks_and_terminal(tmp_path):
    duel(tmp_path, ("e2e4",))
    original = live_board.snapshot(tmp_path)
    eid, body = live_events.checkpoint(original)
    clone = {**original, "server_time": "a different wall time"}
    assert live_events.checkpoint(clone)[0] == eid
    # Even a bracket-only update (no new UCI move) must invalidate viewers.
    changed_bracket = {**original, "tournament": {
        "status": "RUNNING", "bracket": [{"match": "r01-m01", "winner": "fischer"}]}}
    assert live_events.checkpoint(changed_bracket)[0] != eid
    assert len(eid) == 64 and body["games"][0]["ply"] == 1
    assert body["games"][0]["uci"] == "e2e4"
    assert live_events._changes(None, body) == ["snapshot"]

    duel(tmp_path, ("e2e4", "e7e5"))
    next_id, next_body = live_events.checkpoint(live_board.snapshot(tmp_path))
    assert next_id != eid
    assert "move" in live_events._changes(body, next_body)
    modified = json.loads(json.dumps(next_body))
    modified["games"][0]["clocks"] = [52.0, 48.0]
    assert "clock" in live_events._changes(next_body, modified)
    modified["games"][0]["status"] = "COMPLETED"
    assert "terminal" in live_events._changes(next_body, modified)


def test_sse_observes_real_checkpoint_while_other_gets_work(tmp_path, monkeypatch):
    duel(tmp_path)
    monkeypatch.setattr(live_events, "EVENT_POLL_SECONDS", .05)
    monkeypatch.setattr(live_events, "EVENT_STREAM_SECONDS", 2)
    with monitor(tmp_path) as (_, base):
        with urlopen(base + "/api/events", timeout=4) as stream:
            assert stream.status == 200
            assert stream.headers["Content-Type"].startswith("text/event-stream")
            assert stream.headers["Cache-Control"] == "no-store"
            first = read_state_event(stream)
            assert first["games"][0]["ply"] == 1
            assert first["changes"] == ["snapshot"]
            # SSE never holds a lock on the authoritative snapshot endpoint.
            with urlopen(base + "/api/state", timeout=3) as regular:
                assert json.load(regular)["duel"]["games"][0]["states"][-1]["uci"] == "e2e4"
            duel(tmp_path, ("e2e4", "e7e5"))
            second = read_state_event(stream)
            assert second["games"][0]["ply"] == 2
            assert second["games"][0]["uci"] == "e7e5"
            assert "move" in second["changes"]
            assert first["revision"] != second["revision"]


def test_last_event_id_skips_unchanged_checkpoint_on_reconnect(tmp_path, monkeypatch):
    duel(tmp_path)
    monkeypatch.setattr(live_events, "EVENT_POLL_SECONDS", .05)
    monkeypatch.setattr(live_events, "EVENT_STREAM_SECONDS", 2)
    with monitor(tmp_path) as (_, base):
        with urlopen(base + "/api/events", timeout=4) as first:
            ident = read_state_event(first)["revision"]
        # Make the state change after the new connection has settled.
        def later():
            time.sleep(.25)
            duel(tmp_path, ("e2e4", "e7e5"))
        worker = Thread(target=later)
        worker.start()
        with urlopen(Request(base + "/api/events", headers={
            "Last-Event-ID": ident}), timeout=4) as stream:
            update = read_state_event(stream)
            assert update["games"][0]["ply"] == 2
            assert update["revision"] != ident
        worker.join(timeout=2)


def test_event_route_rejects_invalid_selection_cross_origin_and_post(tmp_path):
    duel(tmp_path)
    with monitor(tmp_path) as (_, base):
        for url in ("/api/events?attempt=1", "/api/events?match=../secrets",
                    "/api/events?debug=1"):
            with pytest.raises(HTTPError) as failed:
                urlopen(base + url, timeout=3)
            assert failed.value.code == 400
        with pytest.raises(HTTPError) as forbidden:
            urlopen(Request(base + "/api/events",
                            headers={"Host": "attacker.example"}), timeout=3)
        assert forbidden.value.code == 403
        with pytest.raises(HTTPError) as forbidden:
            urlopen(Request(base + "/api/events",
                            headers={"Origin": "https://attacker.example"}), timeout=3)
        assert forbidden.value.code == 403
        with pytest.raises(HTTPError) as forbidden:
            urlopen(Request(base + "/api/events",
                            headers={"Sec-Fetch-Site": "cross-site"}), timeout=3)
        assert forbidden.value.code == 403
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(base + "/api/events", data=b"{}",
                            method="POST"), timeout=3)
        assert denied.value.code == 405


def test_rejected_cross_origin_never_reads_the_pgn(tmp_path, monkeypatch):
    duel(tmp_path)
    with monitor(tmp_path) as (_, base):
        def forbidden_read(*args, **kwargs):
            raise AssertionError("Untrusted origin reached expensive snapshot reader")
        monkeypatch.setattr(live_board, "snapshot", forbidden_read)
        with pytest.raises(HTTPError) as rejected:
            urlopen(Request(base + "/api/events", headers={
                "Origin": "https://untrusted.example"}), timeout=3)
        assert rejected.value.code == 403


def test_event_slot_quota_fails_fast_without_leaking_state(tmp_path):
    duel(tmp_path)
    with monitor(tmp_path) as (server, base):
        for _ in range(live_events.MAX_EVENT_CLIENTS):
            assert server.event_stream_slots.acquire(blocking=False)
        try:
            with pytest.raises(HTTPError) as limited:
                urlopen(base + "/api/events", timeout=3)
            assert limited.value.code == 429
        finally:
            for _ in range(live_events.MAX_EVENT_CLIENTS):
                server.event_stream_slots.release()


def test_event_notification_has_no_file_or_network_side_effects(tmp_path, monkeypatch):
    duel(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(live_events, "EVENT_STREAM_SECONDS", .1)
    monkeypatch.setattr(live_events, "EVENT_POLL_SECONDS", .05)
    with monitor(tmp_path) as (_, base):
        with urlopen(base + "/api/events", timeout=3) as stream:
            _ = read_state_event(stream)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
