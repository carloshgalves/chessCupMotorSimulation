"""Peak product calibration for Karpov and Kasparov, not human Elo."""
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import argparse
import json
import re
import shutil
from statistics import mean
from tempfile import TemporaryDirectory

import chess
import chess.engine

from .adaptive_quality_parity import adaptive_reference, _policy_metric, STOCKFISH_SHA
from .catalog import find_player, Player
from .fischer_baseline import phase
from .fischer_deep_calibration import choose, tags
from .fischer_final_gate import features, play_clock, validate_clock, RODENT_HASHES
from .fischer_full_games import atomic_json, digest
from .matches import _open_engine, file_hash
from .product_repertoires import audit, build
from .product_strength_ladder import DepthControl
from .real85_neural_stream import neural_arm
from .real_training_cohorts import (
    _player_configuration, _source_bytes, _scan, _iter_verified_games,
)
from .registry import add_to_first_set, registered_aliases

PEAKS = {"karpov": (1975, 1985), "kasparov": (1993, 2000)}
DEPTHS = (12, 16, 20)
CONTROLS = (8, 12, 16, 20)
ROOTS = 12
PROBES_PER_PHASE = 12
VARIANTS = {
    "karpov": {
        "original": {},
        "restriction": {"OppMobility": 110, "Outposts": 135, "PawnStructure": 160},
        "balanced": {"OppAttack": 105, "OwnMobility": 110, "PawnShield": 135},
    },
    "kasparov": {
        "original": {},
        "initiative": {"OwnAttack": 265, "PiecePressure": 160, "KingTropism": 65},
        "balanced": {"Material": 98, "OppAttack": 120, "OwnMobility": 145},
    },
}


def source(slug):
    config = _player_configuration([slug], None, None)[slug]
    _, pgn, origin = _source_bytes(config["source"], None)
    rows, scan = _scan(pgn, slug, config["surname"])
    if not rows:
        raise ValueError("Empty historical source")
    return pgn, rows, scan, origin, config["surname"]


def peak(row, slug):
    start, end = PEAKS[slug]
    return str(row["date"]).isdigit() and start <= int(row["date"]) <= end


def inventory(slug, data):
    pgn, rows, scan, origin, surname = data
    years = Counter()
    phases = Counter()
    style = defaultdict(Counter)
    for game, row in _iter_verified_games(pgn, rows, surname):
        years[str(row["date"])] += 1
        board = game.board()
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise ValueError("Illegal source trajectory")
            if board.turn == (row["color"] == "white"):
                stage = phase(board)
                phases[stage] += 1
                if peak(row, slug) and neural_arm(row["mainline_sha256"]) == "fit":
                    style[stage]["decisions"] += 1
                    style[stage].update(k for k, v in features(board, move).items() if v)
            board.push(move)
    return {"player": slug, "source": origin, "pgn_sha256": sha256(pgn).hexdigest(),
            "scan": scan, "games": len(rows), "by_year": dict(years),
            "peak": list(PEAKS[slug]), "peak_games": sum(peak(r, slug) for r in rows),
            "phase_decisions": dict(phases), "peak_fit_style": dict(style),
            "trajectory_ids": sorted(r["mainline_sha256"] for r in rows)}


def panel(slug, data):
    pgn, rows, _, _, surname = data
    eligible = [r for r in rows if peak(r, slug)
                and neural_arm(r["mainline_sha256"]) == "fit"]
    positions = defaultdict(list)
    roots = {}
    for game, row in _iter_verified_games(pgn, eligible, surname):
        board = game.board()
        prefix = []
        per_phase = {}
        gid = row["mainline_sha256"]
        if board.fen() != chess.STARTING_FEN:
            continue
        for move in game.mainline_moves():
            if board.turn == (row["color"] == "white") and board.legal_moves.count() > 1:
                stage = phase(board)
                rank = digest([slug, gid, board.ply()])
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
    for stage in ("opening", "middlegame", "endgame"):
        candidates = sorted(positions[stage], key=lambda x: x[0])
        if len(candidates) < PROBES_PER_PHASE:
            raise ValueError("Insufficient peak phase evidence")
        probes.extend(candidates[:PROBES_PER_PHASE])
    starts = sorted(roots.values(), key=lambda x: x["id"])[:ROOTS]
    if len(starts) != ROOTS:
        raise ValueError("Insufficient peak opening roots")
    return probes, starts


def render(original, overrides):
    result = original
    for key, val in sorted(overrides.items()):
        pattern = rf"(?m)^(setoption name {re.escape(key)} value )-?\d+[ \t]*$"
        result, count = re.subn(pattern, lambda m: m[1] + str(val), result)
        if count != 1:
            raise ValueError("Unknown or ambiguous Rodent parameter: " + key)
    return result


def overlay(root, destination, slug):
    root = Path(root)
    if file_hash(root / "rodent-iv-plain.exe") != RODENT_HASHES["rodent"]:
        raise ValueError("Unexpected Rodent binary")
    if file_hash(root / "personalities/simple.txt") != RODENT_HASHES["simple"]:
        raise ValueError("Unexpected Simple control")
    shutil.copytree(root, destination)
    directory = destination / "personalities"
    player = find_player(slug)
    raw = (directory / player.profile_filename).read_text(encoding="utf-8-sig")
    registry = directory / "basic.ini"
    variants = {}
    aliases = {}
    for name, params in VARIANTS[slug].items():
        if not params:
            variants[name] = player
            continue
        alias = player.rodent_alias + name.capitalize()
        filename = alias.lower() + ".txt"
        (directory / filename).write_text(render(raw, params), encoding="utf-8")
        aliases[alias] = filename
        variants[name] = replace(player, rodent_alias=alias, rodent_file=filename)
    registry.write_text(add_to_first_set(registry.read_text(), aliases))
    registered = registered_aliases(registry)
    if any(registered.get(p.rodent_alias) != p.profile_filename for p in variants.values()):
        raise ValueError("Candidate alias unavailable")
    return variants


def measure(slug, root, stockfish, out):
    data = source(slug)
    probes, starts = panel(slug, data)
    if file_hash(stockfish) != STOCKFISH_SHA:
        raise ValueError("Unexpected shared Stockfish binary")
    out.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory() as temp, ExitStack() as stack:
        location = Path(temp) / "rodent"
        variants = overlay(root, location, slug)
        exe = location / "rodent-iv-plain.exe"
        engines = {name: stack.enter_context(_open_engine(location, exe, p, book=False))
                   for name, p in variants.items()}
        ref = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(stockfish)))
        ref.configure({"Threads": 1, "Hash": 64})
        records = []
        for _, board, human, gid in probes:
            choices = {"human": human.uci()}
            telemetry = {}
            for name, engine in engines.items():
                for depth in DEPTHS:
                    label = f"{name}{depth}"
                    move, telemetry[label] = choose(engine, board, depth)
                    choices[label] = move.uci()
            history = adaptive_reference(ref, board, choices)
            final = history[-1]
            raw = final["evals"]
            ranked = final["ranked"]
            best = raw[ranked[0]["move"]]
            stage = phase(board)
            initial = history[0]["ranked"]
            labels = tags(board, stage, initial[0], initial[1])
            values = {key: {"move": move, "score": _policy_metric(best, raw[move]),
                            "features": features(board, chess.Move.from_uci(move)),
                            "match": move == human.uci()}
                      for key, move in choices.items()}
            records.append({"game": gid, "fen": board.fen(), "phase": stage,
                            "tags": labels, "values": values, "telemetry": telemetry,
                            "reference": history})
        stats = summarize(records)
        selected = min(stats, key=lambda k: (stats[k]["objective"], k))
        name, depth = split_label(selected)
        repeated = []
        for record, (_, board, _, _) in zip(records[:10], probes[:10]):
            move, _ = choose(engines[name], board, depth)
            repeated.append(move.uci() == record["values"][selected]["move"])
        profile = location / "personalities" / variants[name].profile_filename
        (out / "candidate.txt").write_bytes(profile.read_bytes())
        atomic_json(out / "selection.json", {"player": slug, "selected": selected,
                    "depth": depth, "variant": name, "overrides": VARIANTS[slug][name],
                    "profile_sha256": file_hash(profile), "stats": stats,
                    "repeat_matches": sum(repeated), "repeat_total": len(repeated),
                    "roots": starts, "records": records,
                    "source_sha256": sha256(data[0]).hexdigest(),
                    "book_sha256": digest(build(slug, data, period=PEAKS[slug]))})
        print("PEAK_SELECTION", slug, selected, json.dumps(stats[selected]), flush=True)


def split_label(label):
    for depth in DEPTHS:
        if label.endswith(str(depth)):
            return label[:-len(str(depth))], depth
    raise ValueError("Unknown candidate depth")


def summarize(records):
    """Joint numeric loss and descriptive behavior; report residual instability."""
    labels = sorted(set(records[0]["values"]) - {"human"})
    result = {}
    for label in labels:
        pairs = [(r["values"]["human"], r["values"][label]) for r in records]
        numeric = [(h["score"]["cp_loss"], c["score"]["cp_loss"])
                   for h, c in pairs if h["score"]["numeric"] and c["score"]["numeric"]]
        if not numeric:
            raise ValueError("No comparable numeric evidence")
        style_gap = mean(abs(mean(int(c["features"][k]) for _, c in pairs)
                             - mean(int(h["features"][k]) for h, _ in pairs))
                         for k in pairs[0][0]["features"])
        excess = mean(c - h for h, c in numeric)
        severe = mean(int(c > 100) - int(h > 100) for h, c in numeric)
        groups = {}
        for category in ("opening", "tactics", "defense", "dynamics", "positional", "endgames"):
            scores = [r["values"][label]["score"]["cp_loss"] for r in records
                      if category in r["tags"] and r["values"][label]["score"]["numeric"]]
            groups[category] = {"support": len(scores), "loss_cp": mean(scores) if scores else None}
        result[label] = {"numeric": len(numeric), "excess_cp": excess, "severe_excess": severe,
                         "style_gap": style_gap, "exact_match": mean(c["match"] for _, c in pairs),
                         "unstable_reference": sum(r["reference"][-1]["inconsistent"] for r in records),
                         "depth_reached": mean(r["telemetry"][label]["reached_requested"] for r in records),
                         "competencies": groups,
                         "objective": max(0, excess) + 100 * style_gap + 100 * max(0, severe)}
    return result


def strength(slug, root, selection, index, out):
    doc = json.loads((selection / "selection.json").read_text())
    data = source(slug)
    if (doc["player"] != slug or doc["source_sha256"] != sha256(data[0]).hexdigest()
            or doc["book_sha256"] != digest(build(slug, data, period=PEAKS[slug]))):
        raise ValueError("Selection/source mismatch")
    if file_hash(selection / "candidate.txt") != doc["profile_sha256"]:
        raise ValueError("Selection candidate bytes mismatch")
    name, depth = split_label(doc["selected"])
    if name not in VARIANTS[slug] or depth != doc["depth"]:
        raise ValueError("Unknown candidate")
    start = doc["roots"][index]
    if start != panel(slug, data)[1][index]:
        raise ValueError("Root mismatch")
    records = []
    with TemporaryDirectory() as temp, ExitStack() as stack:
        location = Path(temp) / "rodent"
        variants = overlay(root, location, slug)
        candidate = variants[name]
        if file_hash(location / "personalities" / candidate.profile_filename) != doc["profile_sha256"]:
            raise ValueError("Candidate changed")
        exe = location / "rodent-iv-plain.exe"
        simple = Player(0, "Simple", "control-simple", "control", "Simple", "simple.txt")
        a, b = [stack.enter_context(_open_engine(location, exe, p, book=False))
                for p in (candidate, simple)]
        for control in CONTROLS:
            for color in ("white", "black"):
                case = {"id": digest([slug, start["id"], control, color]),
                        "root": start["id"], "color": color, "depth": depth,
                        "opponent_depth": control}
                game = play_clock(case, start, DepthControl(a, depth), DepthControl(b, control))
                validate_clock(game, case, start)
                records.append({"case": case, "game": game})
    atomic_json(out, {"player": slug, "index": index, "profile_sha256": doc["profile_sha256"],
                      "records": records})


def aggregate(slug, selection, shards, out):
    doc = json.loads((selection / "selection.json").read_text())
    data = source(slug)
    if (doc["player"] != slug or doc["source_sha256"] != sha256(data[0]).hexdigest()
            or doc["book_sha256"] != digest(build(slug, data, period=PEAKS[slug]))
            or file_hash(selection / "candidate.txt") != doc["profile_sha256"]):
        raise ValueError("Aggregate selection binding mismatch")
    roots = panel(slug, data)[1]
    rows = {str(d): Counter() for d in CONTROLS}
    for i, start in enumerate(roots):
        shard = json.loads((shards / f"{slug}-{i}" / "report.json").read_text())
        if (shard["player"] != slug or shard["index"] != i
                or shard["profile_sha256"] != doc["profile_sha256"]):
            raise ValueError("Shard binding mismatch")
        seen = set()
        for item in shard["records"]:
            case = item["case"]
            d, color = case["opponent_depth"], case["color"]
            if (d not in CONTROLS or color not in ("white", "black") or (d, color) in seen
                    or case["depth"] != doc["depth"] or case["root"] != start["id"]
                    or case["id"] != digest([slug, start["id"], d, color])):
                raise ValueError("Invalid paired case")
            seen.add((d, color))
            game = validate_clock(item["game"], case, start)
            result = game["result"]
            key = ("incomplete" if result == "*" else "draws" if result == "1/2-1/2"
                   else "wins" if (result == "1-0") == (color == "white") else "losses")
            rows[str(d)][key] += 1
            rows[str(d)]["flags"] += int(game["time_forfeit"] is not None)
        if len(seen) != 8:
            raise ValueError("Missing paired cases")
    for row in rows.values():
        for key in ("wins", "draws", "losses", "incomplete", "flags"):
            row.setdefault(key, 0)
        n = row["wins"] + row["draws"] + row["losses"]
        row["score"] = (row["wins"] + row["draws"] / 2) / n if n else None
    stats = doc["stats"][doc["selected"]]
    ready = (all(not r["incomplete"] and not r["flags"] for r in rows.values())
             and rows["16"]["score"] >= .40 and stats["numeric"] >= 20
             and stats["excess_cp"] <= 35 and stats["severe_excess"] <= .15
             and doc["repeat_matches"] == doc["repeat_total"])
    report = {"player": slug, "selected": doc["selected"], "depth": doc["depth"],
              "profile_sha256": doc["profile_sha256"], "overrides": doc["overrides"],
              "source_sha256": doc["source_sha256"], "book_sha256": doc["book_sha256"],
              "quality": stats, "repeat_matches": doc["repeat_matches"],
              "repeat_total": doc["repeat_total"], "controls": rows,
              "status": "PRODUCT_REVIEW_READY" if ready else "NEEDS_PRODUCT_REVIEW",
              "human_elo_claim": False}
    atomic_json(out, report)
    print("PEAK_PRODUCT_RESULT", json.dumps(report, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "measure", "strength", "aggregate"))
    parser.add_argument("--player", choices=tuple(PEAKS))
    parser.add_argument("--rodent", type=Path)
    parser.add_argument("--stockfish", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--shards", type=Path)
    parser.add_argument("--index", type=int)
    a = parser.parse_args()
    if a.action == "inventory":
        a.out.mkdir(parents=True, exist_ok=False)
        docs = {}
        for slug in PEAKS:
            data = source(slug)
            docs[slug] = inventory(slug, data)
            book = build(slug, data, period=PEAKS[slug])
            docs[slug]["book_audit"] = audit(book)
            atomic_json(a.out / f"{slug}.json", book)
        overlap = set(docs["karpov"]["trajectory_ids"]) & set(docs["kasparov"]["trajectory_ids"])
        atomic_json(a.out / "inventory.json", {"players": docs, "shared_trajectories": len(overlap)})
        print("PEAK_INVENTORY", json.dumps({k: {x: v for x, v in d.items()
              if x != "trajectory_ids"} for k, d in docs.items()}, sort_keys=True), flush=True)
    elif a.action == "measure":
        measure(a.player, a.rodent, a.stockfish, a.out)
    elif a.action == "strength":
        strength(a.player, a.rodent, a.selection, a.index, a.out)
    else:
        aggregate(a.player, a.selection, a.shards, a.out)


if __name__ == "__main__":
    main()
