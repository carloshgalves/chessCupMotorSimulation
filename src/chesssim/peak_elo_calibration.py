"""Bilateral peak-strength experiment, separate from the production registry."""
from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
import argparse
import csv
import io
import json
import shutil
import tempfile
from urllib.request import Request, urlopen

import chess
import chess.engine
import chess.pgn
import numpy as np

from .adaptive_quality_parity import STOCKFISH_SHA, _reference_top2, _disagree, _score
from .catalog import find_player, Player
from .fischer_final_gate import RODENT_HASHES, play_clock, validate_clock
from .fischer_full_games import atomic_json, digest
from .matches import _open_engine, file_hash
from .peak_elo_targets import TARGETS, BASELINES, EXPERIMENTAL_BASELINES
from .qualified_runtime import assets, PROFILE_KEYS, player_for, LEGACY_SLUGS
from .roster_product_wave import overlay, WAVE, product_arm, source, _iter_verified_games
from .product_peak_profiles import render, split_label
from .registry import add_to_first_set

SCHEMA = "peak-elo-v1"
SOURCES = {
    "open24": "https://theweekinchess.com/assets/files/pgn/olym24.pgn",
    "women24": "https://theweekinchess.com/assets/files/pgn/olymw24.pgn",
    "open22": "https://theweekinchess.com/assets/files/pgn/olym22.pgn",
    "women22": "https://theweekinchess.com/assets/files/pgn/olymw22.pgn",
    "elite14": "https://theweekinchess.com/assets/files/pgn/sinqcup14.pgn",
    "elite15": "https://theweekinchess.com/assets/files/pgn/norway15.pgn",
}
BANDS = (1800, 2000, 2200, 2400, 2600, 2800)
MARGIN = 50
DEPTH = 20
CAP = 5_000_000
SEED = 20261126


def save(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, doc)


def load(path):
    return json.loads(Path(path).read_text())


def board_for(row):
    board = chess.Board()
    for move in row["prefix"]:
        board.push_uci(move)
    return board


def band(rating):
    return max((x for x in BANDS if x <= rating), default=0)


def fen_key(board):
    return " ".join(board.fen().split()[:4])


def partition(gid):
    return "test" if int(gid[:8], 16) % 4 == 0 else "fit"


def corpus(out):
    groups = defaultdict(list)
    roots = defaultdict(dict)
    seen = set()
    hashes = {}
    for name, url in SOURCES.items():
        with urlopen(Request(url, headers={"User-Agent": "ChessSimulation research"}), timeout=120) as response:
            if response.geturl().split("/")[2] not in ("theweekinchess.com", "www.theweekinchess.com"):
                raise ValueError("Unexpected corpus redirect")
            raw = response.read(40_000_001)
        if len(raw) > 40_000_000:
            raise ValueError("Oversize PGN")
        hashes[name] = {"url": url, "sha256": sha256(raw).hexdigest()}
        stream = io.StringIO(raw.decode("utf-8-sig", errors="replace"))
        while (game := chess.pgn.read_game(stream)) is not None:
            h = game.headers
            if (game.errors or game.board().fen() != chess.STARTING_FEN
                    or h.get("Result") not in ("1-0", "0-1", "1/2-1/2")
                    or not h.get("Date", "").startswith("20" + name[-2:])):
                continue
            try:
                ratings = [int(h["WhiteElo"]), int(h["BlackElo"])]
            except (KeyError, ValueError):
                continue
            moves = [m.uci() for m in game.mainline_moves()]
            if len(moves) < 30:
                continue
            gid = digest(moves)
            if gid in seen:
                continue
            seen.add(gid)
            arm = "test" if name.endswith(("22", "15")) else "fit"
            prefix = moves[:12]
            roots[arm][tuple(prefix)] = {"id": digest(prefix), "prefix": prefix}
            board = chess.Board()
            options = []
            for i, move in enumerate(moves):
                side = 0 if board.turn else 1
                rating = ratings[side]
                if 20 <= i <= 100 and 1800 <= rating < 2900 and board.legal_moves.count() > 1:
                    options.append({"id": digest([gid, i]), "game": gid, "prefix": moves[:i],
                                    "move": move, "rating": rating,
                                    "player": h["White" if side == 0 else "Black"],
                                    "source": name, "fen": fen_key(board), "arm": arm})
                board.push_uci(move)
            if options:
                row = min(options, key=lambda r: r["id"])
                groups[arm, band(row["rating"])].append(row)
    selected = {"fit": [], "test": []}
    fens = set()
    counts = {}
    for arm in selected:
        for b in BANDS:
            available = sorted(groups[arm, b], key=lambda r: r["id"])
            chosen = []
            for row in available:
                if row["fen"] in fens:
                    continue
                fens.add(row["fen"])
                chosen.append(row)
                if len(chosen) == 48:
                    break
            selected[arm].extend(chosen)
            counts[f"{arm}:{b}"] = {"available": len(available), "selected": len(chosen)}
    common = {}
    used = set()
    for arm in selected:
        common[arm] = []
        for root in sorted(roots[arm].values(), key=lambda r: r["id"]):
            key = fen_key(board_for(root))
            if key in used:
                continue
            used.add(key)
            common[arm].append(root)
            if len(common[arm]) == 12:
                break
    doc = {"schema": SCHEMA, "sources": hashes, "counts": counts, "positions": selected,
           "roots": common, "games": len(seen)}
    save(out, doc)
    print("PEAK_CORPUS", json.dumps({"games": len(seen), "counts": counts, "sources": hashes}), flush=True)


def reference_engine(stack, path):
    if file_hash(path) != STOCKFISH_SHA:
        raise ValueError("Unpinned reference engine")
    engine = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(path)))
    engine.configure({"Threads": 1, "Hash": 64})
    return engine


def expectation(score, ply):
    value = chess.engine.Mate(score["mate"]) if score["mate"] is not None else chess.engine.Cp(score["cp"])
    wdl = value.wdl(model="sf16", ply=ply)
    return (wdl.wins + 0.5 * wdl.draws) / 1000


def measure_position(ref, board, moves):
    ranked = _reference_top2(ref, board, DEPTH)
    raw = {m: _score(ref, board, chess.Move.from_uci(m), DEPTH)
           for m in sorted(set(moves.values()) | {r["move"] for r in ranked})}
    inconsistent = _disagree(ranked, raw)
    best = expectation(raw[ranked[0]["move"]], board.ply())
    second = expectation(raw[ranked[1]["move"]], board.ply())
    return {"features": [1.0, abs(best - .5), max(0., best - second), min(board.ply(), 100) / 100],
            "losses": {k: max(0., best - expectation(raw[m], board.ply())) for k, m in moves.items()},
            "inconsistent": inconsistent, "raw": raw, "ranked": ranked}


def calibration_shard(panel, stockfish, index, out):
    rows = panel["positions"]["fit"] + panel["positions"]["test"]
    output = []
    with ExitStack() as stack:
        ref = reference_engine(stack, stockfish)
        for row in rows[index::8]:
            result = measure_position(ref, board_for(row), {"human": row["move"]})
            output.append({**row, **result})
    save(out, {"index": index, "panel": digest(panel), "rows": output})


def design(rows):
    return np.array([r["features"] + [(r["rating"] - 2400) / 400] for r in rows], dtype=float)


def fit_model(rows):
    if len(rows) < 12:
        raise ValueError("Insufficient human calibration observations")
    x = design(rows)
    y = np.array([r["losses"]["human"] for r in rows])
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    if np.linalg.matrix_rank(x) != x.shape[1] or beta[-1] >= -1e-5:
        raise ValueError("Human calibration has no identifiable negative Elo slope")
    return beta


def inferred(rows, role, beta):
    y = np.array([r["losses"][role] for r in rows])
    x = np.array([r["features"] for r in rows])
    return float(2400 + 400 * np.mean(y - x @ beta[:-1]) / beta[-1])


def calibrate(panel, shards, out):
    rows = []
    for i in range(8):
        doc = load(Path(shards) / f"calibration-{i}" / "rows.json")
        if doc["index"] != i or doc["panel"] != digest(panel):
            raise ValueError("Calibration shard identity")
        rows.extend(doc["rows"])
    expected = {r["id"] for arm in panel["positions"].values() for r in arm}
    if len(rows) != len(expected) or {r["id"] for r in rows} != expected:
        raise ValueError("Incomplete or duplicated calibration corpus")
    fit = [r for r in rows if r["arm"] == "fit" and not r["inconsistent"]]
    test = [r for r in rows if r["arm"] == "test" and not r["inconsistent"]]
    report = {"schema": SCHEMA, "panel": digest(panel), "fit": fit, "test": test,
              "status": "INSUFFICIENT_CALIBRATION", "beta": None, "bootstrap": []}
    try:
        beta = fit_model(fit)
        bands = []
        for b in BANDS:
            cohort = [r for r in test if band(r["rating"]) == b]
            train = [r for r in fit if band(r["rating"]) == b]
            if len(cohort) < 12 or len(train) < 12:
                continue
            actual = float(np.mean([r["rating"] for r in cohort]))
            estimated = inferred(cohort, "human", beta)
            bands.append({"band": b, "n": len(cohort), "actual": actual, "estimate": estimated,
                          "absolute_error": abs(estimated - actual)})
        rng = np.random.default_rng(SEED)
        clusters = defaultdict(list)
        for row in fit:
            clusters[row["player"]].append(row)
        keys = sorted(clusters)
        for _ in range(400):
            sample = [r for k in rng.choice(keys, len(keys), replace=True) for r in clusters[k]]
            try:
                report["bootstrap"].append(fit_model(sample).tolist())
            except ValueError:
                pass
        report.update(beta=beta.tolist(), bands=bands, support=[min(r["rating"] for r in fit),
                       max(r["rating"] for r in fit)],
                      inconsistent_rate=1 - (len(fit) + len(test)) / len(rows))
        if (len(bands) == len(BANDS) and max(b["absolute_error"] for b in bands) <= 100
                and len(report["bootstrap"]) >= 360 and report["inconsistent_rate"] <= .1):
            report["status"] = "VALIDATED_PROXY"
        else:
            report["status"] = "UNVALIDATED_PROXY"
    except (ValueError, np.linalg.LinAlgError) as exc:
        report["reason"] = str(exc)
    save(out, report)
    print("PEAK_MODEL", json.dumps({k: v for k, v in report.items() if k not in ("fit", "test", "bootstrap")}), flush=True)


def prepare_player(root, dest, slug):
    asset = assets().get(slug)
    if slug in LEGACY_SLUGS:
        from .tal_strength_doe import render_tal_variant
        shutil.copytree(root, dest)
        original = find_player(slug)
        p = player_for(slug, asset)
        directory = dest / "personalities"
        if asset.get("overrides"):
            fn = render_tal_variant if slug == "tal" else render
            raw = (directory / original.profile_filename).read_text(encoding="utf-8-sig")
            (directory / p.profile_filename).write_text(fn(raw, asset["overrides"]), encoding="utf-8", newline="\n")
        ini = directory / "basic.ini"
        from .registry import registered_aliases
        if p.rodent_alias not in registered_aliases(ini):
            ini.write_text(add_to_first_set(ini.read_text(), {p.rodent_alias: p.profile_filename}))
        players = {"frozen": p}
        baseline = ("frozen", asset["depth"], CAP)
    else:
        players, _ = overlay(root, dest, slug)
        if asset:
            baseline = (asset["wave_variant"], asset["depth"], CAP)
        else:
            name, depth = split_label(BASELINES[slug])
            baseline = (name, depth, CAP)
    if asset:
        got = file_hash(dest / "personalities" / players[baseline[0]].profile_filename)
        if got != asset["hashes"][PROFILE_KEYS[slug]]:
            raise ValueError("Frozen profile hash mismatch")
    return players, baseline


class Budget:
    def __init__(self, engine, depth, cap):
        self.engine, self.depth, self.cap = engine, depth, cap

    def play(self, board, limit, **kwargs):
        return self.engine.play(board, chess.engine.Limit(**{**vars(limit), "depth": self.depth,
                                                            "nodes": self.cap}), **kwargs)

    def close(self):
        self.engine.close()


def own_positions(slug, panel):
    target = TARGETS[slug]
    legacy = {"fischer": (1970, 1972), "tal": (1957, 1961),
              "karpov": (1975, 1985), "kasparov": (1993, 2000)}
    period = WAVE.get(slug, legacy.get(slug))
    if target["year"]:
        period = (target["year"] - 2, target["year"] + 2)
    pgn, rows, _, _, surname = source(slug)
    other_games = {r["game"] for arm in panel["positions"].values() for r in arm}
    other_fens = {r["fen"] for arm in panel["positions"].values() for r in arm}
    output = {"fit": [], "test": []}
    for game, row in _iter_verified_games(pgn, rows, surname):
        try:
            year = int(game.headers["Date"][:4])
        except (KeyError, ValueError):
            continue
        if not period[0] <= year <= period[1] or game.board().fen() != chess.STARTING_FEN:
            continue
        gid = row["mainline_sha256"]
        moves = [m.uci() for m in game.mainline_moves()]
        if digest(moves) in other_games:
            continue
        arm = "test" if product_arm(gid) == "excluded_test" else "fit"
        board = chess.Board()
        options = []
        for i, move in enumerate(moves):
            if (20 <= i <= 100 and board.turn == (row["color"] == "white")
                    and board.legal_moves.count() > 1 and fen_key(board) not in other_fens):
                options.append({"id": digest([gid, i]), "game": gid, "prefix": moves[:i],
                                "move": move, "fen": fen_key(board), "arm": arm})
            board.push_uci(move)
        if options:
            output[arm].append(min(options, key=lambda r: r["id"]))
    selected = {"fit": [], "test": []}
    used = set()
    for arm in selected:
        for row in sorted(output[arm], key=lambda r: r["id"]):
            if row["fen"] in used:
                continue
            used.add(row["fen"])
            selected[arm].append(row)
            if len(selected[arm]) == 60:
                break
    return selected, period, sha256(pgn).hexdigest()


def evaluate(ref, engines, cases, rows):
    results = []
    for row in rows:
        board = board_for(row)
        moves = {"human": row["move"]}
        for label, (name, depth, cap) in cases.items():
            move = engines[name].play(board, chess.engine.Limit(depth=depth, nodes=cap),
                                      game=object()).move
            if move not in board.legal_moves:
                raise ValueError("Illegal engine move")
            moves[label] = move.uci()
        results.append({**row, **measure_position(ref, board, moves), "moves": moves})
    return results


def estimate(rows, role, model):
    rows = [r for r in rows if not r["inconsistent"]]
    if len(rows) < 12 or model["beta"] is None:
        return None
    beta = np.array(model["beta"])
    rng = np.random.default_rng(SEED)
    estimates, differences = [], []
    for b in model["bootstrap"]:
        sampled = [rows[i] for i in rng.integers(0, len(rows), len(rows))]
        b = np.array(b)
        e = inferred(sampled, role, b)
        h = inferred(sampled, "human", b)
        estimates.append(e)
        differences.append(e - h)
    rating = inferred(rows, role, beta)
    human = inferred(rows, "human", beta)
    return {"motor": rating, "human": human, "difference": rating-human, "n": len(rows),
            "ci95": np.quantile(estimates, [.025, .975]).tolist() if estimates else None,
            "difference_ci95": np.quantile(differences, [.025, .975]).tolist() if differences else None}


def clock_matches(slug, engine, simple, recipe, roots, arm):
    rows = []
    for i, root in enumerate(roots):
        control = (8, 12, 16, 20)[i % 4]
        for color in ("white", "black"):
            case = {"id": digest([slug, recipe, root["id"], color, control, arm]),
                    "root": root["id"], "color": color, "depth": recipe[1], "opponent_depth": control}
            game = play_clock(case, root, Budget(engine, recipe[1], recipe[2]), Budget(simple, control, CAP))
            validate_clock(game, case, root)
            rows.append({"control": control, "case": case, "game": game})
    return rows


def qualification(model, target, fit, test, matches):
    # Strict interval equivalence, not merely a point estimate near the target.
    if model["status"] != "VALIDATED_PROXY":
        return "MODEL_NOT_VALIDATED"
    if target["rating"] is None or not target["verified_scale"]:
        return "HUMAN_REFERENCE_NOT_VERIFIED"
    if not fit or not test or test["n"] < 40 or not test["difference_ci95"]:
        return "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    if not matches or any(r["game"]["status"] != "COMPLETE" or r["game"]["time_forfeit"] for r in matches):
        return "COMPETITIVE_VALIDATION_FAILED"
    low, high = test["difference_ci95"]
    if low < -MARGIN or high > MARGIN:
        return "BILATERAL_EQUIVALENCE_NOT_ESTABLISHED"
    return "REVIEW_REQUIRED_NOT_FROZEN"


def player(slug, panel, model, root, stockfish, out):
    if model["panel"] != digest(panel):
        raise ValueError("Model and panel mismatch")
    for name, path in (("rodent", root / "rodent-iv-plain.exe"), ("simple", root / "personalities/simple.txt")):
        if file_hash(path) != RODENT_HASHES[name]:
            raise ValueError("Unpinned engine or control")
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        dest = Path(tmp) / "rodent"
        players, baseline = prepare_player(root, dest, slug)
        exe = dest / "rodent-iv-plain.exe"
        engines = {name: stack.enter_context(_open_engine(dest, exe, p, book=False)) for name, p in players.items()}
        simple = stack.enter_context(_open_engine(dest, exe, Player(0, "Simple", "control-simple", "control", "Simple", "simple.txt"), book=False))
        ref = reference_engine(stack, stockfish)
        own, period, pgn_sha = own_positions(slug, panel)
        cases = {"baseline": baseline}
        for d in (8, 12, 16, 20, 24):
            cases[f"depth{d}"] = (baseline[0], d, CAP)
        for n in (500_000, 1_000_000):
            cases[f"nodes{n}"] = (baseline[0], baseline[1], n)
        for name in sorted(players):
            if name != baseline[0] and name != "original":
                cases[f"profile-{name}"] = (name, baseline[1], CAP)
        # Deduplicate equivalent recipes while retaining baseline identity.
        unique = {}
        for label, recipe in cases.items():
            if recipe not in unique.values():
                unique[label] = recipe
        cases = unique
        fit_rows = evaluate(ref, engines, cases, own["fit"])
        fit = {label: estimate(fit_rows, label, model) for label in cases}
        valid = {k: v for k, v in fit.items() if v is not None}
        selected = min(valid, key=lambda k: abs(valid[k]["difference"])) if valid and model["status"] == "VALIDATED_PROXY" else "baseline"
        recipe = cases[selected]
        # Selection is fixed before evaluating either reserved panel.
        reserved = {selected: recipe, "baseline": baseline}
        test_rows = evaluate(ref, engines, reserved, own["test"])
        common_rows = evaluate(ref, engines, reserved, panel["positions"]["test"])
        test = estimate(test_rows, selected, model)
        common = estimate(common_rows, selected, model)
        games = clock_matches(slug, engines[recipe[0]], simple, recipe, panel["roots"]["test"], "test")
        status = qualification(model, TARGETS[slug], fit.get(selected), test, games)
        profile = dest / "personalities" / players[recipe[0]].profile_filename
        report = {"schema": SCHEMA, "slug": slug, "reference": TARGETS[slug], "period": period,
                  "pgn_sha256": pgn_sha, "baseline": baseline, "baseline_experimental": slug in EXPERIMENTAL_BASELINES,
                  "selection": selected, "recipe": recipe, "profile_sha256": file_hash(profile),
                  "profile_text": profile.read_text(encoding="utf-8-sig"), "fit": fit,
                  "test": test, "common": common, "baseline_test": estimate(test_rows, "baseline", model),
                  "baseline_common": estimate(common_rows, "baseline", model), "games": games, "status": status,
                  "fit_rows": fit_rows, "test_rows": test_rows, "common_rows": common_rows,
                  "panel": digest(panel), "model": digest(model)}
        save(out, report)
        print("PEAK_PLAYER", json.dumps({k: report[k] for k in ("slug", "selection", "recipe", "test", "common", "status")}), flush=True)


def aggregate(shards, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for slug, target in sorted(TARGETS.items()):
        path = Path(shards) / f"peak-{slug}" / "report.json"
        doc = load(path) if path.exists() else {}
        if doc and doc["slug"] != slug:
            raise ValueError("Player shard mismatch")
        test = doc.get("test") or {}
        common = doc.get("common") or {}
        games = doc.get("games", [])
        rows.append({"player": slug, "nominal_human": target["rating"], "reference_kind": target["kind"],
                     "human_proxy": test.get("human"), "motor_proxy": test.get("motor"),
                     "delta": test.get("difference"), "ci95": test.get("difference_ci95"),
                     "common_motor_proxy": common.get("motor"),
                     "baseline_motor_proxy": (doc.get("baseline_test") or {}).get("motor"),
                     "baseline_delta": (doc.get("baseline_test") or {}).get("difference"),
                     "games": len(games),
                     "recipe": doc.get("recipe"), "status": doc.get("status", "MISSING_EXECUTION")})
    save(out / "table.json", {"schema": SCHEMA, "players": rows, "frozen": 0})
    with (out / "table.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("PEAK_TABLE", json.dumps(rows), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("corpus", "shard", "calibrate", "player", "aggregate"))
    p.add_argument("--panel", type=Path)
    p.add_argument("--model", type=Path)
    p.add_argument("--stockfish", type=Path)
    p.add_argument("--rodent", type=Path)
    p.add_argument("--shards", type=Path)
    p.add_argument("--index", type=int)
    p.add_argument("--player")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.action == "corpus":
        corpus(a.out)
    elif a.action == "shard":
        calibration_shard(load(a.panel), a.stockfish, a.index, a.out)
    elif a.action == "calibrate":
        calibrate(load(a.panel), a.shards, a.out)
    elif a.action == "player":
        player(a.player, load(a.panel), load(a.model), a.rodent, a.stockfish, a.out)
    else:
        aggregate(a.shards, a.out)


if __name__ == "__main__":
    main()
