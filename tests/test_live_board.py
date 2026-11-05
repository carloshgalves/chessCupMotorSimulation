"""Local live viewer: changing checkpoints, valid FEN, isolation and read-only HTTP."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import live_board, tournament
from chesssim.cli import main


OPENING = ("e2e4", "e7e5", "g1f3", "b8c6")


def write_game(folder: Path, number: int, moves=OPENING, *, final=False,
               white="Bobby Fischer", black="Mikhail Tal") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    game = chess.pgn.Game()
    game.headers["White"] = white
    game.headers["Black"] = black
    game.headers["TimeControl"] = "600+2"
    game.headers["Result"] = "*"  # no result fabricated in a live checkpoint
    board = game.board()
    node = game
    for i, uci in enumerate(moves, 1):
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        node = node.add_variation(move)
        node.set_clock(600 - i * 2)
        board.push(move)
    filename = f"jogo_{number}.pgn" if final else f"jogo_{number}.partial.pgn"
    target = folder / filename
    target.write_text(str(game) + "\n", encoding="utf-8")
    return target


def write_duel(folder: Path, status="RUNNING", games=None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(
        json.dumps({"status": status, "games": games or []}), encoding="utf-8")


@contextmanager
def server_at(folder: Path):
    server = live_board.create_server(folder, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def load_api(base: str):
    with urlopen(base + "/api/state", timeout=3) as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        return json.load(response)


def test_partial_game_is_available_before_duel_finishes(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    write_game(folder, 1, moves=OPENING[:2])
    report = live_board.snapshot(folder)
    assert report["mode"] == "duel"
    game = report["duel"]["games"][0]
    assert game["live"] is True
    assert game["result"] == "*"
    assert [state["san"] for state in game["states"]] == ["Início", "e4", "e5"]
    assert game["states"][-1]["last"] == ["e7", "e5"]
    assert game["states"][-1]["fen"].startswith("rnbqkbnr/pppp1ppp")
    assert game["states"][-1]["clocks"] == [598.0, 596.0]
    assert game["states"][-1]["turn"] == "white"


def test_http_repoll_delivers_new_moves_without_final_pgn(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    write_game(folder, 1, moves=OPENING[:1])
    with server_at(folder) as base:
        first = load_api(base)
        assert len(first["duel"]["games"][0]["states"]) == 2
        write_game(folder, 1, moves=OPENING)
        second = load_api(base)
        assert len(second["duel"]["games"][0]["states"]) == 5
        assert second["duel"]["games"][0]["live"] is True
        assert second["duel"]["games"][0]["states"][-1]["san"] == "Nc6"


def test_latest_finished_game_is_preferred_to_stale_partial(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder, status="COMPLETED",
               games=[{"status": "COMPLETED"}, {"status": "COMPLETED"}])
    write_game(folder, 1, moves=OPENING[:1], final=False)
    write_game(folder, 1, moves=OPENING, final=True)
    write_game(folder, 2, moves=OPENING, final=True,
               white="Mikhail Tal", black="Bobby Fischer")
    report = live_board.snapshot(folder)
    one, two = report["duel"]["games"]
    assert one["status"] == "COMPLETED"
    assert not one["live"]
    assert len(one["states"]) == 5
    assert two["white"] == "Mikhail Tal"
    assert two["black"] == "Bobby Fischer"
    assert two["number"] == 2


def test_unfinished_duel_without_checkpoint_is_displayable(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    report = live_board.snapshot(folder)
    assert report["duel"]["games"] == []


def test_tournament_selects_running_attempt_then_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_preflight", lambda root, slugs, preset: {
        "engine_sha256": "test", "profile_sha256": {s: "test" for s in slugs},
        "book_sha256": {}, "books_by_player": {s: [] for s in slugs}})
    folder = tmp_path / "copa"
    state = tournament.create_tournament(
        tmp_path, folder, participants=["fischer", "tal"], seed=2026, preset="rapid")
    match = state["rounds"][0]["matches"][0]
    a, b = match["players"]
    root = folder / "matches" / match["id"] / "duels" / f"{a}-vs-{b}"
    first = root / "first"
    write_duel(first, status="COMPLETED")
    write_game(first, 1, final=True)
    second = root / "second"
    write_duel(second, status="RUNNING")
    write_game(second, 1, moves=OPENING[:1])
    data = live_board.snapshot(folder)
    assert data["mode"] == "tournament"
    assert data["tournament"]["participants"] == 2
    assert data["duel"]["folder"] == "second"
    assert data["duel"]["match_id"] == "r01-m01"
    assert data["duel"]["games"][0]["live"] is True


def test_no_external_files_served_via_symlinks(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    outside = tmp_path / "outside.pgn"
    outside.write_text(str(chess.pgn.Game()), encoding="utf-8")
    (folder / "jogo_1.partial.pgn").symlink_to(outside)
    with pytest.raises(ValueError, match="fora do diretório"):
        live_board.snapshot(folder)


def test_invalid_pgn_rejected_instead_of_showing_invented_moves(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    path = folder / "jogo_1.partial.pgn"
    path.write_text('[Event "?"]\n\n1. e4 e5 2. Qh5 Qxh5 *\n', encoding="utf-8")
    with pytest.raises(ValueError, match="PGN vazio, inválido"):
        live_board.snapshot(folder)


def test_oversized_partial_checkpoint_refused(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    (folder / "jogo_1.partial.pgn").write_bytes(b"x" * (live_board.MAX_PGN_BYTES + 1))
    with pytest.raises(ValueError, match="limite"):
        live_board.snapshot(folder)


def test_host_filter_routes_and_no_mutations(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    write_game(folder, 1)
    with server_at(folder) as base:
        for resource in ("/", "/app.js", "/app.css"):
            with urlopen(base + resource, timeout=3) as resp:
                assert resp.status == 200
                assert len(resp.read()) > 300
                assert resp.headers["X-Frame-Options"] == "DENY"
        with pytest.raises(HTTPError) as forbidden:
            urlopen(Request(base + "/api/state", headers={"Host": "malicious.test"}), timeout=3)
        assert forbidden.value.code == 403
        with pytest.raises(HTTPError) as post:
            urlopen(Request(base + "/api/state", data=b"delete=true", method="POST"), timeout=3)
        assert post.value.code == 405
        with pytest.raises(HTTPError) as missing:
            urlopen(base + "/secret-file", timeout=3)
        assert missing.value.code == 404
        assert load_api(base)["duel"]["status"] == "RUNNING"


def test_static_assets_use_safe_text_updates_and_no_external_content(tmp_path):
    assets = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"
    html = (assets / "monitor.html").read_text(encoding="utf-8")
    script = (assets / "monitor.js").read_text(encoding="utf-8")
    assert 'src="/app.js"' in html
    assert 'href="/app.css"' in html
    assert "https://" not in html and "http://" not in html
    assert "innerHTML" not in script and "eval(" not in script
    assert "textContent" in script and 'fetch("/api/state"' in script
    assert 'id="pause"' in html and 'id="live"' in html
    assert 'id="white-clock"' in html and 'id="black-clock"' in html


def test_cli_watch_appears_in_help(capsys):
    # argparse's built-in help exits cleanly instead of returning to main.
    with pytest.raises(SystemExit) as exited:
        main(["watch", "--help"])
    assert exited.value.code == 0
    assert "--run" in capsys.readouterr().out


def test_legacy_pgn_without_clock_comments_shows_unknown_time(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    game = chess.pgn.Game()
    game.headers["TimeControl"] = "600+2"
    node = game
    board = game.board()
    move = chess.Move.from_uci("e2e4")
    node.add_variation(move)
    (folder / "jogo_1.partial.pgn").write_text(str(game), encoding="utf-8")
    data = live_board.snapshot(folder)["duel"]["games"][0]
    assert data["states"][0]["clocks"] == [600.0, 600.0]
    assert data["states"][1]["clocks"] == [None, 600.0]


def test_fresh_completed_attempt_beats_stale_running_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_preflight", lambda root, slugs, preset: {
        "engine_sha256": "test", "profile_sha256": {s: "test" for s in slugs},
        "book_sha256": {}, "books_by_player": {s: [] for s in slugs}})
    run = tmp_path / "copa"
    state = tournament.create_tournament(
        tmp_path, run, participants=["fischer", "tal"], seed=2026, preset="rapid")
    match = state["rounds"][0]["matches"][0]
    a, b = match["players"]
    base = run / "matches" / match["id"] / "duels" / f"{a}-vs-{b}"
    old = base / "crashed"
    write_duel(old, status="RUNNING")
    old_pgn = write_game(old, 1, moves=OPENING[:1])
    new = base / "finished"
    write_duel(new, status="COMPLETED")
    new_pgn = write_game(new, 1, moves=OPENING, final=True)
    for path in (old / "summary.json", old_pgn):
        os.utime(path, (1000, 1000))
    for path in (new / "summary.json", new_pgn):
        os.utime(path, (2000, 2000))
    report = live_board.snapshot(run)
    assert report["duel"]["folder"] == "finished"
    assert len(report["duel"]["games"][0]["states"]) == 5


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed; browser JS syntax optional")
def test_live_board_javascript_syntax():
    asset = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data" / "monitor.js"
    result = subprocess.run(["node", "--check", str(asset)], capture_output=True,
                            text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
