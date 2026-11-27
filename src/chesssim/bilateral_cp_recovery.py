"""Retain observed in-band product baselines; select other candidates on FIT and TUNE.

Previously observed reserved data is diagnostic, never a selection input.
"""
import argparse
import base64
from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import chess

from . import bilateral_cp as cp
from .qualified_runtime import assets, PROFILE_KEYS
from .matches import _open_engine

LOCKS = Path("experiments/bilateral-cp/retained-active-baselines.json")
SCHEMA = "cp-generalization-recovery-v1"
DEV_LIMIT = 40


def locks():
    doc = cp.load(LOCKS)
    if doc["schema"] != "cp-baseline-retention-v1" or len(doc["players"]) != 5:
        raise ValueError("Invalid maintainer retention policy")
    return doc["players"]


def verify_locks():
    current = assets()
    for slug, record in locks().items():
        a = current[slug]
        actual = (a["alias"], a["depth"], a["uci_node_cap"], a["hashes"][PROFILE_KEYS[slug]],
                  a.get("historical_book_digest"))
        expected = tuple(record[k] for k in ("alias", "depth", "uci_node_cap", "profile_sha256",
                                              "historical_book_digest"))
        if actual != expected or abs(record["delta_cp"]) > 1:
            raise ValueError("Retained baseline drift: " + slug)
    return locks()


def development_panels(slug, data, reserved):
    """Disjoint trajectories and four-field FENs; no TEST score enters selection."""
    pgn, rows, _, _, surname = data
    start, end = cp.PERIOD[slug]
    eligible = [r for r in rows if str(r["date"]).isdigit() and start <= int(r["date"]) <= end]
    grouped = defaultdict(list)
    for game, row in cp._iter_verified_games(pgn, eligible, surname):
        gid = row["mainline_sha256"]
        arm = cp.partition(slug, gid)
        if arm not in ("fit", "tune"):
            continue
        board = game.board()
        if board.fen() != chess.STARTING_FEN or game.headers.get("Result") not in ("1-0", "0-1", "1/2-1/2"):
            continue
        prefix, choices = [], {}
        for move in game.mainline_moves():
            if board.turn == (row["color"] == "white") and board.ply() >= 16 and board.legal_moves.count() > 1:
                stage = cp.phase(board)
                key = cp.digest([SCHEMA, gid, stage, board.ply()])
                if stage not in choices or key < choices[stage]["id"]:
                    choices[stage] = {"id": key, "game": gid, "phase": stage, "arm": arm,
                                      "prefix": prefix.copy(), "fen": board.fen(), "move": move.uci()}
            board.push(move)
            prefix.append(move.uci())
        for stage, item in choices.items():
            grouped[arm, stage].append(item)
    return choose_panels(grouped, reserved)


def choose_panels(grouped, reserved):
    used = {" ".join(row["fen"].split()[:4]) for row in reserved}
    panels = {}
    for arm in ("fit", "tune"):
        panels[arm] = []
        for stage in cp.PHASES:
            count = 0
            for row in sorted(grouped[arm, stage], key=lambda x: x["id"]):
                key = " ".join(row["fen"].split()[:4])
                if key in used:
                    continue
                used.add(key)
                panels[arm].append(row)
                count += 1
                if count == DEV_LIMIT:
                    break
    game_sets = [{r["game"] for r in rows} for rows in (*panels.values(), reserved)]
    if any(a & b for i, a in enumerate(game_sets) for b in game_sets[i+1:]):
        raise ValueError("Development trajectory leakage")
    return panels


def valid_shape(s):
    return s["delta_cp"] is not None and cp.fit_eligible({**s, "delta_cp": 0}, strict=True)


def robust_select(fit, tune):
    if set(fit) != set(tune) or not fit:
        raise ValueError("Incomplete two-panel candidate inventory")

    def order(key):
        pair = (fit[key], tune[key])
        invalid = not all(valid_shape(s) for s in pair)
        if any(s["delta_cp"] is None for s in pair):
            return (True, True, float("inf"), float("inf"), float("inf"), key != "baseline", key)
        gaps = [abs(s["delta_cp"]) for s in pair]
        phase = max(abs(v) for s in pair for v in s["phase_delta"].values())
        return (invalid, max(gaps) > 1, max(gaps), phase, sum(gaps), key != "baseline", key)
    return min(fit, key=order)


def shortlist(summary, cases):
    # Keep the baseline and the five best FIT recipes. No TEST feedback.
    remaining = dict(summary)
    result = {"baseline": cases["baseline"]}
    while remaining and len(result) < 6:
        key = cp.select(remaining, strict=True)
        result[key] = cases[key]
        del remaining[key]
    return result


def run(slug, root, stockfish, out):
    retained = verify_locks()
    if slug in retained:
        raise ValueError("Maintainer retained this active baseline; do not retune")
    prior = cp.baseline_inventory().get(slug, {})
    data, _ = cp.historical(slug, prior)
    original, _ = cp.panel(slug, data)
    reserved = original["excluded_test"]
    dev = development_panels(slug, data, reserved)
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        dest = Path(tmp) / "rodent"
        players, baseline = cp.prepare(root, dest, slug, prior)
        coarse = cp.recipes(players, baseline)
        engines = {k: stack.enter_context(_open_engine(dest, dest / "rodent-iv-plain.exe", p, book=False))
                   for k, p in players.items()}
        ref = cp.reference_engine(stack, stockfish)
        rows = cp.measure(ref, engines, coarse, dev["fit"])
        cp.verify_rows(rows, dev["fit"], coarse)
        coarse_stats = {k: cp.stats(rows, k) for k in coarse}
        anchor = cp.select(coarse_stats, strict=True)
        extra = cp.intermediate_profiles(dest, players, baseline, coarse[anchor][0])
        players.update(extra)
        engines.update({k: stack.enter_context(_open_engine(dest, dest / "rodent-iv-plain.exe", p, book=False))
                        for k, p in extra.items()})
        fine = cp.refine_cases(coarse, anchor, extra)
        fine_rows = cp.measure(ref, engines, fine, dev["fit"])
        cp.verify_rows(fine_rows, dev["fit"], fine)
        screening = {k: cp.stats(fine_rows, k) for k in fine}
        finalists = shortlist(screening, fine)
        # Same candidates and reference policy on both panels. Recompute FIT
        # for the shortlist so adaptive reference depth is comparable.
        fit_rows = cp.measure(ref, engines, finalists, dev["fit"])
        tune_rows = cp.measure(ref, engines, finalists, dev["tune"])
        cp.verify_rows(fit_rows, dev["fit"], finalists)
        cp.verify_rows(tune_rows, dev["tune"], finalists)
        fit_stats = {k: cp.stats(fit_rows, k) for k in finalists}
        tune_stats = {k: cp.stats(tune_rows, k) for k in finalists}
        selected = robust_select(fit_stats, tune_stats)
        recipe = finalists[selected]
        selected_ok = all(cp.fit_eligible(s, strict=True) for s in (fit_stats[selected], tune_stats[selected]))
        # TEST is evaluated after selection. Repeated TEST is diagnostic only.
        locked = {"baseline": baseline, "candidate": recipe}
        test_rows = cp.measure(ref, engines, locked, reserved)
        cp.verify_rows(test_rows, reserved, locked)
        test = cp.stats(test_rows, "candidate", bootstrap=True)
        test_base = cp.stats(test_rows, "baseline", bootstrap=True)
        book, book_info = cp.historical_book(slug, data, prior)
        profile = cp.profile_bytes(dest, players[recipe[0]])
        doc = {"schema": SCHEMA, "slug": slug, "selected": selected, "recipe": recipe,
               "baseline_recipe": baseline, "source_sha256": sha256(data[0]).hexdigest(),
               "engine_sha256": cp.RODENT_HASHES["rodent"], "stockfish_sha256": cp.STOCKFISH_SHA,
               "panel_digests": {k: cp.digest(v) for k, v in {**dev, "test": reserved}.items()},
               "anchor": anchor, "coarse": coarse, "coarse_rows": rows, "fine": fine,
               "fine_rows": fine_rows, "finalists": finalists, "fit_rows": fit_rows, "tune_rows": tune_rows,
               "fit": fit_stats, "tune": tune_stats, "development_in_band": selected_ok,
               "test_rows": test_rows, "test": test, "test_baseline": test_base,
               "test_gate": cp.gate(test, strict=True), "profile_sha256": sha256(profile).hexdigest(),
               "profile_base64": base64.b64encode(profile).decode(), "alias": players[recipe[0]].rodent_alias,
               "book": book, "book_info": book_info,
               "status": "FRESH_VALIDATION_REQUIRED" if selected_ok else "DEVELOPMENT_TARGET_NOT_ESTABLISHED",
               "activation": False}
        cp.save(out, doc)
        print("CP_RECOVERY", slug, "fit", fit_stats[selected]["delta_cp"], "tune", tune_stats[selected]["delta_cp"],
              "diagnostic-test", test["delta_cp"], doc["status"], doc["test_gate"], flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--player")
    p.add_argument("--rodent", type=Path)
    p.add_argument("--stockfish", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.player:
        run(args.player, args.rodent, args.stockfish, args.out)
    else:
        cp.save(args.out, {"schema": SCHEMA, "retained": verify_locks(), "activation": False})


if __name__ == "__main__":
    main()
