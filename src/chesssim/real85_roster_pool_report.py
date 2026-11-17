"""Verified training-input census for the full historic roster.

This is a manifest over genuine FIT/TUNE PGN trajectories, not a trained
chess engine, a rights grant, a complete historical archive or a style score.
The TEST arm stays excluded from the serialized move stream.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .catalog import load_roster
from .real85_neural_stream import SCHEMA as STREAM_SCHEMA, verify as verify_stream

SCHEMA = "chesssim-real85-roster-training-pool-census-v1"
ARMS = ("fit", "tune")


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()


def census(root: Path, *, require_full_roster: bool = True) -> dict:
    """Validate *every* actual shard before counting training-game identities.

    The source files themselves are deliberately not included in the report.
    Same historical game found in both players' archives counts once globally,
    but still supplies each historical side's own training examples.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or (root / "manifest.json").is_symlink():
        raise ValueError("Unsafe neural training corpus directory")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != STREAM_SCHEMA or manifest.get("excluded_test_games_serialized") != 0:
        raise ValueError("Source manifest violates sealed TEST exclusion")
    expected = {p.slug for p in load_roster()}
    supplied = set(manifest.get("players", {}))
    if require_full_roster and supplied != expected:
        raise ValueError("The 64-player pool must classify the entire roster")
    if not supplied or not supplied <= expected:
        raise ValueError("Unknown or empty historical roster selection")
    checked = verify_stream(root)
    if checked["status"] not in ("VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES",
                                 "PARTIAL_REAL_INPUT_VERIFICATION",
                                 "NO_REAL_INPUT_SHARDS"):
        raise ValueError("Failed full-trajectory verification")
    all_game_owners: dict[str, set[str]] = defaultdict(set)
    by_arm: dict[str, set[str]] = {arm: set() for arm in ARMS}
    reported = {}
    incomplete = []
    total_focal_decisions = 0
    for slug in sorted(supplied):
        row = manifest["players"][slug]
        state = row.get("status")
        if state == "SOURCE_MISSING":
            if row.get("reason") not in ("HTTP_404", "LOCAL_SOURCE_MISSING"):
                raise ValueError("Unclassified missing historical source")
            reported[slug] = {"status": state, "reason": row["reason"]}
            incomplete.append(slug)
            continue
        if state == "NO_REAL_ELIGIBLE_GAMES":
            reported[slug] = {"status": state}
            incomplete.append(slug)
            continue
        if state not in ("READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING",
                         "INSUFFICIENT_INTRA_TRAINING_COVERAGE"):
            raise ValueError("Unknown historical input state")
        training_games = 0
        training_decisions = 0
        distinct_openings: set[tuple[str, ...]] = set()
        proxy = Counter()
        for arm in ARMS:
            path = root / row["shards"][arm]["file"]
            shard_hash = sha256()
            with path.open("rb") as handle:
                for line in handle:
                    shard_hash.update(line)
                    game = json.loads(line)
                    identity = game["game_sha256"]
                    all_game_owners[identity].add(slug)
                    by_arm[arm].add(identity)
                    distinct_openings.add(tuple(game["uci_moves"][:12]))
                    focal_white = game["focal_color"] == "white"
                    for i in range(len(game["uci_moves"])):
                        if i % 2 == (0 if focal_white else 1):
                            proxy["opening_to_ply20"] += int(i < 20)
                            proxy["middle_ply21_to60"] += int(20 <= i < 60)
                            proxy["late_from_ply61"] += int(i >= 60)
                    training_games += 1
                    training_decisions += game["focal_decisions"]
            # The second counting pass must independently bind its exact bytes
            # to the authenticated source manifest, not just trust verify().
            if shard_hash.hexdigest() != row["shards"][arm]["sha256"]:
                raise ValueError("Historical trajectory changed after verification")
        expected_games = row["fit_games"] + row["tune_games"]
        expected_decisions = (row["fit_focal_decisions_all_phases"] +
                              row["tune_focal_decisions_all_phases"])
        actual = checked["players"].get(slug)
        if (not actual or training_games != expected_games or
                training_decisions != expected_decisions or
                actual != {"games": training_games, "focal_decisions": training_decisions} or
                sum(proxy.values()) != training_decisions):
            raise ValueError("Focal decisions or global census disagree with legal-game audit")
        reported[slug] = {
            "status": state, "accepted_distinct_source_games": row["accepted_distinct_games"],
            "fit_games": row["fit_games"], "tune_games": row["tune_games"],
            "excluded_test_games_not_serialized": row["excluded_test_games"],
            "fit_focal_decisions": row["fit_focal_decisions_all_phases"],
            "tune_focal_decisions": row["tune_focal_decisions_all_phases"],
            "source_pgn_sha256": row["source_pgn_sha256"],
            "unique_training_openings_first12": len(distinct_openings),
            "training_ply_phase_proxies": dict(sorted(proxy.items())),
        }
        total_focal_decisions += training_decisions
        if state != "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING":
            incomplete.append(slug)
    if by_arm["fit"] & by_arm["tune"]:
        raise ValueError("Global FIT/TUNE overlap between player archives")
    if not all_game_owners:
        raise ValueError("No actual historical games were verified")
    if require_full_roster and any(
            reported[p]["status"] != "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING"
            for p in ("fischer", "tal", "karpov", "kasparov")):
        raise ValueError("All four original pilots must retain complete real training input")
    census_report = {
        "schema": SCHEMA,
        "status": "VERIFIED_HISTORICAL_TRAINING_INPUTS_NOT_TRAINED_OR_STYLE_VALIDATED",
        "roster_size": len(supplied),
        "source_present": sum(reported[p]["status"] != "SOURCE_MISSING" for p in reported),
        "ready_for_training": sum(reported[p]["status"] ==
                                  "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING" for p in reported),
        "missing_or_insufficient": sorted(incomplete),
        "global_distinct_fit_games": len(by_arm["fit"]),
        "global_distinct_tune_games": len(by_arm["tune"]),
        "global_unique_training_game_trajectories": len(all_game_owners),
        "games_occurring_in_multiple_player_sources": sum(
            len(owners) > 1 for owners in all_game_owners.values()),
        "total_focal_training_decisions_across_profiles": total_focal_decisions,
        "input_manifest_sha256": sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "training_protocol": manifest["training_game_protocol"],
        "excluded_test_trajectories_exported": 0,
        "holdout_used_for_training_or_tuning": False,
        "neural_backbone_finetuned": False,
        "raw_pgn_redistributed": False,
        "players": reported,
        "limitations": [
            "PGN Mentor mirror is an incomplete historical source; missing archives remain missing.",
            "The ply phase buckets are proxies, not a chess-engine endgame classification.",
            "Global unique trajectories are not independent opponent samples.",
            "The corpus census does not demonstrate human Elo or historic personality fidelity.",
        ],
    }
    census_report["report_sha256"] = _digest(census_report)
    return census_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate real85 corpus and count all roster games")
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--allow-partial-roster", action="store_true")
    args = parser.parse_args(argv)
    result = census(args.inputs, require_full_roster=not args.allow_partial_roster)
    if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
        raise ValueError("Refuse to overwrite a corpus census")
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "roster_size", "source_present", "ready_for_training",
        "global_unique_training_game_trajectories",
        "total_focal_training_decisions_across_profiles",
        "missing_or_insufficient", "report_sha256")}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
