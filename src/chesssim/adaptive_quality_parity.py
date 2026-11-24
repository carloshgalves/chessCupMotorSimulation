"""Compare Tal Balanced and frozen Fischer to each one's HUMAN move quality.

This is NOT an Elo estimator. Both historical cohorts and all four Rodent
profiles are judged by the SAME independently hashed Stockfish reference
on original historical boards, not by chess matches or human-move agreement.
A missing mate score or an unsupported tactical phase never silently passes.
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
import chess.engine

from .catalog import find_player
from .fischer_baseline import phase
from .fischer_deep_calibration import choose, tags
from .fischer_full_games import atomic_json, digest, load_json
from .matches import _open_engine, file_hash
from .real85_graded_phase_attribution import _score
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _iter_verified_games
from .fischer_competency_audit import _source as fischer_source
from .tal_product_plan import load_source as tal_source
from .tal_strength_doe import make_isolated_profiles, TAL_ORIGINAL_SHA
from .tal_full_games import frozen_fischer
from .tal_balanced_clock_replication import BALANCED_SHA
from .tal_balanced_validation import _profiles as policy_players

SCHEMA = "chesssim-adaptive-human-move-quality-parity-v1"
REFERENCE_DEPTH = 24
DEPTHS = (24, 28, 32)
STOCKFISH_SHA = "250ff9c3548d06b06616356b01972230607469c95fb187e262f7044c4f6cfbe9"
SOURCE_ARM = "fit"
ENGINE_DEPTH = 16
SHARDS = 32
GAMES_PER_ERA = 96
SALT = "fischer-tal-peak-and-career-move-quality-2026-11-24-v1"
PEAK_YEARS = {"fischer": (1970, 1972), "tal": (1957, 1961)}
EXPECTED_GAMES = {"fischer": 827, "tal": 2431}
EXPECTED_ARMS = {
    "fischer": {"fit": 683, "tune": 23, "excluded_test": 121},
    "tal": {"fit": 2024, "tune": 66, "excluded_test": 341},
}
EXPECTED_SOURCE_SHA = {
    "fischer": "dcb6428779f2506c302c18148b36e83b56123397e2e348d325e292ae01557d19",
    "tal": "586aac4613e170474f12893679ab992d3999ca5109df7df77c634a76846d2687",
}
POLICIES = ("human", "fischer", "tal_balanced", "tal_original", "simple")
PHASES = ("opening", "middlegame", "endgame")
MIN_PHASE_NUMERIC = 20
MIN_TACTICAL_NUMERIC = 20
MIN_NUMERIC_COVERAGE = .85
MIN_REACHED_DEPTH = .90
MAX_REFERENCE_INCONSISTENCY = .02
NONINFERIORITY_CP = 15.0
TACTICAL_NONINFERIORITY_CP = 20.0
SEVERE_NONINFERIORITY_RATE = .05
BOOTSTRAPS = 2000


def source_rows(archive_dir=None):
    docs = {}
    for name, reader in (("fischer", fischer_source), ("tal", tal_source)):
        pgn, rows, scan, origin, surname = reader(archive_dir)
        arms = dict(Counter(neural_arm(row["mainline_sha256"]) for row in rows))
        if (len(rows) != EXPECTED_GAMES[name] or
                arms != EXPECTED_ARMS[name] or
                sha256(pgn).hexdigest() != EXPECTED_SOURCE_SHA[name]):
            raise ValueError(f"Historical {name} source census or SHA changed")
        docs[name] = (pgn, rows, scan, origin, surname)
    return docs


def era(name, year):
    if str(year).isdigit() and PEAK_YEARS[name][0] <= int(year) <= PEAK_YEARS[name][1]:
        return "peak"
    return "other"


def choose_games(sources):
    selections = {}
    taken = set()
    for player in ("fischer", "tal"):
        _, rows, _, _, _ = sources[player]
        selected = []
        for interval in ("peak", "other"):
            # A black player in a source game ending after white's first
            # ply has made NO historical choice. Exclude this by pre-move
            # source length, never by a quality or engine evaluation.
            eligible = [r for r in rows if neural_arm(r["mainline_sha256"]) == SOURCE_ARM
                        and era(player, r["date"]) == interval
                        and r["mainline_sha256"] not in taken
                        and r["plies"] >= (1 if r["color"] == "white" else 2)]
            eligible.sort(key=lambda r: (
                sha256(f"{SALT}:{player}:{interval}:{r['mainline_sha256']}".encode()
                       ).hexdigest(), r["mainline_sha256"]))
            if SOURCE_ARM == "fit" and len(eligible) < GAMES_PER_ERA:
                raise ValueError(f"Insufficient {player} FIT {interval} source games")
            selected.extend(eligible[:GAMES_PER_ERA] if SOURCE_ARM == "fit" else eligible)
        selections[player] = selected
        taken.update(r["mainline_sha256"] for r in selected)
    return selections


def _positions(game, row, player):
    """One objectively precommitted NONFORCED decision per game and phase."""
    board = game.board()
    if not board.is_valid() or row["color"] not in ("white", "black"):
        raise ValueError("Invalid historical board or focal side")
    choices = {}
    gid = row["mainline_sha256"]
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("Illegal move in original historical game")
        if board.turn == (row["color"] == "white") and board.legal_moves.count() > 1:
            stage = phase(board)
            rank = sha256(f"{SALT}:{player}:{gid}:{stage}:{board.ply()}".encode()
                          ).hexdigest()
            if stage not in choices or rank < choices[stage][0]:
                choices[stage] = (rank, board.copy(stack=True), move)
        board.push(move)
    for stage, (_, original, move) in sorted(choices.items()):
        fen_hash = sha256(original.fen().encode()).hexdigest()
        entry = {
            "id": digest([SCHEMA, player, gid, original.ply(), fen_hash]),
            "player": player, "game": gid, "era": era(player, row["date"]),
            "phase": stage, "ply": original.ply(),
            "fen_sha256": fen_hash, "human_move": move.uci(),
            "legal_moves": original.legal_moves.count(),
            "arm": SOURCE_ARM,
        }
        yield entry, original


def panel(archive_dir=None):
    sources = source_rows(archive_dir)
    chosen = choose_games(sources)
    cases, boards = {}, {}
    for player in ("fischer", "tal"):
        pgn, _, _, _, surname = sources[player]
        for game, row in _iter_verified_games(pgn, chosen[player], surname):
            for entry, board in _positions(game, row, player):
                if entry["id"] in cases:
                    raise ValueError("Repeated historical board identity")
                cases[entry["id"]] = entry
                boards[entry["id"]] = board
    expected = {r["mainline_sha256"] for rows in chosen.values() for r in rows}
    seen = {entry["game"] for entry in cases.values()}
    if not expected <= seen:
        raise ValueError("Entire source game silently omitted from study")
    roots = {player: {
        interval: len([r for r in chosen[player]
                       if era(player, r["date"]) == interval])
        for interval in ("peak", "other")}
        for player in ("fischer", "tal")}
    counters = Counter((c["player"], c["era"], c["phase"]) for c in cases.values())
    return cases, boards, {
        "schema": SCHEMA,
        "source_sha256": EXPECTED_SOURCE_SHA,
        "selected_games": roots,
        "case_counts": {"|".join(k): v for k, v in sorted(counters.items())},
        "positions": len(cases), "shards": SHARDS,
        "engine_depth": ENGINE_DEPTH, "reference_depth": REFERENCE_DEPTH,
        "candidate_sha256": BALANCED_SHA,
        "fischer": frozen_fischer(), "source_arm": SOURCE_ARM,
        "reference_depths": list(DEPTHS),
        "reserved_confirmation": SOURCE_ARM == "excluded_test",
        "sacrifice_quality_automatically_inferred": False,
        "human_elo_estimated": False,
        "thresholds": {
            "upper_mean_excess_margin_cp": NONINFERIORITY_CP,
            "upper_tactical_excess_margin_cp": TACTICAL_NONINFERIORITY_CP,
            "upper_severe_mistake_margin": SEVERE_NONINFERIORITY_RATE,
            "min_phase_numeric": MIN_PHASE_NUMERIC,
            "min_tactical_numeric": MIN_TACTICAL_NUMERIC,
            "min_coverage": MIN_NUMERIC_COVERAGE,
            "min_depth_reached": MIN_REACHED_DEPTH,
            "max_reference_inconsistency": MAX_REFERENCE_INCONSISTENCY,
        },
    }


def shard_cases(cases, index):
    if type(index) is not int or not 0 <= index < SHARDS:
        raise ValueError("Unknown parity shard")
    # Each candidate's complete same-board policy comparison is inseparable.
    groups = defaultdict(list)
    for case in cases.values():
        groups[(case["player"], case["era"], case["game"])].append(case)
    assigned = []
    for player in ("fischer", "tal"):
        for span in ("peak", "other"):
            keyset = sorted([k for k in groups if k[:2] == (player, span)])
            for position, key in enumerate(keyset):
                if position % SHARDS == index:
                    assigned.extend(groups[key])
    return sorted(assigned, key=lambda c: c["id"])


def _numeric_loss(best, chosen):
    if best["mate"] is not None or chosen["mate"] is not None:
        return None
    if type(best["cp"]) is not int or type(chosen["cp"]) is not int:
        raise ValueError("Missing Stockfish numeric score")
    return max(0, best["cp"] - chosen["cp"])


def _policy_metric(best, chosen):
    loss = _numeric_loss(best, chosen)
    return {
        "cp_loss": loss, "numeric": loss is not None,
        "over100": loss > 100 if loss is not None else None,
        "near25": loss <= 25 if loss is not None else None,
        "mate_sensitive": loss is None,
        "missed_winning_mate": (
            best["mate"] is not None and best["mate"] > 0 and
            (chosen["mate"] is None or chosen["mate"] <= 0)),
        "selected_losing_mate": (
            chosen["mate"] is not None and chosen["mate"] < 0),
    }


def _reference_top2(stockfish, board, depth):
    infos = stockfish.analyse(board, chess.engine.Limit(depth=depth),
                              multipv=2, game=object())
    if len(infos) != 2:
        raise ValueError("Incomplete independent reference")
    ranked = []
    for info in infos:
        if (not info.get("pv") or info["pv"][0] not in board.legal_moves
                or type(info.get("depth")) is not int or info["depth"] < depth
                or info.get("score") is None):
            raise ValueError("Reference depth not reached")
        score = info["score"].pov(board.turn)
        ranked.append({"move": info["pv"][0].uci(), "cp": score.score(),
                       "mate": score.mate()})
    if ranked[0]["move"] == ranked[1]["move"]:
        raise ValueError("Duplicate reference roots")
    return ranked


def _disagree(ranked, raw):
    best = raw[ranked[0]["move"]]
    return any(
        (best["mate"] is None and row["mate"] is None
         and row["cp"] > best["cp"] + 35)
        or (row["mate"] is not None and row["mate"] > 0
            and (best["mate"] is None or best["mate"] <= 0))
        or (best["mate"] is not None and best["mate"] < 0
            and (row["mate"] is None or row["mate"] > 0))
        for row in raw.values()
    ) or any(
        (item["mate"] is None and raw[item["move"]]["mate"] is None
         and abs(item["cp"] - raw[item["move"]]["cp"]) > 35)
        or ((item["mate"] is None) != (raw[item["move"]]["mate"] is None))
        or (item["mate"] is not None and raw[item["move"]]["mate"] is not None
            and (item["mate"] > 0) != (raw[item["move"]]["mate"] > 0))
        for item in ranked
    )


def adaptive_reference(stockfish, board, moves):
    history = []
    for depth in DEPTHS:
        ranked = _reference_top2(stockfish, board, depth)
        roots = sorted(set(moves.values()) | {item["move"] for item in ranked})
        raw = {m: _score(stockfish, board, chess.Move.from_uci(m), depth)
               for m in roots}
        inconsistent = _disagree(ranked, raw)
        history.append({"depth": depth, "ranked": ranked, "evals": raw,
                        "inconsistent": inconsistent})
        if not inconsistent:
            break
    return history


def measure(board, human_move, engines, stockfish):
    if (set(engines) != set(POLICIES) - {"human"} or
            human_move not in board.legal_moves or board.legal_moves.count() <= 1):
        raise ValueError("Expected complete same-position policy panel")
    moves = {"human": human_move.uci()}
    telemetry = {}
    for key, engine in engines.items():
        move, telemetry[key] = choose(engine, board, ENGINE_DEPTH)
        moves[key] = move.uci()
    history = adaptive_reference(stockfish, board, moves)
    final = history[-1]
    ranked = final["ranked"]
    raw = final["evals"]
    reference = raw[ranked[0]["move"]]
    stage = phase(board)
    context = tags(board, stage, ranked[0], ranked[1])
    scores = {name: _policy_metric(reference, raw[move])
              for name, move in moves.items()}
    return {
        "phase": stage, "tactical_pre_move": "tactics" in context,
        "pre_move_tags": context,
        "reference": {"best": ranked[0]["move"], "second": ranked[1]["move"],
                      "ranked": ranked, "evals": raw,
                      "inconsistent": final["inconsistent"], "history": history},
        "moves": moves, "scores": scores, "search_telemetry": telemetry,
        "joint_numeric": all(scores[k]["numeric"] for k in
                             ("human", "fischer", "tal_balanced")),
    }


def engines_from_original(rodent_root, sandbox):
    from .tal_full_games import _profiles as verify_profiles
    orig_root, _, _, _, orig_paths, orig_hashes = verify_profiles(rodent_root)
    overlay, hashes, variants = make_isolated_profiles(orig_root, sandbox)
    if hashes != orig_hashes or variants.get("TalBalanced") != BALANCED_SHA:
        raise ValueError("Tal candidate or frozen binary hash differs")
    players = policy_players()
    players["fischer"] = find_player("fischer")
    names = {"fischer": players["fischer"], "tal_balanced": players["balanced16"],
             "tal_original": players["original16"], "simple": players["simple16"]}
    return overlay, names, {"originals": hashes, "variants": variants}


def plan(archive_dir=None):
    cases, _, meta = panel(archive_dir)
    sizes = [len(shard_cases(cases, i)) for i in range(SHARDS)]
    if not all(sizes) or sum(sizes) != len(cases):
        raise ValueError("Lost a paired study position")
    return {**meta, "per_shard": sizes,
            "status": "QUALITY_PARITY_DESIGN_ONLY_NOT_MEASURED"}


def run_shard(rodent_root, stockfish_path, output, index, archive_dir=None):
    cases, boards, meta = panel(archive_dir)
    planned = shard_cases(cases, index)
    if not planned:
        raise ValueError("Empty quality-parity runner")
    target = Path(output)
    if target.exists() or target.is_symlink():
        raise ValueError("Refuse to overwrite original evidence")
    overlay, people, hashes = engines_from_original(
        rodent_root, target.parent / f"tal-fischer-quality-overlay-{index}")
    stockfish_path = Path(stockfish_path).resolve(strict=True)
    if file_hash(stockfish_path) != STOCKFISH_SHA:
        raise ValueError("Reference binary differs from original diagnostic")
    fingerprints = {
        **hashes, "stockfish": file_hash(stockfish_path),
        "protocol_sha256": file_hash(Path(__file__)),
    }
    manifest = {**meta, "index": index,
                "case_ids": [x["id"] for x in planned],
                "fingerprints": fingerprints}
    target.mkdir(parents=False)
    atomic_json(target / "manifest.json", manifest)
    bind = digest(manifest)
    with ExitStack() as stack:
        sf = stack.enter_context(
            chess.engine.SimpleEngine.popen_uci(str(stockfish_path), timeout=180.0))
        sf.configure({"Threads": 1, "Hash": 64})
        engines = {
            role: stack.enter_context(
                _open_engine(overlay, overlay / "rodent-iv-plain.exe",
                             person, book=False))
            for role, person in people.items()
        }
        for n, case in enumerate(planned, 1):
            move = chess.Move.from_uci(case["human_move"])
            output_data = measure(boards[case["id"]], move, engines, sf)
            if output_data["phase"] != case["phase"]:
                raise ValueError("Historical phase tag changed")
            doc = {"schema": SCHEMA, "case": case,
                   "manifest_sha256": bind, "measured": output_data}
            atomic_json(target / (case["id"] + ".json"), doc)
            print("HUMAN_MOVE_QUALITY", index, n, len(planned),
                  case["player"], case["era"], case["phase"], flush=True)
    return {"shard": index, "positions": len(planned),
            "fischer_unchanged": True, "tal_original_unchanged": True}


def validate_measurement(doc, original, board, manifest_hash):
    if (doc.get("schema") != SCHEMA or doc.get("case") != original or
            doc.get("manifest_sha256") != manifest_hash):
        raise ValueError("Quality outcome detached from original historical case")
    result = doc.get("measured")
    if not isinstance(result, dict) or result.get("phase") != original["phase"]:
        raise ValueError("Missing original-phase evaluation")
    moves = result.get("moves")
    scores = result.get("scores")
    reference = result.get("reference")
    if (not isinstance(moves, dict) or set(moves) != set(POLICIES) or
            not isinstance(scores, dict) or set(scores) != set(POLICIES) or
            not isinstance(reference, dict) or
            moves["human"] != original["human_move"]):
        raise ValueError("Incomplete same-position policies")
    if (board.ply() != original["ply"] or
            sha256(board.fen().encode()).hexdigest() != original["fen_sha256"]):
        raise ValueError("Historical position changed")
    if (result.get("tactical_pre_move") !=
            ("tactics" in result.get("pre_move_tags", []))):
        raise ValueError("Illegal tactical context alteration")
    for move in moves.values():
        if not isinstance(move, str) or chess.Move.from_uci(move) not in board.legal_moves:
            raise ValueError("Invented illegal engine/human move")
    raw = reference.get("evals")
    best = reference.get("best")
    if not isinstance(raw, dict) or best not in raw or not set(moves.values()) <= set(raw):
        raise ValueError("Lost reference root score")
    rankings = reference.get("ranked")
    if (not isinstance(rankings, list) or len(rankings) != 2 or
            not all(isinstance(item, dict) and
                    set(item) == {"move", "cp", "mate"} and
                    item["move"] in ("", *[m.uci() for m in board.legal_moves])
                    for item in rankings) or
            [item["move"] for item in rankings] !=
            [best, reference.get("second")]):
        raise ValueError("Reference pre-move ranking invalid")
    if (result.get("pre_move_tags") != tags(
            board, original["phase"], rankings[0], rankings[1])):
        raise ValueError("Reference pre-choice tactical tags tampered")
    history = reference.get("history")
    if not isinstance(history, list) or not 1 <= len(history) <= len(DEPTHS):
        raise ValueError("Missing adaptive search history")
    for i, record in enumerate(history):
        ranked, evaluations = record["ranked"], record["evals"]
        if (record["depth"] != DEPTHS[i] or len(ranked) != 2
                or ranked[0]["move"] == ranked[1]["move"]
                or set(evaluations) != set(moves.values()) | {x["move"] for x in ranked}):
            raise ValueError("Invalid adaptive reference schedule")
        for m, score in evaluations.items():
            if (chess.Move.from_uci(m) not in board.legal_moves
                    or score.get("move") != m
                    or (score["mate"] is None and type(score["cp"]) is not int)
                    or (score["mate"] is not None and
                        (type(score["mate"]) is not int or score["cp"] is not None))):
                raise ValueError("Invalid adaptive root score")
        for item in ranked:
            if (item["mate"] is None and type(item["cp"]) is not int
                    or item["mate"] is not None and
                    (type(item["mate"]) is not int or item["cp"] is not None)):
                raise ValueError("Invalid adaptive ranking score")
        disagreement = _disagree(ranked, evaluations)
        if record["inconsistent"] is not disagreement or (
                i < len(history)-1 and not disagreement):
            raise ValueError("Adaptive escalation concealed or unnecessary")
    final = history[-1]
    if (final["inconsistent"] and len(history) != len(DEPTHS)
            or reference["inconsistent"] is not final["inconsistent"]
            or reference["ranked"] != final["ranked"] or raw != final["evals"]):
        raise ValueError("Premature escalation stop or final evidence mismatch")
    for key, row in raw.items():
        if (row.get("move") != key or
                (row.get("mate") is None and type(row.get("cp")) is not int) or
                (row.get("mate") is not None and
                 (type(row["mate"]) is not int or row.get("cp") is not None))):
            raise ValueError("Invalid Stockfish exact-root evaluation")
    for name, move in moves.items():
        if scores[name] != _policy_metric(raw[best], raw[move]):
            raise ValueError("Manipulated reference loss or mate outcome")
    if result["joint_numeric"] != all(scores[k]["numeric"] for k in
                                     ("human", "fischer", "tal_balanced")):
        raise ValueError("Selected away an unfavorable numeric outcome")
    search = result.get("search_telemetry", {})
    if set(search) != set(POLICIES) - {"human"}:
        raise ValueError("Missing Rodent search depth compliance")
    for name, metadata in search.items():
        if (not isinstance(metadata, dict) or
                metadata.get("requested_depth") != ENGINE_DEPTH or
                type(metadata.get("reached_requested")) is not bool or
                (metadata.get("reported_depth") is not None and
                 (type(metadata["reported_depth"]) is not int or
                  metadata["reported_depth"] < 1)) or
                metadata["reached_requested"] != (
                    metadata.get("reported_depth") is not None and
                    metadata["reported_depth"] >= ENGINE_DEPTH) or
                (metadata.get("reported_nodes") is not None and
                 (type(metadata["reported_nodes"]) is not int or
                  metadata["reported_nodes"] < 0))):
            raise ValueError(f"Invalid achieved depth/telemetry for {name}")
    return result


def _phase_mean(records, role):
    return mean([r["scores"][role]["cp_loss"]-r["scores"]["human"]["cp_loss"]
                 for r in records])


def _severe_excess(records, role):
    return mean([float(r["scores"][role]["over100"]) -
                 float(r["scores"]["human"]["over100"]) for r in records])


def _macro(records, role, metric="cp", tactical=False):
    if tactical:
        matching = [r for r in records if r["joint_numeric"] and r["tactical_pre_move"]]
        if not matching:
            return None
        return (_phase_mean(matching, role) if metric == "cp"
                else _severe_excess(matching, role))
    per_phase = {}
    for stage in PHASES:
        matching = [r for r in records if r["phase"] == stage and
                    r["joint_numeric"] and
                    (not tactical or r["tactical_pre_move"])]
        if not matching:
            return None
        per_phase[stage] = (_phase_mean(matching, role) if metric == "cp"
                            else _severe_excess(matching, role))
    return mean(per_phase.values())


def _bootstrap(docs, tactical=False, metric="cp"):
    # Resample WHOLE independent game trajectories, never individual plies.
    groups = {}
    for player in ("fischer", "tal"):
        by_game = defaultdict(list)
        for row in docs:
            if row["case"]["player"] == player and row["case"]["era"] == "peak":
                by_game[row["case"]["game"]].append({
                    **row["measured"], "game": row["case"]["game"]})
        groups[player] = list(by_game.values())
    if min(map(len, groups.values())) < 30:
        return None
    rng = random.Random(2026112402 if not tactical else 2026112403)
    diffs = []
    for _ in range(BOOTSTRAPS):
        sampled = {player: [r for block in
                            rng.choices(groups[player], k=len(groups[player]))
                            for r in block]
                   for player in groups}
        tal = _macro(sampled["tal"], "tal_balanced", metric, tactical)
        fischer = _macro(sampled["fischer"], "fischer", metric, tactical)
        if tal is None or fischer is None:
            return None
        diffs.append(tal - fischer)
    diffs.sort()
    return [diffs[int(.025*len(diffs))],
            diffs[int(.975*len(diffs))-1]]


def aggregate(shards, archive_dir=None):
    cases, boards, meta = panel(archive_dir)
    seen = {}
    common_hashes = None
    for index in range(SHARDS):
        folder = Path(shards) / f"shard-{index}"
        manifest = load_json(folder / "manifest.json")
        assigned = shard_cases(cases, index)
        fingerprints = manifest.get("fingerprints")
        expected = {**meta, "index": index,
                    "case_ids": [c["id"] for c in assigned],
                    "fingerprints": fingerprints}
        if manifest != expected:
            raise ValueError("Historical parity source or manifest mismatch")
        if (not isinstance(fingerprints, dict) or
                fingerprints.get("protocol_sha256") != file_hash(Path(__file__)) or
                fingerprints.get("stockfish") != STOCKFISH_SHA or
                fingerprints.get("variants", {}).get("TalBalanced") != BALANCED_SHA):
            raise ValueError("Missing exact Tal candidate / audit SHA")
        frozen = frozen_fischer()["hashes"]
        for item, expected_hash in (
            ("rodent", frozen["rodent"]),
            ("fischer", frozen["fischer_profile"]),
            ("simple", frozen["simple_control"]),
            ("tal", TAL_ORIGINAL_SHA),
        ):
            if fingerprints.get("originals", {}).get(item) != expected_hash:
                raise ValueError("Original human-calibration motor changed")
        if common_hashes is None:
            common_hashes = fingerprints
        elif common_hashes != fingerprints:
            raise ValueError("Stockfish / Rodent versions differ across cohorts")
        expected_files = {"manifest.json"} | {
            case["id"] + ".json" for case in assigned}
        if {p.name for p in folder.iterdir()} != expected_files:
            raise ValueError("Missing or extra historical quality records")
        for case in assigned:
            if case["id"] in seen:
                raise ValueError("Repeated human policy decision")
            doc = load_json(folder / (case["id"] + ".json"))
            validate_measurement(
                doc, case, boards[case["id"]], digest(manifest))
            seen[case["id"]] = doc
    if set(seen) != set(cases):
        raise ValueError("Not every preregistered quality decision was judged")
    docs = list(seen.values())
    rows = {}
    sufficient = True
    for player in ("fischer", "tal"):
        for span in ("peak", "other"):
            sample = [d["measured"] for d in docs if
                      d["case"]["player"] == player and d["case"]["era"] == span]
            if not sample:
                sufficient = False
                rows[f"{player}|{span}"] = {"observations": 0}
                continue
            numeric = [r for r in sample if r["joint_numeric"]]
            phases = {stage: len([x for x in numeric if x["phase"] == stage])
                      for stage in PHASES}
            tactics = [x for x in numeric if x["tactical_pre_move"]]
            depth_ratios = {
                role: sum(int(r["search_telemetry"][role]["reached_requested"])
                          for r in sample) / len(sample)
                for role in ("fischer", "tal_balanced")}
            bad_reference = sum(bool(x["reference"]["inconsistent"])
                                for x in sample) / len(sample)
            stats = {
                "observations": len(sample), "numeric_joint": len(numeric),
                "depth_histogram": dict(Counter(str(r["reference"]["history"][-1]["depth"]) for r in sample)),
                "numeric_coverage": len(numeric)/len(sample),
                "by_phase_joint_numeric": phases,
                "tactical_numeric": len(tactics),
                "reference_inconsistency_rate": bad_reference,
                "attained_depth_ratio": depth_ratios,
                "human_loss_cp": mean(x["scores"]["human"]["cp_loss"]
                                      for x in numeric) if numeric else None,
                "fischer_loss_cp": mean(x["scores"]["fischer"]["cp_loss"]
                                        for x in numeric) if numeric else None,
                "tal_balanced_loss_cp": mean(x["scores"]["tal_balanced"]["cp_loss"]
                                             for x in numeric) if numeric else None,
                "primary_excess_mean_cp": _macro(
                    numeric, "fischer" if player == "fischer"
                    else "tal_balanced"),
                "primary_excess_severe_rate": _macro(
                    numeric, "fischer" if player == "fischer"
                    else "tal_balanced", "severe"),
                "tactical_excess_cp": _macro(
                    numeric, "fischer" if player == "fischer"
                    else "tal_balanced", tactical=True),
                "mate_sensitive_count": sum(not x["joint_numeric"] for x in sample),
                "lost_winning_mates_by_policy": {
                    role: sum(x["scores"][role]["missed_winning_mate"] for x in sample)
                    for role in POLICIES
                },
            }
            rows[f"{player}|{span}"] = stats
            if span == "peak":
                sufficient &= (all(v >= MIN_PHASE_NUMERIC for v in phases.values())
                               and len(tactics) >= MIN_TACTICAL_NUMERIC
                               and stats["numeric_coverage"] >= MIN_NUMERIC_COVERAGE
                               and bad_reference <= MAX_REFERENCE_INCONSISTENCY
                               and all(v >= MIN_REACHED_DEPTH
                                       for v in depth_ratios.values()))
    output = {
        **meta, "status": "INCONCLUSIVE_NEEDS_HUMAN_QUALITY_SUPPORT",
        "models_compared": list(POLICIES), "stockfish_and_motor_hashes": common_hashes,
        "observed_decisions": len(docs), "verified_decisions": len(seen),
        "by_cohort": rows, "parity_gap_cp": None, "parity_ci95_cp": None,
        "tactical_gap_cp": None, "tactical_ci95_cp": None,
        "severe_gap_rate": None, "severe_ci95_rate": None,
        "sacrifices_certified_by_derivation": False,
        "release_eligible": False, "fischer_asset_unchanged": True,
        "tal_official_unchanged": True, "no_elo_claim": True,
    }
    if not sufficient:
        return output
    a, b = rows["tal|peak"], rows["fischer|peak"]
    gap = a["primary_excess_mean_cp"] - b["primary_excess_mean_cp"]
    tactical = a["tactical_excess_cp"] - b["tactical_excess_cp"]
    severe = a["primary_excess_severe_rate"] - b["primary_excess_severe_rate"]
    gaps = (_bootstrap(docs), _bootstrap(docs, tactical=True),
            _bootstrap(docs, metric="severe"))
    if any(x is None for x in gaps):
        return output
    primary_ci, tactical_ci, severe_ci = gaps
    gates = {
        "peak_main_noninferiority": primary_ci[1] <= NONINFERIORITY_CP,
        "tactical_noninferiority": tactical_ci[1] <= TACTICAL_NONINFERIORITY_CP,
        "severe_error_noninferiority": severe_ci[1] <= SEVERE_NONINFERIORITY_RATE,
    }
    output.update({
        "status": ("HISTORICAL_MOVE_QUALITY_PARITY_PROXY_PASSED_REVIEW_REQUIRED"
                   if all(gates.values()) else
                   "HISTORICAL_MOVE_QUALITY_PARITY_NOT_DEMONSTRATED"),
        "parity_gap_cp": gap, "parity_ci95_cp": primary_ci,
        "tactical_gap_cp": tactical, "tactical_ci95_cp": tactical_ci,
        "severe_gap_rate": severe, "severe_ci95_rate": severe_ci,
        "gates": gates,
    })
    return output


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("plan", "play", "aggregate"):
        a = sub.add_parser(command)
        a.add_argument("--arm", choices=("fit", "excluded_test"), required=True)
        sources = a.add_mutually_exclusive_group(required=True)
        sources.add_argument("--fetch", action="store_true")
        sources.add_argument("--archive-dir", type=Path)
        if command == "play":
            a.add_argument("--rodent-root", type=Path, required=True)
            a.add_argument("--stockfish", type=Path, required=True)
            a.add_argument("--index", type=int, required=True)
            a.add_argument("--out", type=Path, required=True)
        if command == "aggregate":
            a.add_argument("--shards", type=Path, required=True)
            a.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    global SOURCE_ARM
    SOURCE_ARM = args.arm
    if args.command == "plan":
        result = plan(args.archive_dir)
    elif args.command == "play":
        result = run_shard(args.rodent_root, args.stockfish,
                           args.out, args.index, args.archive_dir)
    else:
        result = aggregate(args.shards, args.archive_dir)
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("Refuse to overwrite calibrated quality record")
        atomic_json(args.out, result)
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
