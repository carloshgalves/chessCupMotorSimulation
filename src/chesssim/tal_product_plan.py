"""Full-source Mikhail Tal product-candidate inventory, without strength claims.

This stage reads every accepted legal game and every focal decision. It
pre-registers development-only roots before any Tal motor experiment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

import chess

from .catalog import find_player
from .fischer_baseline import phase
from .fischer_final_gate import features
from .fischer_full_games import atomic_json, digest
from .real85_neural_stream import neural_arm
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)

SCHEMA = "chesssim-tal-complete-source-and-product-plan-v1"
PLAYER = "tal"
MIN_ROOTS = 12
START_PLIES = 12
ROOT_SALT = "tal-product-development-openings-2026-11-v1"
STAGES = ("opening", "middlegame", "endgame")
FEATURES = ("forcing", "check", "pawn_move", "kingward")
ARMS = ("fit", "tune", "excluded_test")


def load_source(archive_dir=None):
    config = _player_configuration([PLAYER], None, None)[PLAYER]
    _, pgn, origin = _source_bytes(config["source"], archive_dir)
    rows, scan = _scan(pgn, PLAYER, config["surname"])
    if not rows or len(set(row["mainline_sha256"] for row in rows)) != len(rows):
        raise ValueError("Tal corpus absent or contains duplicate accepted trajectories")
    return pgn, rows, scan, origin, config["surname"]


def inventory_games(pgn, rows, surname):
    """Real replay: every accepted game/move, no per-player sampling."""
    by_arm = defaultdict(Counter)
    by_phase = defaultdict(Counter)
    style = defaultdict(Counter)
    by_year = Counter()
    roots = defaultdict(lambda: defaultdict(set))
    seen = set()
    for game, row in _iter_verified_games(pgn, rows, surname):
        gid = row["mainline_sha256"]
        arm = neural_arm(gid)
        if gid in seen or arm not in ARMS or row["color"] not in ("white", "black"):
            raise ValueError("Unknown split or historical game reused")
        seen.add(gid)
        by_arm[arm]["games"] += 1
        by_arm[arm][row["color"]] += 1
        by_year[str(row["date"])] += 1
        board = game.board()
        focal_is_white = row["color"] == "white"
        prefix = []
        recorded_moves = 0
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise ValueError("Invalid historical Tal move")
            if board.ply() < START_PLIES:
                prefix.append(move.uci())
            if board.turn == focal_is_white:
                stage = phase(board)
                by_arm[arm]["focal_decisions"] += 1
                if board.legal_moves.count() == 1:
                    by_arm[arm]["forced"] += 1
                    by_phase[(arm, stage)]["forced"] += 1
                elif board.legal_moves.count() > 1:
                    by_arm[arm]["nonforced"] += 1
                    by_phase[(arm, stage)]["nonforced"] += 1
                    measurements = features(board, move)
                    for kind in FEATURES:
                        style[(arm, stage)][kind] += int(measurements[kind])
                    style[(arm, stage)]["decisions"] += 1
                else:
                    raise ValueError("Historical game continues with no legal move")
            board.push(move)
            recorded_moves += 1
        if recorded_moves != row["plies"]:
            raise ValueError("Historical trajectory length mismatch")
        if game.board().fen() == chess.STARTING_FEN and len(prefix) == START_PLIES:
            root = tuple(prefix)
            roots[arm][root].add(gid)
            by_arm[arm]["eligible_root_games"] += 1
        else:
            by_arm[arm]["root_exclusions"] += 1
    if seen != {row["mainline_sha256"] for row in rows}:
        raise ValueError("Historical Tal scan does not reconcile with PGN replay")
    result = {"by_arm": {}, "by_phase": {}, "style": {}, "by_year": dict(by_year)}
    for arm in ARMS:
        if by_arm[arm]["focal_decisions"] != (
            by_arm[arm]["forced"] + by_arm[arm]["nonforced"]
        ):
            raise ValueError("Focal denominator mismatch")
        if by_arm[arm]["games"] != (
            by_arm[arm]["eligible_root_games"] + by_arm[arm]["root_exclusions"]
        ):
            raise ValueError("Opening coverage mismatch")
        result["by_arm"][arm] = {**dict(by_arm[arm]),
                                "distinct_12_ply_prefixes": len(roots[arm])}
        for stage in STAGES:
            key = f"{arm}|{stage}"
            result["by_phase"][key] = dict(by_phase[(arm, stage)])
            n = style[(arm, stage)]["decisions"]
            result["style"][key] = {
                "decisions": n,
                "provisional_frequencies": {
                    label: (style[(arm, stage)][label] / n if n else None)
                    for label in FEATURES
                },
            }
        if (sum(result["by_phase"][f"{arm}|{stage}"].get("nonforced", 0)
                for stage in STAGES) != result["by_arm"][arm]["nonforced"]):
            raise ValueError("Phase distribution omits eligible decisions")
    return result, roots


def choose_development_roots(roots, count=MIN_ROOTS):
    """Representative engineering starts only; not scientific strength claims."""
    if count != MIN_ROOTS:
        raise ValueError("Pilot root count is a preregistered protocol constant")
    ranked = []
    for prefix, source_ids in roots["fit"].items():
        h = sha256((ROOT_SALT + ":" + digest(list(prefix))).encode()).hexdigest()
        ranked.append((h, prefix, source_ids))
    ranked.sort(key=lambda item: (item[0], item[1]))
    if len(ranked) < count:
        raise ValueError("Insufficient distinct Tal FIT roots")
    return [{
        "id": digest(list(prefix)),
        "prefix_sha256": sha256(" ".join(prefix).encode()).hexdigest(),
        "source_games": len(ids),
        "rank": score,
    } for score, prefix, ids in ranked[:count]]


def plan(archive_dir=None):
    pgn, rows, scan, origin, surname = load_source(archive_dir)
    if find_player("tal").rodent_alias != "Tal":
        raise ValueError("Historical Tal profile not registered in roster")
    counts, roots = inventory_games(pgn, rows, surname)
    if not all(counts["by_arm"][arm]["games"] > 0 for arm in ARMS):
        raise ValueError("Missing one full-game partition")
    selected = choose_development_roots(roots)
    if len({x["id"] for x in selected}) != MIN_ROOTS:
        raise ValueError("Development roots are not distinct")
    return {
        "schema": SCHEMA,
        "status": "FULL_SOURCE_INVENTORY_ONLY_STRENGTH_NOT_TESTED",
        "player": PLAYER,
        "roster_alias": "Tal",
        "source": origin,
        "source_snapshot": MIRROR_COMMIT if archive_dir is None else None,
        "pgn_sha256": sha256(pgn).hexdigest(),
        "accepted_games": len(rows),
        "scan": scan,
        "arms": counts["by_arm"],
        "phases": counts["by_phase"],
        "observed_historical_move_preferences": counts["style"],
        "years": counts["by_year"],
        "development_roots_12_ply": selected,
        "pilot_clock_games_planned": len(selected) * 2,
        "validation_test_games_opened_for_training": 0,
        "reference_depth": 16,
        "competitive_rating_or_elo": None,
        "frozen": False,
        "fischer_asset_untouched": True,
        "limitations": [
            "Full historical source scanned; no Tal engine UCI games run",
            "Style rates describe Tal's actual historical decisions, not motor behavior",
            "Twelve opening roots are engineering smoke tests, not a power or Elo estimate",
            "Original historical TEST may have been inspected in previous pilot work",
            "No player-specific strength, six-attribute or statistical equivalence claim",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.fetch == (args.archive_dir is not None):
        raise ValueError("Select exactly one pinned source")
    payload = plan(args.archive_dir)
    if args.out is not None:
        if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
            raise ValueError("Refuse to overwrite inventory")
        atomic_json(args.out, payload)
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
