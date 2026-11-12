"""Opt-in Debian/Linux cgroup scope for ChessCup UCI workloads (#27).

This launcher does not change the program or assert resource enforcement until
systemd accepts a real scope. It fails closed; there is no silent unrestricted
fallback. The CPU quota is a *rate* cap, not an execution deadline.
"""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys


def build_command(memory_mib: int, cpu_percent: int, tasks_max: int,
                  chesssim_args: list[str]) -> list[str]:
    if type(memory_mib) is not int or not 256 <= memory_mib <= 32768:
        raise ValueError("MemoryMax needs 256..32768 MiB")
    if type(cpu_percent) is not int or not 10 <= cpu_percent <= 1600:
        raise ValueError("CPUQuota needs 10..1600 percent (100 = one core)")
    if type(tasks_max) is not int or not 16 <= tasks_max <= 256:
        raise ValueError("TasksMax needs 16..256 tasks")
    if not chesssim_args or chesssim_args[0] not in {
        "tournament-run", "arena", "play", "play-styles", "play-provisional",
        "diversity-run"
    }:
        raise ValueError("Choose an explicitly supported UCI workload")
    if any(not isinstance(v, str) or not v or "\x00" in v for v in chesssim_args):
        raise ValueError("Invalid chesssim argument")
    return [
        "systemd-run", "--user", "--scope", "--collect", "--same-dir", "--quiet",
        "--property=MemoryMax=" + str(memory_mib) + "M",
        "--property=CPUQuota=" + str(cpu_percent) + "%",
        "--property=TasksMax=" + str(tasks_max),
        "--", sys.executable, "-m", "chesssim", *chesssim_args
    ]


def _has_unified_cgroup() -> bool:
    return Path("/sys/fs/cgroup/cgroup.controllers").is_file()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Restrict ChessCup on Linux cgroup v2 via systemd user scope")
    p.add_argument("--memory-mib", type=int, required=True)
    p.add_argument("--cpu-percent", type=int, required=True)
    p.add_argument("--max-tasks", type=int, default=64)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- tournament-run ...")
    args = p.parse_args(argv)
    rest = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        cmd = build_command(args.memory_mib, args.cpu_percent, args.max_tasks, rest)
    except ValueError as exc:
        p.error(str(exc))
    if args.dry_run:
        print(json.dumps({"status": "PLAN_ONLY_NOT_ENFORCED",
                          "command": cmd}, ensure_ascii=False))
        return 0
    if (platform.system() != "Linux" or shutil.which("systemd-run") is None
            or not _has_unified_cgroup()):
        print("No Linux cgroup v2 / systemd-run; refusing unbounded fallback", file=sys.stderr)
        return 3
    # systemd user manager may be offline or delegation disabled. A failure
    # is an explicit nonzero exit; NEVER retry by running chesssim directly.
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:
        print("Failed to create resource scope: " + str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
