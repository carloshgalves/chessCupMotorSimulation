"""Regression gates for a player-profile replay isolated from watch and Arena."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chesssim import arena, live_board


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/chesssim/data"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_history_replay_browser_contract_adversarial():
    scripts = [
        DATA / "chess_board.js", DATA / "history_replay.js",
        DATA / "history.js", DATA / "monitor.js",
    ]
    for script in scripts:
        syntax = subprocess.run(["node", "--check", str(script)],
                                capture_output=True, text=True, timeout=10, check=False)
        assert syntax.returncode == 0, f"{script}:\n{syntax.stdout}{syntax.stderr}"
    harness = ROOT / "tests/assets/history_replay_browser_harness.cjs"
    run = subprocess.run(
        ["node", str(harness), str(DATA / "chess_board.js"),
         str(DATA / "history_replay.js")],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "rapid switch, invalid FEN, keyboard, offline GET): PASS" in run.stdout


def test_history_replay_does_not_import_external_chess_engines_or_html():
    html = (DATA / "arena.html").read_text(encoding="utf-8")
    source = (DATA / "history_replay.js").read_text(encoding="utf-8")
    board = (DATA / "chess_board.js").read_text(encoding="utf-8")
    assert 'src="/chess-board.js"' in html
    assert 'src="/history_replay.js"' in html
    assert 'href="/chess-board.css"' in html
    assert 'id="history-replay-seek"' in html
    assert 'id="history-replay-board"' in html
    assert 'id="history-game-list"' in html
    for text in (source, board):
        assert "innerHTML" not in text
        assert "eval(" not in text
        assert "new Function(" not in text
        assert "document.write" not in text
    assert 'fetch("/api/history/players/" + playerSlug + "/games"' in source
    assert 'fetch("/api/history/games/" + gameId + "/replay"' in source
    assert '"cache:"no-store"' not in source  # no malformed cache options
    assert 'method:"POST"' not in source
    assert 'method:"PUT"' not in source
    assert 'WebSocket' not in source
    assert 'Stockfish' not in source and 'Rodent' not in source
    css = (DATA / "chess_board.css").read_text(encoding="utf-8")
    assert "grid-template-rows:repeat(8,minmax(0,1fr))" in css
    assert ".square.whitepiece" in css and ".square.blackpiece" in css


@contextmanager
def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        if hasattr(server, "arena_manager"):
            server.arena_manager.shutdown()
        thread.join(timeout=2)


def test_history_replay_assets_same_origin_with_csp_and_no_post(tmp_path):
    arenas = [
        arena.create_server(tmp_path, tmp_path / "sessions", port=0),
        live_board.create_server(tmp_path, port=0),
    ]
    for server in arenas:
        with _serve(server) as base:
            for path in ("/chess-board.js", "/chess-board.css"):
                with urlopen(base + path, timeout=5) as response:
                    assert response.status == 200
                    assert response.headers["Cache-Control"] == "no-store"
                    assert response.headers["X-Content-Type-Options"] == "nosniff"
                    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
                    assert response.read()
            if hasattr(server, "arena_manager"):
                with urlopen(base + "/history_replay.js", timeout=5) as response:
                    assert response.status == 200
                    assert b"ChessSimHistoryGames" in response.read()
                with urlopen(base + "/", timeout=5) as response:
                    page = response.read().decode("utf-8")
                    assert '/history_replay.js' in page
                    assert '/chess-board.js' in page
            else:
                with urlopen(base + "/", timeout=5) as response:
                    page = response.read().decode("utf-8")
                    assert '/chess-board.js' in page
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + "/chess-board.js", data=b"{}", method="POST"),
                        timeout=5)
            assert error.value.code in (403, 404, 405)
