"""Stage-boundary and cooperative-stop checkpoints for CP measurements."""
import json
import os
from pathlib import Path
import signal
import tempfile

from . import bilateral_cp as cp


class Paused(Exception):
    """A cooperative stop was requested."""


class Checkpoints:
    def __init__(self, directory, stop_file, identity):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stop_file = Path(stop_file) if stop_file else None
        self.identity = identity
        self.stopping = False

    def request_stop(self, *_):
        self.stopping = True

    def install_signals(self):
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def stopped(self):
        return self.stopping or (self.stop_file is not None and self.stop_file.exists())

    def write(self, path, doc):
        fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(doc, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            dfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def measure(self, stage, ref, engines, cases, panel, profiles, measure=None, verify=None):
        measure = measure or cp.measure
        verify = verify or cp.verify_rows
        path = self.directory / (stage + ".json")
        binding = cp.digest([self.identity, stage, cases, panel, profiles])
        rows = []
        saved_count = -1
        if path.exists():
            saved = cp.load(path)
            if saved["binding"] != binding:
                raise ValueError("Checkpoint identity mismatch: " + stage)
            rows = saved["rows"]
            saved_count = len(rows)
            if saved_count > len(panel):
                raise ValueError("Checkpoint exceeds panel")
            verify(rows, panel[:saved_count], cases)
        def save():
            self.write(path, {"binding": binding, "rows": rows})
        if self.stopped():
            save()
            raise Paused(stage)
        for pos in panel[len(rows):]:
            rows.extend(measure(ref, engines, cases, [pos]))
            if self.stopped():
                save()
                print("CP_PAUSED", stage, len(rows), len(panel), flush=True)
                raise Paused(stage)
        verify(rows, panel, cases)
        if saved_count != len(rows):
            save()
        return rows
