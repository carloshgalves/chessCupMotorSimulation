"""Approved history/archive UI contract, DOM identity, static security gates."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest

DATA=Path(__file__).resolve().parents[1]/"src"/"chesssim"/"data"


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])


def test_history_archive_sections_and_filters_are_explicit():
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    parser=IDs();parser.feed(html)
    assert len(parser.ids)==len(set(parser.ids))
    required={
        "history-modern","archive-player-roster","archive-player-name",
        "archive-pane-overview","archive-pane-games","archive-pane-tournaments",
        "archive-pane-elo","archive-pane-records","archive-overview-stats",
        "archive-career-timeline","archive-recent-games",
        "archive-mode-performance","archive-curated-games",
        "archive-search","archive-origin","archive-modality","archive-result",
        "archive-games-table-body","archive-prev-page","archive-next-page",
        "archive-export-csv","archive-export-json","archive-export-pgn",
        "archive-replay-dialog","archive-replay-board","archive-replay-moves",
        "archive-replay-first","archive-replay-last",
        "history-original","history-profile-body","history-rating-profile",
        "history-stats-section","history-games-section"
    }
    assert required <= set(parser.ids)
    assert '/history_archive.js' in html
    assert '/history_archive.css' in html
    assert "mockup" not in html.lower()


def test_history_archive_does_not_fabricate_historical_elo_or_pgn():
    js=(DATA/"history_archive.js").read_text(encoding="utf-8")
    css=(DATA/"history_archive.css").read_text(encoding="utf-8")
    history=(DATA/"history.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "eval(" not in js
    assert "document.createElement" in js
    assert "textContent" in js
    assert "ChessSimHistoryArchive" in history
    assert "/api/history/archive/replay" in js
    assert "/archive/export/" in js
    assert "fast_statistical" not in js  # isolated via server-validated type
    assert "2705" not in js
    assert "94,2%" not in js
    assert "window.ChessSimBoard?.paint" in js
    assert "overflow:auto" in css
    assert "@media(max-width:850px)" in css


@pytest.mark.skipif(shutil.which("node") is None,reason="Node not installed")
def test_history_archive_js_syntax():
    for path in ("history_archive.js","history.js"):
        result=subprocess.run(["node","--check",str(DATA/path)],
            timeout=10,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
