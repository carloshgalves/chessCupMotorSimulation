"""Roster expansion: source readiness, FIT selection, competitive screen and independent TUNE."""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

import chess
import chess.engine

from .adaptive_quality_parity import adaptive_reference, _policy_metric, STOCKFISH_SHA
from .catalog import find_player, load_roster, Player
from .fischer_baseline import phase
from .fischer_deep_calibration import choose, tags
from .fischer_final_gate import features, play_clock, validate_clock, RODENT_HASHES
from .fischer_full_games import atomic_json, digest
from .matches import _open_engine, file_hash, validate
from .product_peak_profiles import render, summarize, split_label
from .product_repertoires import build, audit
from .product_strength_ladder import DepthControl
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _player_configuration, _source_bytes, _scan, _iter_verified_games
from .registry import add_to_first_set

FROZEN = frozenset(("fischer", "tal", "karpov", "kasparov"))
WAVE = {"alekhine": (1927, 1934), "botvinnik": (1948, 1958),
        "smyslov": (1953, 1958), "petrosian": (1963, 1969)}
DEPTHS = (12, 16, 20)
PHASES = ("opening", "middlegame", "endgame")
ROOTS = 12
PER_PHASE = 12
CONTROL = 16
MIN_SCORE = .40
SHARDS = 10


def remaining():
    return [p.slug for p in load_roster() if p.slug not in FROZEN]


def source(slug):
    cfg = _player_configuration([slug], None, None)[slug]
    _, pgn, origin = _source_bytes(cfg["source"], None)
    rows, scan = _scan(pgn, slug, cfg["surname"])
    return pgn, rows, scan, origin, cfg["surname"]


def in_period(row, period):
    return type(row["date"]) is int and period[0] <= row["date"] <= period[1]


def readiness(slug):
    p = find_player(slug)
    cfg = _player_configuration([slug], None, None)[slug]
    base = {"name": p.name, "base_alias": p.rodent_alias,
            "source_candidate": cfg["source"], "peak": list(WAVE[slug]) if slug in WAVE else None}
    try:
        data = source(slug)
    except HTTPError as e:
        if e.code != 404:
            raise
        return {**base, "status": "SOURCE_MISSING_404"}
    rows = data[1]
    years = Counter(str(r["date"]) for r in rows if r["date"] is not None)
    by_arm = Counter(neural_arm(r["mainline_sha256"]) for r in rows)
    peak = [r for r in rows if slug in WAVE and in_period(r, WAVE[slug])]
    peak_arms = Counter(neural_arm(r["mainline_sha256"]) for r in peak)
    status = ("EMPTY_OR_UNRESOLVED_SOURCE" if not rows else
              "NEEDS_INDIVIDUAL_BASE" if not p.rodent_alias else
              "NEEDS_PEAK_WINDOW" if slug not in WAVE else
              "INSUFFICIENT_PEAK_GAMES" if min(peak_arms["fit"], peak_arms["tune"]) < PER_PHASE else
              "WAVE_CANDIDATE")
    return {**base, "status": status, "source": data[3], "sha256": sha256(data[0]).hexdigest(),
            "games": len(rows), "scan": data[2], "years": dict(years),
            "unknown_year": sum(r["date"] is None for r in rows), "arms": dict(by_arm),
            "peak_games": len(peak), "peak_arms": dict(peak_arms),
            "trajectory_ids": sorted(r["mainline_sha256"] for r in rows)}


def inventory(index, out):
    if not 0 <= index < SHARDS:
        raise ValueError("Invalid inventory shard")
    result = {s: readiness(s) for s in remaining()[index::SHARDS]}
    atomic_json(out, {"index": index, "players": result})
    print("ROSTER_INVENTORY", index, json.dumps({k: {a: b for a, b in v.items() if a != "trajectory_ids"}
                                              for k, v in result.items()}, sort_keys=True), flush=True)


def inventory_total(shards, out):
    result, owners = {}, defaultdict(list)
    for index in range(SHARDS):
        doc = json.loads((shards / f"inventory-{index}" / "report.json").read_text())
        if doc["index"] != index or set(doc["players"]) != set(remaining()[index::SHARDS]):
            raise ValueError("Inventory partition mismatch")
        for slug, row in doc["players"].items():
            for gid in row.pop("trajectory_ids", []):
                owners[gid].append(slug)
            result[slug] = row
    if set(result) != set(remaining()) or len(result) != 60:
        raise ValueError("Incomplete roster")
    report = {"players": result, "count": 60, "statuses": dict(Counter(r["status"] for r in result.values())),
              "unique_trajectories": len(owners), "shared_trajectories": sum(len(x) > 1 for x in owners.values()),
              "wave": WAVE, "promotions": []}
    atomic_json(out, report)
    print("ROSTER_TOTAL", json.dumps(report["statuses"], sort_keys=True), flush=True)


def sample(slug, data, arm):
    if arm not in ("fit", "tune"):
        raise ValueError("Only development arms allowed")
    period = WAVE[slug]
    pgn, rows, _, _, surname = data
    eligible = [r for r in rows if in_period(r, period) and neural_arm(r["mainline_sha256"]) == arm]
    positions = defaultdict(list)
    roots = {}
    for game, row in _iter_verified_games(pgn, eligible, surname):
        board = game.board()
        if board.fen() != chess.STARTING_FEN:
            continue
        prefix, per_phase = [], {}
        gid = row["mainline_sha256"]
        for move in game.mainline_moves():
            if board.turn == (row["color"] == "white") and board.legal_moves.count() > 1:
                stage = phase(board)
                rank = digest([slug, arm, gid, board.ply()])
                if stage not in per_phase or rank < per_phase[stage][0]:
                    per_phase[stage] = (rank, board.copy(stack=True), move, gid)
            board.push(move)
            if len(prefix) < 12:
                prefix.append(move.uci())
                if len(prefix) == 12 and not board.is_game_over(claim_draw=True):
                    roots[tuple(prefix)] = {"id": digest(prefix), "prefix": prefix.copy()}
        for stage, record in per_phase.items():
            positions[stage].append(record)
    probes = []
    for stage in PHASES:
        candidates = sorted(positions[stage], key=lambda x: x[0])
        if len(candidates) < PER_PHASE:
            raise ValueError(f"Insufficient {slug} {arm} {stage} evidence: {len(candidates)}")
        probes.extend(candidates[:PER_PHASE])
    starts = sorted(roots.values(), key=lambda x: x["id"])[:ROOTS]
    if len(starts) != ROOTS:
        raise ValueError("Insufficient distinct roots")
    return probes, starts


def parameters(raw):
    """Symmetric local candidate search around individual original, not stereotypes."""
    def get(key):
        hits = re.findall(rf"(?m)^setoption name {key} value (-?\d+)[ \t]*$", raw)
        if len(hits) != 1:
            raise ValueError("Missing or ambiguous parameter " + key)
        return int(hits[0])
    def bounded(key, delta):
        return max(0, min(1200, get(key) + delta))
    return {"original": {},
            "initiative": {k: bounded(k, d) for k, d in
                           (("OwnAttack", 15), ("PiecePressure", 10), ("OwnMobility", 10))},
            "restriction": {k: bounded(k, d) for k, d in
                            (("OppMobility", 15), ("Outposts", 10), ("PawnStructure", 10))}}


def overlay(root, dest, slug):
    if (file_hash(root / "rodent-iv-plain.exe") != RODENT_HASHES["rodent"]
            or file_hash(root / "personalities/simple.txt") != RODENT_HASHES["simple"]):
        raise ValueError("Unpinned Rodent build")
    original = find_player(slug)
    validate(root, original, original)
    shutil.copytree(root, dest)
    directory = dest / "personalities"
    raw = (directory / original.profile_filename).read_text(encoding="utf-8-sig")
    params = parameters(raw)
    candidates, additions = {"original": original}, {}
    for name in ("initiative", "restriction"):
        alias = original.rodent_alias + "Wave" + name.capitalize()
        filename = alias.lower() + ".txt"
        (directory / filename).write_text(render(raw, params[name]), encoding="utf-8")
        candidates[name] = replace(original, rodent_alias=alias, rodent_file=filename)
        additions[alias] = filename
    ini = directory / "basic.ini"
    ini.write_text(add_to_first_set(ini.read_text(), additions))
    for p in candidates.values():
        validate(dest, p, p)
    return candidates, params


def evaluate(probes, engines, labels, reference):
    records = []
    for _, board, human, gid in probes:
        choices = {"human": human.uci()}
        telemetry = {}
        for label in labels:
            name, depth = split_label(label)
            move, telemetry[label] = choose(engines[name], board, depth)
            choices[label] = move.uci()
        history = adaptive_reference(reference, board, choices)
        final = history[-1]
        raw = final["evals"]
        best = raw[final["ranked"][0]["move"]]
        stage = phase(board)
        initial = history[0]["ranked"]
        values = {key: {"move": move, "score": _policy_metric(best, raw[move]),
                       "features": features(board, chess.Move.from_uci(move)),
                       "match": move == human.uci()} for key, move in choices.items()}
        records.append({"game": gid, "fen": board.fen(), "phase": stage,
                        "tags": tags(board, stage, initial[0], initial[1]),
                        "values": values, "telemetry": telemetry, "reference": history})
    return records


def eligible_quality(q):
    return q["numeric"] >= 20 and q["excess_cp"] <= 35 and q["severe_excess"] <= .15


def measure(slug, root, stockfish, out):
    data = source(slug)
    fit, roots = sample(slug, data, "fit")
    tune, _ = sample(slug, data, "tune")
    if {r[3] for r in fit} & {r[3] for r in tune}:
        raise ValueError("FIT/TUNE trajectory overlap")
    book = build(slug, data, period=WAVE[slug])
    book_stats = audit(book)
    if file_hash(stockfish) != STOCKFISH_SHA:
        raise ValueError("Unpinned Stockfish")
    out.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        location = Path(tmp) / "rodent"
        players, params = overlay(root, location, slug)
        engines = {k: stack.enter_context(_open_engine(location, location / "rodent-iv-plain.exe", p, book=False))
                   for k, p in players.items()}
        ref = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(stockfish)))
        ref.configure({"Threads": 1, "Hash": 64})
        labels = [f"{n}{d}" for n in players for d in DEPTHS]
        records = evaluate(fit, engines, labels, ref)
        stats = summarize(records)
        # Advance the best FIT candidate at each depth, even if it fails quality;
        # final screening never promotes an ineligible candidate.
        shortlist = [min((f"{n}{d}" for n in players), key=lambda k: (
            not eligible_quality(stats[k]), stats[k]["objective"], k)) for d in DEPTHS]
        hashes = {n: file_hash(location / "personalities" / p.profile_filename) for n, p in players.items()}
        doc = {"slug": slug, "period": WAVE[slug], "source_sha256": sha256(data[0]).hexdigest(),
               "book_sha256": digest(book), "book_audit": book_stats, "params": params,
               "hashes": hashes, "roots": roots, "shortlist": shortlist,
               "fit_stats": stats, "fit_records": records}
        atomic_json(out / "measure.json", doc)
        atomic_json(out / "book.json", book)
    print("WAVE_MEASURE", slug, json.dumps({"shortlist": shortlist, "fit_stats": stats}, sort_keys=True), flush=True)


def bound_measure(slug, selection, data):
    doc = json.loads((selection / "measure.json").read_text())
    _, roots = sample(slug, data, "fit")
    if (doc["slug"] != slug or doc["period"] != list(WAVE[slug])
            or doc["source_sha256"] != sha256(data[0]).hexdigest()
            or doc["book_sha256"] != digest(build(slug, data, period=WAVE[slug]))
            or doc["roots"] != roots or len(doc["shortlist"]) != 3
            or {split_label(k)[1] for k in doc["shortlist"]} != set(DEPTHS)):
        raise ValueError("Measurement binding mismatch")
    if doc["fit_stats"] != summarize(doc["fit_records"]):
        raise ValueError("FIT summary mismatch")
    return doc, roots


def screen(slug, root, selection, index, out):
    doc, roots = bound_measure(slug, selection, source(slug))
    start = roots[index]
    records = []
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        location = Path(tmp) / "rodent"
        players, params = overlay(root, location, slug)
        if params != doc["params"]:
            raise ValueError("Candidate parameters changed")
        simple = Player(0, "Simple", "control-simple", "control", "Simple", "simple.txt")
        exe = location / "rodent-iv-plain.exe"
        control = stack.enter_context(_open_engine(location, exe, simple, book=False))
        for label in doc["shortlist"]:
            name, depth = split_label(label)
            p = players[name]
            profile_hash = file_hash(location / "personalities" / p.profile_filename)
            if profile_hash != doc["hashes"][name]:
                raise ValueError("Candidate profile changed")
            with _open_engine(location, exe, p, book=False) as engine:
                for color in ("white", "black"):
                    case = {"id": digest([slug, label, start["id"], color]), "root": start["id"],
                            "color": color, "depth": depth, "opponent_depth": CONTROL}
                    raw = play_clock(case, start, DepthControl(engine, depth), DepthControl(control, CONTROL))
                    validate_clock(raw, case, start)
                    records.append({"label": label, "case": case, "game": raw, "profile_sha256": profile_hash})
    atomic_json(out, {"slug": slug, "index": index, "records": records})


def scores(slug, doc, roots, shards):
    result = {label: Counter() for label in doc["shortlist"]}
    for i, start in enumerate(roots):
        raw = json.loads((shards / f"screen-{slug}-{i}" / "report.json").read_text())
        if raw["slug"] != slug or raw["index"] != i:
            raise ValueError("Wrong screen shard")
        seen = set()
        for row in raw["records"]:
            label, case = row["label"], row["case"]
            name, depth = split_label(label)
            color = case["color"]
            if (label not in result or color not in ("white", "black") or (label, color) in seen
                    or case["root"] != start["id"] or case["depth"] != depth
                    or case["opponent_depth"] != CONTROL or row["profile_sha256"] != doc["hashes"][name]
                    or case["id"] != digest([slug, label, start["id"], color])):
                raise ValueError("Wrong screen case")
            seen.add((label, color))
            game = validate_clock(row["game"], case, start)
            res = game["result"]
            key = ("incomplete" if res == "*" else "draws" if res == "1/2-1/2"
                   else "wins" if (res == "1-0") == (color == "white") else "losses")
            result[label][key] += 1
            result[label]["flags"] += int(game["time_forfeit"] is not None)
        if len(seen) != 2 * len(result):
            raise ValueError("Missing screen game")
    for row in result.values():
        for key in ("wins", "draws", "losses", "incomplete", "flags"):
            row.setdefault(key, 0)
        row["score"] = (row["wins"] + row["draws"] / 2) / (2 * ROOTS)
    return result


def finish(slug, root, stockfish, selection, shards, out):
    data = source(slug)
    doc, roots = bound_measure(slug, selection, data)
    rows = scores(slug, doc, roots, shards)
    eligible = [k for k, r in rows.items() if not r["incomplete"] and not r["flags"]
                and r["score"] >= MIN_SCORE and eligible_quality(doc["fit_stats"][k])]
    selected = min(eligible, key=lambda k: (doc["fit_stats"][k]["objective"], -rows[k]["score"], k)) if eligible else None
    report = {"slug": slug, "period": WAVE[slug], "source_sha256": doc["source_sha256"],
              "book_sha256": doc["book_sha256"], "book_audit": doc["book_audit"],
              "fit_stats": doc["fit_stats"], "strength": rows, "selected": selected,
              "screen_control": CONTROL, "screen_min_score": MIN_SCORE,
              "status": "NO_ELIGIBLE_CANDIDATE", "promotion": "DENIED_PENDING_REVIEW"}
    out.mkdir(parents=True, exist_ok=False)
    if selected:
        name, depth = split_label(selected)
        probes, _ = sample(slug, data, "tune")
        if {p[3] for p in probes} & {r["game"] for r in doc["fit_records"]}:
            raise ValueError("FIT/TUNE overlap")
        if file_hash(stockfish) != STOCKFISH_SHA:
            raise ValueError("Unpinned Stockfish")
        with TemporaryDirectory() as tmp, ExitStack() as stack:
            location = Path(tmp) / "rodent"
            players, params = overlay(root, location, slug)
            candidate = location / "personalities" / players[name].profile_filename
            if params != doc["params"] or file_hash(candidate) != doc["hashes"][name]:
                raise ValueError("Selected candidate mismatch")
            exe = location / "rodent-iv-plain.exe"
            engine = stack.enter_context(_open_engine(location, exe, players[name], book=False))
            ref = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(stockfish)))
            ref.configure({"Threads": 1, "Hash": 64})
            tune = evaluate(probes, {name: engine}, [selected], ref)
            indices = list(range(0, len(probes), 3))[:10]
            repeats = sum(choose(engine, probes[i][1], depth)[0].uci() == tune[i]["values"][selected]["move"]
                          for i in indices)
            stats = summarize(tune)[selected]
            shutil.copyfile(candidate, out / "candidate.txt")
            atomic_json(out / "tune.json", tune)
            report.update({"tune": stats, "repeat_matches": repeats, "repeat_total": len(indices),
                           "profile_sha256": doc["hashes"][name], "overrides": params[name], "depth": depth,
                           "status": "MOTOR_BASE_REVIEW_READY" if eligible_quality(stats) and repeats == 10
                           else "TUNE_OR_STABILITY_FAILED"})
    atomic_json(out / "report.json", report)
    print("WAVE_RESULT", json.dumps(report, sort_keys=True), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inventory", "inventory-total", "measure", "screen", "finish"))
    p.add_argument("--player", choices=sorted(WAVE))
    p.add_argument("--rodent", type=Path)
    p.add_argument("--stockfish", type=Path)
    p.add_argument("--index", type=int)
    p.add_argument("--selection", type=Path)
    p.add_argument("--shards", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.action == "inventory":
        inventory(a.index, a.out)
    elif a.action == "inventory-total":
        inventory_total(a.shards, a.out)
    elif a.action == "measure":
        measure(a.player, a.rodent, a.stockfish, a.out)
    elif a.action == "screen":
        screen(a.player, a.rodent, a.selection, a.index, a.out)
    else:
        finish(a.player, a.rodent, a.stockfish, a.selection, a.shards, a.out)


if __name__ == "__main__":
    main()
