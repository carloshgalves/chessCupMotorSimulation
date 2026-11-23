"""Tal's first PRACTICAL-strength benchmark: full legal Rodent UCI games.

The frozen original Fischer asset is read-only. Tal is NOT frozen here.
A salted 96-root development panel and every eligible distinct TUNE root
are crossed by color against Simple d12 and d16. Results are reported only
when every planned game terminates legally. This is not a human Elo test.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import random

import chess

from .catalog import Player, find_player
from .fischer_deep_calibration import NODE_CAP, choose
from .fischer_full_games import atomic_json, digest, initial_board, load_json
from .matches import _open_engine, file_hash
from .real85_neural_stream import neural_arm
from .real_training_cohorts import MIRROR_COMMIT, _iter_verified_games
from .registry import registered_aliases
from .tal_product_plan import load_source

SCHEMA = "chesssim-tal-graded-real-games-v1"
PLAYER = "tal"
ARM_FIT = "fit"
ARM_TUNE = "tune"
DEVELOPMENT_ROOTS = 96
MIN_TUNE_ROOTS = 12
ROOT_SALT = "tal-graded-full-games-2026-11-v1"
DEPTH = 16
OPPONENT_DEPTHS = (12, 16)
COLORS = ("white", "black")
SHARD_COUNT = 32
START_PLIES = 12
MAX_CONTINUATION_PLIES = 1800
BOOTSTRAP_SALT = 20261123
EXPECTED_SOURCE_GAMES = 2431
EXPECTED_FROZEN_RUN = 45097738965


def protocol_hashes():
    directory = Path(__file__).resolve().parent
    names = (
        "tal_full_games.py", "tal_product_plan.py", "fischer_full_games.py",
        "fischer_deep_calibration.py", "real_training_cohorts.py",
        "real85_neural_stream.py", "matches.py", "registry.py", "catalog.py",
        "game_diversity.py",
    )
    return {name: file_hash(directory / name) for name in names}


def frozen_fischer():
    """Guard pre-existing release; Tal evaluation must not mutate Fischer."""
    raw = files("chesssim.data").joinpath("qualified_game_assets.json")
    frozen = json.loads(raw.read_text(encoding="utf-8"))["players"]["fischer"]
    if (frozen["status"] != "FROZEN_GAME_ASSET" or
            frozen["depth"] != 16 or
            frozen["qualification_run_id"] != EXPECTED_FROZEN_RUN or
            frozen["internal_opening_book"] or frozen["phase_adaptive_depth"] or
            frozen["neural_override"]):
        raise ValueError("Fischer immutable release contract changed")
    return {
        "run": frozen["qualification_run_id"],
        "depth": frozen["depth"],
        "hashes": frozen["hashes"],
    }


def source_panel(archive_dir=None):
    pgn, rows, scan, origin, surname = load_source(archive_dir)
    if len(rows) != EXPECTED_SOURCE_GAMES:
        raise ValueError("Historical Tal accepted game census changed")
    selected = [r for r in rows if neural_arm(r["mainline_sha256"]) in (ARM_FIT, ARM_TUNE)]
    expected_ids = {r["mainline_sha256"] for r in selected}
    seen = set()
    groups = defaultdict(list)
    exclusions = Counter()
    for game, meta in _iter_verified_games(pgn, selected, surname):
        gid = meta["mainline_sha256"]
        arm = neural_arm(gid)
        if gid in seen or gid not in expected_ids:
            raise ValueError("Duplicate or unexpected source Tal game")
        seen.add(gid)
        if game.board().fen() != chess.STARTING_FEN:
            exclusions[f"{arm}:nonstandard_start"] += 1
            continue
        board = game.board()
        prefix = []
        for move in game.mainline_moves():
            if len(prefix) == START_PLIES:
                break
            if move not in board.legal_moves:
                raise ValueError("Illegal source Tal opening")
            board.push(move)
            prefix.append(move.uci())
        if len(prefix) != START_PLIES:
            exclusions[f"{arm}:short"] += 1
            continue
        if board.is_game_over(claim_draw=True):
            exclusions[f"{arm}:terminal_start"] += 1
            continue
        groups[(arm, tuple(prefix))].append(gid)
    if seen != expected_ids:
        raise ValueError("PGN replay omitted an accepted development game")
    fit = []
    tune = []
    for (arm, prefix), games in groups.items():
        item = {
            "id": digest([list(prefix), arm]),
            "prefix": list(prefix),
            "arm": arm,
            "source_games": sorted(games),
        }
        (fit if arm == ARM_FIT else tune).append(item)
    # TUNE is held out from FIT selection and never used to choose Tal's
    # depth. Same exact opening prefix cannot occur in the FIT score panel.
    tune_prefix = {tuple(x["prefix"]) for x in tune}
    fit_unshared = [x for x in fit if tuple(x["prefix"]) not in tune_prefix]
    fit_unshared.sort(key=lambda x: (
        sha256((ROOT_SALT + ":" + x["id"]).encode()).hexdigest(), x["id"]))
    tune.sort(key=lambda x: x["id"])
    if len(fit_unshared) < DEVELOPMENT_ROOTS or len(tune) < MIN_TUNE_ROOTS:
        raise ValueError("Too few FIT-exclusive or disjoint TUNE roots")
    panel = fit_unshared[:DEVELOPMENT_ROOTS] + tune
    if len({tuple(x["prefix"]) for x in panel}) != len(panel):
        raise ValueError("Unintended cross-arm identical roots")
    if sum(len(v) for v in groups.values()) + sum(exclusions.values()) != len(selected):
        raise ValueError("All eligible development games must reconcile")
    root_digest = digest(panel)
    extra = {
        "schema": SCHEMA,
        "source": origin,
        "source_snapshot": MIRROR_COMMIT if archive_dir is None else None,
        "source_pgn_sha256": sha256(pgn).hexdigest(),
        "panel_sha256": root_digest,
        "protocol_code_hashes": protocol_hashes(),
        "python_chess_version": chess.__version__,
        "accepted_games": len(rows),
        "development_game_counts": dict(Counter(neural_arm(r["mainline_sha256"])
                                                  for r in selected)),
        "reserved_test_games": len(rows) - len(selected),
        "development_distinct_fit_roots": len(fit),
        "development_distinct_tune_roots": len(tune),
        "shared_fit_tune_prefixes": len(fit) - len(fit_unshared),
        "source_exclusions": dict(exclusions),
        "roots_fit": DEVELOPMENT_ROOTS,
        "roots_tune": len(tune),
        "planned_games": len(panel) * len(OPPONENT_DEPTHS) * len(COLORS),
        "focal_depth": DEPTH,
        "opponent_depths": list(OPPONENT_DEPTHS),
        "node_cap": NODE_CAP,
        "start_plies": START_PLIES,
        "max_continuation_plies": MAX_CONTINUATION_PLIES,
        "fischer_frozen": frozen_fischer(),
        "outer_test_moves_scored": 0,
        "opening_book_enabled": False,
        "fide_or_human_elo": None,
        "tal_qualified": False,
        "limitations": [
            "Salt-selected FIT roots, not every FIT opening or every historical move.",
            "Every eligible TUNE root included; TUNE remains retrospective, not blind.",
            "Twelve historical half-moves precede each continuation; not standard-start Elo.",
            "Depth and node budgets, no real clock; 10+2 stability is a separate future gate.",
            "No assertion of Tal's human rating, style fidelity or player qualification.",
        ],
    }
    return panel, extra


def cases(panel):
    for root in panel:
        for depth in OPPONENT_DEPTHS:
            for color in COLORS:
                yield {
                    "id": digest([SCHEMA, root["id"], depth, color]),
                    "root": root["id"],
                    "arm": root["arm"],
                    "opponent_depth": depth,
                    "color": color,
                }


def shard_cases(panel, index, count=SHARD_COUNT):
    if (type(index) is not int or type(count) is not int or
            not 1 <= count <= 256 or not 0 <= index < count):
        raise ValueError("Invalid shard index")
    ordered = sorted(panel, key=lambda x: x["id"])
    # All four comparisons of one root remain in the same runner.
    return list(cases([root for i, root in enumerate(ordered) if i % count == index]))


def plan(archive_dir=None):
    panel, metadata = source_panel(archive_dir)
    count = len(list(cases(panel)))
    if count != metadata["planned_games"]:
        raise ValueError("Invalid planned Tal denominator")
    if any(not shard_cases(panel, i) for i in range(SHARD_COUNT)):
        raise ValueError("Sharding underpopulated")
    return metadata


def _profiles(root):
    root = Path(root).resolve(strict=True)
    exe = root / "rodent-iv-plain.exe"
    registry = root / "personalities/basic.ini"
    tal = find_player("tal")
    simple = Player(number=0, name="Rodent IV Simple", slug="control-simple",
                    group="baseline", rodent_alias="Simple",
                    rodent_file="simple.txt")
    fischer = find_player("fischer")
    aliases = registered_aliases(registry)
    if (aliases.get(tal.rodent_alias) != tal.profile_filename or
            aliases.get("Simple") != "simple.txt" or
            aliases.get(fischer.rodent_alias) != fischer.profile_filename):
        raise ValueError("Historical UCI personality registry was altered")
    paths = {
        "rodent": exe,
        "tal": root / "personalities" / tal.profile_filename,
        "simple": root / "personalities/simple.txt",
        "fischer": root / "personalities" / fischer.profile_filename,
        "registry": registry,
    }
    hashes = {name: file_hash(file) for name, file in paths.items()}
    frozen = frozen_fischer()["hashes"]
    if (hashes["rodent"] != frozen["rodent"] or
            hashes["simple"] != frozen["simple_control"] or
            hashes["fischer"] != frozen["fischer_profile"]):
        raise ValueError("Cannot run Tal with a different frozen Fischer/Rodent installation")
    return root, exe, tal, simple, paths, hashes


def play_case(case, root, tal_engine, opponent):
    board = initial_board(root)
    moves = []
    reached, depth_missing, nodes_exceeded = Counter(), Counter(), Counter()
    focal_white = case["color"] == "white"
    for _ in range(MAX_CONTINUATION_PLIES):
        if board.is_game_over(claim_draw=True):
            break
        is_tal = board.turn == focal_white
        role = "tal" if is_tal else "simple"
        wanted_depth = DEPTH if is_tal else case["opponent_depth"]
        move, telemetry = choose(tal_engine if is_tal else opponent, board, wanted_depth)
        if move not in board.legal_moves:
            raise ValueError("UCI returned an illegal Tal benchmark move")
        reached[role] += int(telemetry["reached_requested"])
        depth_missing[role] += int(telemetry["reported_depth"] is None)
        nodes_exceeded[role] += int(
            telemetry["reported_nodes"] is not None and
            telemetry["reported_nodes"] > NODE_CAP)
        moves.append(move.uci())
        board.push(move)
    outcome = board.outcome(claim_draw=True)
    return {
        "case": case,
        "moves": moves,
        "result": outcome.result() if outcome else "*",
        "termination": outcome.termination.name if outcome else "PLY_CAP_INCOMPLETE",
        "status": "COMPLETE" if outcome else "INCOMPLETE",
        "reached": dict(reached),
        "depth_missing": dict(depth_missing),
        "nodes_exceeded": dict(nodes_exceeded),
    }


def validate_case(record, case, root):
    if not isinstance(record, dict) or record.get("case") != case:
        raise ValueError("Result not bound to planned Tal case")
    moves = record.get("moves")
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_CONTINUATION_PLIES:
        raise ValueError("Invalid Tal game length")
    board = initial_board(root)
    turns = Counter()
    focal_white = case["color"] == "white"
    for encoded in moves:
        if board.is_game_over(claim_draw=True):
            raise ValueError("Move after legal terminal")
        if not isinstance(encoded, str):
            raise ValueError("Invalid move encoding")
        move = chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal replayed Tal game move")
        turns["tal" if board.turn == focal_white else "simple"] += 1
        board.push(move)
    outcome = board.outcome(claim_draw=True)
    expected = ((outcome.result(), outcome.termination.name, "COMPLETE")
                if outcome else ("*", "PLY_CAP_INCOMPLETE", "INCOMPLETE"))
    if (record.get("result"), record.get("termination"),
            record.get("status")) != expected:
        raise ValueError("Invented Tal result or adjudication")
    if not outcome and len(moves) != MAX_CONTINUATION_PLIES:
        raise ValueError("Truncated Tal game")
    for name in ("reached", "depth_missing", "nodes_exceeded"):
        values = record.get(name)
        if (not isinstance(values, dict) or set(values) - set(turns) or
                any(type(v) is not int or not 0 <= v <= turns[k]
                    for k, v in values.items())):
            raise ValueError("Missing or impossible UCI telemetry")
    return record


def run_shard(rodent_root, out, index, count=SHARD_COUNT, archive_dir=None):
    panel, meta = source_panel(archive_dir)
    planned = shard_cases(panel, index, count)
    if not planned:
        raise ValueError("Empty Tal shard")
    roots = {root["id"]: root for root in panel}
    location, exe, tal, simple, paths, hashes = _profiles(rodent_root)
    manifest = {**meta, "shard_index": index, "shard_count": count,
                "case_ids": [case["id"] for case in planned],
                "hashes": hashes}
    target = Path(out)
    if target.is_symlink():
        raise ValueError("Symlink output")
    target.mkdir(exist_ok=True, parents=False)
    path = target / "manifest.json"
    if path.exists():
        if load_json(path) != manifest:
            raise ValueError("Invalid Tal checkpoint manifest")
    else:
        if any(target.iterdir()):
            raise ValueError("Unknown Tal checkpoint content")
        atomic_json(path, manifest)
    binding = digest(manifest)
    with ExitStack() as stack:
        engine = stack.enter_context(_open_engine(location, exe, tal, book=False))
        opponent = stack.enter_context(_open_engine(location, exe, simple, book=False))
        for number, case in enumerate(planned, 1):
            file = target / (case["id"] + ".json")
            if file.exists():
                result = load_json(file)
                if result.get("manifest_sha256") != binding:
                    raise ValueError("Incompatible Tal checkpoint hash")
                validate_case(result, case, roots[case["root"]])
            else:
                result = play_case(case, roots[case["root"]], engine, opponent)
                result["manifest_sha256"] = binding
                atomic_json(file, validate_case(result, case, roots[case["root"]]))
            print(f"TAL_FULL {index}/{count} {number}/{len(planned)} "
                  f"{result['status']}", flush=True)
    if (hashes != {key: file_hash(f) for key, f in paths.items()} or
            meta["protocol_code_hashes"] != protocol_hashes()):
        raise ValueError("Original engine or scientific protocol was modified")
    return {"schema": SCHEMA, "index": index, "games": len(planned),
            "complete": True, "hashes": hashes}


def _points(result, color):
    if result == "1/2-1/2":
        return .5
    if result not in ("1-0", "0-1"):
        raise ValueError("Cannot score a censored game")
    return float((result == "1-0") == (color == "white"))


def root_ci(pairs):
    """Bootstrap entire two-color roots, not each individual move or game."""
    groups = defaultdict(list)
    for root, score in pairs:
        groups[root].append(score)
    blocks = list(groups.values())
    if len(blocks) < 12:
        return None
    rng = random.Random(BOOTSTRAP_SALT)
    draws = []
    for _ in range(2000):
        chosen = rng.choices(blocks, k=len(blocks))
        draws.append(sum(map(sum, chosen)) / sum(map(len, chosen)))
    draws.sort()
    return [draws[49], draws[1949]]


def aggregate(shards, count=SHARD_COUNT, archive_dir=None):
    panel, meta = source_panel(archive_dir)
    roots = {root["id"]: root for root in panel}
    expected = {case["id"]: case for case in cases(panel)}
    records = []
    seen = set()
    common_hashes = None
    for i in range(count):
        directory = Path(shards) / f"shard-{i}"
        manifest = load_json(directory / "manifest.json")
        planned = shard_cases(panel, i, count)
        wanted = {**meta, "shard_index": i, "shard_count": count,
                  "case_ids": [case["id"] for case in planned],
                  "hashes": manifest["hashes"]}
        if manifest != wanted:
            raise ValueError("Tal manifest mismatch or tampered protocol")
        hashes = manifest["hashes"]
        if (set(hashes) != {"rodent", "tal", "simple", "fischer", "registry"}
                or any(not isinstance(x, str) or len(x) != 64 or
                       any(c not in "0123456789abcdef" for c in x)
                       for x in hashes.values())):
            raise ValueError("Invalid engine evidence provenance")
        frozen = frozen_fischer()["hashes"]
        if (hashes["rodent"] != frozen["rodent"] or
                hashes["simple"] != frozen["simple_control"] or
                hashes["fischer"] != frozen["fischer_profile"]):
            raise ValueError("Tal benchmark diverges from frozen Fischer binary")
        if common_hashes is None:
            common_hashes = hashes
        elif hashes != common_hashes:
            raise ValueError("Incompatible Tal motor hashes across runners")
        case_ids = {case["id"] for case in planned}
        if {f.name for f in directory.iterdir()} != (
                {"manifest.json"} | {cid + ".json" for cid in case_ids}):
            raise ValueError("Missing/extra/truncated Tal evidence files")
        binding = digest(manifest)
        for case in planned:
            cid = case["id"]
            if cid in seen:
                raise ValueError("Duplicate Tal case")
            seen.add(cid)
            entry = load_json(directory / (cid + ".json"))
            if entry.get("manifest_sha256") != binding:
                raise ValueError("Record does not belong to exact Tal protocol")
            records.append(validate_case(entry, case, roots[case["root"]]))
    if seen != set(expected):
        raise ValueError("Incomplete paired roots in Tal benchmark")
    complete = sum(r["status"] == "COMPLETE" for r in records)
    missing = len(records) - complete
    report = {**meta, "hashes": common_hashes,
              "status": ("ALL_GAMES_TERMINAL_UNCALIBRATED" if missing == 0
                         else "INCOMPLETE_DO_NOT_SCORE"),
              "observed_games": len(records), "complete_games": complete,
              "censored_games": missing, "shard_count": count,
              "results": None, "by_arm_opponent": None,
              "qualified_as_human_tal": False, "fischer_unchanged": True}
    if missing:
        report["censored_cases"] = [
            r["case"] for r in records if r["status"] != "COMPLETE"]
        return report
    by_cell = defaultdict(list)
    telemetry = defaultdict(Counter)
    for rec in records:
        case = rec["case"]
        key = f'{case["arm"]}|{case["opponent_depth"]}'
        val = _points(rec["result"], case["color"])
        by_cell[key].append((case["root"], val))
        telemetry[key]["games"] += 1
        telemetry[key]["wins"] += int(val == 1)
        telemetry[key]["draws"] += int(val == .5)
        telemetry[key]["losses"] += int(val == 0)
        for metric in ("reached", "depth_missing", "nodes_exceeded"):
            for role, n in rec[metric].items():
                telemetry[key][f"{role}:{metric}"] += n
    result = {}
    for arm, nroots in ((ARM_FIT, DEVELOPMENT_ROOTS), (ARM_TUNE, meta["roots_tune"])):
        for depth in OPPONENT_DEPTHS:
            key = f"{arm}|{depth}"
            scores = by_cell[key]
            if len(scores) != nroots * 2 or len({root for root, _ in scores}) != nroots:
                raise ValueError("Missing paired Tal color crossover")
            values = [v for _, v in scores]
            cell = telemetry[key]
            if cell["wins"] + cell["draws"] + cell["losses"] != len(values):
                raise ValueError("Invalid Tal WDL denominator")
            result[key] = {
                **dict(cell),
                "distinct_roots": nroots,
                "points": sum(values),
                "score_fraction": sum(values) / len(values),
                "root_cluster_ci95": root_ci(scores),
            }
    report["by_arm_opponent"] = result
    report["results"] = dict(telemetry)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("plan", "play", "aggregate"):
        a = sub.add_parser(name)
        src = a.add_mutually_exclusive_group(required=True)
        src.add_argument("--fetch", action="store_true")
        src.add_argument("--archive-dir", type=Path)
        if name == "play":
            a.add_argument("--rodent-root", required=True, type=Path)
            a.add_argument("--index", required=True, type=int)
            a.add_argument("--count", type=int, default=SHARD_COUNT)
            a.add_argument("--out", required=True, type=Path)
        elif name == "aggregate":
            a.add_argument("--shards", required=True, type=Path)
            a.add_argument("--count", type=int, default=SHARD_COUNT)
            a.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    if args.command == "plan":
        report = plan(args.archive_dir)
    elif args.command == "play":
        report = run_shard(args.rodent_root, args.out, args.index,
                           args.count, args.archive_dir)
    else:
        report = aggregate(args.shards, args.count, args.archive_dir)
        atomic_json(args.out, report)
    print(json.dumps(report, sort_keys=True, allow_nan=False), flush=True)
    return int(report.get("status") == "INCOMPLETE_DO_NOT_SCORE")


if __name__ == "__main__":
    raise SystemExit(main())
