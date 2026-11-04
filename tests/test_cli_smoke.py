"""Engine-independent command-line smoke tests."""
from __future__ import annotations

from chesssim.cli import main


def test_roster_command_lists_all_64_players(capsys) -> None:
    assert main(["roster"]) == 0
    output = capsys.readouterr().out
    player_lines = [
        line for line in output.splitlines()
        if len(line) >= 4 and line[:2].isdigit() and line[2:4] == ". "
    ]
    assert len(player_lines) == 64
    assert "64 selecionados" in output

