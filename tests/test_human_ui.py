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
    assert 'board.addEventListener("pointerdown"' in js
    assert 'board.addEventListener("pointermove"' in js
    assert 'board.addEventListener("pointerup"' in js
    assert 'board.addEventListener("pointercancel"' in js
    assert 'board.addEventListener("lostpointercapture"' in js
    assert 'g.legal_moves.some(' in js
    assert 'g.revision!==gesture.revision' in js
    assert 'moveFromTo(gesture.from,to)' in js
    assert 'state.mode === "human"' in js
    assert "premoves" not in js.lower()
    assert "document.write" not in js
    assert 'touch-action:none' in css
    assert ".human-drag-preview" in css
    # Visual acceptance from the user's Chess.com reference: only the
    # already-rendered piece moves; no faded duplicate or floating tile.
    assert "cell.appendChild(piece)" in js
    assert "source.appendChild(piece)" in js
    assert "piece.cloneNode(" not in js
    assert "gesture.startX,clientY:gesture.startY" in js
    assert ".human-board .square.human-drag-origin .piece-glyph{opacity" not in css
    assert "background:none!important" in css
    assert "box-shadow:none!important" in css
    assert "transform:none" in css
    assert "pointer-events:none" in css


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
