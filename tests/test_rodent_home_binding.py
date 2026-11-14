"""Rodent IV home binding regression; UCI subprocesses are mocks here only."""
from __future__ import annotations

from types import SimpleNamespace

import chess.engine
import pytest

from chesssim.catalog import find_player
from chesssim.matches import _open_engine


class FakeRodent:
    def __init__(self, personality=True):
        self.options = ({"Personality": SimpleNamespace(var=["Fischer"]),
                         "UseBook": SimpleNamespace(var=None)}
                        if personality else {})
        self.configured = []
        self.quit_called = False
        self.close_called = False

    def configure(self, data):
        self.configured.append(dict(data))

    def quit(self):
        self.quit_called = True

    def close(self):
        self.close_called = True


def test_uci_explicit_rodent_home_overrides_ambient_value(tmp_path, monkeypatch):
    monkeypatch.setenv("RODENT4HOME", "/invalid/operator/installation")
    fixture = FakeRodent()
    captured = {}

    def fake_launch(executable, *, cwd, env, timeout):
        captured.update({"command": executable, "cwd": cwd,
                         "env": env.copy(), "timeout": timeout})
        return fixture

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", fake_launch)
    p = find_player("fischer")
    executable = tmp_path / "rodent-iv-plain.exe"
    result = _open_engine(tmp_path, executable, p, book=False)
    assert result is fixture
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["env"]["RODENT4HOME"] == str(tmp_path.resolve())
    assert captured["timeout"] == 120.0
    assert captured["command"] == str(executable)
    assert fixture.configured[0] == {"Personality": "Fischer"}
    assert fixture.configured[1] == {"UseBook": False}
    assert not fixture.quit_called


def test_uci_missing_personality_does_not_silently_play_generic(
        tmp_path, monkeypatch):
    fixture = FakeRodent(personality=False)
    monkeypatch.setattr(
        chess.engine.SimpleEngine, "popen_uci",
        lambda *args, **kwargs: fixture)
    with pytest.raises(RuntimeError, match="Personality"):
        _open_engine(tmp_path, tmp_path / "rodent-iv-plain.exe",
                     find_player("fischer"), book=False)
    assert fixture.quit_called
