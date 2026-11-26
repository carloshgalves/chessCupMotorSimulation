from pathlib import Path
import shutil
import subprocess

import pytest


def test_observer_browser_navigation_and_live_edge():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node not installed")
    root = Path(__file__).resolve().parents[1]
    script = root / "src/chesssim/data/competition_observer.js"
    harness = root / "tests/assets/competition_observer_browser_harness.cjs"
    checked = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    result = subprocess.run([node, str(harness), str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Competition observer browser smoke: PASS" in result.stdout
