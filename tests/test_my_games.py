"""#84: separate user-only Human-vs-UCI records; no motor archive or Elo."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import json
import os
import shutil
import subprocess
import threading

import chess
import chess.pgn
import pytest

from chesssim import arena, competition_rating, history_archive, history_stats
from chesssim import my_games
from chesssim.catalog import find_player
from chesssim.human_game import SCHEMA


def make_game(workspace: Path, number: int = 1, *,
              finished: bool = True, opponent: str = "tal",
              color: str = "white") -> Path:
    directory = (workspace / "human-games" /
                 f"human-20261109T200000Z-{number:012x}")
    directory.mkdir(parents=True)
    opponent_name = find_player(opponent).name
    game = chess.pgn.Game()
    game.headers.update({
        "White": "Você" if color == "white" else opponent_name,
        "Black": opponent_name if color == "white" else "Você",
        "Event": "ChessSimulation - Jogar versus UCI",
        "TimeControl": "-", "EngineSHA256": "a" * 64,
        "EngineProfileSHA256": "b" * 64,
        "EngineSlug": opponent, "SourceKind": "human_vs_engine",
        "Result": "0-1" if finished else "*",
    })
    board = chess.Board()
    node = game
    sequence = ["f2f3", "e7e5", "g2g4", "d8h4"] if finished else ["e2e4", "e7e5"]
    for uci in sequence:
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    assert board.is_checkmate() if finished else not board.is_game_over()
    term = "CHECKMATE" if finished else "OperatorStop"
    game.headers["Termination"] = term
    created = datetime(2026, 11, 9, 20, tzinfo=timezone.utc)
    state = {
        "schema": SCHEMA, "id": directory.name, "revision": len(sequence),
        "status": "COMPLETED" if finished else "INTERRUPTED",
        "result": "0-1" if finished else "*", "termination": term,
        "config": {"opponent": opponent, "color": color, "requested_color": color,
                   "depth": 2, "preset": "none", "clock": None,
                   "opening_mode": "none"},
        "hashes": {"engine": "a"*64, "profile": "b"*64},
        "clocks": None, "created_utc": created.isoformat(),
        "updated_utc": (created + timedelta(minutes=1)).isoformat(),
        "finished_utc": (created + timedelta(seconds=59)).isoformat(),
    }
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (directory / "game.pgn").write_text(str(game)+"\n", encoding="utf-8")
    return directory


def test_my_games_empty_is_not_a_motor_statistic(tmp_path):
    report = my_games.listing(tmp_path)
    assert report["games"] == []
    assert report["stats"]["games"] == 0
    assert report["rating_eligible"] is False
    assert history_archive.archive(tmp_path, "tal")["total"] == 0
    assert competition_rating.rating_report(tmp_path, modality="rapid")[
        "counts"]["scored_games"] == 0


def test_my_games_per_user_wdl_pagination_replay_and_evidence(tmp_path):
    game = make_game(tmp_path)
    unfinished = make_game(tmp_path, 2, finished=False, opponent="fischer")
    before = {p.relative_to(tmp_path): p.read_bytes()
              for p in (game / "state.json", game / "game.pgn",
                        unfinished / "state.json", unfinished / "game.pgn")}
    report = my_games.listing(tmp_path, "page=1&page_size=1")
    assert report["total"] == report["stats"]["games"] == 2
    assert report["pages"] == 2
    assert (report["stats"]["completed"], report["stats"]["wins"],
            report["stats"]["draws"], report["stats"]["losses"],
            report["stats"]["unfinished"]) == (1, 0, 0, 1, 1)
    assert report["coverage"] == {"scanned": 2, "rejected": 0, "partial": False}
    assert my_games.listing(tmp_path, "outcome=loss")["total"] == 1
    assert my_games.listing(tmp_path, "status=INTERRUPTED")["total"] == 1
    assert my_games.listing(tmp_path, "color=black")["total"] == 0
    assert my_games.listing(tmp_path, "opponent=tal")["total"] == 1
    assert my_games.listing(tmp_path, "page=2&page_size=1")["total"] == 2
    record = my_games.listing(tmp_path, "opponent=tal")["games"][0]
    assert record["rating_eligible"] is False
    assert record["origin"] == "human_vs_engine"
    assert record["started_at_utc"] is None
    assert record["duration_seconds"] is None
    assert record["provenance"] == "locally_consistent"
    assert len(record["pgn_sha256"]) == 64
    replay = my_games.replay(tmp_path, game.name)
    assert len(replay["states"]) == 5
    assert replay["states"][-1]["san"] == "Qh4#"
    assert replay["game"]["result"] == "0-1"
    assert my_games.pgn(tmp_path, game.name).startswith(b'[Event ')
    assert my_games.replay(tmp_path, unfinished.name)["game"]["outcome"] == "incomplete"
    after = {p.relative_to(tmp_path): p.read_bytes()
             for p in (game / "state.json", game / "game.pgn",
                       unfinished / "state.json", unfinished / "game.pgn")}
    assert before == after, "GET must not recover or mutate sessions"


def test_my_games_never_affects_64_engine_profiles_or_elo(tmp_path):
    make_game(tmp_path)
    make_game(tmp_path, 2, opponent="fischer", finished=False)
    assert history_archive.archive(tmp_path, "tal")["total"] == 0
    assert history_archive.archive(tmp_path, "fischer")["total"] == 0
    assert history_stats.player_stats(tmp_path, "tal")["totals"]["completed"] == 0
    assert history_stats.player_stats(tmp_path, "fischer")["totals"]["completed"] == 0
    assert competition_rating.rating_report(tmp_path, modality="rapid")[
        "counts"]["scored_games"] == 0


@pytest.mark.parametrize("filter_", [
    "status=admin", "outcome=victory", "color=green",
    "opponent=../../etc", "page=0", "page=10001", "page_size=51",
    "page_size=abc", "status=all&status=COMPLETED", "fictitious=value",
    "page=1%26status=COMPLETED",
])
def test_my_games_rejects_invalid_filters(tmp_path, filter_):
    with pytest.raises(my_games.MyGamesInput):
        my_games.listing(tmp_path, filter_)


def test_my_games_rejects_invalid_sources_and_never_invents_wins(tmp_path):
    a = make_game(tmp_path)
    b = make_game(tmp_path, 2)
    c = make_game(tmp_path, 3)
    d = make_game(tmp_path, 4)
    # A committed result unsupported by the chessboard cannot count.
    (a / "game.pgn").write_text((a / "game.pgn").read_text().replace(
        "Qh4#", "Qf6"), encoding="utf-8")
    state = json.loads((b / "state.json").read_text())
    state["revision"] = 3
    (b / "state.json").write_text(json.dumps(state), encoding="utf-8")
    # Missing evidence must fail closed.
    (c / "game.pgn").unlink()
    (c / "game.pgn").symlink_to(d / "game.pgn")
    # Duplicate keys cannot override the verdict silently.
    raw = (d / "state.json").read_text().replace(
        '"result": "0-1"', '"result": "0-1", "result": "1-0"')
    (d / "state.json").write_text(raw, encoding="utf-8")
    report = my_games.listing(tmp_path)
    assert report["total"] == 0
    assert report["coverage"]["rejected"] == 4
    assert report["stats"]["wins"] == report["stats"]["losses"] == 0
    with pytest.raises(my_games.MyGamesInput):
        my_games.replay(tmp_path, a.name)


def test_my_games_rejects_redirected_directory_and_invalid_id(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "human-games"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        my_games.listing(tmp_path)
    parent.unlink()
    make_game(tmp_path)
    with pytest.raises(my_games.MyGamesInput):
        my_games.replay(tmp_path, "../../etc/passwd")


def test_my_games_limit_is_enforced_before_opening_all_sessions(tmp_path, monkeypatch):
    make_game(tmp_path)
    make_game(tmp_path, 2)
    monkeypatch.setattr(my_games, "MAX_SESSIONS", 1)
    with pytest.raises(my_games.MyGamesInput, match="Limite"):
        my_games.listing(tmp_path)


def test_my_games_http_loopback_and_static_ui(tmp_path):
    make_game(tmp_path)
    root = tmp_path / "engine"
    root.mkdir()
    server = arena.create_server(root, tmp_path, port=0)
    worker = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/my-games", timeout=5) as response:
            report = json.load(response)
        assert report["stats"]["losses"] == 1
        assert report["games"][0]["opponent"] == "tal"
        game_id = report["games"][0]["id"]
        with urlopen(base + "/api/my-games/" + game_id + "/replay",
                     timeout=5) as response:
            assert json.load(response)["states"][-1]["san"] == "Qh4#"
        with urlopen(base + "/api/my-games/" + game_id + "/pgn",
                     timeout=5) as response:
            assert response.headers["Content-Type"] == "application/x-chess-pgn"
            assert b"Qh4" in response.read()
        for path in ("/my-games.js", "/my-games.css"):
            with urlopen(base + path, timeout=5) as response:
                assert response.status == 200 and len(response.read()) > 100
        for path in ("/api/my-games?page=0", "/api/my-games?status=wrong",
                     "/api/my-games/" + game_id + "/replay?file=/etc/passwd"):
            with pytest.raises(HTTPError) as err:
                urlopen(base + path, timeout=5)
            assert err.value.code in (400, 404)
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        worker.join(timeout=2)


def test_my_games_dom_and_browser_scripts_are_packaged():
    from importlib.resources import files

    root = files("chesssim.data")
    html = root.joinpath("arena.html").read_text(encoding="utf-8")
    assert 'id="tab-my-games"' in html
    assert 'id="my-games-view"' in html
    assert "/my-games.js" in html and "/my-games.css" in html
    source = root.joinpath("my_games.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in source
    assert "/api/my-games" in source
    assert 'window.ChessSimHistoryNavigation?.leave()' in source
    assert "rating_report" not in source and "/api/history/players/" not in source
    assert len(root.joinpath("my_games.css").read_text(encoding="utf-8")) > 100
    if shutil.which("node"):
        subprocess.run(["node", "--check",
                        str(Path(__file__).parents[1] /
                            "src/chesssim/data/my_games.js")],
                       check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_my_games_browser_interactions():
    root = Path(__file__).parents[1]
    run = subprocess.run([
        "node", str(root / "tests/assets/my_games_harness.cjs"),
        str(root / "src/chesssim/data/my_games.js"),
    ], capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "My Games navigation, filters, replay, isolation: PASS" in run.stdout


def test_my_games_pagination_of_55_real_personal_checkpoints(tmp_path):
    for number in range(1, 56):
        make_game(tmp_path, number)
    first = my_games.listing(tmp_path, "page=1&page_size=50")
    second = my_games.listing(tmp_path, "page=2&page_size=50")
    assert first["total"] == second["total"] == 55
    assert first["pages"] == second["pages"] == 2
    assert len(first["games"]) == 50 and len(second["games"]) == 5
    assert len({g["id"] for g in first["games"] + second["games"]}) == 55
    assert first["stats"]["losses"] == 55


@pytest.mark.skipif(os.name != "posix", reason="FIFO test needs POSIX open flags")
def test_my_games_rejects_fifo_without_blocking_read(tmp_path):
    directory = make_game(tmp_path)
    (directory / "game.pgn").unlink()
    os.mkfifo(directory / "game.pgn")
    report = my_games.listing(tmp_path)
    assert report["total"] == 0 and report["coverage"]["rejected"] == 1


def test_my_games_rejects_false_timeout_winner_and_false_resignation(tmp_path):
    directory = make_game(tmp_path, finished=False)
    path = directory / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["status"] = "COMPLETED"
    state["result"] = "0-1"
    state["termination"] = "TimeForfeit"
    state["config"]["clock"] = [180, 2]
    state["clocks"] = [180.0, 0.0]
    path.write_text(json.dumps(state), encoding="utf-8")
    game_path = directory / "game.pgn"
    with game_path.open(encoding="utf-8") as stream:
        game = chess.pgn.read_game(stream)
    game.headers.update({"TimeControl": "180+2", "Result": "0-1",
                         "Termination": "TimeForfeit"})
    game_path.write_text(str(game)+"\n", encoding="utf-8")
    with pytest.raises(my_games.MyGamesInput, match="Queda de tempo"):
        my_games.replay(tmp_path, directory.name)
    state["clocks"] = [0.0, 180.0]
    path.write_text(json.dumps(state), encoding="utf-8")
    assert my_games.replay(tmp_path, directory.name)["game"]["outcome"] == "loss"

    another = make_game(tmp_path, 2)
    path = another / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["termination"] = "HumanResignation"
    path.write_text(json.dumps(state), encoding="utf-8")
    game_path = another / "game.pgn"
    with game_path.open(encoding="utf-8") as stream:
        game = chess.pgn.read_game(stream)
    game.headers["Termination"] = "HumanResignation"
    game_path.write_text(str(game)+"\n", encoding="utf-8")
    assert my_games.listing(tmp_path)["coverage"]["rejected"] == 1
    with pytest.raises(my_games.MyGamesInput, match="causa"):
        my_games.replay(tmp_path, another.name)


def test_my_games_index_reuses_pgn_audit_and_invalidates_on_change(
        tmp_path, monkeypatch):
    session = make_game(tmp_path)
    original = my_games._document
    calls = []
    def recorded(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(my_games, "_document", recorded)
    assert my_games.listing(tmp_path, "page=1&page_size=1")["total"] == 1
    assert len(calls) == 1
    assert my_games.listing(tmp_path, "page=2&page_size=1")["total"] == 1
    assert len(calls) == 1, "Page change must not re-parse PGNs"
    state = session / "state.json"
    state.write_text(state.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert my_games.listing(tmp_path)["total"] == 1
    assert len(calls) == 2, "State changes must invalidate the index"
    pgn = session / "game.pgn"
    pgn.write_text(pgn.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert my_games.listing(tmp_path)["total"] == 1
    assert len(calls) == 3, "PGN changes must invalidate the index"


@pytest.mark.parametrize(("field", "invalid"), [
    ("status", ["COMPLETED"]),
    ("opponent", ["tal"]),
    ("color", {"unexpected": "white"}),
])
def test_my_games_unhashable_metadata_is_rejected_as_input(
        tmp_path, field, invalid):
    directory = make_game(tmp_path)
    path = directory / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    if field == "status":
        state["status"] = invalid
    else:
        state["config"][field] = invalid
    path.write_text(json.dumps(state), encoding="utf-8")
    report = my_games.listing(tmp_path)
    assert report["coverage"]["rejected"] == 1
    with pytest.raises(my_games.MyGamesInput):
        my_games.replay(tmp_path, directory.name)


def test_my_games_query_parser_rejects_malformed_separator(tmp_path):
    with pytest.raises(my_games.MyGamesInput, match="malformada"):
        my_games.listing(tmp_path, "page=1&")


def test_my_games_rejects_alternate_initial_fen_and_stop_reason_mismatch(tmp_path):
    directory = make_game(tmp_path, finished=False)
    state_file = directory / "state.json"
    pgn_file = directory / "game.pgn"
    with pgn_file.open(encoding="utf-8") as source:
        original = chess.pgn.read_game(source)

    # A syntactically valid PGN can represent a position the HumanGame
    # application would never create. The personal reader must reject it.
    start = chess.Board(
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
    forged = chess.pgn.Game.from_board(start)
    forged.headers.update(dict(original.headers))
    forged.headers["SetUp"] = "1"
    forged.headers["FEN"] = start.fen()
    pgn_file.write_text(str(forged) + "\n", encoding="utf-8")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["revision"] = 1
    state_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(my_games.MyGamesInput):
        my_games.replay(tmp_path, directory.name)

    # A stopped session needs the termination reason in the manifest/PGN
    # to agree. Preserve the special read-only ServerRestart recovery case.
    data["revision"] = 2
    state_file.write_text(json.dumps(data), encoding="utf-8")
    original.headers["Termination"] = "ServerShutdown"
    pgn_file.write_text(str(original) + "\n", encoding="utf-8")
    with pytest.raises(my_games.MyGamesInput, match="Causa"):
        my_games.replay(tmp_path, directory.name)

    data["termination"] = "ServerRestart"
    data["finished_utc"] = None
    state_file.write_text(json.dumps(data), encoding="utf-8")
    original.headers.pop("Termination", None)
    pgn_file.write_text(str(original) + "\n", encoding="utf-8")
    replay = my_games.replay(tmp_path, directory.name)
    assert replay["game"]["status"] == "INTERRUPTED"
    assert replay["game"]["outcome"] == "incomplete"
