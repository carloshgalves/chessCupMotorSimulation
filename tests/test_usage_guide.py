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
DOCUMENTED_OPTIONS = {
    "setup": ("--apply", "--with-books"),
    "play": ("--depth", "--movetime", "--reverse", "--preset", "--max-plies"),
    "corpus-fetch": ("--dry-run", "--out", "--delay"),
    "corpus-build": ("--match-name", "--sources", "--out"),
    "opening-build": (
        "--v5-report",
        "--legacy-v3",
        "--legacy-pgn",
        "--ack-unpartitioned",
        "--max-plies",
    ),
    "build-provisional": (
        "--v5-report",
        "--legacy-v3",
        "--legacy-pgn",
        "--cohort-ledger",
        "--allow-provisional",
    ),
    "provisional-install": ("--profile", "--apply"),
    "play-provisional": ("--depth", "--movetime", "--preset", "--reverse"),
    "play-styles": (
        "--white-book",
        "--black-book",
        "--depth",
        "--movetime",
        "--rounds",
        "--seed",
    ),
    "replay-html": ("--pgn", "--out"),
    "analyze-quality": ("--pgn", "--stockfish", "--depth", "--out"),
    "compare-quality": ("--reports", "--out"),
    "style-audit": ("--pgn", "--out"),
}


@pytest.mark.parametrize("command", DOCUMENTED_COMMANDS)
def test_usage_guide_documents_supported_cli_help(command: str, capsys) -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert f"python -m chesssim {command}" in guide

    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])

    assert exit_info.value.code == 0
    assert f"usage: chesssim {command}" in capsys.readouterr().out


@pytest.mark.parametrize(("command", "options"), DOCUMENTED_OPTIONS.items())
def test_usage_guide_options_exist_in_cli_help(
    command: str, options: tuple[str, ...], capsys
) -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    for option in options:
        assert option in guide

    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    for option in options:
        assert option in help_text


def test_readme_links_to_the_usage_guide() -> None:
    readme = GUIDE.parents[1] / "README.md"
    assert "[manual de uso](docs/USAGE.md)" in readme.read_text(encoding="utf-8")
