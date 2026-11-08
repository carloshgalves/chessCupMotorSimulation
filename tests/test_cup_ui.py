"""Issue #58: UI identity, interaction contract and anti-XSS gates."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest

DATA=Path(__file__).resolve().parents[1]/"src"/"chesssim"/"data"


class Analyzer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
        self.scripts=[]
        self.styles=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag=="script":
            self.scripts.append(attrs.get("src"))
        if tag=="link" and attrs.get("rel")=="stylesheet":
            self.styles.append(attrs.get("href"))


def test_approved_cup_dashboard_and_creation_are_in_arena():
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    ids=Analyzer()
    ids.feed(html)
    assert len(ids.ids)==len(set(ids.ids)),"Duplicate IDs"
    required={
        "tab-cups","cup-view","cup-create-view","cup-list",
        "cup-bracket","cup-title","cup-status-pill",
        "cup-progress-bar","cup-progress-games","cup-progress-matches",
        "cup-progress-running","cup-selected-players",
        "cup-next-stage","cup-run","cup-open-match",
        "cup-tiebreak-list","cup-export-json","cup-export-pgn",
        "cup-pause","cup-quick-configure",
        "cup-creation-presets","cup-create-form","cup-seed",
        "cup-participants","cup-depth","cup-execution",
        "cup-white-seconds","cup-increment",
        "cup-bid-min","cup-bid-max","cup-save","cup-back",
        "cup-replay-board","cup-replay-dialog",
        "cup-replay-prev","cup-replay-next","cup-replay-moves",
        "cup-women-table",
    }
    assert required <= set(ids.ids)
    assert "/cup.js" in ids.scripts
    assert "/cup.css" in ids.styles
    assert "cup-clear" not in ids.ids
    assert "limpar" not in html.lower()


def test_cup_theme_matches_approved_league_and_responsive_layout():
    css=(DATA/"cup.css").read_text(encoding="utf-8")
    assert "grid-template-columns:minmax(175px,21%)" in css
    assert ".cup-bracket-game" in css
    assert ".cup-selected-player img" in css
    assert ".cup-creation-right" in css
    assert ".cup-preset-list" in css
    assert "@media(max-width:710px)" in css
    assert "#cup-view[hidden]" in css
    assert "overflow:auto" in css


def test_cup_safety_requires_no_html_injection_or_arbitrary_files():
    js=(DATA/"cup.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "eval(" not in js
    assert "document.createElement" in js
    assert '"/api/cups/create"' in js
    assert '"/api/cups/presets"' in js
    assert '"X-Chesssim-Token"' in js
    assert "CupNavigation" in js
    assert "window.ChessSimBoard.paint" in js
    assert "/api/history/portraits/" in js
    assert "fast_statistical" in js
    assert "state.commandPending" in js
    history=(DATA/"history.js").read_text(encoding="utf-8")
    league=(DATA/"league.js").read_text(encoding="utf-8")
    assert "ChessSimCupNavigation?.hide()" in history
    assert "ChessSimCupNavigation?.hide()" in league


@pytest.mark.skipif(shutil.which("node") is None,reason="Node unavailable")
def test_cup_js_and_navigation_are_syntax_valid():
    for asset in ("cup.js","history.js","league.js"):
        r=subprocess.run(["node","--check",str(DATA/asset)],
                         capture_output=True,text=True,timeout=10)
        assert r.returncode==0,r.stdout+"\n"+r.stderr
