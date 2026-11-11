"""Bounded, read-only SSE checkpoint notifications for live chess viewers.

This is a change feed, not an authoritative move bus: clients fetch /api/state
on notification. The full PGN/FEN and legality stay on the existing endpoint.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
import time

EVENT_SCHEMA = "chesssim-game-events-v1"
EVENT_POLL_SECONDS = 0.4
EVENT_STREAM_SECONDS = 8.0
EVENT_RETRY_MS = 900
MAX_EVENT_CLIENTS = 4
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def checkpoint(report: dict) -> tuple[str, dict]:
    """Stable fingerprint excluding volatile server_time and wall-clock estimates."""
    duel = report.get("duel") or {}
    games = []
    for game in duel.get("games", []):
        states = game.get("states") or []
        last = states[-1] if states else {}
        games.append({
            "id": game["id"],
            "ply": len(states) - 1,
            "uci": last.get("uci"),
            "turn": last.get("turn"),
            "clocks": last.get("clocks"),
            "status": game.get("status"),
            "result": game.get("result"),
            "termination": game.get("termination"),
            "live": game.get("live"),
        })
    tournament = report.get("tournament") or {}
    body = {
        "schema": EVENT_SCHEMA,
        "mode": report.get("mode"),
        "selection": report.get("selection"),
        "duel": {
            "folder": duel.get("folder"),
            "match_id": duel.get("match_id"),
            "status": duel.get("status"),
        },
        "tournament": {
            "status": tournament.get("status"),
            "matches_completed": tournament.get("matches_completed"),
            "matches_total_generated": tournament.get("matches_total_generated"),
            "stop_requested": tournament.get("stop_requested"),
        },
        "games": games,
    }
    # Hash the entire authoritative checkpoint (except volatile wall time).
    # A tournament bracket, terminal reason or metadata change must not be
    # missed merely because the current game's ply did not advance.
    stable_report = {k: v for k, v in report.items() if k != "server_time"}
    stable = json.dumps(stable_report, sort_keys=True, ensure_ascii=False,
                        allow_nan=False, separators=(",", ":")).encode("utf-8")
    digest = sha256(stable).hexdigest()
    return digest, body


def _changes(before: dict | None, after: dict) -> list[str]:
    """Describe detectable checkpoint classes, never invent missed intermediate moves."""
    if before is None:
        return ["snapshot"]
    result = []
    if (before["selection"] != after["selection"] or
            before["duel"]["folder"] != after["duel"]["folder"] or
            [x["id"] for x in before["games"]] !=
            [x["id"] for x in after["games"]]):
        result.append("session")
    old = {g["id"]: g for g in before["games"]}
    for game in after["games"]:
        prior = old.get(game["id"])
        if prior is None:
            continue
        if game["ply"] != prior["ply"] or game["uci"] != prior["uci"]:
            if "move" not in result:
                result.append("move")
        if game["clocks"] != prior["clocks"] and "clock" not in result:
            result.append("clock")
        if (game["status"] != prior["status"] or
                game["result"] != prior["result"] or
                game["termination"] != prior["termination"] or
                game["live"] != prior["live"]):
            if "terminal" not in result:
                result.append("terminal")
    if (before["duel"]["status"] != after["duel"]["status"] or
            before["tournament"] != after["tournament"]):
        if "terminal" not in result:
            result.append("terminal")
    return result or ["state"]


def _frame(event_id: str, body: dict, changes: list[str]) -> bytes:
    payload = {**body, "revision": event_id, "changes": changes}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":"))
    return (f"id: {event_id}\nevent: state\ndata: {encoded}\n\n").encode("utf-8")


def same_origin(handler) -> bool:
    # Same-origin requests from watch / Arena iframe are accepted; foreign
    # webpages cannot hold local SSE slots via a blind cross-site EventSource.
    origin = handler.headers.get("Origin")
    host = handler.headers.get("Host", "")
    site = handler.headers.get("Sec-Fetch-Site")
    return (not origin or origin == "http://" + host) and site in (
        None, "same-origin", "none")


def stream_events(handler, initial: dict, read_state) -> None:
    """Stream at most eight seconds with a per-server concurrent-client quota.

    Browser EventSource reconnects automatically; Last-Event-ID suppresses
    repeating the unchanged checkpoint. No engine, PGN or clock writes occur.
    """
    if not same_origin(handler):
        handler._reply(403, b"Forbidden", "text/plain; charset=utf-8")
        return
    slots = handler.server.event_stream_slots
    if not slots.acquire(blocking=False):
        handler._reply(429, b"Too many event viewers", "text/plain; charset=utf-8")
        return
    try:
        handler.send_response(200)
        for key, value in (
            ("Content-Type", "text/event-stream; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("Connection", "close"),
            ("X-Accel-Buffering", "no"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
        ):
            handler.send_header(key, value)
        handler.end_headers()
        handler.close_connection = True
        handler.wfile.write(f"retry: {EVENT_RETRY_MS}\n\n".encode("ascii"))
        last = handler.headers.get("Last-Event-ID", "")
        if not _DIGEST.fullmatch(last):
            last = ""
        previous = None
        end = time.monotonic() + EVENT_STREAM_SECONDS
        report = initial
        last_ping = time.monotonic()
        while time.monotonic() < end:
            eid, body = checkpoint(report)
            if eid != last:
                handler.wfile.write(_frame(eid, body, _changes(previous, body)))
                last = eid
                last_ping = time.monotonic()
            elif time.monotonic() - last_ping >= 2.0:
                handler.wfile.write(b": keepalive\n\n")
                last_ping = time.monotonic()
            handler.wfile.flush()
            time.sleep(EVENT_POLL_SECONDS)
            report = read_state()
            previous = body
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        pass
    except (ValueError, FileNotFoundError, OSError, TypeError, KeyError,
            json.JSONDecodeError):
        # The run might be moving an atomic checkpoint or become unavailable.
        # Never expose local paths or untrusted exception messages.
        try:
            handler.wfile.write(b"event: unavailable\ndata: {}\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
    finally:
        slots.release()


def stream_events_v2(handler, initial: dict, read_state, stream_key: str) -> None:
    """Bounded replay of observed checkpoints, never a complete move log.

    The browser's Last-Event-ID is an untrusted cursor. Unknown/expired IDs
    trigger a snapshot resync. The stream is read-only and uses the same
    cross-site and concurrency limits as the v1 endpoint.
    """
    if not same_origin(handler):
        handler._reply(403, b"Forbidden", "text/plain; charset=utf-8")
        return
    slots = handler.server.event_stream_slots
    if not slots.acquire(blocking=False):
        handler._reply(429, b"Too many event viewers", "text/plain; charset=utf-8")
        return
    try:
        handler.send_response(200)
        for key, value in (
            ("Content-Type", "text/event-stream; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("Connection", "close"),
            ("X-Accel-Buffering", "no"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
        ):
            handler.send_header(key, value)
        handler.end_headers()
        handler.close_connection = True
        handler.wfile.write(f"retry: {EVENT_RETRY_MS}\n\n".encode("ascii"))
        cursor = handler.headers.get("Last-Event-ID", "")
        if len(cursor) > 112:
            cursor = ""
        end = time.monotonic() + EVENT_STREAM_SECONDS
        last_ping = time.monotonic()
        state = initial
        while time.monotonic() < end:
            pending = handler.server.event_journal.observe(stream_key, state, cursor)
            for item in pending:
                encoded = json.dumps(item, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":"))
                frame = (f"id: {item['event_id']}\nevent: state\n"
                         f"data: {encoded}\n\n")
                handler.wfile.write(frame.encode("utf-8"))
                cursor = item["event_id"]
                last_ping = time.monotonic()
            if not pending and time.monotonic() - last_ping >= 2:
                handler.wfile.write(b": keepalive\n\n")
                last_ping = time.monotonic()
            handler.wfile.flush()
            time.sleep(EVENT_POLL_SECONDS)
            state = read_state()
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        pass
    except (ValueError, FileNotFoundError, OSError, TypeError, KeyError,
            json.JSONDecodeError):
        # Never disclose local source paths or untrusted parser content.
        try:
            handler.wfile.write(b"event: unavailable\ndata: {}\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
    finally:
        slots.release()
