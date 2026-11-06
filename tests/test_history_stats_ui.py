"""Browser and packaged-asset gates for local-only profile statistics."""
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chesssim import arena


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/chesssim/data"


@pytest.mark.skipif(shutil.which("node") is None, reason="Requires Node.js")
def test_stats_browser_adversarial_interaction():
    script = ASSETS / "history_stats.js"
    check = subprocess.run(
        ["node", "--check", str(script)], capture_output=True,
        text=True, timeout=10, check=False,
    )
    assert check.returncode == 0, check.stderr
    harness = ROOT / "tests/assets/history_stats_browser_harness.cjs"
    run = subprocess.run(
        ["node", str(harness), str(script)], capture_output=True,
        text=True, timeout=15, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "stale GET, no POST: PASS" in run.stdout


def test_stats_assets_do_not_leak_to_history_or_engines():
    html = (ASSETS / "arena.html").read_text(encoding="utf-8")
    history = (ASSETS / "history.js").read_text(encoding="utf-8")
    script = (ASSETS / "history_stats.js").read_text(encoding="utf-8")
    css = (ASSETS / "history.css").read_text(encoding="utf-8")
    assert 'id="history-stats-section"' in html
    assert 'src="/history_stats.js"' in html
    assert "ChessSimHistoryStats.enter" in history
    assert "ChessSimHistoryStats.leave" in history
    assert "ChessSimHistoryStats.suspend" in history
    assert "ChessSimHistoryStats.resume" in history
    assert "No simulador" in html
    assert "Não são feitos históricos" in html
    assert "rating FIDE" in html
    assert "history-stats-warning" in css
    for token in ("innerHTML", "eval(", "document.write", "WebSocket",
                  'method:"POST"', "Stockfish", "Rodent", "localStorage"):
        assert token not in script
    assert 'fetch("/api/history/players/" + slug + "/stats"' in script


def test_stats_static_route_respects_loopback_and_csp(tmp_path):
    server = arena.create_server(tmp_path, tmp_path / "local", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/history_stats.js", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "script-src 'self'" in response.headers["Content-Security-Policy"]
            assert b"ChessSimHistoryStats" in response.read()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/history_stats.js", data=b"{}", method="POST"))
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=2)
