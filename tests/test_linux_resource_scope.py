"""#27 Linux opt-in resource scope: no silent unrestricted retry."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_limited_debian.py"
    spec = importlib.util.spec_from_file_location("limited_chesssim", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_scope_limits_include_child_python_and_stop_option_parsing():
    runner = _module()
    cmd = runner.build_command(768, 200, 64, ["tournament-run", "--run", "/some run"])
    assert cmd[:6] == ["systemd-run", "--user", "--scope", "--collect", "--same-dir", "--quiet"]
    assert "--property=MemoryMax=768M" in cmd
    assert "--property=CPUQuota=200%" in cmd
    assert "--property=TasksMax=64" in cmd
    assert cmd[-6:] == [sys.executable, "-m", "chesssim", "tournament-run", "--run", "/some run"]
    assert "--" in cmd and cmd.index("--") == len(cmd) - 7


@pytest.mark.parametrize("memory,cpu,tasks,argv", [
    (0, 100, 64, ["arena"]),
    (128, 100, 64, ["arena"]),
    (768, 0, 64, ["arena"]),
    (768, 200, 2, ["arena"]),
    (768, 200, 64, ["roster"]),
    (768, 200, 64, ["--", "python", "-c", "bad"]),
    (768, 200, 64, ["play", "\x00evil"]),
    (True, 100, 64, ["arena"]),
])
def test_bad_limits_and_unknown_workloads_are_rejected(memory, cpu, tasks, argv):
    with pytest.raises(ValueError):
        _module().build_command(memory, cpu, tasks, argv)


def test_fail_closed_without_linux_and_never_run_plain_chesssim(monkeypatch):
    runner = _module()
    called = []
    monkeypatch.setattr(runner.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: called.append(a))
    assert runner.main(["--memory-mib", "768", "--cpu-percent", "100",
                        "--", "arena", "--out", "/tmp/new-run"]) == 3
    assert not called


def test_dry_run_is_explicitly_not_enforced(capsys):
    runner = _module()
    assert runner.main(["--memory-mib", "768", "--cpu-percent", "200",
                        "--dry-run", "--", "arena", "--out", "/tmp/new-run"]) == 0
    out = capsys.readouterr().out
    assert "PLAN_ONLY_NOT_ENFORCED" in out
    assert "systemd-run" in out

def test_linux_invocation_propagates_scope_exit_without_fallback(monkeypatch):
    runner = _module()
    calls = []
    class Completed:
        returncode = 17
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda cmd: "/usr/bin/systemd-run")
    monkeypatch.setattr(runner, "_has_unified_cgroup", lambda: True)
    def fake_run(cmd, *, check):
        assert check is False
        calls.append(cmd)
        return Completed()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.main(["--memory-mib", "768", "--cpu-percent", "200",
                          "--", "tournament-run", "--run", "/tmp/isolated"])
    assert result == 17
    assert len(calls) == 1
    assert calls[0][0] == "systemd-run"
    assert calls[0][-4:] == ["chesssim", "tournament-run", "--run", "/tmp/isolated"]


def test_missing_cgroup_v2_never_spawns_unbounded_child(monkeypatch):
    runner = _module()
    calls = []
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda cmd: "/usr/bin/systemd-run")
    monkeypatch.setattr(runner, "_has_unified_cgroup", lambda: False)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: calls.append(a))
    result = runner.main(["--memory-mib", "768", "--cpu-percent", "100",
                          "--", "arena", "--out", "/tmp/new"])
    assert result == 3
    assert calls == []

def test_missing_systemd_run_fails_closed_even_with_cgroup_v2(monkeypatch):
    runner = _module()
    calls = []
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(runner, "_has_unified_cgroup", lambda: True)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: calls.append(a))
    assert runner.main(["--memory-mib", "768", "--cpu-percent", "100",
                        "--", "tournament-run", "--run", "/tmp/isolated"]) == 3
    assert not calls


def test_dry_run_never_launches_scope_even_when_systemd_is_available(monkeypatch, capsys):
    runner = _module()
    calls = []
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.shutil, "which", lambda cmd: "/usr/bin/systemd-run")
    monkeypatch.setattr(runner, "_has_unified_cgroup", lambda: True)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: calls.append(a))
    assert runner.main(["--memory-mib", "768", "--cpu-percent", "100",
                        "--dry-run", "--", "tournament-run",
                        "--run", "/tmp/isolated"]) == 0
    assert not calls
    assert "PLAN_ONLY_NOT_ENFORCED" in capsys.readouterr().out


def test_diversity_execution_uses_the_same_optional_cgroup_limits():
    runner = _module()
    cmd = runner.build_command(
        1024, 100, 48,
        ["diversity-run", "--plan", "/tmp/registered-plan.json",
         "--out", "/tmp/new-trial", "--blocks", "1"])
    assert cmd[:3] == ["systemd-run", "--user", "--scope"]
    assert "--property=MemoryMax=1024M" in cmd
    assert "--property=CPUQuota=100%" in cmd
    assert "--property=TasksMax=48" in cmd
    assert cmd[-7:] == [
        "diversity-run", "--plan", "/tmp/registered-plan.json",
        "--out", "/tmp/new-trial", "--blocks", "1"]
