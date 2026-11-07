"""Issue #53: independent History release gates, not a visual-signoff claim."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chesssim import arena
from chesssim.catalog import load_roster
from chesssim.history_profiles import load_history_profile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/chesssim/data"


def _luminance(rgb: str) -> float:
    """sRGB relative luminance (WCAG 2.x)."""
    assert re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", rgb)
    if len(rgb) == 4:
        rgb = "#" + "".join(ch * 2 for ch in rgb[1:])
    channels = [int(rgb[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))


def _contrast(foreground: str, background: str) -> float:
    low, high = sorted((_luminance(foreground), _luminance(background)))
    return (high + 0.05) / (low + 0.05)


def test_history_primary_button_meets_aa_normal_text():
    css = (DATA / "history.css").read_text(encoding="utf-8")
    match = re.search(
        r"#history-actions button\s*\{\s*background:(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?);"
        r"color:(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?);min-height:44px\s*\}", css,
    )
    assert match, "History CTA palette/target must be explicitly auditable"
    background, foreground = match.groups()
    assert _contrast(foreground, background) >= 4.5, (
        "Normal text must meet WCAG AA (4.5:1)"
    )


def test_history_keyboard_touch_and_reduced_motion_contract():
    css = (DATA / "history.css").read_text(encoding="utf-8")
    html = (DATA / "arena.html").read_text(encoding="utf-8")
    js = (DATA / "history.js").read_text(encoding="utf-8")
    assert ".history-carousel>button{min-width:44px;min-height:44px}" in css
    assert ".history-side-button{min-height:44px}" in css
    assert ".history-shell :is(button,a,input):focus-visible" in css
    assert "outline:3px solid #ffe39f;outline-offset:3px" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "animation:none!important" in css and "transition:none!important" in css
    assert "aria-label=\"Enxadrista anterior\"" in html
    assert "aria-label=\"Próximo enxadrista\"" in html
    assert "aria-label=\"Jogador selecionado\"" in html
    assert 'id="history-position" class="history-position" aria-live="polite"' in html
    assert 'id="history-open"' in html and 'id="history-back"' in html
    assert "byId(\"history-open\").focus()" in js
    assert 'event.altKey || event.ctrlKey || event.metaKey' in js
    # The reused board must remain square and keep 64 slots irrespective of empty ranks.
    board = (DATA / "chess_board.css").read_text(encoding="utf-8")
    assert "grid-template-columns:repeat(8,minmax(0,1fr))" in board
    assert "grid-template-rows:repeat(8,minmax(0,1fr))" in board
    assert "aspect-ratio:1 / 1" in board


def test_64_portraits_integrity_attribution_and_known_legal_review_blocker():
    roster = load_roster()
    assert len(roster) == 64
    packages = files("chesssim.data")
    licenses = Counter()
    for player in roster:
        profile = load_history_profile(player.slug)
        photo = profile["portrait"]
        assert photo is not None
        assert photo["approval_status"] == "approved"
        assert photo["local_asset"] == f"history/portraits/{player.slug}.jpg"
        binary = packages.joinpath("history", "portraits", player.slug + ".jpg")
        assert binary.is_file()
        assert sha256(binary.read_bytes()).hexdigest() == photo["sha256"]
        for field in ("alt", "author", "event", "event_date", "rights_holder",
                      "derivative_note", "source_url", "license_url"):
            assert isinstance(photo[field], str) and photo[field].strip(), (
                f"{player.slug}: missing {field}"
            )
        assert photo["source_url"].startswith("https://")
        assert photo["license_url"].startswith("https://")
        licenses[photo["license"]] += 1
    assert sum(licenses.values()) == 64
    assert licenses["Public Domain Mark 1.0"] == 16
    assert licenses["CC BY-SA 4.0"] == 1
    assert licenses["CC0 1.0"] == 28
    # A file being hash-verified is NOT legal clearance for commercial reuse.
    docs = (ROOT / "docs/PHOTO_LICENSES.md").read_text(encoding="utf-8")
    assert "não é licença" in docs
    assert "Public Domain Mark" in docs


def test_history_assets_are_local_and_narrative_does_not_impersonate():
    html = (DATA / "arena.html").read_text(encoding="utf-8")
    scripts = [DATA / n for n in ("history.js", "history_replay.js",
                                 "history_stats.js", "chess_board.js")]
    assert not re.search(r"<(?:script|link|iframe)[^>]+(?:src|href)=[\"']https?://", html)
    assert 'src="/viewer"' in html
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "innerHTML" not in text
        assert "eval(" not in text
        assert "document.write" not in text
        assert not re.search(r"https?://[^/\s]*(?:stockfish|rodent|openai)", text, re.I)
    assert "não uma declaração do enxadrista" in scripts[0].read_text(encoding="utf-8")
    assert "não representam a carreira" in html.casefold()
    assert "Play Magnus" not in html


def test_history_routes_host_csp_read_only_and_no_token_leak(tmp_path):
    server = arena.create_server(tmp_path, tmp_path / "arena", port=0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for path in ("/", "/history.js", "/history.css", "/history_replay.js",
                     "/history_stats.js", "/api/history/players",
                     "/api/history/players/fischer/stats",
                     "/api/history/players/fischer/games"):
            with urlopen(base + path, timeout=10) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Referrer-Policy"] == "no-referrer"
                csp = response.headers["Content-Security-Policy"]
                assert "default-src 'none'" in csp
                assert "script-src 'self'" in csp
                body = response.read()
                assert server.arena_token.encode() not in body
                assert len(body) > 0
        for path in (
            "/api/history/players/not-a-player",
            "/api/history/players/fischer/extra",
            "/api/history/players/fischer/stats?path=/tmp",
            "/api/history/players/fischer/games?token=test",
            "/api/history/portraits/not-a-player.jpg",
            "/api/history/games/not-a-game/replay",
            "/api/history/games/not-a-game/replay?file=/tmp/a",
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path, timeout=10)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as evil:
            urlopen(Request(base + "/api/history/players",
                            headers={"Host": "evil.invalid"}), timeout=10)
        assert evil.value.code == 403
        for path in ("/api/history/players/fischer/stats",
                     "/api/history/players/fischer/games"):
            with pytest.raises(HTTPError) as post:
                urlopen(Request(base + path, data=b"{}", method="POST"), timeout=10)
            assert post.value.code in (403, 404, 405)
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=5)
