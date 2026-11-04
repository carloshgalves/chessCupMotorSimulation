"""Cliente UCI mínimo para PersonalityFile oculto no GUI do Rodent.

Sem mexer em basic.ini, sem APIs privadas do python-chess. Um processo por perfil
é reiniciado entre candidatos para não contaminar o estado da busca.
"""
from __future__ import annotations

from pathlib import Path
import queue
import subprocess
import threading
import time


class UciFailure(RuntimeError):
    pass


class RodentUci:
    def __init__(self, executable: Path, root: Path, *, profile: str, book: bool = False,
                 threads: int = 1, hash_mb: int = 64, timeout: float = 45.0):
        self.timeout = timeout
        self.proc = subprocess.Popen([str(executable)], cwd=str(root), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1, encoding="utf-8", errors="replace")
        self.lines: queue.Queue[str | None] = queue.Queue()

        def reader():
            try:
                assert self.proc.stdout is not None
                for line in self.proc.stdout:
                    self.lines.put(line.strip())
            finally:
                self.lines.put(None)

        threading.Thread(target=reader, daemon=True).start()
        try:
            self.send("uci")
            self.wait_for("uciok")
            self.send("setoption name Verbose value true")
            self.send(f"setoption name PersonalityFile value {profile}")
            self.send(f"setoption name Threads value {threads}")
            self.send(f"setoption name Hash value {hash_mb}")
            self.send("setoption name UCI_LimitStrength value false")
            self.send(f"setoption name UseBook value {'true' if book else 'false'}")
            self.send("setoption name Ponder value false")
            self.send("isready")
            info = self.wait_for("readyok", profile=profile)
            if not any("reading personality" in ln and "(success)" in ln and profile in ln for ln in info):
                raise UciFailure(f"Rodent não confirmou carregamento de PersonalityFile: {profile} ({info[-8:]})")
        except BaseException:
            self.close()
            raise

    def send(self, line: str) -> None:
        if self.proc.poll() is not None:
            raise UciFailure(f"Rodent finalizou prematuramente: code={self.proc.returncode}")
        assert self.proc.stdin is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def wait_for(self, word: str, *, profile: str = "") -> list[str]:
        until = time.monotonic() + self.timeout
        collected = []
        while time.monotonic() < until:
            try:
                line = self.lines.get(timeout=max(0.01, until - time.monotonic()))
            except queue.Empty as exc:
                raise UciFailure(f"Timeout esperando {word} ({profile})") from exc
            if line is None:
                raise UciFailure(f"Rodent encerrou antes de {word} ({profile})")
            collected.append(line)
            if line.startswith(word):
                return collected
        raise UciFailure(f"Timeout esperando {word} ({profile})")

    def bestmove(self, fen: str, depth: int):
        self.send(f"position fen {fen}")
        self.send(f"go depth {depth}")
        lines = self.wait_for("bestmove")
        parts = lines[-1].split()
        if len(parts) < 2 or parts[1] in ("(none)", "0000"):
            raise UciFailure(f"Sem melhor lance: {lines[-1]}")
        return parts[1], lines

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.send("quit")
                self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired, UciFailure):
                self.proc.kill()
                self.proc.wait(timeout=5)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if pipe:
                pipe.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
