"""UI contract for #57: approved compact layout, no reset and safe scripts."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest


DATA = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.scripts = []
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag=="script":
            self.scripts.append(attrs.get("src"))


def test_human_duel_html_contract_and_no_clear_button():
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    parsed=Ids()
    parsed.feed(html)
    assert len(parsed.ids)==len(set(parsed.ids)), "IDs must be unique"
    required={
        "arena-mode-duel","arena-mode-human","duel-form","human-form",
        "duel-viewer","human-viewer","human-opponent","human-side-buttons",
        "human-clock","human-depth","human-board","human-moves",
        "human-white-clock","human-black-clock","human-launch",
        "human-draw","human-resign","human-claim","human-interrupt",
        "human-resume","human-promotion",
    }
    assert required<=set(parsed.ids)
    assert "human-clear" not in parsed.ids
    assert 'src="/human-play.js"' in html
    assert "/human-play.js" in parsed.scripts
    assert "relógio" in html.lower()
    assert "profundidade" in html.lower()


def test_human_board_styles_reuse_current_theme_and_fixed_move_rows():
    css=(DATA/"arena.css").read_text(encoding="utf-8")
    prefs=(DATA/"board_preferences.js").read_text(encoding="utf-8")
    js=(DATA/"human_play.js").read_text(encoding="utf-8")
    assert ".human-play-columns" in css
    assert "grid-template-columns:minmax(310px,1.15fr) minmax(290px,.85fr)" in css
    assert ".human-move-row" in css and "height:34px" in css
    assert '"human-board"' in prefs
    assert "window.ChessSimBoard.paint" in js
    assert "MOVE_DISPLAY_INTERVAL_MS = 650" in js
    assert 'blackAtBottom=state.game?.config.color==="black"' in js
    assert 'blackAtBottom?g.black:g.white' in js
    assert "setInterval(advancePlayback,100)" in js
    assert "game.states" not in js or "g.states" in js
    assert "window.prompt" not in js
    assert "innerHTML" not in js and "eval(" not in js


def test_human_browser_script_parses_in_node():
    node=shutil.which("node")
    if node is None:
        pytest.skip("Node not installed")
    result=subprocess.run([node,"--check",str(DATA/"human_play.js")],
                          capture_output=True,text=True,timeout=10,check=False)
    assert result.returncode==0,result.stderr


def test_human_browser_interaction_paces_fast_uci_replies():
    node=shutil.which("node")
    if node is None:
        pytest.skip("Node not installed")
    harness=Path(__file__).parent/"assets"/"human_play_browser_harness.cjs"
    result=subprocess.run([node,str(harness),str(DATA/"human_play.js")],
                          capture_output=True,text=True,timeout=15,check=False)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
    assert "Human play browser smoke: PASS" in result.stdout
