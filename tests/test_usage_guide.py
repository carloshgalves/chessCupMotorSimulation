"""Keep the user guide aligned with the supported command-line interface."""
from pathlib import Path

import pytest

from chesssim.cli import main


GUIDE = Path(__file__).resolve().parents[1] / "docs" / "USAGE.md"
DOCUMENTED_COMMANDS = (
    "roster",
    "check",
    "setup",
    "draw",
    "play",
    "corpus-sources",
    "corpus-fetch",
    "corpus-build",
    "opening-build",
    "build-provisional",
    "provisional-install",
    "play-provisional",
    "play-styles",
    "replay-html",
    "analyze-quality",
    "style-audit",
    "compare-quality",
)


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_usage_guide_documents_supported_cli_help(command: str, capsys) -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert f"python -m chesssim {command}" in guide

    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])

    assert exit_info.value.code == 0
    assert f"usage: chesssim {command}" in capsys.readouterr().out
