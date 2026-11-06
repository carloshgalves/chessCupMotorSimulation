"""UI, API and packaging contract tests for simulation-only statistics."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_history_statistics_browser_contract():
    script = ROOT / "src/chesssim/data/history_stats.js"
    check = subprocess.run(["node", "--check", str(script)],
                           capture_output=True, text=True, timeout=10, check=False)
    assert check.returncode == 0, check.stdout + check.stderr
    test = subprocess.run(
        ["node", str(ROOT / "tests/assets/history_stats_browser_harness.cjs"), str(script)],
        capture_output=True, text=True, timeout=12, check=False,
    )
    assert test.returncode == 0, test.stdout + test.stderr
    assert "offline GET: PASS" in test.stdout


def test_history_statistics_assets_are_same_origin_and_do_not_change_motor():
    root = ROOT / "src/chesssim/data"
    js = (root / "history_stats.js").read_text(encoding="utf-8")
    html = (root / "arena.html").read_text(encoding="utf-8")
    css = (root / "history.css").read_text(encoding="utf-8")
    assert 'src="/history_stats.js"' in html
    assert 'id="history-stats-totals"' in html
    assert 'id="history-stats-coverage"' in html
    assert 'id="history-stats-section"' in html
    assert "history-stats-warning" in css
    assert "innerHTML" not in js and "eval(" not in js
    assert "method:\"POST\"" not in js and "WebSocket" not in js
    assert 'fetch("/api/history/players/" + slug + "/stats"' in js
    assert "Stockfish" not in js and "Rodent" not in js
    from importlib.resources import files
    packaged = files("chesssim.data").joinpath("history_stats.js")
    assert packaged.read_text(encoding="utf-8").startswith('"use strict";')
