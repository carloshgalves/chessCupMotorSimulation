"""Bounded Tal strength improvement DOE; no historical Elo or automatic promotion.

A diagnosis-driven sandbox compares original Tal with three conservative
parameter repairs and a deeper-search control. The official personality,
Rodent binary, Fischer freeze and game Arena never change.
Pilot root selection and independent confirmation root selection are
committed before seeing any candidate result. No TUNE/TEST fitting.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import shutil

import chess

from .catalog import Player
from .fischer_deep_calibration import NODE_CAP, choose
from .fischer_full_games import atomic_json, digest, initial_board, load_json
from .matches import _open_engine, file_hash
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _iter_verified_games
from .tal_full_games import _profiles, frozen_fischer, source_panel as old_panel
from .tal_product_plan import load_source

SCHEMA = "chesssim-tal-strength-profile-doe-v1"
PILOT_ROOTS = 12
CONFIRM_ROOTS = 12
SHARDS = 12
MAX_PLIES = 1800
ROOT_SALT = "tal-strength-fit-unseen-root-design-2026-11-23-v1"
BOOTSTRAP_SALT = 8301297
PREVIOUS_GAME_RUN = 45167232903
EXPECTED_GAMES = 2431
TAL_ORIGINAL_SHA = "f6b58d7cc9e7d2fcdbbc24a20ea72035d05ca5ed5e3401bda240b1b298ce149d"

# Only values which already occur in Rodent IV's documented original
# personality parameters; never overwrite this source profile on disk.
# Each modification is a mechanistic HYPOTHESIS, not a proven improvement.
VARIANTS = {
    "original16": {"depth": 16, "overrides": {}, "alias": "Tal"},
    "defense16": {"depth": 16, "alias": "TalDefense",
                  "overrides": {"OppAttack": 145, "OppMobility": 90,
                                "PawnShield": 150, "PawnStorm": 150}},
    "balanced16": {"depth": 16, "alias": "TalBalanced",
                   "overrides": {"OwnAttack": 260, "OppAttack": 135,
                                 "OwnMobility": 105, "OppMobility": 90,
                                 "KingTropism": 60, "PiecePressure": 150,
                                 "PawnShield": 150, "PawnStorm": 145}},
    "material16": {"depth": 16, "alias": "TalMaterial",
                   "overrides": {"PawnValueMg": 100, "KnightValueMg": 335,
                                 "BishopValueMg": 350, "RookValueMg": 510,
                                 "QueenValueMg": 1040, "PawnValueEg": 100,
                                 "KnightValueEg": 340, "BishopValueEg": 350,
                                 "RookValueEg": 530, "QueenValueEg": 1040}},
    "original18": {"depth": 18, "overrides": {}, "alias": "Tal"},
}
CANDIDATES = tuple(k for k in VARIANTS if k != "original16")
STYLE_NOTE = "Style retention not measured yet; no candidate can be frozen."


def design(archive_dir=None):
    """All historical games scanned. Training panel excludes earlier 608 roots."""
    old_roots, prior = old_panel(archive_dir)
    pgn, rows, _, source, surname = load_source(archive_dir)
    if len(rows) != EXPECTED_GAMES or sha256(pgn).hexdigest() != prior["source_pgn_sha256"]:
        raise ValueError("The Tal historical source is no longer the audited snapshot")
    previous_fit = {tuple(root["prefix"]) for root in old_roots if root["arm"] == "fit"}
    groups = defaultdict(set)
    seen = set()
    for game, row in _iter_verified_games(pgn, rows, surname):
        gid = row["mainline_sha256"]
        if gid in seen:
            raise ValueError("Duplicate Tal historical game")
        seen.add(gid)
        arm = neural_arm(gid)
        if arm not in ("fit", "tune", "excluded_test"):
            raise ValueError("Unrecognized historical arm")
        if arm == "excluded_test":
            continue
        if game.board().fen() != chess.STARTING_FEN:
            continue
        board = game.board()
        prefix = []
        for move in game.mainline_moves():
            if len(prefix) == 12:
                break
            if move not in board.legal_moves:
                raise ValueError("Illegal Tal source root")
            prefix.append(move.uci())
            board.push(move)
        if len(prefix) == 12 and not board.is_game_over(claim_draw=True):
            groups[(arm, tuple(prefix))].add(gid)
    if len(seen) != len(rows):
        raise ValueError("Omitted historical source records")
    tune = {prefix for arm, prefix in groups if arm == "tune"}
    candidates = []
    for (arm, prefix), gids in groups.items():
        if arm != "fit" or prefix in tune or prefix in previous_fit:
            continue
        signature = digest([ROOT_SALT, list(prefix)])
        rank = sha256((ROOT_SALT + ":" + signature).encode()).hexdigest()
        candidates.append((rank, signature, prefix, gids))
    candidates.sort()
    chosen = candidates[:PILOT_ROOTS + CONFIRM_ROOTS]
    if len(chosen) != PILOT_ROOTS + CONFIRM_ROOTS:
        raise ValueError("Insufficient fresh and exclusive FIT roots")
    panel = []
    for i, (rank, ident, prefix, gids) in enumerate(chosen):
        panel.append({
            "id": ident, "prefix": list(prefix),
            "arm": "fit", "rank": rank, "source_games": sorted(gids),
            "stage": "pilot" if i < PILOT_ROOTS else "confirm",
        })
    if len({tuple(x["prefix"]) for x in panel}) != len(panel):
        raise ValueError("Repeated chosen Tal prefix")
    if any(tuple(x["prefix"]) in previous_fit or tuple(x["prefix"]) in tune
           for x in panel):
        raise ValueError("Source contamination across previous FIT/TUNE samples")
    meta = {
        "schema": SCHEMA, "status": "DESIGN_ONLY_NO_IMPROVEMENT_CLAIM",
        "previous_game_run": PREVIOUS_GAME_RUN,
        "pgn_sha256": sha256(pgn).hexdigest(),
        "pinned_source": source,
        "source_counts": prior["development_game_counts"],
        "reserved_test_games": prior["reserved_test_games"],
        "fresh_fit_frame": len(candidates),
        "prior_fit_roots_excluded": len(previous_fit),
        "roots_pilot": PILOT_ROOTS,
        "roots_confirm": CONFIRM_ROOTS,
        "roots_sha256": digest(panel),
        "variants": VARIANTS, "profile_source_sha256": TAL_ORIGINAL_SHA,
        "fischer": frozen_fischer(),
        "cases": len(panel) * 2 * len(VARIANTS),
        "shards": SHARDS, "node_cap": NODE_CAP,
        "original_personalities_modified": False,
        "outer_test_decisions_evaluated": 0,
        "tal_frozen": False,
        "limitations": [
            "A bounded, randomized FIT-only experiment; cannot infer Elo.",
            "Confirmation roots remain FIT corpus but were not part of prior 608 games.",
            "Profile variants are hypotheses until complete paired games show an effect.",
            STYLE_NOTE,
        ],
    }
    return panel, meta


def render_tal_variant(original_text, overrides):
    """Replace exactly one numeric source assignment per permitted parameter."""
    if not original_text.startswith("; Personality for Rodent IV chess engine"):
        raise ValueError("Unexpected original Tal personality syntax")
    result = original_text
    for key, value in sorted(overrides.items()):
        if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", key)
                or type(value) is not int or not 0 <= value <= 1200):
            raise ValueError("Invalid candidate parameter")
        regex = rf"(?m)^(setoption name {re.escape(key)} value )(-?\d+)[ \t]*$"
        result, count = re.subn(regex, lambda m: m.group(1) + str(value), result)
        if count != 1:
            raise ValueError(f"Tal personality lacks exactly one {key}")
    return result


def make_isolated_profiles(real_root, workdir):
    """Copy immutable engine and personality files to an ephemeral test root."""
    root, exe, tal, simple, source_paths, hashes = _profiles(real_root)
    if hashes["tal"] != TAL_ORIGINAL_SHA:
        raise ValueError("Tal original profile changed since 608-game baseline")
    target = Path(workdir)
    if target.exists() or target.is_symlink():
        raise ValueError("Sandbox must be new; do not overwrite a previous experiment")
    target.mkdir(parents=True)
    shutil.copy2(exe, target / "rodent-iv-plain.exe")
    shutil.copytree(root / "personalities", target / "personalities",
                    symlinks=False)
    p = target / "personalities"
    tal_bytes = source_paths["tal"].read_bytes()
    if sha256(tal_bytes).hexdigest() != TAL_ORIGINAL_SHA:
        raise ValueError("Tal bytes changed during materialization")
    original_text = tal_bytes.decode("utf-8-sig")
    aliases = []
    overlay_hashes = {"Tal": TAL_ORIGINAL_SHA}
    for name in ("defense16", "balanced16", "material16"):
        spec = VARIANTS[name]
        file = f"{spec['alias'].lower()}.txt"
        payload = render_tal_variant(original_text, spec["overrides"])
        (p / file).write_text(payload, encoding="utf-8")
        overlay_hashes[spec["alias"]] = file_hash(p / file)
        aliases.append(f"{spec['alias']}={file}")
    registry = p / "basic.ini"
    raw = registry.read_text()
    start = raw.find("PERSONALITY_SET=all\n")
    end = raw.find("PERSONALITY_SET=distinct\n")
    if start < 0 or end <= start or raw.count("PERSONALITY_SET=all") != 1:
        raise ValueError("Unknown Rodent personality set layout")
    begin = raw[:start]
    tail = raw[end:]
    header = "PERSONALITY_SET=all\n"
    lines = ["Simple=simple.txt", "Fischer=fischer.txt",
             "Tal=tal.txt", *aliases]
    registry.write_text(begin + header + "\n".join(lines) +
                        "\n\n# Test-only reduced alias list\n" + tail)
    # Every original is unchanged, including the precise frozen Fischer binary.
    for name in ("rodent", "tal", "fischer", "simple"):
        file = {"rodent": target / "rodent-iv-plain.exe",
                "tal": p / "tal.txt", "fischer": p / "fischer.txt",
                "simple": p / "simple.txt"}[name]
        if file_hash(file) != hashes[name]:
            raise ValueError("Overlay illegally altered a frozen/source asset")
    return target, hashes, overlay_hashes


def game_cases(panel):
    for root in panel:
        for variant in VARIANTS:
            for color in ("white", "black"):
                yield {"id": digest([SCHEMA, root["id"], variant, color]),
                       "root": root["id"], "variant": variant,
                       "color": color, "stage": root["stage"],
                       "depth": VARIANTS[variant]["depth"]}


def shard_cases(panel, index, count=SHARDS):
    if (type(index) is not int or type(count) is not int or
            not 1 <= count <= SHARDS or not 0 <= index < count):
        raise ValueError("Invalid Tal DOE shard")
    ordered = sorted(panel, key=lambda x: x["id"])
    # All 10 paired games from one root are in the same shard.
    return list(game_cases([x for i, x in enumerate(ordered) if i % count == index]))


def play(case, root, focus, simple):
    board = initial_board(root)
    focal_white = case["color"] == "white"
    moves = []
    tele = defaultdict(Counter)
    for _ in range(MAX_PLIES):
        if board.is_game_over(claim_draw=True):
            break
        role = "tal" if board.turn == focal_white else "simple"
        wanted = case["depth"] if role == "tal" else 16
        engine = focus if role == "tal" else simple
        move, info = choose(engine, board, wanted)
        if move not in board.legal_moves:
            raise ValueError("Illegal candidate move")
        tele[role]["searched"] += 1
        tele[role]["depth_reached"] += int(info["reached_requested"])
        tele[role]["depth_missing"] += int(info["reported_depth"] is None)
        tele[role]["nodes_exceeded"] += int(
            info["reported_nodes"] is not None and info["reported_nodes"] > NODE_CAP)
        moves.append(move.uci())
        board.push(move)
    ending = board.outcome(claim_draw=True)
    return {
        "case": case, "moves": moves,
        "result": ending.result() if ending else "*",
        "termination": ending.termination.name if ending else "PLY_CAP_INCOMPLETE",
        "status": "COMPLETE" if ending else "INCOMPLETE",
        "telemetry": {role: dict(v) for role, v in tele.items()},
    }


def validate(record, case, root):
    if not isinstance(record, dict) or record.get("case") != case:
        raise ValueError("Wrong candidate/position binding")
    moves = record.get("moves")
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_PLIES:
        raise ValueError("Bad full-game history")
    board = initial_board(root)
    turns = Counter()
    white = case["color"] == "white"
    for encoded in moves:
        if board.is_game_over(claim_draw=True):
            raise ValueError("Moves after terminal")
        if not isinstance(encoded, str):
            raise ValueError("Malformed move")
        move = chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal historical continuation")
        turns["tal" if board.turn == white else "simple"] += 1
        board.push(move)
    result = board.outcome(claim_draw=True)
    expected = ((result.result(), result.termination.name, "COMPLETE")
                if result else ("*", "PLY_CAP_INCOMPLETE", "INCOMPLETE"))
    if tuple(record.get(k) for k in ("result", "termination", "status")) != expected:
        raise ValueError("False adjudication or fake draw")
    if result is None and len(moves) != MAX_PLIES:
        raise ValueError("Incomplete game not at explicit cap")
    tele = record.get("telemetry")
    if not isinstance(tele, dict) or set(tele) != set(turns):
        raise ValueError("Missing search telemetry")
    for role, total in turns.items():
        row = tele[role]
        if (set(row) != {"searched", "depth_reached", "depth_missing",
                         "nodes_exceeded"}
                or row["searched"] != total
                or any(type(x) is not int or not 0 <= x <= total
                       for x in row.values())):
            raise ValueError("Tampered per-turn telemetry")
    return record


def run_shard(rodent_root, out, index, archive_dir=None):
    panel, meta = design(archive_dir)
    planned = shard_cases(panel, index)
    if not planned:
        raise ValueError("Empty experiment shard")
    roots = {x["id"]: x for x in panel}
    real_root, exe, tal, simple, paths, hashes = _profiles(rodent_root)
    target = Path(out)
    if target.exists() or target.is_symlink():
        raise ValueError("Output checkpoint directory must be created by experiment")
    overlay, originals, variants = make_isolated_profiles(
        real_root, target.parent / f"tal-overlay-{index}")
    if originals != hashes:
        raise ValueError("Profile originals changed before experiment")
    manifest = {**meta, "index": index,
                "case_ids": [c["id"] for c in planned],
                "original_hashes": originals,
                "variant_hashes": variants}
    target.mkdir(parents=False)
    atomic_json(target / "manifest.json", manifest)
    bind = digest(manifest)
    # The parameter files are loaded by UCI Personality aliases, not
    # nonexistent hidden spin options. Never select an absent alias.
    with ExitStack() as stack:
        players = {}
        for name, spec in VARIANTS.items():
            if spec["alias"] in players:
                continue
            person = Player(number=0, name=name, slug="tal-doe-"+name,
                            group="baseline", rodent_alias=spec["alias"],
                            rodent_file=(spec["alias"].lower()+".txt"
                                         if spec["alias"] != "Tal" else "tal.txt"))
            players[spec["alias"]] = stack.enter_context(
                _open_engine(overlay, overlay / "rodent-iv-plain.exe", person, book=False))
        control = stack.enter_context(
            _open_engine(overlay, overlay / "rodent-iv-plain.exe", simple, book=False))
        for i, case in enumerate(planned, 1):
            file = target / (case["id"]+".json")
            person = players[VARIANTS[case["variant"]]["alias"]]
            record = play(case, roots[case["root"]], person, control)
            record["manifest_sha256"] = bind
            atomic_json(file, validate(record, case, roots[case["root"]]))
            print("TAL_DOE", index, i, len(planned), record["status"], flush=True)
    if hashes != {k: file_hash(v) for k, v in paths.items()}:
        raise ValueError("Original profiles were modified by experiment")
    if variants != {k: file_hash(overlay / "personalities" /
                    (k.lower()+".txt")) if k != "Tal" else TAL_ORIGINAL_SHA
                    for k in variants}:
        raise ValueError("Candidate profile changed during run")
    return {"schema": SCHEMA, "shard": index, "played": len(planned)}


def _score(result, color):
    if result == "1/2-1/2":
        return .5
    if result not in ("1-0", "0-1"):
        raise ValueError("Unscorable nonterminal DOE result")
    return float((result == "1-0") == (color == "white"))


def ci_by_root(grouped):
    rng = random.Random(BOOTSTRAP_SALT)
    blocks = list(grouped.values())
    if len(blocks) < 10 or any(len(x) != 2 for x in blocks):
        return None
    values = []
    for _ in range(3000):
        picked = rng.choices(blocks, k=len(blocks))
        values.append(sum(sum(x) for x in picked) / (2*len(blocks)))
    values.sort()
    return [values[74], values[2924]]


def difference_ci(pairs):
    # One root corresponds to both reversed colors, never to independent games.
    return ci_by_root(pairs)


def aggregate(shards, archive_dir=None):
    panel, meta = design(archive_dir)
    roots = {x["id"]: x for x in panel}
    seen = set()
    scores = defaultdict(lambda: defaultdict(dict))
    manifests = []
    completed = 0
    partial = []
    for index in range(SHARDS):
        path = Path(shards) / f"shard-{index}"
        manifest = load_json(path / "manifest.json")
        cases = shard_cases(panel, index)
        needed = {**meta, "index": index,
                  "case_ids": [c["id"] for c in cases],
                  "original_hashes": manifest["original_hashes"],
                  "variant_hashes": manifest["variant_hashes"]}
        if manifest != needed:
            raise ValueError("Experiment shard manifesto mismatch")
        if manifest["original_hashes"]["tal"] != TAL_ORIGINAL_SHA:
            raise ValueError("Original Tal source mismatch")
        locked = frozen_fischer()["hashes"]
        for key,expected in (("rodent",locked["rodent"]),
                             ("fischer",locked["fischer_profile"]),
                             ("simple",locked["simple_control"])):
            if manifest["original_hashes"][key] != expected:
                raise ValueError("Fischer immutability violated")
        for value in manifest["variant_hashes"].values():
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Invalid candidate SHA")
        manifests.append((manifest["original_hashes"],
                          manifest["variant_hashes"]))
        if {x.name for x in path.iterdir()} != (
                {"manifest.json"} | {c["id"]+".json" for c in cases}):
            raise ValueError("Missing extra or partial Tal DOE cases")
        for case in cases:
            if case["id"] in seen:
                raise ValueError("Duplicated game evidence")
            seen.add(case["id"])
            entry = load_json(path / (case["id"]+".json"))
            if entry.get("manifest_sha256") != digest(manifest):
                raise ValueError("Wrong checkpoint source")
            validate(entry, case, roots[case["root"]])
            if entry["status"] != "COMPLETE":
                partial.append(case["id"])
                continue
            completed += 1
            scores[case["stage"]][case["root"]][
                (case["variant"],case["color"])] = _score(
                    entry["result"],case["color"])
    if len(seen) != meta["cases"] or len(set(digest(x) for x in manifests)) != 1:
        raise ValueError("Omitted cases or inconsistent candidate hashes")
    output = {**meta, "observed_games":len(seen), "terminal_games":completed,
              "censored_games":len(partial), "status":"INCOMPLETE_DO_NOT_SCORE",
              "trial":None, "confirmation":None, "selected_candidate":None,
              "official_tal_modified":False, "official_fischer_modified":False}
    if partial:
        output["unscored_case_ids"] = partial
        return output
    stage_stats = {}
    for stage,n in (("pilot",PILOT_ROOTS),("confirm",CONFIRM_ROOTS)):
        stage_scores = {x["id"]: scores[stage][x["id"]]
                        for x in panel if x["stage"] == stage}
        if len(stage_scores) != n:
            raise ValueError("Missing stage roots")
        base = {root: [row[("original16", color)] for color in ("white","black")]
                for root,row in stage_scores.items()}
        cells = {}
        for name in VARIANTS:
            trial = {root: [row[(name, color)] for color in ("white","black")]
                     for root,row in stage_scores.items()}
            differences = {
                root:[a-b for a,b in zip(trial[root], base[root])]
                for root in stage_scores}
            values = [x for two in trial.values() for x in two]
            diff = [x for two in differences.values() for x in two]
            cells[name] = {
                "games":n*2,
                "points":sum(values),
                "score":sum(values)/len(values),
                "ci95":ci_by_root(trial),
                "improvement_vs_original16":sum(diff)/len(diff),
                "improvement_ci95":difference_ci(differences),
            }
        stage_stats[stage] = cells
    chosen = max(CANDIDATES, key=lambda name: (
        stage_stats["pilot"][name]["improvement_vs_original16"], name))
    gain = stage_stats["pilot"][chosen]["improvement_vs_original16"]
    confirm = stage_stats["confirm"][chosen]
    output["trial"] = stage_stats["pilot"]
    output["confirmation"] = stage_stats["confirm"]
    output["selected_candidate"] = chosen if gain >= .10 else None
    output["status"] = (
        "PROMISING_STRENGTH_REPAIR_STYLE_GATE_PENDING"
        if gain >= .10 and confirm["improvement_vs_original16"] >= .10 and
        confirm["improvement_ci95"][0] > 0 else
        "NO_VALIDATED_STRENGTH_GAIN_DO_NOT_PROMOTE"
    )
    output["no_human_elo_inferred"] = True
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan","play","aggregate"):
        p=sub.add_parser(command)
        source=p.add_mutually_exclusive_group(required=True)
        source.add_argument("--fetch",action="store_true")
        source.add_argument("--archive-dir",type=Path)
        if command=="play":
            p.add_argument("--rodent-root",type=Path,required=True)
            p.add_argument("--index",type=int,required=True)
            p.add_argument("--out",type=Path,required=True)
        if command=="aggregate":
            p.add_argument("--shards",type=Path,required=True)
            p.add_argument("--out",type=Path,required=True)
    args = parser.parse_args(argv)
    if args.command=="plan":
        _,result=design(args.archive_dir)
    elif args.command=="play":
        result=run_shard(args.rodent_root,args.out,args.index,args.archive_dir)
    else:
        result=aggregate(args.shards,args.archive_dir)
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("Refuse overwrite of scientific results")
        atomic_json(args.out,result)
    print(json.dumps(result,sort_keys=True,allow_nan=False),flush=True)
    return int(result.get("status")=="INCOMPLETE_DO_NOT_SCORE")


if __name__ == "__main__":
    raise SystemExit(main())
