"""#68 — Board appearance is a pure, same-origin presentation concern."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
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
def test_board_preferences_browser_adversarial():
    for file in ("chess_board.js", "board_preferences.js", "history_replay.js",
                 "monitor.js", "arena.js"):
        check = subprocess.run(["node", "--check", str(DATA / file)],
                               capture_output=True, text=True, timeout=10, check=False)
        assert check.returncode == 0, file + ": " + check.stderr
    result = subprocess.run(
        ["node", str(ROOT / "tests/assets/board_preferences_browser_harness.cjs"),
         str(DATA / "board_preferences.js"), str(DATA / "chess_board.js")],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "64 squares, real-change highlights: PASS" in result.stdout


def test_preferences_are_local_to_shared_8x8_board_and_reduced_motion():
    js = (DATA / "board_preferences.js").read_text(encoding="utf-8")
    css = (DATA / "chess_board.css").read_text(encoding="utf-8")
    renderer = (DATA / "chess_board.js").read_text(encoding="utf-8")
    assert "chesssim.board-presentation.v1" in js
    assert '["board","history-replay-board"]' in js
    assert "window.localStorage" in js and "addEventListener(\"storage\"" in js
    for word in ("innerHTML", "eval(", "fetch(", "WebSocket", "document.write"):
        assert word not in js
    for name in ("data-board-theme", "data-piece-style", "data-board-motion"):
        assert name in js
    for value in ("classic", "wood", "slate", "light", "dark", "contrast",
                  "green", "blue", "walnut", "marble", "lavender", "terracotta"):
        assert f'[data-board-theme="{value}"]' in css or value == "classic"
    assert '[data-piece-style="outline"]' in css
    assert "-webkit-text-fill-color:transparent" not in css
    assert "-webkit-text-fill-color:var(--piece-white)" in css
    assert "-webkit-text-fill-color:var(--piece-black)" in css
    assert ".board .square.dark.coordinate::after{color:#fff}" in css
    assert ".board .square.light.coordinate::after{color:#101820}" in css
    assert "animation:chesssim-board-move-highlight 160ms" in css
    assert "animation:chesssim-board-move-highlight 330ms" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "animation:none!important" in css
    assert "grid-template-columns:repeat(8,minmax(0,1fr))" in css
    assert "grid-template-rows:repeat(8,minmax(0,1fr))" in css
    assert "aspect-ratio:1 / 1" in css
    assert "pieceSlots = new WeakMap()" in renderer
    assert "refreshArtwork(container)" in renderer
    assert '"/piece-assets/" + selected + "/" + code + ".svg"' in renderer
    assert 'image.addEventListener("error"' in renderer
    assert ".board[data-piece-kind=\"svg\"] .piece-glyph[data-art-ready=\"1\"]" in css
    assert ".square .piece-glyph .piece-svg" in css
    assert "lastPosition = new WeakMap()" in renderer
    assert "position.fen" in renderer
    assert "container.replaceChildren(fragment)" in renderer
    assert "file = 0; file < 8" in renderer
    assert "--square-light" in css and "--square-dark" in css
    assert "https://" not in css  # no external images/fonts/dependencies
    assert "url(" not in css


def test_board_preferences_both_html_surfaces_have_controls():
    for page in ("arena.html", "monitor.html"):
        html = (DATA / page).read_text(encoding="utf-8")
        assert 'href="/chess-board.css"' in html
        assert 'src="/chess-board.js"' in html
        assert 'src="/board-preferences.js" defer' in html
        assert 'class="board-prefs"' in html
        assert 'id="board-pref-status" role="status" aria-live="polite"' in html
        assert 'id="board-pref-reset"' in html
        assert 'id="board-theme-gallery"' in html
        assert 'id="board-piece-gallery"' in html
        assert len(re.findall(r'class="board-theme-chip"', html)) == 12
        assert len(re.findall(r'class="board-piece-chip"', html)) == 7
        for name, n in (("theme", 12), ("pieces", 7), ("motion", 3)):
            assert f'id="board-pref-{name}"' in html
            match = re.search(rf'<select id="board-pref-{name}">(.*?)</select>', html, re.S)
            assert match
            assert len(re.findall(r'<option value="[^"]+">', match.group(1))) == n
        assert "Somente visualização" in html or "Execução local" in html


def test_source_code_cannot_forward_board_prefs_to_duel_engine():
    script = (DATA / "board_preferences.js").read_text(encoding="utf-8")
    for marker in ("/api/", "rodent", "stockfish", "uci_elo", "skill level",
                   "X-Chesssim-Token", "window.location", "document.cookie"):
        assert marker.lower() not in script.lower()
    arena_client = (DATA / "arena.js").read_text(encoding="utf-8")
    assert "board-pref-theme" not in arena_client
    assert "board-pref-motion" not in arena_client
    assert "board-pref-pieces" not in arena_client


@contextmanager
def _running(server):
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


def test_same_origin_board_settings_work_for_arena_and_standalone_watch(tmp_path):
    # watch must be opened against a valid saved duel checkpoint.
    (tmp_path / "summary.json").write_text('{"status":"RUNNING","games":[]}',
                                          encoding="utf-8")
    for server in (arena.create_server(tmp_path, tmp_path / "sessions", port=0),
                   live_board.create_server(tmp_path, port=0)):
        with _running(server) as base:
            with urlopen(base + "/", timeout=5) as response:
                html = response.read().decode("utf-8")
                assert 'src="/board-preferences.js"' in html
            with urlopen(base + "/board-preferences.js", timeout=5) as response:
                assert response.status == 200
                assert response.headers["Content-Type"].startswith("text/javascript")
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert "script-src 'self'" in response.headers["Content-Security-Policy"]
                assert b"chesssim.board-presentation.v1" in response.read()
            with pytest.raises(HTTPError) as err:
                urlopen(Request(base + "/board-preferences.js",
                                data=b"{}", method="POST"), timeout=5)
            assert err.value.code in (403, 404, 405)


def test_wheel_manifest_requires_board_preferences():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"board_preferences.js"' in project
    assert '"board_preferences.js"' in workflow
