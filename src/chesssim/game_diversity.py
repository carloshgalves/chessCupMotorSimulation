"""Canonical game identity and independent-observation accounting.

PGN header changes must never manufacture new evidence. A move sequence
from a different starting position, however, is a distinct chess trajectory.
No engines, PGN datasets or external resources are bundled here.
"""
from __future__ import annotations

from hashlib import sha256
import json


SCHEMA = "chesssim-game-diversity-v1"


def mainline_identity(game) -> tuple[str, int]:
    """Hash the starting position plus *all* mainline UCI moves, not PGN headers.

    Using JSON with explicit keys and canonical separators prevents ambiguous
    concatenations. Initial FEN is needed for SetUp/FEN PGNs; the sequence
    alone would misidentify unrelated trajectories as duplicates.
    """
    moves = [move.uci() for move in game.mainline_moves()]
    payload = {
        "initial_fen": game.board().fen(),
        "moves": moves,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest(), len(moves)


def audit_mainlines(entries: list[dict]) -> tuple[list[dict], dict]:
    """Annotate input-order game records, excluding repetitions from evidence.

    Empty/zero-ply PGNs remain visible as raw records but cannot contribute
    independent observations. Incomplete games with moves can contribute
    *descriptive move statistics*, not completed-game result estimates.
    """
    first = {}
    annotated = []
    duplicates = []
    sources = {"opening_seeds": set(), "opening_modes": set(), "color_orders": set()}
    for index, row in enumerate(entries):
        digest = row.get("mainline_sha256")
        plies = row.get("plies")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"Invalid mainline SHA-256 at game {index + 1}")
        if type(plies) is not int or plies < 0:
            raise ValueError(f"Invalid ply count at game {index + 1}")
        source = row.get("source", index + 1)
        is_duplicate = plies > 0 and digest in first
        eligible = plies > 0 and not is_duplicate
        previously = first.get(digest)
        if eligible:
            first[digest] = source
        output = dict(row)
        output.update({
            "duplicate_mainline": is_duplicate,
            "first_occurrence": previously if is_duplicate else None,
            "eligible_for_independent_evidence": eligible,
            "independence_reason": (
                "DUPLICATE_MAINLINE" if is_duplicate else
                "NO_MOVES" if plies == 0 else
                "DISTINCT_MAINLINE"
            ),
        })
        if is_duplicate:
            duplicates.append({
                "source": source, "first_occurrence": previously,
                "mainline_sha256": digest,
            })
        annotated.append(output)
        if row.get("opening_seed") is not None:
            if type(row["opening_seed"]) is not int:
                raise ValueError(f"Invalid opening seed at game {index + 1}")
            sources["opening_seeds"].add(row["opening_seed"])
        if row.get("opening_mode"):
            sources["opening_modes"].add(str(row["opening_mode"]))
        if row.get("color_order"):
            sources["color_orders"].add(str(row["color_order"]))
    total = len(entries)
    distinct = sum(row["eligible_for_independent_evidence"] for row in annotated)
    report = {
        "schema": SCHEMA,
        "games_total": total,
        "games_with_moves": sum(row["plies"] > 0 for row in annotated),
        "games_distinct": distinct,
        "games_duplicate": len(duplicates),
        "games_without_moves": sum(row["plies"] == 0 for row in annotated),
        "effective_independent_games": distinct,
        "duplicate_references": duplicates,
        "variation_sources": {
            "historical_opening_seeds": sorted(sources["opening_seeds"]),
            "opening_modes": sorted(sources["opening_modes"]),
            "color_orders": sorted(sources["color_orders"]),
            "warning": (
                "A historical opening seed controls the external position-conditioned "
                "opening policy only when enabled. UCI engine search and built-in "
                "engine books may also vary; a different seed does not prove a "
                "different game or an independent observation."
            ),
        },
        "warning": (
            "Only distinct, non-empty trajectories count as independent move evidence. "
            "Incomplete games can contribute descriptive moves, not completed-game "
            "outcomes; exact duplicates are retained in raw PGNs but excluded from "
            "independent game-level estimates."
        ),
    }
    return annotated, report
