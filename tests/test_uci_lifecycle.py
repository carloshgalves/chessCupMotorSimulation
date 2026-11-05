"""Linux subprocess integration: UCI children terminate after a cooperative stop."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

from chesssim.catalog import find_player
from chesssim.matches import _play_one


# No Stockfish or Rodent download required. This script speaks the minimal UCI
# subset used by _open_engine and _play_one, and writes its own PID for auditing.
UCI_STUB = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys

with Path("uci-child-pids.txt").open("a", encoding="ascii") as stream:
    stream.write(f"{os.getpid()}\n")

moves = []
sequence = ["f2f3", "e7e5", "g2g4", "d8h4"]
for raw in sys.stdin:
    cmd = raw.strip()
    if cmd == "uci":
        print("id name Test UCI Stub")
        print("option name Personality type combo default --- var --- var Fischer var Tal")
        print("option name Threads type spin default 1 min 1 max 8")
        print("option name Hash type spin default 64 min 1 max 1024")
        print("option name UseBook type check default false")
        print("uciok", flush=True)
    elif cmd == "isready":
        print("readyok", flush=True)
    elif cmd.startswith("position "):
        moves = cmd.split(" moves ", 1)[1].split() if " moves " in cmd else []
    elif cmd.startswith("go"):
        print("bestmove " + sequence[len(moves) % len(sequence)], flush=True)
    elif cmd == "quit":
        break
'''


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process-lifecycle gate")
def test_two_native_uci_subprocesses_exit_after_stop(tmp_path):
    exe = tmp_path / "rodent-iv-plain.exe"
    exe.write_text(UCI_STUB, encoding="utf-8")
    exe.chmod(0o755)
    a = find_player("fischer")
    b = find_player("tal")
    checks = [0]

    def stop() -> bool:
        checks[0] += 1
        return checks[0] >= 3

    game, meta = _play_one(
        tmp_path, exe, a, b, 1, None, 12, 1,
        {"exe": "fake", a.slug: "a", b.slug: "b"},
        use_books=False, stop_check=stop)

    assert game.end().ply() == 2
    assert meta["status"] == "INCOMPLETE"
    assert meta["termination"] == "OperatorStop"
    pids = [int(line) for line in
            (tmp_path / "uci-child-pids.txt").read_text(encoding="ascii").splitlines()]
    assert len(pids) == 2

    def alive(pid: int) -> bool:
        # No shell/ps dependency. A reaped subprocess disappears from procfs.
        return Path(f"/proc/{pid}").exists()

    for _ in range(100):
        if not any(alive(pid) for pid in pids):
            break
        time.sleep(0.02)
    assert not any(alive(pid) for pid in pids), f"Orphan UCI processes: {pids}"
