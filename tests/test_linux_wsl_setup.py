"""Offline tests and a real-subprocess synthetic UCI probe for Linux/WSL setup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from scripts.verify_linux_wsl import (
    SetupError,
    main,
    probe_uci,
    require_profiles,
    resolve_rodent,
    run_smoke,
    validate_pgn,
)


FAKE_UCI = '''
import chess
import sys

board = chess.Board()
for raw in sys.stdin:
    command = raw.strip()
    if command == "uci":
        print("id name Synthetic Rodent Test Engine")
        print("option name Personality type combo default Fischer var Fischer var Tal")
        print("option name Threads type spin default 1 min 1 max 8")
        print("option name Hash type spin default 16 min 1 max 128")
        print("option name UseBook type check default false")
        print("uciok", flush=True)
    elif command == "isready":
        print("readyok", flush=True)
    elif command.startswith("position "):
        tokens = command.split()
        if len(tokens) > 1 and tokens[1] == "startpos":
            board = chess.Board()
            if "moves" in tokens:
                for move in tokens[tokens.index("moves") + 1:]:
                    board.push_uci(move)
    elif command.startswith("go "):
        move = next(iter(board.legal_moves), None)
        print("bestmove " + (move.uci() if move else "(none)"), flush=True)
    elif command == "quit":
        break
'''


@pytest.fixture
def rodent(tmp_path: Path) -> Path:
    root = tmp_path / "Rodent With Spaces"
    (root / "personalities").mkdir(parents=True)
    (root / "personalities" / "basic.ini").write_text(
        "Fischer=fischer.txt\nTal=tal.txt\n", encoding="utf-8"
    )
    for filename in ("fischer.txt", "tal.txt"):
        (root / "personalities" / filename).write_text(
            "setoption name Threads value 1\n", encoding="utf-8"
        )
    executable = root / "rodent-iv-plain.exe"
    executable.write_text("#!" + sys.executable + "\n" + FAKE_UCI, encoding="utf-8")
    executable.chmod(0o755)
    return root


def test_missing_engine_path_is_actionable(tmp_path: Path):
    with pytest.raises(SetupError, match="RODENT_IV_HOME"):
        resolve_rodent(None)
    with pytest.raises(SetupError, match="directory not found"):
        resolve_rodent(str(tmp_path / "missing"))


def test_missing_registry_or_binary_is_actionable(tmp_path: Path):
    (tmp_path / "personalities").mkdir()
    with pytest.raises(SetupError, match="binary missing"):
        resolve_rodent(str(tmp_path))
    (tmp_path / "rodent-iv-plain.exe").write_text("placeholder", encoding="utf-8")
    with pytest.raises(SetupError, match="registry missing"):
        resolve_rodent(str(tmp_path))


def test_existing_rodent_can_be_probed_with_external_uci_subprocess(rodent: Path):
    root, exe = resolve_rodent(str(rodent))
    info = probe_uci(exe, cwd=root, require_personality=True)
    assert info["legal_move"] in {"g1h3", "g1f3", "b1c3", "b1a3"}
    assert info["engine_id"]["name"] == "Synthetic Rodent Test Engine"
    assert require_profiles(root, "fischer", "tal") == ("fischer", "tal")


def test_real_uci_smoke_produces_valid_incomplete_pgn_and_summary(rodent: Path, tmp_path: Path):
    output = tmp_path / "smoke artifacts"
    report = run_smoke(
        Path(sys.executable), rodent, output,
        white="fischer", black="tal", depth=1, max_plies=4
    )
    assert report["status"] == "INCOMPLETE"
    assert report["result"] == "*"
    assert report["plies"] == 4
    assert report["mode"] == "SHORT_REAL_UCI_SMOKE"
    assert Path(report["pgn"]).is_file()
    assert (output / "smoke_report.json").is_file()
    saved = json.loads((output / "smoke_report.json").read_text(encoding="utf-8"))
    assert saved["pgn_sha256"] == report["pgn_sha256"]

    with pytest.raises(SetupError, match="already exists"):
        run_smoke(
            Path(sys.executable), rodent, output,
            white="fischer", black="tal", depth=1, max_plies=4
        )

    summary = Path(report["summary"])
    payload = json.loads(summary.read_text(encoding="utf-8"))
    payload["games"][0]["result"] = "1-0"
    summary.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SetupError, match="Result does not match"):
        validate_pgn(summary.parent, "fischer", "tal")


def test_smoke_rejects_missing_profile(rodent: Path):
    (rodent / "personalities" / "tal.txt").unlink()
    with pytest.raises(SetupError, match="--apply-profiles"):
        require_profiles(rodent, "fischer", "tal")


def test_smoke_rejects_same_participant(rodent: Path):
    with pytest.raises(SetupError, match="distinct"):
        require_profiles(rodent, "tal", "tal")


def test_with_books_requires_explicit_permission():
    with pytest.raises(SystemExit) as exc:
        main(["--with-books"])
    assert exc.value.code == 2


def test_setup_without_rodent_keeps_engine_optional(monkeypatch, capsys):
    monkeypatch.delenv("RODENT_IV_HOME", raising=False)
    monkeypatch.delenv("CHESSSIM_UCI_ENGINE", raising=False)
    assert main([]) == 0
    assert "not configured" in capsys.readouterr().out


def test_missing_rodent_fails_when_real_smoke_requested(monkeypatch, capsys):
    monkeypatch.delenv("RODENT_IV_HOME", raising=False)
    monkeypatch.delenv("CHESSSIM_UCI_ENGINE", raising=False)
    assert main(["--smoke"]) == 2
    assert "RODENT_IV_HOME" in capsys.readouterr().err


def test_cli_does_not_mutate_profiles_by_default(rodent: Path, monkeypatch):
    before = (rodent / "personalities" / "basic.ini").read_bytes()
    monkeypatch.delenv("CHESSSIM_UCI_ENGINE", raising=False)
    assert main(["--rodent", str(rodent)]) == 0
    assert (rodent / "personalities" / "basic.ini").read_bytes() == before
    assert not (rodent / "personalities" / "_backups").exists()
