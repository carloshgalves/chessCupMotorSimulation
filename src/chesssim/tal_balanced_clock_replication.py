"""External-to-TUNE replication of Tal Balanced's real 10+2 improvement.

No edits to original Tal, Rodent, Simple or frozen Fischer. No human Elo
assertions. New FIT roots exclude all previous FIT experiment root prefixes.
The style gate was observed on TUNE; it is NOT rerun or retuned here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import random

from .fischer_final_gate import (
    SCHEMA as CLOCK_SCHEMA, play_clock, validate_clock,
)
from .fischer_full_games import atomic_json, digest, load_json
from .matches import _open_engine, file_hash
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _iter_verified_games
from .tal_balanced_validation import _profiles
from .tal_full_games import source_panel as initial_panel, frozen_fischer
from .tal_product_plan import load_source
from .tal_strength_doe import (
    TAL_ORIGINAL_SHA, design as doe_design, make_isolated_profiles,
)

SCHEMA = "chesssim-tal-balanced-unseen-fit-10plus2-v1"
PRIOR_STYLE_RUN = 45216462418
PRIOR_DOE_RUN = 45197012079
HISTORICAL_SHA = "586aac4613e170474f12893679ab992d3999ca5109df7df77c634a76846d2687"
BALANCED_SHA = "c0f291047023a6f936fd337f11530e6fe886ca0f4f157a5d2f838086857c0897"
ROOT_SALT = "tal-balanced-fresh-fit-clock-2026-11-24-v1"
ROOTS = 48
SHARDS = 24
CLOCK = [600, 2]
GAME_TYPES = ("original_vs_simple", "balanced_vs_simple", "balanced_vs_original")
MAX_ROOT_CI_ITERATIONS = 3000


def fixed_roots(archive_dir=None):
    """Choose FIT-only starts not used by ANY previously scored Tal FIT game."""
    prior, graded = initial_panel(archive_dir)
    doe, prior_doe = doe_design(archive_dir)
    previous = {
        tuple(x["prefix"]) for x in prior if x["arm"] == "fit"
    } | {tuple(x["prefix"]) for x in doe}
    tune_prefixes = {tuple(x["prefix"]) for x in prior if x["arm"] == "tune"}
    pgn, rows, _, origin, surname = load_source(archive_dir)
    if len(rows) != 2431 or sha256(pgn).hexdigest() != HISTORICAL_SHA:
        raise ValueError("Historical Tal source SHA or census drift")
    training = [r for r in rows if neural_arm(r["mainline_sha256"]) == "fit"]
    if len(training) != 2024 or graded["reserved_test_games"] != 341:
        raise ValueError("FIT source population or excluded TEST has changed")
    grouped = defaultdict(list)
    seen = set()
    for game, row in _iter_verified_games(pgn, training, surname):
        gid = row["mainline_sha256"]
        if gid in seen:
            raise ValueError("Duplicate FIT historical game")
        seen.add(gid)
        board = game.board()
        if board.fen() != "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1":
            # No guessing a standard 12-ply opening from a nonstandard FEN.
            continue
        prefix = []
        for move in game.mainline_moves():
            if len(prefix) == 12:
                break
            if move not in board.legal_moves:
                raise ValueError("Illegal historical FIT opening")
            prefix.append(move.uci())
            board.push(move)
        if len(prefix) != 12 or board.is_game_over(claim_draw=True):
            continue
        key = tuple(prefix)
        if key in previous or key in tune_prefixes:
            continue
        grouped[key].append(gid)
    if seen != {r["mainline_sha256"] for r in training}:
        raise ValueError("FIT historical games missing from source replay")
    ranked = sorted(grouped, key=lambda k: (
        sha256((ROOT_SALT + ":" + digest(list(k))).encode()).hexdigest(), k
    ))
    if len(ranked) < ROOTS:
        raise ValueError("Insufficient fresh and nonoverlapping FIT openings")
    selected = [{
        "id": digest([SCHEMA, list(prefix)]), "prefix": list(prefix),
        "source_games": sorted(grouped[prefix]), "arm": "fit",
    } for prefix in ranked[:ROOTS]]
    if len({tuple(x["prefix"]) for x in selected}) != ROOTS:
        raise ValueError("Duplicated Tal prospective FIT root")
    meta = {
        "schema": SCHEMA, "source": origin,
        "pgn_sha256": sha256(pgn).hexdigest(),
        "prior_doe_run": PRIOR_DOE_RUN,
        "prior_style_run": PRIOR_STYLE_RUN,
        "previous_fit_roots": len(previous),
        "tune_roots_excluded": len(tune_prefixes),
        "fresh_fit_frame": len(grouped),
        "roots_sha256": digest(selected), "roots": ROOTS,
        "shards": SHARDS, "clock": CLOCK, "game_types": list(GAME_TYPES),
        "games_planned": ROOTS * 2 * len(GAME_TYPES),
        "candidate_sha256": BALANCED_SHA,
        "original_tal_sha256": TAL_ORIGINAL_SHA,
        "fischer_frozen": frozen_fischer(),
        "test_games_scored": 0, "tune_games_scored": 0,
        "original_profiles_modified": False, "tal_frozen": False,
        "human_elo_estimate": None,
        "limitations": [
            "New FIT openings; no outer-TEST or retrospective-TUNE scoring",
            "Strength is relative to Rodent Simple, not human Elo",
            "The style gate was assessed previously on nonblind TUNE",
            "Product asset release needs explicit maintainer signoff",
        ],
    }
    return selected, meta


def matchups(roots):
    for root in roots:
        for kind in GAME_TYPES:
            for color in ("white", "black"):
                yield {
                    "id": digest([SCHEMA, root["id"], kind, color]),
                    "root": root["id"], "kind": kind,
                    "color": color, "time_control": CLOCK,
                    "depth": 16,
                }


def cases_by_shard(roots, index):
    if type(index) is not int or not 0 <= index < SHARDS:
        raise ValueError("Unknown Tal fresh-FIT shard index")
    sorted_roots = sorted(roots, key=lambda x: x["id"])
    return list(matchups([r for i, r in enumerate(sorted_roots)
                          if i % SHARDS == index]))


def plan(archive_dir=None):
    roots, meta = fixed_roots(archive_dir)
    counts = [len(cases_by_shard(roots, i)) for i in range(SHARDS)]
    if counts != [12] * SHARDS or sum(counts) != meta["games_planned"]:
        raise ValueError("Unbalanced Tal replication experiment")
    return {**meta, "cases_per_shard": counts,
            "status": "PREDECLARED_FIT_CLOCK_REPLICATION_NOT_EXECUTED"}


def roles(kind):
    pairing = {
        "original_vs_simple": ("original16", "simple16"),
        "balanced_vs_simple": ("balanced16", "simple16"),
        "balanced_vs_original": ("balanced16", "original16"),
    }
    if kind not in pairing:
        raise ValueError("Unknown experimental pairing")
    return pairing[kind]


def _game(case, root, engines):
    focal, opponent = roles(case["kind"])
    raw = play_clock(case, root, engines[focal], engines[opponent])
    validate_clock(raw, case, root)
    return raw


def validate_game(doc, case, root, binding):
    focal, opponent = roles(case["kind"])
    if (doc.get("schema") != SCHEMA or doc.get("case") != case or
            doc.get("manifest_sha256") != binding or
            doc.get("slot_mapping") != {"fischer": focal, "simple": opponent}):
        raise ValueError("Tampered case, pairing or SHA-bound checkpoint")
    if not isinstance(doc.get("game"), dict):
        raise ValueError("Missing full chess game")
    if doc["game"].get("schema") != CLOCK_SCHEMA:
        raise ValueError("Unrecognized source-independent 10+2 game schema")
    return validate_clock(doc["game"], case, root)


def run_shard(rodent_root, out, index, archive_dir=None):
    roots, source = fixed_roots(archive_dir)
    cases = cases_by_shard(roots, index)
    roots_by_id = {r["id"]: r for r in roots}
    destination = Path(out)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Do not overwrite scientific checkpoint directory")
    overlay, originals, variants = make_isolated_profiles(
        rodent_root, destination.parent / f"tal-new-fit-overlay-{index}"
    )
    if variants.get("TalBalanced") != BALANCED_SHA:
        raise ValueError("The qualified Tal Balanced profile candidate changed")
    frozen = source["fischer_frozen"]["hashes"]
    if (originals["rodent"] != frozen["rodent"] or
            originals["simple"] != frozen["simple_control"] or
            originals["fischer"] != frozen["fischer_profile"] or
            originals["tal"] != TAL_ORIGINAL_SHA):
        raise ValueError("Pinned source or frozen Fischer artifact changed")
    fingerprints = {
        "originals": originals, "variants": variants,
        "protocol_sha256": file_hash(Path(__file__)),
    }
    manifest = {
        "schema": SCHEMA, "source": source, "index": index,
        "case_ids": [c["id"] for c in cases], "fingerprints": fingerprints,
    }
    destination.mkdir(parents=False)
    atomic_json(destination / "manifest.json", manifest)
    binding = digest(manifest)
    with ExitStack() as stack:
        engines = {
            name: stack.enter_context(_open_engine(
                overlay, overlay / "rodent-iv-plain.exe", player, book=False))
            for name, player in _profiles().items()
        }
        for n, case in enumerate(cases, 1):
            focal, opponent = roles(case["kind"])
            doc = {
                "schema": SCHEMA, "case": case,
                "slot_mapping": {"fischer": focal, "simple": opponent},
                "manifest_sha256": binding,
                "game": _game(case, roots_by_id[case["root"]], engines),
            }
            validate_game(doc, case, roots_by_id[case["root"]], binding)
            atomic_json(destination / (case["id"] + ".json"), doc)
            print("TAL_FRESH_CLOCK", index, n, len(cases),
                  doc["game"]["status"], flush=True)
    if variants.get("TalBalanced") != file_hash(
            overlay / "personalities/talbalanced.txt"):
        raise ValueError("Candidate mutated while running")
    from .tal_full_games import _profiles as original_paths
    _, _, _, _, all_paths, actual = original_paths(rodent_root)
    if originals != actual or originals != {key: file_hash(file)
                                             for key, file in all_paths.items()}:
        raise ValueError("Original Tal, Fischer or control assets mutated")
    return {"schema": SCHEMA, "index": index, "games": len(cases)}


def _points(result, color):
    if result == "1/2-1/2":
        return 0.5
    if result not in ("1-0", "0-1"):
        raise ValueError("Attempted to score a nonterminal game")
    return float((result == "1-0") == (color == "white"))


def ci_cluster(groups):
    if len(groups) < 24 or any(len(x) != 2 for x in groups.values()):
        return None
    rng = random.Random(2026112401)
    blocks = list(groups.values())
    vals = []
    for _ in range(MAX_ROOT_CI_ITERATIONS):
        picked = rng.choices(blocks, k=len(blocks))
        vals.append(sum(map(sum, picked)) / (2 * len(blocks)))
    vals.sort()
    return [vals[int(.025 * len(vals))], vals[int(.975 * len(vals)) - 1]]


def aggregate(shards, archive_dir=None):
    roots, meta = fixed_roots(archive_dir)
    expected = list(matchups(roots))
    refs = {r["id"]: r for r in roots}
    manifest_fingerprint = None
    grouped = defaultdict(lambda: defaultdict(dict))
    totals = defaultdict(Counter)
    depth = defaultdict(Counter)
    censored = []
    seen = set()
    for index in range(SHARDS):
        path = Path(shards) / f"shard-{index}"
        manifest = load_json(path / "manifest.json")
        cases = cases_by_shard(roots, index)
        fingerprint = manifest.get("fingerprints")
        want = {"schema": SCHEMA, "source": meta, "index": index,
                "case_ids": [c["id"] for c in cases],
                "fingerprints": fingerprint}
        if manifest != want:
            raise ValueError("Incompatible Tal source or case manifests")
        if not isinstance(fingerprint, dict) or set(fingerprint) != {
                "originals", "variants", "protocol_sha256"}:
            raise ValueError("Missing SHA-bound source and binary identities")
        if fingerprint["protocol_sha256"] != file_hash(Path(__file__)):
            raise ValueError("Execution and audit protocol code changed")
        hashes = fingerprint["originals"]
        frozen = meta["fischer_frozen"]["hashes"]
        for key, expected_hash in (("rodent", frozen["rodent"]),
                                   ("fischer", frozen["fischer_profile"]),
                                   ("simple", frozen["simple_control"]),
                                   ("tal", TAL_ORIGINAL_SHA)):
            if hashes.get(key) != expected_hash:
                raise ValueError("Non-original source engine or frozen profile")
        if fingerprint["variants"].get("TalBalanced") != BALANCED_SHA:
            raise ValueError("Candidate's SHA has changed")
        if manifest_fingerprint is None:
            manifest_fingerprint = fingerprint
        elif manifest_fingerprint != fingerprint:
            raise ValueError("Cross-shard engine candidate mismatch")
        expected_files = {"manifest.json"} | {
            c["id"] + ".json" for c in cases}
        if {file.name for file in path.iterdir()} != expected_files:
            raise ValueError("Missing or additional game evidence")
        binding = digest(manifest)
        for case in cases:
            if case["id"] in seen:
                raise ValueError("Duplicated clock match")
            seen.add(case["id"])
            doc = load_json(path / (case["id"] + ".json"))
            validate_game(doc, case, refs[case["root"]], binding)
            game = doc["game"]
            if game["status"] != "COMPLETE":
                censored.append(case["id"])
                continue
            value = _points(game["result"], case["color"])
            grouped[case["kind"]][case["root"]][case["color"]] = value
            stats = totals[case["kind"]]
            stats["games"] += 1
            stats["wins"] += int(value == 1)
            stats["draws"] += int(value == .5)
            stats["losses"] += int(value == 0)
            stats["focal_flags"] += int(game["time_forfeit"] == "fischer")
            stats["opponent_flags"] += int(game["time_forfeit"] == "simple")
            for role, turns in game["depth_reached"].items():
                depth[case["kind"]][role] += turns
    if len(seen) != len(expected):
        raise ValueError("Incompletely evaluated historical opening roots")
    result = {
        "schema": SCHEMA, "source": meta,
        "status": "INCOMPLETE_DO_NOT_SCORE",
        "games_expected": len(expected), "games_observed": len(seen),
        "censored": len(censored), "candidate_sha256": BALANCED_SHA,
        "fischer_untouched": True, "tal_official_untouched": True,
        "clock": None, "strength_gates": None, "no_human_elo": True,
        "do_not_auto_promote": True,
    }
    if censored:
        result["censored_cases"] = censored
        return result
    summaries = {}
    for kind in GAME_TYPES:
        group = grouped[kind]
        if len(group) != ROOTS or any(set(pair) != {"white", "black"}
                                      for pair in group.values()):
            raise ValueError("Paired color matches omitted or duplicated")
        n = totals[kind]["games"]
        if n != ROOTS * 2 or n != sum(totals[kind][key] for key in
                                      ("wins", "draws", "losses")):
            raise ValueError("Incorrect Tal actual WDL denominator")
        values = [v for pair in group.values() for v in pair.values()]
        summaries[kind] = {
            **dict(totals[kind]), "score": sum(values) / len(values),
            "ci95": ci_cluster({k: list(v.values()) for k, v in group.items()}),
            "achieved_depth_moves": dict(depth[kind]),
        }
    paired_gain = {
        root: [grouped["balanced_vs_simple"][root][color] -
               grouped["original_vs_simple"][root][color]
               for color in ("white", "black")]
        for root in grouped["balanced_vs_simple"]
    }
    gain = sum(x for pair in paired_gain.values() for x in pair) / (ROOTS * 2)
    gain_ci = ci_cluster(paired_gain)
    summaries["balanced_vs_simple"]["gain_pp_vs_original"] = gain * 100
    summaries["balanced_vs_simple"]["gain_ci95_pp"] = [x * 100 for x in gain_ci]
    all_legal = not censored
    no_focal_flags = all(
        totals[kind]["focal_flags"] == 0 for kind in GAME_TYPES)
    gates = {
        "complete_legal_10plus2": all_legal,
        "zero_focal_time_forfeits": no_focal_flags,
        "balanced_score_at_least_40pct":
            summaries["balanced_vs_simple"]["score"] >= .40,
        "balanced_improves_10pp_with_positive_lower_ci":
            gain >= .10 and gain_ci[0] > 0,
        "direct_head_to_head_advantage":
            summaries["balanced_vs_original"]["score"] >= .50,
    }
    result.update({
        "clock": summaries, "strength_gates": gates,
        "status": (
            "ELIGIBLE_FOR_TAL_BALANCED_FURTHER_RELEASE_REVIEW"
            if all(gates.values()) else
            "NO_CONFIRMED_TAL_BALANCED_CLOCK_STRENGTH_DO_NOT_FREEZE"
        ),
    })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "play", "aggregate"):
        p = sub.add_parser(command)
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--fetch", action="store_true")
        source.add_argument("--archive-dir", type=Path)
        if command == "play":
            p.add_argument("--rodent-root", required=True, type=Path)
            p.add_argument("--index", required=True, type=int)
            p.add_argument("--out", required=True, type=Path)
        if command == "aggregate":
            p.add_argument("--shards", required=True, type=Path)
            p.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "plan":
        report = plan(args.archive_dir)
    elif args.command == "play":
        report = run_shard(args.rodent_root, args.out,
                           args.index, args.archive_dir)
    else:
        report = aggregate(args.shards, args.archive_dir)
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("Results overwrite forbidden")
        atomic_json(args.out, report)
    print(json.dumps(report, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
