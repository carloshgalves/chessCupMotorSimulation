"""Direct, bilateral peak-human move-quality qualification; no Elo conversion."""
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
import argparse
import base64
import json
import random
import re
import shutil

import chess
import chess.engine

from .adaptive_quality_parity import STOCKFISH_SHA, adaptive_reference, _policy_metric
from .catalog import find_player, load_roster, Player
from .fischer_baseline import phase
from .fischer_final_gate import RODENT_HASHES, play_clock, validate_clock
from .fischer_full_games import atomic_json, digest
from .historical_quality_parity import source_rows
from .matches import _open_engine, file_hash, validate
from .pioneer_corpus_audit import SOURCES, fetch, merge_standard
from .product_peak_profiles import PEAKS, render, split_label
from .product_repertoires import PERIODS, build, audit
from .qualified_runtime import assets, PROFILE_KEYS, LEGACY_SLUGS, player_for, book_digest
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _scan, _iter_verified_games
from .registry import add_to_first_set, registered_aliases
from .roster_product_wave import WAVE, source, product_arm, product_book, overlay
from .tal_strength_doe import render_tal_variant

SCHEMA = "bilateral-cp-v1"
PHASES = ("opening", "middlegame", "endgame")
CAP = 5_000_000
MARGIN = 5
PREFERRED = 3
PHASE_MARGIN = 10
MIN_PHASE = 20
MIN_GAMES = 40
MIN_COVERAGE = .85
MAX_SEVERE_GAP = .05
MAX_MATE_GAP = .05
FIT_LIMIT = 30
TEST_LIMIT = 100
BOOTSTRAPS = 2000
SEED = 20261126
PERIOD = {**WAVE, **PEAKS, "fischer": (1970, 1972), "tal": (1957, 1961)}
PIONEERS = {
    "morphy": ("5a1ab537af5479416e34a49c7a1b700847d68c6528f9e69b1cd4bc2c17a512e7",
               "ba1eaf78187e70d9de2f2abbe03b3d188270447dae229f45428e33a43ff62e81", 250),
    "philidor": ("b6e793ddf763deb7d52ac3ace052f922b38ae94fd9676748f174ffc6918b22df",
                 "bfc88b9e8fb8986a5a53777b0d612473e819eb71582aac715d1581f4f584aa4c", 9),
}
BASELINE_DIR = Path("experiments/bilateral-cp")
EVAL_KEYS = ("Material", "OwnAttack", "OppAttack", "OwnMobility", "OppMobility",
             "PawnStructure", "PassedPawns", "Outposts", "PiecePressure", "PawnShield", "PawnStorm")


def save(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, doc)


def load(path):
    return json.loads(Path(path).read_text())


def baseline_inventory(folder=BASELINE_DIR):
    data = {}
    for path in sorted(Path(folder).glob("baselines-*.json")):
        rows = load(path)
        if set(rows) & set(data):
            raise ValueError("Duplicate baseline")
        data.update(rows)
    if set(data) != {p.slug for p in load_roster()} - set(assets()):
        raise ValueError("Baseline inventory must cover exactly the nonfrozen roster")
    return data


def historical(slug, prior):
    if slug in ("fischer", "tal"):
        data = source_rows()[slug]
    else:
        data = source(slug)
    if slug in PIONEERS:
        source_hash, merged_hash, count = PIONEERS[slug]
        extra = fetch(slug)
        if sha256(extra).hexdigest() != source_hash:
            raise ValueError("Supplemental archive changed")
        merged, provenance = merge_standard(slug, [(data[3], data[0]), (SOURCES[slug], extra)])
        rows, scan = _scan(merged, slug, slug)
        if sha256(merged).hexdigest() != merged_hash or len(rows) != count:
            raise ValueError("Audited pioneer corpus mismatch")
        data = (merged, rows, scan, "pinned-supplemental-corpus-45884539314", slug)
        return data, provenance
    expected = prior.get("source_sha256") if prior else None
    if expected and sha256(data[0]).hexdigest() != expected:
        raise ValueError("Previously measured source changed")
    return data, []


def partition(slug, game):
    return neural_arm(game) if slug in LEGACY_SLUGS else product_arm(game)


def historical_book(slug, data, prior):
    if slug in LEGACY_SLUGS:
        book = build(slug, data, period=(PERIODS | PEAKS)[slug])
    else:
        book = product_book(slug, data)
    book_stats = audit(book)
    if slug in assets():
        old = book_digest(book, slug)
    else:
        old = prior.get("book_sha256")
        if old and slug not in PIONEERS and digest(book) != old:
            raise ValueError("Candidate historical repertoire changed")
    return book, {"sha256": digest(book), "prior_digest": old, "audit": book_stats}


def board_for(row):
    board = chess.Board()
    for move in row["prefix"]:
        board.push_uci(move)
    if board.fen() != row["fen"] or row["move"] not in {m.uci() for m in board.legal_moves}:
        raise ValueError("Historical position binding mismatch")
    return board


def panel(slug, data):
    pgn, rows, _, _, surname = data
    groups = defaultdict(list)
    roots = defaultdict(dict)
    start, end = PERIOD[slug]
    eligible = [r for r in rows if str(r["date"]).isdigit() and start <= int(r["date"]) <= end]
    for game, row in _iter_verified_games(pgn, eligible, surname):
        board = game.board()
        if board.fen() != chess.STARTING_FEN or game.headers.get("Result") not in ("1-0", "0-1", "1/2-1/2"):
            continue
        gid = row["mainline_sha256"]
        arm = partition(slug, gid)
        if arm not in ("fit", "excluded_test"):
            continue
        prefix, choices = [], {}
        for move in game.mainline_moves():
            if board.turn == (row["color"] == "white") and board.ply() >= 16 and board.legal_moves.count() > 1:
                stage = phase(board)
                key = digest([SCHEMA, gid, stage, board.ply()])
                if stage not in choices or key < choices[stage]["id"]:
                    choices[stage] = {"id": key, "game": gid, "phase": stage, "arm": arm,
                                      "prefix": prefix.copy(), "fen": board.fen(),
                                      "move": move.uci()}
            board.push(move)
            prefix.append(move.uci())
            if len(prefix) == 12 and not board.is_game_over(claim_draw=True):
                roots[arm][tuple(prefix)] = {"id": digest(prefix), "prefix": prefix.copy()}
        for stage, item in choices.items():
            groups[arm, stage].append(item)
    result, used = {}, set()
    for arm, limit in (("fit", FIT_LIMIT), ("excluded_test", TEST_LIMIT)):
        result[arm] = []
        for stage in PHASES:
            chosen = []
            for row in sorted(groups[arm, stage], key=lambda x: x["id"]):
                # Same position with a different move number is not independent.
                key = " ".join(row["fen"].split()[:4])
                if key in used:
                    continue
                used.add(key)
                chosen.append(row)
                if len(chosen) == limit:
                    break
            result[arm].extend(chosen)
    if {r["game"] for r in result["fit"]} & {r["game"] for r in result["excluded_test"]}:
        raise ValueError("Historical trajectory leakage")
    return result, sorted(roots["excluded_test"].values(), key=lambda r: r["id"])


def profile_bytes(location, player):
    return (location / "personalities" / player.profile_filename).read_bytes()


def prepare(root, dest, slug, prior):
    for name, path in (("rodent", root / "rodent-iv-plain.exe"), ("simple", root / "personalities/simple.txt")):
        if file_hash(path) != RODENT_HASHES[name]:
            raise ValueError("Unpinned Rodent assets")
    asset = assets().get(slug)
    if slug in LEGACY_SLUGS:
        shutil.copytree(root, dest)
        original = find_player(slug)
        p = player_for(slug, asset)
        directory = dest / "personalities"
        if asset.get("overrides"):
            renderer = render_tal_variant if slug == "tal" else render
            raw = (directory / original.profile_filename).read_text(encoding="utf-8-sig")
            (directory / p.profile_filename).write_text(renderer(raw, asset["overrides"]), encoding="utf-8", newline="\n")
        ini = directory / "basic.ini"
        if p.rodent_alias not in registered_aliases(ini):
            ini.write_text(add_to_first_set(ini.read_text(), {p.rodent_alias: p.profile_filename}))
        players = {"frozen": p}
        baseline = ("frozen", asset["depth"], CAP)
    else:
        players, _ = overlay(root, dest, slug)
        if asset:
            baseline = (asset["wave_variant"], asset["depth"], CAP)
        elif prior.get("label"):
            name, depth = split_label(prior["label"])
            baseline = (name, depth, CAP)
        else:
            baseline = ("individual" if "individual" in players else "original", 16, CAP)
    p = players[baseline[0]]
    expected = asset["hashes"][PROFILE_KEYS[slug]] if asset else prior.get("profile_sha256")
    if expected and sha256(profile_bytes(dest, p)).hexdigest() != expected:
        raise ValueError("Previously measured profile mismatch")
    validate(dest, p, p)
    # Symmetric, conservative changes to evaluation, leaving search settings
    # and book options byte-equivalent. Test against the unmodified baseline.
    directory = dest / "personalities"
    raw = profile_bytes(dest, p).decode("utf-8-sig")
    simple = (directory / "simple.txt").read_text(encoding="utf-8-sig")
    additions = {}
    for label, scale in (("toward", .75), ("away", 1.25)):
        params = {}
        for key in EVAL_KEYS:
            rx = rf"(?m)^setoption name {key} value (-?\d+)[ \t]*$"
            a, b = re.findall(rx, raw), re.findall(rx, simple)
            if len(a) == len(b) == 1:
                old, control = int(a[0]), int(b[0])
                new = round(control + scale * (old - control))
                if new >= 0 and new != old:
                    params[key] = new
        if params:
            alias = p.rodent_alias + "Cp" + label.title()
            filename = alias.lower() + ".txt"
            (directory / filename).write_text(render(raw, params), encoding="utf-8", newline="\n")
            players[label] = replace(p, rodent_alias=alias, rodent_file=filename)
            additions[alias] = filename
    if additions:
        ini = directory / "basic.ini"
        ini.write_text(add_to_first_set(ini.read_text(), additions))
    for p in players.values():
        validate(dest, p, p)
    return players, baseline


def recipes(players, baseline):
    name, depth, cap = baseline
    candidates = {"baseline": baseline}
    for d in (8, 10, 12, 14, 16, 18, 20, 24):
        candidates[f"depth-{d}"] = (name, d, cap)
    for n in (50_000, 200_000, 750_000, 2_000_000):
        candidates[f"nodes-{n}"] = (name, depth, n)
    for k in sorted(players):
        if k != name and k != "original":
            candidates[f"profile-{k}"] = (k, depth, cap)
    unique = {}
    for label, recipe in candidates.items():
        if recipe not in unique.values():
            unique[label] = recipe
    return unique


def reference_engine(stack, stockfish):
    if file_hash(stockfish) != STOCKFISH_SHA:
        raise ValueError("Unpinned Stockfish")
    ref = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(stockfish)))
    ref.configure({"Threads": 1, "Hash": 64})
    return ref


def engine_move(engine, board, recipe):
    reply = engine.play(board, chess.engine.Limit(depth=recipe[1], nodes=recipe[2]),
                        game=object(), info=chess.engine.INFO_ALL)
    if reply.move is None or reply.move not in board.legal_moves:
        raise ValueError("Illegal UCI choice")
    return reply.move.uci(), {k: reply.info.get(k) for k in ("depth", "nodes")}


def measure(ref, engines, cases, rows):
    results = []
    for row in rows:
        board = board_for(row)
        moves, telemetry = {"human": row["move"]}, {}
        for label, recipe in cases.items():
            moves[label], telemetry[label] = engine_move(engines[recipe[0]], board, recipe)
        history = adaptive_reference(ref, board, moves)
        final = history[-1]
        best = final["evals"][final["ranked"][0]["move"]]
        scores = {k: _policy_metric(best, final["evals"][move]) for k, move in moves.items()}
        results.append({**row, "moves": moves, "scores": scores, "reference": history,
                        "telemetry": telemetry, "stable": not final["inconsistent"]})
    return results


def macro(groups):
    return mean(mean(groups[p]) for p in PHASES)


def stats(rows, role, bootstrap=False):
    phases = defaultdict(list)
    humans, motors = defaultdict(list), defaultdict(list)
    clusters = defaultdict(list)
    severe, mate = [], []
    for r in rows:
        if not r["stable"]:
            continue
        h, m = r["scores"]["human"], r["scores"][role]
        # Mate-sensitive decisions are tracked even if no numeric loss exists.
        mate.append(int(m["missed_winning_mate"] or m["selected_losing_mate"])
                    - int(h["missed_winning_mate"] or h["selected_losing_mate"]))
        if not h["numeric"] or not m["numeric"]:
            continue
        p, delta = r["phase"], h["cp_loss"] - m["cp_loss"]
        phases[p].append(delta)
        humans[p].append(h["cp_loss"])
        motors[p].append(m["cp_loss"])
        clusters[r["game"]].append((p, delta))
        severe.append(int(m["over100"]) - int(h["over100"]))
    coverage = sum(map(len, phases.values())) / len(rows) if rows else 0
    result = {"positions": len(rows), "numeric": sum(map(len, phases.values())),
              "games": len(clusters), "coverage": coverage,
              "phase_support": {p: len(phases[p]) for p in PHASES},
              "phase_delta": {p: mean(phases[p]) if phases[p] else None for p in PHASES},
              "human_cp": None, "motor_cp": None, "delta_cp": None, "ci95": None,
              "severe_gap": mean(severe) if severe else None,
              "mate_gap": mean(mate) if mate else None}
    if not all(phases[p] for p in PHASES):
        return result
    result.update(human_cp=macro(humans), motor_cp=macro(motors), delta_cp=macro(phases))
    if bootstrap and len(clusters) >= 2:
        rng = random.Random(SEED)
        keys = sorted(clusters)
        samples = []
        for _ in range(BOOTSTRAPS):
            cells = defaultdict(list)
            for key in rng.choices(keys, k=len(keys)):
                for p, d in clusters[key]:
                    cells[p].append(d)
            if all(cells[p] for p in PHASES):
                samples.append(macro(cells))
        if len(samples) >= .95 * BOOTSTRAPS:
            samples.sort()
            result["ci95"] = [samples[int(.025 * len(samples))],
                              samples[min(len(samples)-1, int(.975 * len(samples)))]]
    return result


def gate(s):
    if (s["games"] < MIN_GAMES or s["coverage"] < MIN_COVERAGE
            or min(s["phase_support"].values()) < MIN_PHASE or s["ci95"] is None):
        return "INSUFFICIENT_RESERVED_EVIDENCE"
    if abs(s["delta_cp"]) > MARGIN:
        return "MOTOR_TOO_STRONG" if s["delta_cp"] > 0 else "MOTOR_TOO_WEAK"
    if any(abs(v) > PHASE_MARGIN for v in s["phase_delta"].values()):
        return "PHASE_IMBALANCE"
    if abs(s["severe_gap"]) > MAX_SEVERE_GAP or abs(s["mate_gap"]) > MAX_MATE_GAP:
        return "SEVERE_OR_MATE_IMBALANCE"
    if s["ci95"][0] < -MARGIN or s["ci95"][1] > MARGIN:
        return "UNCERTAIN_EQUIVALENCE"
    return "EXCELLENT" if abs(s["delta_cp"]) <= PREFERRED else "COMPATIBLE"


def select(results):
    def order(key):
        s = results[key]
        if s["delta_cp"] is None:
            return (True, float("inf"), float("inf"), key)
        phase_gap = max(abs(v) for v in s["phase_delta"].values())
        invalid = (s["coverage"] < MIN_COVERAGE or min(s["phase_support"].values()) < 12
                   or abs(s["severe_gap"]) > MAX_SEVERE_GAP or abs(s["mate_gap"]) > MAX_MATE_GAP)
        # Optimize absolute gap, not the direction of strength.
        return (invalid, abs(s["delta_cp"]) > MARGIN, abs(s["delta_cp"]) > PREFERRED,
                max(0, phase_gap - PHASE_MARGIN), abs(s["delta_cp"]), key != "baseline", key)
    return min(results, key=order)


def fit(slug, root, stockfish, out):
    prior = baseline_inventory().get(slug, {})
    data, supplement = historical(slug, prior)
    panels, _ = panel(slug, data)
    fit_rows = panels["fit"]
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        dest = Path(tmp) / "rodent"
        players, baseline = prepare(root, dest, slug, prior)
        cases = recipes(players, baseline)
        engines = {k: stack.enter_context(_open_engine(dest, dest / "rodent-iv-plain.exe", p, book=False))
                   for k, p in players.items()}
        ref = reference_engine(stack, stockfish)
        rows = measure(ref, engines, cases, fit_rows)
        summaries = {label: stats(rows, label) for label in cases}
        chosen = select(summaries)
        book, book_info = historical_book(slug, data, prior)
        hashes = {k: sha256(profile_bytes(dest, players[v[0]])).hexdigest() for k, v in cases.items()}
        doc = {"schema": SCHEMA, "slug": slug, "period": PERIOD[slug], "prior": prior,
               "source_sha256": sha256(data[0]).hexdigest(), "supplement": supplement,
               "engine_sha256": RODENT_HASHES["rodent"], "stockfish_sha256": STOCKFISH_SHA,
               "fit_panel_sha256": digest(fit_rows), "cases": cases, "profiles": hashes,
               "baseline": baseline, "selected": chosen, "fit": summaries, "rows": rows,
               "book": book, "book_info": book_info}
        save(out, doc)
        print("CP_FIT", slug, json.dumps({"selected": chosen, "stats": summaries[chosen],
                                         "baseline": summaries["baseline"]}, sort_keys=True), flush=True)


class Budget:
    def __init__(self, engine, recipe):
        self.engine, self.recipe = engine, recipe

    def play(self, board, limit, **kwargs):
        return self.engine.play(board, chess.engine.Limit(**{**vars(limit), "depth": self.recipe[1],
                                                            "nodes": self.recipe[2]}), **kwargs)

    def close(self):
        self.engine.close()


def verify_rows(rows, expected, labels):
    if len(rows) != len(expected):
        raise ValueError("Measurement count mismatch")
    for r, e in zip(rows, expected):
        if any(r[k] != v for k, v in e.items()):
            raise ValueError("Historical panel mismatch")
        board = board_for(e)
        if set(r["moves"]) != {"human", *labels} or r["moves"]["human"] != e["move"]:
            raise ValueError("Measurement policies mismatch")
        if any(m not in {v.uci() for v in board.legal_moves} for m in r["moves"].values()):
            raise ValueError("Illegal recorded choice")
        final = r["reference"][-1]
        best = final["evals"][final["ranked"][0]["move"]]
        scores = {k: _policy_metric(best, final["evals"][move]) for k, move in r["moves"].items()}
        if scores != r["scores"] or r["stable"] != (not final["inconsistent"]):
            raise ValueError("Score replay mismatch")


def reserved(slug, root, stockfish, selection, out):
    doc = load(selection)
    if doc["slug"] != slug or doc["schema"] != SCHEMA:
        raise ValueError("Wrong selection shard")
    prior = baseline_inventory().get(slug, {})
    data, _ = historical(slug, prior)
    panels, roots = panel(slug, data)
    if doc["source_sha256"] != sha256(data[0]).hexdigest() or doc["fit_panel_sha256"] != digest(panels["fit"]):
        raise ValueError("Selection source mismatch")
    verify_rows(doc["rows"], panels["fit"], doc["cases"])
    recomputed = {k: stats(doc["rows"], k) for k in doc["cases"]}
    if recomputed != doc["fit"] or select(recomputed) != doc["selected"]:
        raise ValueError("Selection not reproducible")
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        dest = Path(tmp) / "rodent"
        players, baseline = prepare(root, dest, slug, prior)
        cases = recipes(players, baseline)
        if json.loads(json.dumps(cases)) != doc["cases"]:
            raise ValueError("Recipe inventory mismatch")
        selected = doc["selected"]
        locked = {"baseline": baseline, "candidate": cases[selected]}
        for k, v in cases.items():
            if sha256(profile_bytes(dest, players[v[0]])).hexdigest() != doc["profiles"][k]:
                raise ValueError("Profile changed since FIT")
        book, info = historical_book(slug, data, prior)
        if book != doc["book"] or info != doc["book_info"]:
            raise ValueError("Historical repertoire changed")
        engines = {k: stack.enter_context(_open_engine(dest, dest / "rodent-iv-plain.exe", players[k], book=False))
                   for k in {v[0] for v in locked.values()}}
        ref = reference_engine(stack, stockfish)
        rows = measure(ref, engines, locked, panels["excluded_test"])
        verify_rows(rows, panels["excluded_test"], locked)
        result = stats(rows, "candidate", bootstrap=True)
        base_result = stats(rows, "baseline", bootstrap=True)
        status = gate(result)
        recipe = locked["candidate"]
        profile = profile_bytes(dest, players[recipe[0]])
        repeats = []
        games = []
        if status in ("EXCELLENT", "COMPATIBLE"):
            # Repeat fixed-budget choices on development positions only.
            probes = doc["rows"][:10]
            for p in probes:
                move, _ = engine_move(engines[recipe[0]], board_for(p), recipe)
                repeats.append(move == p["moves"][selected])
            if len(repeats) != 10 or not all(repeats):
                status = "REPRODUCIBILITY_FAILED"
            elif len(roots) < 2:
                status = "INSUFFICIENT_RESERVED_GAME_ROOTS"
            else:
                simple = stack.enter_context(_open_engine(dest, dest / "rodent-iv-plain.exe",
                                             Player(0, "Simple", "control-simple", "control", "Simple", "simple.txt"), book=False))
                for start in roots[:2]:
                    for color in ("white", "black"):
                        case = {"id": digest([slug, recipe, start["id"], color]),
                                "root": start["id"], "color": color, "depth": recipe[1],
                                "opponent_depth": 16}
                        g = play_clock(case, start, Budget(engines[recipe[0]], recipe),
                                       Budget(simple, ("control", 16, CAP)))
                        validate_clock(g, case, start)
                        games.append({"case": case, "root": start, "game": g})
                if any(g["game"]["status"] != "COMPLETE" or g["game"]["time_forfeit"] for g in games):
                    status = "COMPETITIVE_SANITY_FAILED"
        report = {"schema": SCHEMA, "slug": slug, "period": PERIOD[slug],
                  "source_sha256": doc["source_sha256"], "selection_sha256": file_hash(selection),
                  "fit": doc["fit"], "baseline_recipe": baseline, "selected": selected, "recipe": recipe,
                  "test_panel_sha256": digest(panels["excluded_test"]), "test_rows": rows,
                  "baseline_test": base_result, "candidate_test": result, "status": status,
                  "profile_sha256": sha256(profile).hexdigest(), "alias": players[recipe[0]].rodent_alias,
                  "profile_filename": players[recipe[0]].profile_filename,
                  "profile_base64": base64.b64encode(profile).decode(),
                  "engine_sha256": RODENT_HASHES["rodent"], "stockfish_sha256": STOCKFISH_SHA,
                  "book": book, "book_info": info, "repeats": repeats, "games": games,
                  "reserve_scope": "global excluded TEST; not asserted never accessed by prior experiments"}
        save(out, report)
        print("CP_RESERVED", json.dumps({"slug": slug, "status": status, "recipe": recipe,
                                         "baseline": base_result, "candidate": result}, sort_keys=True), flush=True)


def aggregate(shards, out):
    table, candidates = [], {}
    for p in load_roster():
        slug = p.slug
        path = Path(shards) / f"cp-reserved-{slug}" / "report.json"
        if not path.exists():
            table.append({"slug": slug, "status": "MISSING_EXECUTION"})
            continue
        r = load(path)
        if r["slug"] != slug or r["schema"] != SCHEMA:
            raise ValueError("Wrong reserved result")
        rows = r["test_rows"]
        for row in rows:
            if partition(slug, row["game"]) != "excluded_test":
                raise ValueError("Nonreserved game in result")
        computed = stats(rows, "candidate", bootstrap=True)
        if computed != r["candidate_test"] or stats(rows, "baseline", bootstrap=True) != r["baseline_test"]:
            raise ValueError("Reserved statistics mismatch")
        status = r["status"]
        if status in ("EXCELLENT", "COMPATIBLE"):
            if gate(computed) != status or len(r["repeats"]) != 10 or not all(r["repeats"]) or len(r["games"]) != 4:
                raise ValueError("Invalid positive gate")
            for g in r["games"]:
                validate_clock(g["game"], g["case"], g["root"])
                if g["game"]["status"] != "COMPLETE" or g["game"]["time_forfeit"]:
                    raise ValueError("Invalid positive game gate")
            profile = base64.b64decode(r["profile_base64"], validate=True)
            if sha256(profile).hexdigest() != r["profile_sha256"] or digest(r["book"]) != r["book_info"]["sha256"]:
                raise ValueError("Release asset bytes mismatch")
            audit(r["book"])
            # Freeze exact experimental assets, not the active product registry.
            # Runtime must explicitly implement and verify per-player node caps
            # and deeper searches before any activation; old TEST exposure must
            # also be reviewed. No silent replacement of previous assets.
            candidate = {k: r[k] for k in ("slug", "recipe", "profile_sha256", "profile_base64",
                         "alias", "profile_filename", "engine_sha256", "stockfish_sha256",
                         "source_sha256", "book", "book_info", "selection_sha256",
                         "test_panel_sha256", "candidate_test")}
            candidate.update(status="FROZEN_EXPERIMENTAL_CANDIDATE", report_sha256=file_hash(path),
                             activation="DENIED_PENDING_INDEPENDENT_REVIEW_AND_RUNTIME_INTEGRATION")
            candidates[slug] = candidate
        table.append({"slug": slug, "status": status, "recipe": r["recipe"],
                      "baseline": r["baseline_test"], "candidate": computed,
                      "book_sha256": r["book_info"]["sha256"]})
    save(Path(out) / "table.json", {"schema": SCHEMA, "players": table, "counts": dict(Counter(r["status"] for r in table))})
    save(Path(out) / "frozen-candidates.json", {"schema": SCHEMA, "players": candidates})
    lines = ["| Player | Baseline delta cp | Candidate delta cp | 95% CI | Status |",
             "|---|---:|---:|---|---|"]
    for row in table:
        b, c = row.get("baseline", {}), row.get("candidate", {})
        def fmt(x):
            return "—" if x is None else f"{x:+.2f}"
        ci = c.get("ci95")
        interval = "—" if ci is None else f"[{ci[0]:+.2f}, {ci[1]:+.2f}]"
        lines.append(f"| {row['slug']} | {fmt(b.get('delta_cp'))} | {fmt(c.get('delta_cp'))} | {interval} | {row['status']} |")
    (Path(out) / "table.md").write_text("\n".join(lines) + "\n")
    print("CP_TABLE", json.dumps({"counts": dict(Counter(r["status"] for r in table)),
                                  "frozen_experimental": sorted(candidates)}, sort_keys=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("fit", "reserved", "aggregate"))
    p.add_argument("--player", choices=[p.slug for p in load_roster()])
    p.add_argument("--rodent", type=Path)
    p.add_argument("--stockfish", type=Path)
    p.add_argument("--selection", type=Path)
    p.add_argument("--shards", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.action == "fit":
        fit(a.player, a.rodent, a.stockfish, a.out)
    elif a.action == "reserved":
        reserved(a.player, a.rodent, a.stockfish, a.selection, a.out)
    else:
        aggregate(a.shards, a.out)


if __name__ == "__main__":
    main()
