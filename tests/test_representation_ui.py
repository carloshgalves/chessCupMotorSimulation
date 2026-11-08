"""HTML/asset/accessibility gates for the opt-in representation lab."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

DATA=Path(__file__).resolve().parents[1]/"src"/"chesssim"/"data"


def test_representation_has_approved_actions_and_explicit_unmeasured_states():
    html=(DATA/"arena.html").read_text(encoding="utf-8")
    for marker in (
        'id="history-representation-open"', 'id="representation-view"',
        'id="rep-roster"', 'id="rep-depth"', 'id="rep-depth-label"',
        'id="rep-run"', 'id="rep-tests"', 'id="rep-analysis"',
        'id="rep-match"', 'id="rep-from"', 'id="rep-to"',
        'id="rep-back"', 'id="rep-pane-representation"',
        'id="rep-tab-tests"', 'id="rep-historical-rating"',
        'src="/representation.js"', 'href="/representation.css"'
    ):
        assert marker in html
    assert "Força (Elo real)" in html
    assert "Não medida" in html
    assert 'type="checkbox" disabled' in html
    script=(DATA/"representation.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "textContent" in script
    assert '"X-Chesssim-Token"' in script
    assert "ChessSimRepresentation" in script
    assert "/api/history/representation/" in script
    assert "test-" not in script or "test-" in script
    css=(DATA/"representation.css").read_text(encoding="utf-8")
    assert "@media(max-width:730px)" in css
    assert ".rep-roster-player[aria-pressed=true]" in css


@pytest.mark.skipif(shutil.which("node") is None,reason="Node unavailable")
def test_representation_scripts_parse_without_js_syntax_errors():
    for asset in ("representation.js","history.js"):
        result=subprocess.run(["node","--check",str(DATA/asset)],
                              capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr


def test_representations_have_no_claimed_unverified_elo_curve():
    js=(DATA/"representation.js").read_text(encoding="utf-8")
    assert "Dados não verificados" in js
    assert "94,2" not in js
    assert "93,6" not in js
    assert "2705" not in js
    assert ".toFixed(1)" in js  # Real measured top-1 only
