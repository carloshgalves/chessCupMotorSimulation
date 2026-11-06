"""Adversarial, deterministic contract tests for the read-only History games API."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import arena, history_games


MOVES = ("f2f3", "e7e5", "g2g4", "d8h4")


def pgn(*, event="Example", white="Bobby Fischer", black="Mikhail Tal",
        incomplete=False):
    game = chess.pgn.Game()
    game.headers["Event"] = event
    game.headers["White"] = white
    game.headers["Black"] = black
    game.headers["Date"] = "1972.01.01"
    node = game
    board = game.board()
    for uci in (MOVES[:2] if incomplete else MOVES):
        move = chess.Move.from_uci(uci)
        node = node.add_variation(move)
        board.push(move)
    game.headers["Result"] = "*" if incomplete else "0-1"
    return (str(game) + "\n").encode("utf-8")


def write_historical(workspace: Path, n: int, *, event_prefix="Event"):
    root = workspace / "history-games"
    (root / "games").mkdir(parents=True, exist_ok=True)
    records = []
    for i in range(n):
        ident = f"h-fixture-{i:03}"
        raw = pgn(event=f"{event_prefix} {i}")
        (root / "games" / f"{ident}.pgn").write_bytes(raw)
        records.append({
            "game_id": ident, "white_slug": "fischer", "black_slug": "tal",
            "pgn": f"games/{ident}.pgn", "sha256": sha256(raw).hexdigest(),
            "source_url": f"https://example.org/record/{i}",
            "source_label": "Example archive",
            "priority": i,
        })
    (root / "manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }), encoding="utf-8")
    return records


def write_simulation(workspace: Path, *, status="COMPLETED", incomplete=False):
    ident = "session-20261106T120000Z-123456789abc"
    root = workspace / ident / "duels" / "fischer-vs-tal" / "20261106T120000Z-test"
    root.mkdir(parents=True)
    raw = pgn(event="Local simulated", incomplete=incomplete)
    name = "jogo_1.partial.pgn" if incomplete else "jogo_1.pgn"
    (root / name).write_bytes(raw)
    (root / "summary.json").write_text(json.dumps({
        "status": status, "players": ["fischer", "tal"],
        "games": [{"status": status, "result": "*" if incomplete else "0-1"}],
    }), encoding="utf-8")
    return root


@pytest.mark.parametrize("n", [0, 1, 4, 5, 6])
def test_zero_one_four_five_and_limit_are_deterministic(tmp_path, n):
    if n:
        write_historical(tmp_path, n)
    before = history_games.list_games(tmp_path, "fischer")
    after = history_games.list_games(tmp_path, "fischer")
    assert before == after
    assert before["schema"] == history_games.SCHEMA
    assert before["count"] == min(n, 5)
    assert len(before["games"]) == min(n, 5)
    assert all(g["origin"] == "historical" for g in before["games"])
    assert [g["game_id"] for g in before["games"]] == [
        f"h-fixture-{i:03}" for i in range(min(n, 5))
    ]
    assert history_games.list_games(tmp_path, "tal")["games"] == before["games"]
    assert history_games.list_games(tmp_path, "kasparov")["count"] == 0


def test_historical_replay_is_legal_and_sourced(tmp_path):
    records = write_historical(tmp_path, 1)
    data = history_games.replay(tmp_path, records[0]["game_id"])
    assert data["schema"] == history_games.REPLAY_SCHEMA
    assert data["game"]["source"]["url"] == "https://example.org/record/0"
    assert data["game"]["result"] == "0-1"
    assert len(data["states"]) == 5
    assert chess.Board(data["states"][-1]["fen"]).is_checkmate()


@pytest.mark.parametrize("bad_id", [
    "../../etc/passwd", "%2e%2e", "/tmp/secret", "h-a/../b", "s-xyz",
    "h-fixture-777", "h-fixture-000?path=/etc/passwd", "h-fixture-000/",
])
def test_replay_rejects_unlisted_or_malformed_id(tmp_path, bad_id):
    write_historical(tmp_path, 1)
    with pytest.raises(history_games.UnknownGame):
        history_games.replay(tmp_path, bad_id)


def test_manifest_id_collision_rejected_even_with_distinct_pgn(tmp_path):
    records = write_historical(tmp_path, 2)
    records[1]["game_id"] = records[0]["game_id"]
    (tmp_path / "history-games/manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")


def test_duplicate_content_hash_does_not_create_fake_second_game(tmp_path):
    records = write_historical(tmp_path, 2)
    first = tmp_path / "history-games/games/h-fixture-000.pgn"
    second = tmp_path / "history-games/games/h-fixture-001.pgn"
    second.write_bytes(first.read_bytes())
    records[1]["sha256"] = records[0]["sha256"]
    (tmp_path / "history-games/manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }))
    assert history_games.list_games(tmp_path, "fischer")["count"] == 1


def test_tampered_hash_and_replay_change_are_rejected(tmp_path):
    write_historical(tmp_path, 1)
    file = tmp_path / "history-games/games/h-fixture-000.pgn"
    file.write_bytes(pgn(event="adulterated"))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.replay(tmp_path, "h-fixture-000")


def test_symlink_outside_root_and_symlink_directory_rejected(tmp_path):
    write_historical(tmp_path, 1)
    outside = tmp_path.parent / "outside-chesssim-test.pgn"
    outside.write_bytes(pgn(event="Outside"))
    file = tmp_path / "history-games/games/h-fixture-000.pgn"
    file.unlink()
    file.symlink_to(outside)
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")
    file.unlink()
    file.write_bytes(pgn(event="Event 0"))
    directory = tmp_path / "history-games/games"
    directory.rename(tmp_path / "real-games")
    directory.symlink_to(tmp_path / "real-games", target_is_directory=True)
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")
    outside.unlink()


def test_non_approved_path_does_not_read_outside_catalog(tmp_path):
    records = write_historical(tmp_path, 1)
    records[0]["pgn"] = "../secret.pgn"
    (tmp_path / "history-games/manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")


def test_excessive_pgn_size_rejected_before_parsing(tmp_path):
    write_historical(tmp_path, 1)
    file = tmp_path / "history-games/games/h-fixture-000.pgn"
    file.write_bytes(b" " * (history_games.MAX_PGN_BYTES + 1))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")


@pytest.mark.parametrize("corruption", [
    b"[Event \"X\"]\n[White \"Bobby Fischer\"]\n[Black \"Mikhail Tal\"]\n[Result \"0-1\"]\n\n1. e4 e5 2. Ke3 0-1\n",
    b"\xff\xfe",
    b"[Event \"X\"]\n[White \"Bobby Fischer\"]\n[Black \"Mikhail Tal\"]\n[Result \"0-1\"]\n\n1. f3 e5 2. g4 Qh4# 0-1\n\n[Event \"Two\"]\n[Result \"*\"]\n\n*\n",
])
def test_illegal_encoding_or_multiple_games_rejected(tmp_path, corruption):
    records = write_historical(tmp_path, 1)
    file = tmp_path / "history-games/games/h-fixture-000.pgn"
    file.write_bytes(corruption)
    records[0]["sha256"] = sha256(corruption).hexdigest()
    (tmp_path / "history-games/manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")


def test_player_identity_and_terminal_result_are_checked(tmp_path):
    records = write_historical(tmp_path, 1)
    file = tmp_path / "history-games/games/h-fixture-000.pgn"
    raw = pgn(event="Event 0", white="Garry Kasparov")
    file.write_bytes(raw)
    records[0]["sha256"] = sha256(raw).hexdigest()
    (tmp_path / "history-games/manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")


def test_local_complete_simulation_fallback_and_incomplete_flag(tmp_path):
    write_simulation(tmp_path, status="COMPLETED")
    games = history_games.list_games(tmp_path, "fischer")["games"]
    assert len(games) == 1
    assert games[0]["origin"] == "simulation"
    assert games[0]["result"] == "0-1"
    assert games[0]["source"]["session_id"].startswith("session-")
    assert len(history_games.replay(tmp_path, games[0]["game_id"])["states"]) == 5


def test_incomplete_game_never_claims_a_final_result(tmp_path):
    write_simulation(tmp_path, status="INCOMPLETE", incomplete=True)
    game = history_games.list_games(tmp_path, "fischer")["games"][0]
    assert game["status"] == "INCOMPLETE"
    assert game["result"] is None
    assert len(history_games.replay(tmp_path, game["game_id"])["states"]) == 3


def test_repeated_simulated_hash_is_not_counted_twice(tmp_path):
    one = write_simulation(tmp_path)
    other = tmp_path / "session-20261106T120001Z-123456789abc/duels/fischer-vs-tal/20261106T120000Z-test"
    other.mkdir(parents=True)
    for filename in ("jogo_1.pgn", "summary.json"):
        (other / filename).write_bytes((one / filename).read_bytes())
    assert history_games.list_games(tmp_path, "fischer")["count"] == 1


def test_local_summary_result_contradiction_fails_closed(tmp_path):
    root = write_simulation(tmp_path)
    summary = json.loads((root / "summary.json").read_text())
    summary["games"][0]["result"] = "1-0"
    (root / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(history_games.InvalidCatalogue):
        history_games.list_games(tmp_path, "fischer")


def test_http_get_only_and_replay_by_closed_id(tmp_path):
    workspace = tmp_path / "arena"
    write_historical(workspace, 1)
    server = arena.create_server(tmp_path, workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/history/players/fischer/games") as response:
            payload = json.load(response)
            assert payload["count"] == 1
            game_id = payload["games"][0]["game_id"]
        with urlopen(base + f"/api/history/games/{game_id}/replay") as response:
            payload = json.load(response)
            assert payload["game"]["game_id"] == game_id
            assert len(payload["states"]) == 5
        for route in ("/api/history/games/h-nope/replay",
                      "/api/history/players/not-a-player/games",
                      "/api/history/players/fischer/games?path=/etc/passwd",
                      "/api/history/games/%2e%2e/replay"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + route)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/api/history/games/h-fixture-000/replay",
                            data=b"{}", method="POST"))
        assert error.value.code in (403, 404)
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=2)


def test_incomplete_final_pgn_accepted_without_declaring_winner(tmp_path):
    folder = write_simulation(tmp_path, status="INCOMPLETE", incomplete=True)
    (folder / "jogo_1.partial.pgn").rename(folder / "jogo_1.pgn")
    record = history_games.list_games(tmp_path, "fischer")["games"][0]
    assert record["status"] == "INCOMPLETE"
    assert record["result"] is None
    assert len(history_games.replay(tmp_path, record["game_id"])["states"]) == 3


def test_semantically_same_game_in_differently_formatted_pgn_is_deduplicated(tmp_path):
    records = write_historical(tmp_path, 2)
    first = tmp_path / "history-games/games/h-fixture-000.pgn"
    second = tmp_path / "history-games/games/h-fixture-001.pgn"
    raw = first.read_bytes()
    # Same headers, position and moves, different byte content / SHA.
    changed = raw.replace(b"1. f3", b"\n1. f3", 1)
    assert changed != raw
    second.write_bytes(changed)
    records[1]["sha256"] = sha256(changed).hexdigest()
    (tmp_path / "history-games/manifest.json").write_text(json.dumps({
        "schema": history_games.MANIFEST_SCHEMA, "games": records,
    }))
    assert history_games.list_games(tmp_path, "fischer")["count"] == 1


def test_distinct_dated_games_with_same_moves_are_not_conflated(tmp_path):
    records = write_historical(tmp_path, 2)
    assert records[0]["sha256"] != records[1]["sha256"]
    assert history_games.list_games(tmp_path, "fischer")["count"] == 2
