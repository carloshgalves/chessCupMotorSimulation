"""Bounded in-memory replay journal for observed SSE checkpoints (#17).

No authoritative state is stored: clients must fetch /api/state for full PGN,
clock and legality. A server restart, evicted event, or unobserved mutation
always results in explicit snapshot resynchronization rather than false replay.
"""
from __future__ import annotations

from collections import OrderedDict, deque
import re
from secrets import token_hex
from threading import Lock

from .live_events import _changes, checkpoint

JOURNAL_SCHEMA = "chesssim-game-events-v2"
MAX_STREAMS = 16
MAX_EVENTS_PER_STREAM = 32
_EVENT_ID = re.compile(r"([a-f0-9]{16})\.([1-9][0-9]{0,15})\.([a-f0-9]{64})\Z")


class EventJournal:
    """Per-process, bounded, thread-safe checkpoint-notification history."""

    def __init__(self) -> None:
        self.epoch = token_hex(8)
        self._lock = Lock()
        self._next = 0
        self._streams: OrderedDict[str, deque[dict]] = OrderedDict()

    def observe(self, stream: str, report: dict, last_id: str = "") -> list[dict]:
        """Return observed changes after last_id, or one explicit resync.

        last_id is *only* accepted if the exact entry exists in the current
        epoch and same selection. It is not used as a path or authorization.
        """
        if not isinstance(stream, str) or not 1 <= len(stream) <= 96:
            raise ValueError("Stream key invalid")
        revision, payload = checkpoint(report)
        with self._lock:
            events = self._streams.get(stream)
            if events is None:
                if len(self._streams) >= MAX_STREAMS:
                    self._streams.popitem(last=False)
                events = deque(maxlen=MAX_EVENTS_PER_STREAM)
                self._streams[stream] = events
            else:
                self._streams.move_to_end(stream)
            if not events or events[-1]["revision"] != revision:
                prior = events[-1]["body"] if events else None
                self._next += 1
                ident = f"{self.epoch}.{self._next}.{revision}"
                events.append({
                    "id": ident, "revision": revision,
                    "sequence": self._next, "epoch": self.epoch,
                    "body": payload, "changes": _changes(prior, payload),
                })
            if not last_id:
                return [self._presentation(events[-1], resync=True)]
            valid = _EVENT_ID.fullmatch(last_id) if len(last_id) <= 112 else None
            if valid is not None and valid.group(1) == self.epoch:
                for idx, item in enumerate(events):
                    if item["id"] == last_id:
                        return [self._presentation(event, resync=False)
                                for event in list(events)[idx + 1:]]
            # Stale, evicted, different selection/epoch, or forged cursor.
            return [self._presentation(events[-1], resync=True)]

    @staticmethod
    def _presentation(entry: dict, *, resync: bool) -> dict:
        return {
            **entry["body"], "schema": JOURNAL_SCHEMA,
            "event_id": entry["id"], "revision": entry["revision"],
            "sequence": entry["sequence"], "epoch": entry["epoch"],
            "changes": ["snapshot"] if resync else list(entry["changes"]),
            "resync_required": resync,
            "replay_scope": "OBSERVED_CHECKPOINTS_ONLY",
        }
