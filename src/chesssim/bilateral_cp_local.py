"""Bounded local recovery: exact FIT reuse and at most two development passes."""
import argparse
import base64
from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import fcntl
import json
import chess

from . import bilateral_cp as cp
from . import bilateral_cp_recovery as recovery
from .cp_checkpoints import Checkpoints, Paused
from .matches import _open_engine

SCHEMA = "cp-local-recovery-v2"
MAX_NEW_RECIPES = 12


def fit_from_artifact(path, slug, data, panels, cases, hashes, book, book_info):
    old = cp.load(path)
    if (old["schema"] != cp.SCHEMA or old["slug"] != slug
            or old["source_sha256"] != sha256(data[0]).hexdigest()
            or old["fit_panel_sha256"] != cp.digest(panels["fit"])
            or old["engine_sha256"] != cp.RODENT_HASHES["rodent"]
            or old["stockfish_sha256"] != cp.STOCKFISH_SHA
            or old["book"] != book or old["book_info"] != book_info):
        raise ValueError("Prior FIT artifact source, engine or book mismatch")
    if old["cases"] != json.loads(json.dumps(cases)) or old["profiles"] != hashes:
        raise ValueError("Prior FIT recipe/profile mismatch")
    rows, summary = old["rows"], old["fit"]
    cp.verify_rows(rows, panels["fit"], cases)
    recomputed = {k: cp.stats(rows, k) for k in cases}
    if recomputed != summary or cp.select(recomputed, strict=old.get("strict", False)) != old["selected"]:
        raise ValueError("Prior FIT statistics or selection mismatch")
    return rows, recomputed


def tune_panel(slug, data, original):
    pgn, rows, _, _, surname = data
    start, end = cp.PERIOD[slug]
    eligible = [r for r in rows if str(r["date"]).isdigit() and start <= int(r["date"]) <= end]
    grouped = defaultdict(list)
    used_games = {r["game"] for r in original["fit"] + original["excluded_test"]}
    for game, row in cp._iter_verified_games(pgn, eligible, surname):
        gid = row["mainline_sha256"]
        arm = cp.partition(slug, gid)
        if gid in used_games or arm not in ("fit", "tune"):
            continue
        board = game.board()
        if board.fen() != chess.STARTING_FEN or game.headers.get("Result") not in ("1-0", "0-1", "1/2-1/2"):
            continue
        prefix, choices = [], {}
        for move in game.mainline_moves():
            if board.turn == (row["color"] == "white") and board.ply() >= 16 and board.legal_moves.count() > 1:
                stage = cp.phase(board)
                key = ("0" if arm == "tune" else "1") + cp.digest([SCHEMA, gid, stage, board.ply()])
                if stage not in choices or key < choices[stage]["id"]:
                    choices[stage] = {"id": key, "game": gid, "phase": stage, "arm": arm,
                                      "prefix": prefix.copy(), "fen": board.fen(), "move": move.uci()}
            board.push(move)
            prefix.append(move.uci())
        for stage, item in choices.items():
            grouped["tune", stage].append(item)
    return recovery.choose_panels(grouped, original["fit"] + original["excluded_test"])["tune"]


def bounded_fine(coarse, anchor, extra):
    all_cases = cp.refine_cases(coarse, anchor, extra)
    name, depth, cap = coarse[anchor]
    new = [(k, v) for k, v in all_cases.items() if k not in coarse]
    buckets = [
        [(k, v) for k, v in new if v[0] == name and v[2] == cap],
        [(k, v) for k, v in new if v[0] == name and v[2] != cap],
        [(k, v) for k, v in new if v[0] != name],
    ]
    for bucket in buckets:
        bucket.sort(key=lambda x: (abs(x[1][1]-depth), abs(x[1][2]-cap), x[0]))
    result = {}
    while any(buckets) and len(result) < MAX_NEW_RECIPES:
        for bucket in buckets:
            if bucket and len(result) < MAX_NEW_RECIPES:
                k, v = bucket.pop(0)
                result[k] = v
    return result


def in_band(fit, tune, key):
    return cp.fit_eligible(fit[key], strict=True) and cp.fit_eligible(tune[key], strict=True)


def run(slug, root, stockfish, out, checkpoint_dir, stop_file, prior_fit=None):
    if slug in recovery.verify_locks():
        raise ValueError("Retained active baseline")
    prior = cp.baseline_inventory().get(slug, {})
    data, _ = cp.historical(slug, prior)
    original, _ = cp.panel(slug, data)
    tune = tune_panel(slug, data, original)
    source_sha = sha256(data[0]).hexdigest()
    counts = {name: {phase: sum(r["phase"] == phase for r in panel) for phase in cp.PHASES}
              for name, panel in (("fit", original["fit"]), ("tune", tune))}
    if any(n < 12 for cells in counts.values() for n in cells.values()):
        cp.save(out, {"schema": SCHEMA, "slug": slug, "source_sha256": source_sha,
                      "status": "INSUFFICIENT_DEVELOPMENT_EVIDENCE", "support": counts,
                      "attempts": 0, "activation": False})
        print("CP_LOCAL", slug, "INSUFFICIENT_DEVELOPMENT_EVIDENCE", counts, flush=True)
        return
    identity = [SCHEMA, slug, source_sha, cp.RODENT_HASHES, cp.STOCKFISH_SHA,
                cp.file_hash(Path(__file__)), cp.file_hash(Path(cp.__file__)),
                cp.file_hash(Path(recovery.__file__)), "checkpoint-v1",
                cp.file_hash(prior_fit) if prior_fit else None]
    chk = Checkpoints(checkpoint_dir, stop_file, identity)
    chk.install_signals()
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        dest = Path(tmp) / "rodent"
        players, baseline = cp.prepare(root, dest, slug, prior)
        coarse = cp.recipes(players, baseline)
        book, book_info = cp.historical_book(slug, data, prior)
        def profiles(cases):
            return {k: sha256(cp.profile_bytes(dest, players[v[0]])).hexdigest() for k, v in cases.items()}
        engines = {}
        ref = cp.reference_engine(stack, stockfish)
        def measure(stage, cases, panel):
            for name in sorted({v[0] for v in cases.values()} - set(engines)):
                engines[name] = stack.enter_context(_open_engine(
                    dest, dest / "rodent-iv-plain.exe", players[name], book=False))
            return chk.measure(stage, ref, engines, cases, panel, profiles(cases))

        prior_refined = False
        initial_cases = coarse
        if prior_fit:
            old = cp.load(prior_fit)
            if old.get("refinement"):
                stage = old["refinement"]
                extra = cp.intermediate_profiles(dest, players, baseline, coarse[stage["anchor"]][0])
                players.update(extra)
                initial_cases = cp.recipe_inventory(old, coarse, original, extra)
                prior_refined = True
            coarse_rows, coarse_stats = fit_from_artifact(
                prior_fit, slug, data, original, initial_cases, profiles(initial_cases), book, book_info)
        else:
            coarse_rows = measure("coarse-fit", coarse, original["fit"])
            coarse_stats = {k: cp.stats(coarse_rows, k) for k in coarse}
        finalists = recovery.shortlist(coarse_stats, initial_cases)
        fit_stats = {k: coarse_stats[k] for k in finalists}
        tune_rows = measure("pass1-tune", finalists, tune)
        tune_stats = {k: cp.stats(tune_rows, k) for k in finalists}
        selected = recovery.robust_select(fit_stats, tune_stats)
        passes = [{"cases": finalists, "fit": fit_stats, "tune": tune_stats,
                   "tune_rows": tune_rows, "selected": selected}]
        if not prior_refined and not in_band(fit_stats, tune_stats, selected):
            extra = cp.intermediate_profiles(dest, players, baseline, finalists[selected][0])
            players.update(extra)
            fine = {**finalists, **bounded_fine(coarse, selected, extra)}
            fine_rows = measure("pass2-fit", fine, original["fit"])
            screening = {k: cp.stats(fine_rows, k) for k in fine}
            finalists = recovery.shortlist(screening, fine)
            fit_stats = {k: screening[k] for k in finalists}
            tune_rows = measure("pass2-tune", finalists, tune)
            tune_stats = {k: cp.stats(tune_rows, k) for k in finalists}
            selected = recovery.robust_select(fit_stats, tune_stats)
            passes.append({"cases": finalists, "fit": fit_stats, "tune": tune_stats,
                           "fine_cases": fine, "fine_rows": fine_rows,
                           "tune_rows": tune_rows, "selected": selected})
        recipe = finalists[selected]
        dev_ok = in_band(fit_stats, tune_stats, selected)
        # Do not spend reserved CPU on development failures. Prior TEST evidence
        # stays archived; it must never be used for tuning.
        test_rows = []
        test = test_base = None
        if dev_ok:
            test_rows = measure("reserved", {"baseline": baseline, "candidate": recipe}, original["excluded_test"])
            test = cp.stats(test_rows, "candidate", bootstrap=True)
            test_base = cp.stats(test_rows, "baseline", bootstrap=True)
        profile = cp.profile_bytes(dest, players[recipe[0]])
        report = {"schema": SCHEMA, "slug": slug, "selected": selected, "recipe": recipe,
                  "baseline_recipe": baseline, "source_sha256": source_sha,
                  "engine_sha256": cp.RODENT_HASHES["rodent"], "stockfish_sha256": cp.STOCKFISH_SHA,
                  "coarse_cases": initial_cases, "coarse_rows": coarse_rows, "passes": passes,
                  "reused_refined_fit": prior_refined,
                  "prior_fit_sha256": cp.file_hash(prior_fit) if prior_fit else None,
                  "fit": fit_stats, "tune": tune_stats, "test": test, "test_baseline": test_base,
                  "test_rows": test_rows, "test_gate": cp.gate(test, strict=True) if test else "NOT_RUN_DEVELOPMENT_FAILED",
                  "profile_sha256": sha256(profile).hexdigest(),
                  "profile_base64": base64.b64encode(profile).decode(),
                  "alias": players[recipe[0]].rodent_alias, "book": book, "book_info": book_info,
                  "attempts": 2 if prior_refined else len(passes), "activation": False,
                  "status": "FRESH_VALIDATION_REQUIRED" if dev_ok else "DEVELOPMENT_TARGET_NOT_ESTABLISHED"}
        cp.save(out, report)
        print("CP_LOCAL", slug, "attempts", report["attempts"], "FIT", fit_stats[selected]["delta_cp"],
              "TUNE", tune_stats[selected]["delta_cp"], "TEST", test["delta_cp"] if test else None,
              report["status"], report["test_gate"], flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--player", required=True)
    p.add_argument("--rodent", type=Path, required=True)
    p.add_argument("--stockfish", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--checkpoint-dir", type=Path, required=True)
    p.add_argument("--stop-file", type=Path, required=True)
    p.add_argument("--prior-fit", type=Path)
    a = p.parse_args()
    a.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with (a.checkpoint_dir / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.out.exists():
            raise ValueError("Existing report; do not overwrite evidence")
        try:
            run(a.player, a.rodent, a.stockfish, a.out, a.checkpoint_dir, a.stop_file, a.prior_fit)
        except Paused:
            raise SystemExit(75) from None


if __name__ == "__main__":
    main()
