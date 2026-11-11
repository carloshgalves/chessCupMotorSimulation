"""Adversarial contract tests for bounded SSE v2 checkpoint replay (#17)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import event_journal, live_board, live_events


def report(moves=1, *, selection="r01-m01", status="RUNNING"):
    states = [{"uci": None, "turn": "white", "clocks": [60, 60]}]
    states += [{"uci": f"p{i}", "turn": "black", "clocks": [60 - i, 60]}
               for i in range(1, moves + 1)]
    return {
        "schema": "chesssim-live-board-v1",
        "mode": "tournament",
        "selection": {"match_id": selection, "attempt": None},
        "duel": {
            "folder": "allowed-local-run", "match_id": selection,
            "status": status,
            "games": [{
                "id": "game-1", "states": states, "live": status == "RUNNING",
                "status": status, "result": "*" if status == "RUNNING" else "1-0",
                "termination": None if status == "RUNNING" else "CHECKMATE",
            }],
        },
        "tournament": {"status": status, "matches_completed": 0},
        "server_time": "unstable",
    }


def test_journal_monotonic_revision_replay_and_state_digest():
    journal = event_journal.EventJournal()
    first = journal.observe("selection-a", report(1))
    assert len(first) == 1
    assert first[0]["schema"] == "chesssim-game-events-v2"
    assert first[0]["changes"] == ["snapshot"]
    assert first[0]["resync_required"] is True
    assert len(first[0]["revision"]) == 64
    assert first[0]["event_id"].endswith(first[0]["revision"])
    assert not journal.observe("selection-a", report(1), first[0]["event_id"])

    second = journal.observe("selection-a", report(2), first[0]["event_id"])
    assert len(second) == 1
    assert second[0]["sequence"] > first[0]["sequence"]
    assert second[0]["resync_required"] is False
    assert "move" in second[0]["changes"]
    third = journal.observe("selection-a", report(3), second[0]["event_id"])
    assert len(third) == 1 and third[0]["sequence"] > second[0]["sequence"]
    backlog = journal.observe("selection-a", report(3), first[0]["event_id"])
    assert [x["event_id"] for x in backlog] == [
        second[0]["event_id"], third[0]["event_id"],
    ]
    assert all(x["resync_required"] is False for x in backlog)


def test_gapped_cursor_wrong_selection_and_restart_fail_to_snapshot():
    journal = event_journal.EventJournal()
    first = journal.observe("A", report(1))[0]
    other = journal.observe("B", report(1))[0]
    assert other["event_id"] != first["event_id"]
    wrong = journal.observe("B", report(1), first["event_id"])
    assert len(wrong) == 1 and wrong[0]["resync_required"] is True
    assert wrong[0]["changes"] == ["snapshot"]
    malformed = journal.observe("A", report(1), "forged-secret-\n\n")
    assert malformed[0]["resync_required"] is True
    assert malformed[0]["event_id"] == first["event_id"]
    restarted = event_journal.EventJournal()
    changed_epoch = restarted.observe("A", report(1), first["event_id"])
    assert changed_epoch[0]["resync_required"] is True
    assert changed_epoch[0]["epoch"] != first["epoch"]


def test_expired_cursor_is_detected_without_inventing_missed_moves():
    journal = event_journal.EventJournal()
    original = journal.observe("A", report(1))[0]["event_id"]
    for i in range(2, event_journal.MAX_EVENTS_PER_STREAM + 8):
        journal.observe("A", report(i))
    gap = journal.observe("A", report(event_journal.MAX_EVENTS_PER_STREAM + 7),
                          original)
    assert len(gap) == 1
    assert gap[0]["resync_required"] is True
    assert gap[0]["changes"] == ["snapshot"]
    assert gap[0]["replay_scope"] == "OBSERVED_CHECKPOINTS_ONLY"


def test_journal_is_bounded_by_stream_and_event_count_and_thread_safe():
    journal = event_journal.EventJournal()
    def sample(i):
        return journal.observe(f"session-{i % 40}", report(i % 20 + 1))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(sample, range(160)))
    assert all(results)
    assert len(journal._streams) <= event_journal.MAX_STREAMS
    assert all(len(history) <= event_journal.MAX_EVENTS_PER_STREAM
               for history in journal._streams.values())
    assert len({event["event_id"] for row in results for event in row}) <= 160


def make_duel(root: Path, moves: tuple[str, ...]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    summary = root / "summary.json"
    temp = root / ".summary-temp"
    temp.write_text(json.dumps({"status": "RUNNING", "games": []}),
                    encoding="utf-8")
    temp.replace(summary)
    game = chess.pgn.Game()
    game.headers["White"] = "Bobby Fischer"
    game.headers["Black"] = "Mikhail Tal"
    game.headers["Result"] = "*"
    board = game.board()
    node = game
    for move_uci in moves:
        move = chess.Move.from_uci(move_uci)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    temp_pgn = root / ".game-temp"
    temp_pgn.write_text(str(game) + "\n", encoding="utf-8")
    temp_pgn.replace(root / "jogo_1.partial.pgn")


@contextmanager
def watch(root: Path):
    server = live_board.create_server(root, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def read_event(stream):
    data = {}
    for _ in range(100):
        line = stream.readline()
        if not line:
            raise AssertionError("stream ended without event")
        text = line.decode("utf-8").strip()
        if not text:
            if data.get("event") == "state":
                item = json.loads(data["data"])
                assert item["event_id"] == data["id"]
                return item
            data = {}
            continue
        if text.startswith(":"):
            continue
        field, _, value = text.partition(":")
        data[field] = value.strip()
    raise AssertionError("missing v2 state event")


def test_watch_v2_reconnect_with_valid_last_event_id(tmp_path, monkeypatch):
    make_duel(tmp_path, ("e2e4",))
    monkeypatch.setattr(live_events, "EVENT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(live_events, "EVENT_STREAM_SECONDS", 0.12)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with watch(tmp_path) as (_, base):
        with urlopen(base + "/api/events/v2", timeout=3) as stream:
            first = read_event(stream)
            assert first["resync_required"] is True
            assert first["games"][0]["ply"] == 1
        make_duel(tmp_path, ("e2e4", "e7e5"))
        with urlopen(Request(base + "/api/events/v2", headers={
            "Last-Event-ID": first["event_id"],
        }), timeout=3) as stream:
            second = read_event(stream)
            assert second["resync_required"] is False
            assert second["games"][0]["ply"] == 2
            assert "move" in second["changes"]
            assert second["sequence"] > first["sequence"]
        with urlopen(base + "/api/state", timeout=3) as stream:
            assert json.load(stream)["duel"]["games"][0]["states"][-1]["uci"] == "e7e5"
    assert before["summary.json"] == (tmp_path / "summary.json").read_bytes()


def test_v2_cross_site_bad_selection_and_quota_rejected_early(tmp_path):
    make_duel(tmp_path, ("e2e4",))
    with watch(tmp_path) as (server, base):
        for url in ("/api/events/v2?attempt=1", "/api/events/v2?match=../../secrets",
                    "/api/events/v2?debug=1"):
            with pytest.raises(HTTPError) as err:
                urlopen(base + url, timeout=3)
            assert err.value.code == 400
        for headers in ({"Origin": "https://attacker.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with pytest.raises(HTTPError) as err:
                urlopen(Request(base + "/api/events/v2", headers=headers), timeout=3)
            assert err.value.code == 403
        for _ in range(live_events.MAX_EVENT_CLIENTS):
            assert server.event_stream_slots.acquire(blocking=False)
        try:
            with pytest.raises(HTTPError) as err:
                urlopen(base + "/api/events/v2", timeout=3)
            assert err.value.code == 429
        finally:
            for _ in range(live_events.MAX_EVENT_CLIENTS):
                server.event_stream_slots.release()


def test_old_v1_feed_remains_available(tmp_path, monkeypatch):
    make_duel(tmp_path, ("e2e4",))
    monkeypatch.setattr(live_events, "EVENT_STREAM_SECONDS", 0.1)
    monkeypatch.setattr(live_events, "EVENT_POLL_SECONDS", 0.03)
    with watch(tmp_path) as (_, base):
        with urlopen(base + "/api/events", timeout=3) as stream:
            old = read_event(stream)
            assert old["schema"] == "chesssim-game-events-v1"
            assert old["revision"] == old["event_id"] if "event_id" in old else True
