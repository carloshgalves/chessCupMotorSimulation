"""#59 League visual contract and browser syntax gates."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest


DATA = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.scripts = []
        self.styles = []
        self.in_league = False
        self.league_text = []
    def handle_starttag(self, tag, attrs):
        options = dict(attrs)
        if options.get("id")=="league-view":
            self.in_league=True
        if "id" in options:
            self.ids.append(options["id"])
        if tag=="script":
            self.scripts.append(options.get("src"))
        if tag=="link" and options.get("rel")=="stylesheet":
            self.styles.append(options.get("href"))
    def handle_endtag(self, tag):
        # The league view is a sibling section in the main application.
        if tag=="section" and self.in_league:
            pass
    def handle_data(self, text):
        self.league_text.append(text)


def test_league_approved_screen_is_wired_and_has_no_reset_control():
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    doc=Collect()
    doc.feed(html)
    assert len(doc.ids)==len(set(doc.ids)), "Duplicate HTML IDs"
    required={
        "tab-arena","tab-leagues","tab-history","league-view",
        "league-list","league-season-name","league-selection",
        "league-progress-bar","league-progress-games","league-progress-complete",
        "league-progress-running","league-next-round","league-next-match",
        "league-view-matches","league-run","league-pause",
        "league-tab-standings","league-tab-rounds","league-tab-games",
        "league-tab-details","league-standings-body","league-round-matches",
        "league-games-list","league-game-player","league-games-prev",
        "league-games-next","league-export-pgn","league-export-json",
        "league-create-form","league-create-name","league-create-format",
        "league-create-season","league-create-modality","league-create-count",
        "league-create-execution","league-participant-options",
        "league-create-submit",
    }
    assert required<=set(doc.ids)
    assert "/league.js" in doc.scripts
    assert "/league.css" in doc.styles
    assert "league-clear" not in doc.ids
    assert '<h2 id="league-heading" tabindex="-1">' in html
    assert "limpar liga" not in html.lower()


def test_league_css_maintains_three_columns_and_mobile_reflow():
    css=(DATA/"league.css").read_text(encoding="utf-8")
    assert ".league-overview" in css
    assert "grid-template-columns:minmax(168px,22%)" in css
    assert "@media(max-width:660px)" in css
    assert ".league-create-row" in css
    assert ".league-standings-table" in css
    assert ".league-progress-fill" in css
    assert "overflow:auto" in css
    assert ":focus-visible" in (DATA/"arena.css").read_text(encoding="utf-8")


def test_league_frontend_uses_safe_dom_and_local_api():
    script=(DATA/"league.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script and "eval(" not in script
    assert "document.createElement" in script
    assert '"/api/leagues"' in script
    assert '"/api/leagues/create"' in script
    assert '"/api/competition/ratings?modality="' in script
    assert "state.pending" in script
    assert 'get("/api/arena")' in script
    assert "X-Chesssim-Token" in script
    assert "fast_statistical" in script and "engine_full" in script
    assert "ChessSimHistoryNavigation" in script
    assert "ChessSimLeagueNavigation" in (DATA/"history.js").read_text(
        encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None,reason="Node.js not installed")
def test_league_javascript_parses_in_node():
    for name in ("league.js","history.js"):
        result=subprocess.run(
            ["node","--check",str(DATA/name)],
            capture_output=True,text=True,timeout=10,check=False)
        assert result.returncode==0, result.stdout+"\n"+result.stderr


@pytest.mark.skipif(shutil.which("node") is None,reason="Node.js not installed")
def test_league_browser_navigation_create_rounds_and_exports():
    harness=Path(__file__).parent/"assets"/"league_browser_harness.cjs"
    result=subprocess.run(
        ["node",str(harness),str(DATA/"league.js")],
        capture_output=True,text=True,timeout=15,check=False)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
    assert "League browser smoke: PASS" in result.stdout
