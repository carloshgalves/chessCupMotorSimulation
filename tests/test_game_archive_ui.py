"""Issue #70: static UI and route contracts for game archive dashboard."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest

DATA=Path(__file__).resolve().parents[1]/"src"/"chesssim"/"data"


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
    def handle_starttag(self,tag,attrs):
        properties=dict(attrs)
        if "id" in properties:
            self.ids.append(properties["id"])


def test_history_sports_dashboard_has_no_duplicate_ids_and_expected_slots():
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    parsed=IdParser();parsed.feed(html)
    assert len(parsed.ids)==len(set(parsed.ids))
    for mark in ("ha-layout","ha-player-list","ha-tab-overview","ha-tab-games",
                 "ha-tab-events","ha-tab-rating","ha-tab-records","ha-overview-pane",
                 "ha-games-pane","ha-filters","ha-rows","ha-prev","ha-next",
                 "ha-export-json","ha-export-csv","ha-export-pgn",
                 "ha-replay-board","ha-replay-dialog","ha-replay-moves",
                 "ha-rating-plot","ha-rating-mode","history-profile-body"):
        assert mark in parsed.ids,mark
    assert 'src="/game_archive.js"' in html
    assert 'href="/game_archive.css"' in html
    assert 'id="history-games-section"' in html
    js=(DATA/"game_archive.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "textContent" in js
    assert "ha-replay-board" in js
    assert "has_replay" in js
    assert '"/api/history/archive/"' in js
    assert "ChessSimArchive" in js
    css=(DATA/"game_archive.css").read_text(encoding="utf-8")
    assert "grid-template-columns" in css
    assert "@media(max-width:850px)" in css


@pytest.mark.skipif(shutil.which("node") is None,reason="Node not installed")
def test_archive_scripts_parse():
    for name in ("game_archive.js","history.js","history_stats.js"):
        result=subprocess.run(["node","--check",str(DATA/name)],
                              capture_output=True,text=True,timeout=12)
        assert result.returncode==0,result.stderr


def test_history_profile_wires_game_archive_without_replacing_historical_packs():
    s=(DATA/"history.js").read_text(encoding="utf-8")
    for token in ("ChessSimArchive?.enter","ChessSimArchive?.leave",
                  "ChessSimArchive?.suspend","ChessSimArchive?.resume"):
        assert token in s
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    assert "Partidas para explorar" in html
    assert "Carreira e conquistas históricas documentadas" in html
