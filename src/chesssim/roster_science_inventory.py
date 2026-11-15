"""Auditable, real 64-player PGN provenance inventory, never a style claim."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from .catalog import load_roster
from .real_gate_v4 import _position_key
from .real_policy_experiment import _digest, _write_once
from .real_training_cohorts import (
    MIRROR_COMMIT, _assign, _career_band, _focal_decisions,
    _iter_verified_games, _player_configuration, _scan, _source_bytes,
)

SCHEMA = "chesssim-real-roster-64-source-inventory-v1"
MIN_INDEPENDENT_GAMES = 100
MIN_PLY = 21


def _summarize(rows: list[dict], partitions: dict) -> dict:
    years = sorted({x["date"] for x in rows if x["date"] is not None})
    bands = Counter()
    middle = late = 0
    for x in rows:
        if x["date"] is not None:
            bands[_career_band(x["date"], years)] += 1
        first = MIN_PLY if x["color"] == "white" else MIN_PLY + 1
        if x["plies"] >= first:
            middle += len(range(first, min(x["plies"], 60) + 1, 2))
        end_first = 61 if x["color"] == "white" else 62
        if x["plies"] >= end_first:
            late += len(range(end_first, x["plies"] + 1, 2))
    enough = (partitions["validation"]["games"] >= MIN_INDEPENDENT_GAMES
              and partitions["holdout"]["games"] >= MIN_INDEPENDENT_GAMES)
    return {
        "accepted_distinct_trajectories": len(rows),
        "observed_years": years,
        "unknown_year_games": sum(x["date"] is None for x in rows),
        "career_band_games": dict(sorted(bands.items())),
        "whole_year_partitions": partitions,
        "phase_proxy_focal_decisions": {"middle_ply_21_to_60": middle,
                                        "late_ply_61_plus": late},
        "sufficiency": ("CANDIDATE_NOT_STYLE_VALIDATED" if enough else
                        "UNDERPOWERED_INDEPENDENT_EVALUATION"),
    }


def build_inventory(out: Path, slugs: list[str], *,
                    archive_dir: Path | None = None,
                    position_audit: bool = False) -> dict:
    cfg = _player_configuration(slugs, None, None)
    entries = {}
    game_owners = {}
    cross_arm_trajectory = 0
    with TemporaryDirectory(prefix="chesssim-inventory-") as directory:
        db = sqlite3.connect(str(Path(directory) / "seen.sqlite"))
        try:
            db.execute("CREATE TABLE fen (hash TEXT PRIMARY KEY, owner TEXT NOT NULL)")
            for slug in slugs:
                info = cfg[slug]
                if archive_dir is not None and not any(
                    (archive_dir / (info["source"] + ext)).is_file()
                    for ext in (".pgn", ".zip")
                ):
                    entries[slug] = {
                        "name": info["name"], "source": info["source"] + ".pgn",
                        "status": "LOCAL_SOURCE_NOT_SUPPLIED", "pgn_sha256": None}
                    continue
                try:
                    archive, pgn, origin = _source_bytes(info["source"], archive_dir)
                except HTTPError as error:
                    if archive_dir is not None or error.code != 404:
                        raise
                    entries[slug] = {
                        "name": info["name"], "source": info["source"] + ".pgn",
                        "status": "SOURCE_MISSING_HTTP_404", "pgn_sha256": None}
                    continue
                rows, counters = _scan(pgn, slug, info["surname"])
                partitions, quarantined = _assign(rows, "career-stratified")
                for row in rows:
                    owner = game_owners.setdefault(
                        row["mainline_sha256"], (slug, row["arm"]))
                    if owner[0] != slug and owner[1] != row["arm"]:
                        cross_arm_trajectory += 1
                transpositions = "NOT_MEASURED"
                if position_audit:
                    counts = Counter()
                    for game, meta in _iter_verified_games(pgn, rows, info["surname"]):
                        if meta["arm"] != "train":
                            continue
                        for _ply, fen, _move in _focal_decisions(
                            game, meta["color"], MIN_PLY
                        ):
                            key = sha256(_position_key(fen).encode()).hexdigest()
                            owner = db.execute(
                                "SELECT owner FROM fen WHERE hash=?", (key,)).fetchone()
                            if owner is None:
                                db.execute(
                                    "INSERT INTO fen (hash,owner) VALUES (?,?)",
                                    (key, slug))
                                counts["first_observation"] += 1
                            elif owner[0] == slug:
                                counts["repeat_within_player"] += 1
                            else:
                                counts["repeat_across_players"] += 1
                            counts["train_only_focal_positions"] += 1
                    db.commit()
                    transpositions = dict(counts)
                entries[slug] = {
                    "name": info["name"], "source": info["source"] + ".pgn",
                    "status": "SOURCE_PRESENT", "source_snapshot": MIRROR_COMMIT,
                    "source_origin": origin,
                    "archive_sha256": sha256(archive).hexdigest(),
                    "pgn_sha256": sha256(pgn).hexdigest(),
                    "parse_diagnostics": counters,
                    "quarantined_sparse_bands": quarantined,
                    "train_only_canonical_fen_repetitions": transpositions,
                    **_summarize(rows, partitions)}
        finally:
            db.close()
    if len(entries) != len(slugs):
        raise ValueError("Incomplete player classifications")
    result = {
        "schema": SCHEMA,
        "source_snapshot": MIRROR_COMMIT,
        "partition_policy": "career-stratified",
        "player_count": len(entries),
        "source_present": sum(x["status"] == "SOURCE_PRESENT"
                              for x in entries.values()),
        "missing_404": sorted(k for k,v in entries.items()
                              if v["status"] == "SOURCE_MISSING_HTTP_404"),
        "unique_global_game_trajectories": len(game_owners),
        "shared_game_cross_arm_occurrences": cross_arm_trajectory,
        "position_audit_train_only": position_audit,
        "players": entries,
        "holdout_predictions_scored": False,
        "model_promotion": "DENIED",
        "limitations": [
            "Middle/end phase is a ply proxy, not chess-position assessment.",
            "Repeated canonical FEN is not demonstrated tactical transposition.",
            "Cross-player game overlaps need global embargo before any fitting.",
            "Source coverage never establishes individual historical fidelity.",
        ]}
    result["report_sha256"] = _digest(result)
    _write_once(out, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="64 real-player PGN provenance inventory")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all-roster", action="store_true")
    group.add_argument("--players", nargs="+")
    parser.add_argument("--position-audit", action="store_true")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    roster = [p.slug for p in load_roster()]
    slugs = roster if args.all_roster or not args.players else args.players
    result = build_inventory(args.out, slugs, archive_dir=args.archive_dir,
                             position_audit=args.position_audit)
    print(json.dumps({
        "source_present": result["source_present"], "missing_404": result["missing_404"],
        "entries": result["player_count"], "report_sha256": result["report_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
