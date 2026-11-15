"""Train-year-only experiment: four historical identities on frozen DSC-150M.

The fit model was computed from ALL eligible FIT examples of original PGNs.
TUNE/AUDIT contain different real games and are NEVER used for fitting.
No original historical final HOLDOUT or previously inspected VALIDATION is read.

The UCI engine emits a ranked top-20 policy; -log(rank+1) is explicitly an
ordinal proxy, never a claim about the true DSC softmax probabilities.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
import sys

import chess
import chess.engine

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model,
    paired_game_inference,
)
from .maia_personalization_v7 import (
    _calibration_samples, _features, _load_adapter,
)
from .maia_transfer_v6 import _restore_real_histories
from .real_gate_v4 import _position_key
from .real_policy_experiment import _digest, _metric, _write_once
from .science_multimodel_cpu import (
    DSC_FIDE_SCENARIO, DSC_SOURCE_COMMIT, DSC150_WEIGHTS_SHA256,
    _candidates, _dsc_preflight,
)

SCHEMA = "chesssim-dsc-four-historical-identities-train-audit-v1"
PLAYERS = ("fischer", "karpov", "kasparov", "tal")
ALPHAS = (0.0, 0.05, 0.1, 0.2, 0.4)
FIXED_IDENTITY_ALPHA = 0.2
TUNE_PER_PLAYER = 48
AUDIT_PER_PLAYER = 48
SEED = 2042


def _centered_weights(profiles: dict) -> dict[str, dict[str, float]]:
    """Subtract a symmetric equal-profile reference; no named-player leakage."""
    if set(profiles) != set(PLAYERS):
        raise ValueError("Historical four-player roster must be fixed")
    keys = sorted(set().union(*(set(p["weights"]) for p in profiles.values())))
    pooled = {
        key: sum(profiles[s]["weights"].get(key, 0.0) for s in PLAYERS) / 4
        for key in keys
    }
    return {
        slug: {key: round(profiles[slug]["weights"].get(key, 0.0) - pooled[key], 9)
               for key in keys}
        for slug in PLAYERS
    }


def _policy(board: chess.Board, moves: list[str], weights: dict,
            strength: float) -> tuple[str, dict[str, float]]:
    if strength not in ALPHAS and strength != FIXED_IDENTITY_ALPHA:
        raise ValueError("Unregistered style scale")
    if len(set(moves)) != len(moves) or not moves:
        raise ValueError("Duplicate or empty DSC MultiPV response")
    parsed = [chess.Move.from_uci(move) for move in moves]
    if any(move not in board.legal_moves for move in parsed):
        raise ValueError("Illegal style-candidate")
    logits = [
        -math.log(i + 1) + strength * sum(
            weights.get(key, 0.0)
            for key in _features(board, move, assumed_legal=True))
        for i, move in enumerate(parsed)
    ]
    greatest = max(logits)
    denom = sum(math.exp(v-greatest) for v in logits)
    probs = [math.exp(x-greatest)/denom for x in logits]
    best = min(range(len(moves)), key=lambda i: (-logits[i], i))
    return moves[best], dict(zip(moves, probs))


def _select_alpha(rows: list[dict], candidates: list[list[str]], weights: dict) -> float:
    """Selection on TUNE labels only; deterministic conservative tie break."""
    if len(rows) != len(candidates) or not rows:
        raise ValueError("Insufficient genuine tuning observations")
    base = [moves[0] for moves in candidates]
    def objective(alpha: float) -> float:
        chosen = [
            _policy(chess.Board(row["fen"]), options, weights, alpha)[0]
            for row, options in zip(rows, candidates)
        ]
        per_game = defaultdict(list)
        for row,pred,b in zip(rows, chosen, base):
            per_game[row["game_sha256"]].append(
                int(pred == row["move"]) - int(b == row["move"]))
        if len(per_game) < 12:
            raise ValueError("Too few independent TUNE games")
        avg = sum(sum(v)/len(v) for v in per_game.values()) / len(per_game)
        overrides = sum(a != b for a,b in zip(chosen, base))/len(rows)
        return avg - 0.008 * overrides
    return max(ALPHAS, key=lambda alpha: (objective(alpha), -alpha))


def _independent_split(tune: dict, audit: dict) -> tuple[dict, dict]:
    """Canonical FEN, game trajectory disjointness across ALL FOUR players."""
    fit = {
        (row["game_sha256"], row["ply"])
        for slug in PLAYERS for row in tune[slug]
    }
    if len(fit) != sum(len(tune[s]) for s in PLAYERS):
        raise ValueError("Duplicated training-arm tuning observation across players")
    tune_games = {r["game_sha256"] for s in PLAYERS for r in tune[s]}
    tune_fens = {_position_key(r["fen"]) for s in PLAYERS for r in tune[s]}
    audit_new = {}
    for slug in PLAYERS:
        audit_new[slug] = [
            r for r in audit[slug]
            if r["game_sha256"] not in tune_games
            and _position_key(r["fen"]) not in tune_fens
        ]
    return tune, audit_new


def _blind_accuracy(rows: list[dict], candidate: list[list[str]],
                    weights: dict[str, dict], alpha: float, seed: int) -> dict:
    """Use human move *only as evaluation label*, never to condition inference."""
    covered = []
    classification = []
    confusion = {slug: {pred: 0 for pred in PLAYERS} for slug in PLAYERS}
    for row, suggestions in zip(rows, candidate):
        if row["move"] not in suggestions:
            continue
        board = chess.Board(row["fen"])
        q = {}
        for slug in PLAYERS:
            _, probabilities = _policy(board, suggestions, weights[slug], alpha)
            q[slug] = probabilities[row["move"]]
        classified = min(PLAYERS, key=lambda s: (-q[s], s))
        covered.append(row)
        classification.append(int(classified == row["player"]))
        confusion[row["player"]][classified] += 1
    if len({r["game_sha256"] for r in covered}) < 20:
        return {"status": "UNDERPOWERED_CANDIDATE_COVERAGE",
                "covered": len(covered), "positions": len(rows)}
    games = defaultdict(list)
    for row, correct in zip(covered, classification):
        games[row["game_sha256"]].append(correct)
    x = [sum(g)/len(g) for _,g in sorted(games.items())]
    avg = sum(x)/len(x)
    counts = {slug: sum(confusion[slug].values()) for slug in PLAYERS}
    if min(counts.values()) < 10:
        return {"status": "UNDERPOWERED_UNBALANCED_PLAYER_COVERAGE",
                "covered": len(covered), "positions": len(rows),
                "coverage_per_player": counts}
    balanced = sum(confusion[slug][slug] / counts[slug] for slug in PLAYERS) / 4
    majority = max(counts.values()) / len(covered)
    rng = random.Random(seed)
    draws = sorted(sum(x[rng.randrange(len(x))] for _ in x)/len(x)
                   for _ in range(2000))
    return {
        "status": "BLIND_LABEL_CLASSIFIER_DIAGNOSTIC_ONLY",
        "covered": len(covered), "positions": len(rows),
        "coverage": len(covered)/len(rows),
        "distinct_games": len(x),
        "correct": sum(classification),
        "conditional_accuracy_by_game": avg,
        "chance": 0.25,
        "majority_class_baseline": majority,
        "balanced_accuracy": balanced,
        "coverage_per_player": counts,
        "confusion_matrix": confusion,
        "bootstrap_95_ci_by_game": [draws[50], draws[1949]],
        "caveat": "Classifier uses evaluated human move; labels never used for fitted weights.",
    }


def _open_dsc() -> chess.engine.SimpleEngine:
    _dsc_preflight()
    cmd = [
        sys.executable, "-m", "chessds_dsc.uci", "--model", "dsc-150m",
        "--device", "cpu", "--use-uci-history",
        "--temperature", "0", "--top-p", "1", "--multipv", "20",
        "--time-control", "classical", "--rating-system", "fide",
        "--elo", str(DSC_FIDE_SCENARIO), "--output-mode", "likelihood",
    ]
    engine = chess.engine.SimpleEngine.popen_uci(cmd, timeout=360)
    if not {"MultiPV", "Elo", "TimeControl", "RatingSystem"} <= set(engine.options):
        engine.quit()
        raise ValueError("Original DSC UCI controls absent")
    engine.configure({"Elo": DSC_FIDE_SCENARIO,
                      "TimeControl": "classical", "RatingSystem": "fide"})
    return engine


def run(cohorts: Path, v3_path: Path, adapter_path: Path,
        out: Path, *, archive_dir: Path | None = None) -> dict:
    v3 = _read_model(v3_path, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    if set(v3["profiles"]) != set(PLAYERS):
        raise ValueError("Four original historical players only")
    adapter = _load_adapter(adapter_path, v3)
    if adapter.get("sum_fitted_decisions", 0) < 100_000:
        raise ValueError("Not the complete FIT historical learning corpus")
    weights = _centered_weights(adapter["profiles"])
    tune, audit = _calibration_samples(
        cohorts, v3, adapter, TUNE_PER_PLAYER, SEED)
    tune, audit = _independent_split(tune, audit)
    if any(len(tune[s]) != TUNE_PER_PLAYER or len(audit[s]) < 30
           for s in PLAYERS):
        raise ValueError("Not enough per-player disjoint historical training-arm games")
    both = {s: tune[s] + audit[s] for s in PLAYERS}
    histories, source_hashes = _restore_real_histories(cohorts, v3, both, archive_dir)
    if source_hashes != v3["source_pgn_sha256"]:
        raise ValueError("Actual PGN snapshot changed")
    candidates = {}
    engine = _open_dsc()
    try:
        for slug in PLAYERS:
            candidates[slug] = [
                _candidates(engine, histories[slug][(r["game_sha256"], r["ply"])])
                for r in both[slug]
            ]
    finally:
        engine.quit()
    per_player = {}
    aggregate_rows, aggregate_candidates = [], []
    aggregate_own = []
    fixed_rows, fixed_candidates = [], []
    for index,slug in enumerate(PLAYERS):
        t = len(tune[slug])
        chosen = _select_alpha(tune[slug], candidates[slug][:t], weights[slug])
        rows = audit[slug]
        suggestions = candidates[slug][t:]
        swapped = PLAYERS[(index + 1) % len(PLAYERS)]
        own = [
            _policy(chess.Board(r["fen"]), c, weights[slug], chosen)[0]
            for r,c in zip(rows,suggestions)]
        wrong = [
            _policy(chess.Board(r["fen"]), c, weights[swapped], chosen)[0]
            for r,c in zip(rows,suggestions)]
        original = [c[0] for c in suggestions]
        fixed_own = [
            _policy(chess.Board(r["fen"]), c, weights[slug],
                    FIXED_IDENTITY_ALPHA)[0] for r,c in zip(rows,suggestions)]
        fixed_wrong = [
            _policy(chess.Board(r["fen"]), c, weights[swapped],
                    FIXED_IDENTITY_ALPHA)[0] for r,c in zip(rows,suggestions)]
        proof = paired_game_inference(
            rows, own, original, seed=SEED+index, repetitions=2000)
        identity = paired_game_inference(
            rows, fixed_own, fixed_wrong, seed=SEED+index+40,
            repetitions=2000)
        per_player[slug] = {
            "tune_games": len({r["game_sha256"] for r in tune[slug]}),
            "audit_games": len({r["game_sha256"] for r in rows}),
            "audit_decisions": len(rows),
            "alpha_selected_on_tune_only": chosen,
            "correct": _metric(rows, own),
            "generic_original_dsc": _metric(rows, original),
            "swapped": _metric(rows, wrong),
            "swapped_identity": swapped,
            "paired_personal_vs_original": proof,
            "fixed_alpha_correct_vs_swapped": identity,
            "accepted": (
                chosen > 0 and proof["bootstrap_95_ci"][0] > 0
                and identity["bootstrap_95_ci"][0] > 0
                and len({r["game_sha256"] for r in rows}) >= 30
            ),
        }
        aggregate_rows.extend(rows)
        aggregate_candidates.extend(suggestions)
        aggregate_own.extend(own)
        fixed_rows.extend(rows)
        fixed_candidates.extend(suggestions)
    blind = _blind_accuracy(fixed_rows, fixed_candidates,
                            weights, FIXED_IDENTITY_ALPHA, SEED+90)
    audit_result = paired_game_inference(
        aggregate_rows, aggregate_own, [c[0] for c in aggregate_candidates],
        seed=SEED+91, repetitions=3000)
    distinct_policies = sum(
        len({
            _policy(chess.Board(r["fen"]), c, weights[s], FIXED_IDENTITY_ALPHA)[0]
            for s in PLAYERS
        }) > 1 for r,c in zip(fixed_rows,fixed_candidates)
    )
    result = {
        "schema": SCHEMA,
        "status": "TRAIN_YEAR_TUNE_AUDIT_EXPLORATORY_NOT_FINAL_HOLDOUT",
        "players": per_player,
        "blind_identity": blind,
        "cross_policy_top1_disagreement_positions": distinct_policies,
        "independent_audit_vs_dsc": audit_result,
        "sum_fitted_decisions": adapter["sum_fitted_decisions"],
        "fit_model_sha256": adapter["model_sha256"],
        "v3_binding_sha256": v3["model_sha256"],
        "source_pgn_sha256": source_hashes,
        "DSC_model_code_commit": DSC_SOURCE_COMMIT,
        "DSC150_checkpoint_sha256": DSC150_WEIGHTS_SHA256,
        "DSC_rating_context": {
            "elo": DSC_FIDE_SCENARIO, "rating_system": "fide",
            "time_control": "classical", "elo_is_scenario": True,
        },
        "seed": SEED, "tune_limit_per_player": TUNE_PER_PLAYER,
        "audit_limit_per_player": AUDIT_PER_PLAYER,
        "fixed_identity_alpha": FIXED_IDENTITY_ALPHA,
        "alphas_predeclared": list(ALPHAS),
        "holdout_opened": False,
        "validation_opened": False,
        "official_promotion": "DENIED",
        "candidate_promotion": "NOT_AUTHORIZED_UNTIL_INDEPENDENT_FINAL_TEST",
        "limitations": [
            "Train-year audit games are disjoint from FIT; this is NOT a held-year final test.",
            "Original DSC pretraining may have seen historical PGNs: external contamination is not resolved.",
            "DSC rank logarithm is an ordinal proxy, NOT calibrated DSC move likelihood.",
            "Conditional moves in top-20 only for blind identity likelihood; coverage reported.",
            "The V3 binding is used to verify corpus; V7 FIT weights alone define personal residuals.",
            "Most historical FIDE Elo and time controls are absent; input rating is a fixed scenario.",
        ],
    }
    result["report_sha256"] = _digest(result)
    _write_once(out, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Four-player DSC train-year independent audit")
    parser.add_argument("--cohorts", type=Path, required=True)
    parser.add_argument("--v3-model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.cohorts, args.v3_model, args.adapter, args.out,
                 archive_dir=args.archive_dir)
    print(json.dumps({
        "status": report["status"], "blind_identity": report["blind_identity"],
        "gates": {k:v["accepted"] for k,v in report["players"].items()},
        "audit_vs_dsc": report["independent_audit_vs_dsc"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
