"""Independent Tal Balanced validation: historical style plus real 10+2 games.

The prior 240-game FIT DOE selected balanced16 without using TUNE/TEST.
This preregistered retrospective TUNE evaluation compares that frozen
candidate to untouched Tal and Simple on shared positions and paired clocks.
It DOES NOT certify Tal's historic Elo, or alter any official profile.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import mean

import chess

from .catalog import Player
from .fischer_baseline import phase
from .fischer_deep_calibration import choose
from .fischer_final_gate import (
    SCHEMA as CLOCK_SCHEMA, features, play_clock, validate_clock,
)
from .fischer_full_games import atomic_json, digest, load_json
from .matches import _open_engine, file_hash
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _iter_verified_games
from .tal_full_games import source_panel as graded_panel, frozen_fischer
from .tal_product_plan import load_source
from .tal_strength_doe import (
    TAL_ORIGINAL_SHA, VARIANTS, make_isolated_profiles,
)

SCHEMA = "chesssim-tal-balanced-retrospective-style-clock-v1"
PRIOR_DOE_RUN = 45197012079
PRIOR_DOE_STATUS = "PROMISING_STRENGTH_REPAIR_STYLE_GATE_PENDING"
ROOT_SALT = "tal-balanced-retro-10plus2-2026-11-23-v1"
STYLE_SALT = "tal-balanced-retro-style-2026-11-23-v1"
SHARDS = 12
ROOTS = 12
REPEAT_MOD = 7
CLOCK_VARIANTS = ("original16", "balanced16")
STYLE_ROLES = ("human", "original16", "balanced16", "simple16")
STYLE_FEATURES = ("forcing", "check", "pawn_move", "kingward")
EXPECTED_GAMES = 2431
EXPECTED_TUNE = 66
EXPECTED_TEST = 341
MIN_STYLES = 100
MIN_STYLE_GAMES = 50
PRIOR_SOURCE_SHA = "586aac4613e170474f12893679ab992d3999ca5109df7df77c634a76846d2687"


def _profiles():
    return {
        "original16": Player(number=0, name="Tal original", slug="tal-original",
                             group="baseline", rodent_alias="Tal", rodent_file="tal.txt"),
        "balanced16": Player(number=0, name="Tal Balanced", slug="tal-balanced",
                             group="baseline", rodent_alias="TalBalanced",
                             rodent_file="talbalanced.txt"),
        "simple16": Player(number=0, name="Simple", slug="control-simple",
                           group="baseline", rodent_alias="Simple",
                           rodent_file="simple.txt"),
    }


def _style_case(game, row):
    """One SHA-selected nonforced historical Tal position per observed phase."""
    gid = row["mainline_sha256"]
    board = game.board()
    chosen = {}
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("Historical source trajectory is illegal")
        if board.turn == (row["color"] == "white") and board.legal_moves.count() > 1:
            stage = phase(board)
            rank = sha256(
                f"{STYLE_SALT}:{gid}:{stage}:{board.ply()}".encode()
            ).hexdigest()
            if stage not in chosen or rank < chosen[stage][0]:
                chosen[stage] = (rank, board.copy(stack=True), move)
        board.push(move)
    for stage, (_, position, historical) in sorted(chosen.items()):
        case_id = digest([SCHEMA, gid, stage, position.ply(), position.fen()])
        yield {
            "id": case_id,
            "game": gid,
            "phase": stage,
            "ply": position.ply(),
            "fen_sha256": sha256(position.fen().encode()).hexdigest(),
            "human": historical.uci(),
        }, position


def source_cases(archive_dir=None):
    pgn, rows, _, origin, surname = load_source(archive_dir)
    if len(rows) != EXPECTED_GAMES or sha256(pgn).hexdigest() != PRIOR_SOURCE_SHA:
        raise ValueError("Tal PGN does not match accepted source snapshot")
    tune = [row for row in rows if neural_arm(row["mainline_sha256"]) == "tune"]
    test_count = sum(neural_arm(row["mainline_sha256"]) == "excluded_test"
                     for row in rows)
    if len(tune) != EXPECTED_TUNE or test_count != EXPECTED_TEST:
        raise ValueError("Tal development split changed")
    roots, meta = graded_panel(archive_dir)
    # All TUNE roots were available in the earlier baseline: this is a
    # retrospective confirmation, not a new blind validation corpus.
    tune_roots = [root for root in roots if root["arm"] == "tune"]
    if len(tune_roots) != 56:
        raise ValueError("The pinned historical TUNE root panel changed")
    roots_ranked = sorted(
        tune_roots, key=lambda root: (
            sha256(f"{ROOT_SALT}:{root['id']}".encode()).hexdigest(), root["id"]
        )
    )[:ROOTS]
    if len(roots_ranked) != ROOTS or len({
        tuple(root["prefix"]) for root in roots_ranked
    }) != ROOTS:
        raise ValueError("TUNE roots are missing or duplicated")
    cases = [
        {"id": digest([SCHEMA, root["id"], variant, color]),
         "root": root["id"], "variant": variant, "color": color,
         "clock": [600, 2], "control": "simple16", "depth": 16}
        for root in roots_ranked
        for variant in CLOCK_VARIANTS
        for color in ("white", "black")
    ]
    styles = {}
    for game, row in _iter_verified_games(pgn, tune, surname):
        for case, _ in _style_case(game, row):
            if case["id"] in styles:
                raise ValueError("Historical style position duplicated")
            styles[case["id"]] = case
    if len(styles) < MIN_STYLES or len({s["game"] for s in styles.values()}) < MIN_STYLE_GAMES:
        raise ValueError("Not enough TUNE source positions for style check")
    return {
        "schema": SCHEMA, "source": origin, "pgn_sha256": sha256(pgn).hexdigest(),
        "prior_doe_run": PRIOR_DOE_RUN,
        "prior_doe_status": PRIOR_DOE_STATUS,
        "graded_root_snapshot": meta["panel_sha256"],
        "tune_games": len(tune), "test_games_unopened": test_count,
        "clock_roots": roots_ranked, "clock_cases": cases, "style_cases": styles,
        "fischer_frozen": frozen_fischer(),
        "candidate": VARIANTS["balanced16"],
        "official_profiles_modified": False,
        "blind_holdout": False,
        "limits": [
            "TUNE roots appeared in prior original Tal baseline, not a new blind test",
            "One selected position per TUNE game per phase is descriptive only",
            "Same-depth 10+2 Rodent-relative strength is not human FIDE Elo",
            "All results require explicit release review before Tal promotion",
        ],
    }


def shard(item, source):
    if source == "clock":
        root = item["root"]
        roots = [r["id"] for r in source_cases_cached_roots(item)]
        return roots.index(root) % SHARDS
    return int(sha256(item["game"].encode()).hexdigest()[:12], 16) % SHARDS


def source_cases_cached_roots(item):
    """Unused by execution; keep root mapping explicit in clock_shard."""
    raise RuntimeError(f"Use clock_shard with the frozen root panel: {item['root']}")


def clock_shard(case, roots):
    order = {root["id"]: i % SHARDS for i, root in enumerate(roots)}
    if case["root"] not in order:
        raise ValueError("Unregistered root")
    return order[case["root"]]


def style_shard(case):
    return int(sha256(case["game"].encode()).hexdigest()[:12], 16) % SHARDS


def shard_plan(source, index):
    if type(index) is not int or not 0 <= index < SHARDS:
        raise ValueError("Illegal shard index")
    games = [c for c in source["clock_cases"] if
             clock_shard(c, source["clock_roots"]) == index]
    styles = [c for c in source["style_cases"].values() if style_shard(c) == index]
    if len(games) != 4:
        raise ValueError("Every runner must have exactly one paired opening")
    return games, styles


def public_plan(source):
    return {
        "schema": SCHEMA,
        "status": "PREDECLARED_TAL_RETROSPECTIVE_VALIDATION_NOT_EXECUTED",
        "pgn_sha256": source["pgn_sha256"],
        "prior_doe_run": source["prior_doe_run"],
        "clock_roots": ROOTS, "clock_games": len(source["clock_cases"]),
        "style_positions": len(source["style_cases"]),
        "style_source_games": len({x["game"] for x in source["style_cases"].values()}),
        "shards": SHARDS, "clock_shards": [
            len(shard_plan(source, i)[0]) for i in range(SHARDS)],
        "style_shards": [
            len(shard_plan(source, i)[1]) for i in range(SHARDS)],
        "tune_games": source["tune_games"],
        "test_games_unopened": source["test_games_unopened"],
        "candidate_overrides": source["candidate"]["overrides"],
        "official_profiles_modified": False, "blind_holdout": False,
    }


def _source_style_positions(archive_dir=None):
    pgn, rows, _, _, surname = load_source(archive_dir)
    tune = [r for r in rows if neural_arm(r["mainline_sha256"]) == "tune"]
    for game, row in _iter_verified_games(pgn, tune, surname):
        yield from _style_case(game, row)


def _make_clock(case, root, focal, control):
    # Fischer's verified generic focal-slot 10+2 executor is reused.
    # In the nested record 'fischer' means the focal Tal variant,
    # never the historical Fischer motor.
    raw = play_clock(case, root, focal, control)
    validate_clock(raw, case, root)
    return raw


def _validate_game(record, case, root, manifest_sha):
    if (record.get("schema") != SCHEMA or
            record.get("case") != case or
            record.get("manifest_sha256") != manifest_sha or
            record.get("slot_mapping") != {
                "fischer": case["variant"], "simple": "simple16"
            }):
        raise ValueError("Tal clock record not bound to candidate/root")
    game = record.get("game")
    if not isinstance(game, dict) or game.get("schema") != CLOCK_SCHEMA:
        raise ValueError("Expected independent clock replay format")
    validate_clock(game, case, root)
    return record


def _validate_style(record, original, board, manifest_sha):
    if (record.get("schema") != SCHEMA or
            record.get("id") != original["id"] or
            record.get("game") != original["game"] or
            record.get("phase") != original["phase"] or
            record.get("ply") != original["ply"] or
            record.get("human") != original["human"] or
            record.get("fen_sha256") != original["fen_sha256"] or
            record.get("manifest_sha256") != manifest_sha):
        raise ValueError("Tampered Tal historic position or evidence binding")
    for name in STYLE_ROLES:
        encoded = record.get(name)
        if not isinstance(encoded, str):
            raise ValueError("Missing Tal choice")
        move = chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal historical or engine choice")
        if record["features"][name] != features(board, move):
            raise ValueError("Historical Tal style feature changed")
    repeat = record.get("repeat_balanced")
    expected = int(original["id"][:12], 16) % REPEAT_MOD == 0
    if (repeat is None) == expected:
        raise ValueError("Incorrect precommitted Tal repeat selection")
    if repeat is not None and chess.Move.from_uci(repeat) not in board.legal_moves:
        raise ValueError("Illegal Tal repeated choice")
    achieved = record.get("depth_met")
    if (not isinstance(achieved, dict) or set(achieved) != set(STYLE_ROLES) - {"human"}
            or any(type(x) is not bool for x in achieved.values())):
        raise ValueError("Invalid depth telemetry")
    return record


def run_shard(rodent_root, out, index, archive_dir=None):
    source = source_cases(archive_dir)
    game_cases, style_cases = shard_plan(source, index)
    roots = {x["id"]: x for x in source["clock_roots"]}
    source_styles = source["style_cases"]
    target = Path(out)
    if target.exists() or target.is_symlink():
        raise ValueError("Output location already exists")
    overlay, originals, candidates = make_isolated_profiles(
        rodent_root, target.parent / f"tal-balanced-overlay-{index}")
    if originals["tal"] != TAL_ORIGINAL_SHA:
        raise ValueError("Tal original hash modified")
    fingerprints = {
        "originals": originals, "candidates": candidates,
        "protocol_sha256": file_hash(Path(__file__)),
        "chess_version": chess.__version__,
    }
    manifest = {
        "schema": SCHEMA, "index": index, "pgn_sha256": source["pgn_sha256"],
        "clock_ids": sorted(x["id"] for x in game_cases),
        "style_ids": sorted(x["id"] for x in style_cases),
        "prior_doe_run": PRIOR_DOE_RUN,
        "frozen_profile_hashes": fingerprints,
        "candidate": source["candidate"],
        "tune_games": source["tune_games"],
    }
    target.mkdir(parents=False)
    atomic_json(target / "manifest.json", manifest)
    bind = digest(manifest)
    players = _profiles()
    with ExitStack() as stack:
        engines = {name: stack.enter_context(_open_engine(
            overlay, overlay / "rodent-iv-plain.exe", person, book=False
        )) for name, person in players.items()}
        for i, case in enumerate(game_cases, 1):
            record = {
                "schema": SCHEMA, "case": case,
                "slot_mapping": {
                    "fischer": case["variant"], "simple": "simple16"
                }, "manifest_sha256": bind,
                "game": _make_clock(
                    case, roots[case["root"]],
                    engines[case["variant"]], engines["simple16"]
                ),
            }
            atomic_json(target / (case["id"] + ".json"),
                        _validate_game(record, case, roots[case["root"]], bind))
            print("TAL_CLOCK", index, i, len(game_cases),
                  record["game"]["status"], flush=True)
        positions = 0
        for meta, board in _source_style_positions(archive_dir):
            if style_shard(meta) != index:
                continue
            if meta != source_styles[meta["id"]]:
                raise ValueError("Inconsistent Tal historical style case")
            decisions = {}
            measured = {}
            achieved = {}
            for name in STYLE_ROLES:
                if name == "human":
                    move = chess.Move.from_uci(meta["human"])
                else:
                    move, tele = choose(engines[name], board, 16)
                    achieved[name] = tele["reached_requested"]
                decisions[name] = move.uci()
                measured[name] = features(board, move)
            repeat = (
                choose(engines["balanced16"], board, 16)[0].uci()
                if int(meta["id"][:12], 16) % REPEAT_MOD == 0 else None
            )
            entry = {
                "schema": SCHEMA, "id": meta["id"],
                "game": meta["game"], "phase": meta["phase"],
                "ply": meta["ply"], "fen_sha256": meta["fen_sha256"],
                "human": meta["human"],
                "original16": decisions["original16"],
                "balanced16": decisions["balanced16"],
                "simple16": decisions["simple16"],
                "features": measured, "repeat_balanced": repeat,
                "depth_met": achieved, "manifest_sha256": bind,
            }
            atomic_json(target / (meta["id"] + ".json"),
                        _validate_style(entry, meta, board, bind))
            positions += 1
        if positions != len(style_cases):
            raise ValueError("Missing one or more source Tal positions")
    # Neither official profiles nor binary may have been edited.
    from .tal_full_games import _profiles as original_profiles
    _, _, _, _, paths, original_hashes = original_profiles(rodent_root)
    if original_hashes != originals:
        raise ValueError("Original Rodent installation modified during run")
    return {"schema": SCHEMA, "shard": index, "clock_games": len(game_cases),
            "style_positions": positions, "candidate_hash": candidates["TalBalanced"]}


def _points(result, color):
    if result == "1/2-1/2":
        return 0.5
    if result not in ("1-0", "0-1"):
        raise ValueError("Cannot score incomplete clock game")
    return float((result == "1-0") == (color == "white"))


def paired_ci(pairs):
    if len(pairs) < 10 or any(len(v) != 2 for v in pairs.values()):
        return None
    rng = random.Random(20261124)
    blocks = list(pairs.values())
    boots = []
    for _ in range(3000):
        draw = rng.choices(blocks, k=len(blocks))
        boots.append(sum(sum(x) for x in draw) / (2 * len(draw)))
    boots.sort()
    return [boots[74], boots[2924]]


def aggregate(shards, archive_dir=None):
    source = source_cases(archive_dir)
    roots = {x["id"]: x for x in source["clock_roots"]}
    style_meta = source["style_cases"]
    boards = {
        meta["id"]: board
        for meta, board in _source_style_positions(archive_dir)
    }
    if set(boards) != set(style_meta):
        raise ValueError("Tal style corpus does not replay")
    records, styles = [], []
    fingerprints = None
    for index in range(SHARDS):
        d = Path(shards) / f"shard-{index}"
        manifest = load_json(d / "manifest.json")
        games, probes = shard_plan(source, index)
        expected = {
            "schema": SCHEMA, "index": index,
            "pgn_sha256": source["pgn_sha256"],
            "clock_ids": sorted(x["id"] for x in games),
            "style_ids": sorted(x["id"] for x in probes),
            "prior_doe_run": PRIOR_DOE_RUN,
            "frozen_profile_hashes": manifest["frozen_profile_hashes"],
            "candidate": source["candidate"],
            "tune_games": source["tune_games"],
        }
        if manifest != expected:
            raise ValueError("Mismatched source/protocol Tal shard")
        fp = manifest["frozen_profile_hashes"]
        if fp["protocol_sha256"] != file_hash(Path(__file__)):
            raise ValueError("Tal protocol mutated during run")
        frozen = frozen_fischer()["hashes"]
        for key, required in (
            ("rodent", frozen["rodent"]),
            ("fischer", frozen["fischer_profile"]),
            ("simple", frozen["simple_control"]),
            ("tal", TAL_ORIGINAL_SHA),
        ):
            if fp["originals"].get(key) != required:
                raise ValueError("Tal experiment changed original identity")
        if not isinstance(fp["candidates"].get("TalBalanced"), str):
            raise ValueError("Missing Tal Balanced candidate hash")
        if fingerprints is None:
            fingerprints = fp
        elif fp != fingerprints:
            raise ValueError("Inconsistent Tal UCI profiles across runners")
        expected_files = (
            {"manifest.json"} |
            {c["id"] + ".json" for c in games} |
            {s["id"] + ".json" for s in probes}
        )
        if {x.name for x in d.iterdir()} != expected_files:
            raise ValueError("Missing, duplicate or additional Tal record files")
        binding = digest(manifest)
        for case in games:
            doc = load_json(d / (case["id"] + ".json"))
            _validate_game(doc, case, roots[case["root"]], binding)
            records.append(doc)
        for probe in probes:
            doc = load_json(d / (probe["id"] + ".json"))
            _validate_style(doc, probe, boards[probe["id"]], binding)
            styles.append(doc)
    if len(records) != len(source["clock_cases"]) or len(styles) != len(style_meta):
        raise ValueError("Incomplete Tal retrospective evidence")
    censored = [x["case"]["id"] for x in records
                if x["game"]["status"] != "COMPLETE"]
    base = {
        "schema": SCHEMA, "clock_games": len(records),
        "clock_complete": len(records) - len(censored),
        "clock_censored": len(censored),
        "style_positions": len(styles),
        "style_games": len({x["game"] for x in styles}),
        "candidate_hash_sha256": fingerprints["candidates"]["TalBalanced"],
        "fischer_untouched": True, "tal_official_untouched": True,
        "blind_holdout": False, "status": "INCOMPLETE_DO_NOT_SCORE",
        "time_control": "10+2",
        "prior_doe_run": PRIOR_DOE_RUN,
        "style": None, "clock": None, "gates": None,
        "do_not_auto_promote": True,
    }
    if censored:
        base["censored_case_ids"] = censored
        return base
    grouped = defaultdict(lambda: defaultdict(dict))
    wdl = defaultdict(Counter)
    flag = Counter()
    for doc in records:
        case, game = doc["case"], doc["game"]
        variant = case["variant"]
        score = _points(game["result"], case["color"])
        grouped[variant][case["root"]][case["color"]] = score
        wdl[variant]["win" if score == 1 else "draw" if score == .5 else "loss"] += 1
        flag[(variant, game["time_forfeit"])] += int(game["time_forfeit"] is not None)
    for variant in CLOCK_VARIANTS:
        if len(grouped[variant]) != ROOTS or any(
            set(pair) != {"white", "black"} for pair in grouped[variant].values()
        ):
            raise ValueError("Missing paired 10+2 Tal games")
    by_variant = {
        variant: {
            "games": ROOTS * 2,
            "score": sum(v for pair in grouped[variant].values()
                         for v in pair.values()) / (ROOTS * 2),
            "wdl": dict(wdl[variant]),
            "flags_tal": flag[(variant, "fischer")],
            "flags_simple": flag[(variant, "simple")],
        } for variant in CLOCK_VARIANTS
    }
    diffs = {
        key: [grouped["balanced16"][key][color] -
              grouped["original16"][key][color]
              for color in ("white", "black")] for key in grouped["balanced16"]
    }
    by_variant["balanced16"]["gain_vs_original16"] = mean(
        [v for two in diffs.values() for v in two])
    by_variant["balanced16"]["gain_ci95"] = paired_ci(diffs)
    style_rates = {
        role: {
            feature: mean(int(x["features"][role][feature]) for x in styles)
            for feature in STYLE_FEATURES
        } for role in STYLE_ROLES
    }
    agreement = {
        role: sum(x[role] == x["human"] for x in styles) / len(styles)
        for role in STYLE_ROLES if role != "human"
    }
    differences = {
        "balanced_vs_simple": sum(x["balanced16"] != x["simple16"]
                                  for x in styles) / len(styles),
        "balanced_vs_original": sum(x["balanced16"] != x["original16"]
                                    for x in styles) / len(styles),
    }
    closer_to_human = [
        feature for feature in STYLE_FEATURES
        if abs(style_rates["balanced16"][feature] - style_rates["human"][feature])
        <= abs(style_rates["simple16"][feature] - style_rates["human"][feature]) + .025
    ]
    aggressive = all(
        style_rates["balanced16"][feature] >= style_rates["original16"][feature] - .10
        for feature in ("forcing", "check")
    )
    repeats = [x for x in styles if x["repeat_balanced"] is not None]
    reproducible = (
        len(repeats) >= 12 and
        sum(x["repeat_balanced"] == x["balanced16"] for x in repeats) / len(repeats)
        >= .95
    )
    achieved = sum(x["depth_met"]["balanced16"] for x in styles) / len(styles)
    style_pass = (
        len(styles) >= MIN_STYLES and
        len({x["game"] for x in styles}) >= MIN_STYLE_GAMES and
        differences["balanced_vs_simple"] >= .05 and
        agreement["balanced16"] >= .30 and
        agreement["balanced16"] >= agreement["original16"] - .08 and
        len(closer_to_human) >= 2 and aggressive
    )
    clock_pass = (
        by_variant["balanced16"]["score"] >= .40 and
        by_variant["balanced16"]["gain_vs_original16"] >= .10 and
        by_variant["balanced16"]["gain_ci95"][0] > 0 and
        by_variant["balanced16"]["flags_tal"] == 0 and
        by_variant["original16"]["flags_tal"] == 0
    )
    gates = {
        "prior_strength_doe": True,
        "clock_competitive_gain": clock_pass,
        "historical_style_retained": style_pass,
        "repeatability": reproducible and achieved >= .90,
    }
    base.update({
        "status": (
            "ELIGIBLE_FOR_TAL_BALANCED_ASSET_RELEASE_REVIEW"
            if all(gates.values()) else "DO_NOT_FREEZE_TAL_BALANCED"
        ), "gates": gates,
        "clock": by_variant,
        "style": {
            "rates": style_rates, "historical_move_agreement": agreement,
            "different_moves": differences,
            "closer_to_human_than_simple_with_tolerance": closer_to_human,
            "forcing_check_retention": aggressive,
            "repeats": len(repeats),
            "repeat_agreement": (
                sum(x["repeat_balanced"] == x["balanced16"] for x in repeats)
                / len(repeats) if repeats else None
            ),
            "balanced_depth_reached": achieved,
        },
    })
    return base


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "play", "aggregate"):
        p = sub.add_parser(mode)
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--fetch", action="store_true")
        source.add_argument("--archive-dir", type=Path)
        if mode == "play":
            p.add_argument("--rodent-root", type=Path, required=True)
            p.add_argument("--index", type=int, required=True)
            p.add_argument("--out", type=Path, required=True)
        if mode == "aggregate":
            p.add_argument("--shards", type=Path, required=True)
            p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "plan":
        report = public_plan(source_cases(args.archive_dir))
    elif args.mode == "play":
        report = run_shard(args.rodent_root, args.out, args.index, args.archive_dir)
    else:
        report = aggregate(args.shards, args.archive_dir)
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("Do not overwrite an existing release report")
        atomic_json(args.out, report)
    print(json.dumps(report, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
