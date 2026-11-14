"""V5: supervised historical chess choice with REAL Rodent IV UCI MultiPV scores.

Candidate moves are emitted by the chess engine, not selected with knowledge of
the human's historic move. Only TRAIN-arm real PGN targets fit weights. Human
choices outside the independently produced top-k are *not* inserted into
candidate lists; report both top-k coverage and unconditional top-1 accuracy.
Validation is separated by whole years and canonical chess positions.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path
import random

import chess
import chess.engine

from .catalog import find_player
from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model,
    paired_game_inference, predict_contextual,
)
from .real_gate_v4 import _position_key
from .real_policy_experiment import (
    _bottom_k, _digest, _metric, _read_eval_positions, _read_json,
    _validated_training_rows, _write_once,
)

SCHEMA = "chesssim-real-uci-multipv-supervised-ranker-v5"
REPORT_SCHEMA = "chesssim-real-uci-multipv-paired-validation-v1"
STATUS = "REAL_PGN_MULTIPV_TRAINED_NOT_IDENTITY_OR_STRENGTH_VALIDATED"
FEATURES = (
    "uci_score", "uci_score_gap", "uci_rank", "pv_length",
    "pv_reply_capture", "pv_reply_check", "pv_line_captures",
    "pv_line_checks", "uci_forced_mate",
)
DEFAULT_WEIGHTS = (1.0, 0.25, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
ENGINE_SHA = "RODENT_IV_REAL_MULTIPV_PINNED_BINARY_AND_PROFILE"
MIN_COVERED = 12


def _clamp(n: float, low: float, high: float) -> float:
    return max(low, min(high, n))


def _record_candidate(board: chess.Board, info: dict, rank: int,
                      best_cp: int, total: int, expected_depth: int) -> dict:
    """Use only actual engine score/PV plus observable consequences of that PV."""
    pv = info.get("pv")
    score = info.get("score")
    if (not pv or score is None or not isinstance(pv, list) or
            not isinstance(pv[0], chess.Move) or
            int(info.get("depth", 0)) < expected_depth):
        raise ValueError("Rodent MultiPV returned incomplete principal variation")
    if pv[0] not in board.legal_moves:
        raise ValueError("Rodent MultiPV emitted an illegal root move")
    board_copy = board.copy(stack=False)
    captures = checks = 0
    response_capture = response_check = 0
    for index, move in enumerate(pv):
        if move not in board_copy.legal_moves:
            raise ValueError("Rodent MultiPV emitted an illegal continuation")
        capture = int(board_copy.is_capture(move))
        check = int(board_copy.gives_check(move))
        if index == 1:
            response_capture, response_check = capture, check
        captures += capture
        checks += check
        board_copy.push(move)
    centipawns = score.pov(board.turn).score(mate_score=30_000)
    if centipawns is None:
        raise ValueError("Rodent failed to provide a usable score")
    centipawns = int(centipawns)
    mate = score.pov(board.turn).mate()
    vector = (
        _clamp(centipawns / 300, -2.0, 2.0),
        _clamp((centipawns - best_cp) / 300, -3.0, 3.0),
        -(rank / max(1, total - 1)),
        min(len(pv), 8) / 8,
        float(response_capture),
        float(response_check),
        min(captures, 4) / 4,
        min(checks, 4) / 4,
        float(mate is not None and mate > 0),
    )
    return {
        "move": pv[0].uci(),
        "cp": centipawns,
        "depth": int(info["depth"]),
        "pv": [move.uci() for move in pv[:8]],
        "features": list(vector),
    }


def _analyse(engine: chess.engine.SimpleEngine, board: chess.Board, *,
             multipv: int, depth: int) -> list[dict]:
    """One genuine multi-line UCI search, with all candidates target-blind."""
    if not 2 <= multipv <= 8 or not 2 <= depth <= 8:
        raise ValueError("Unsafe MultiPV/depth configuration")
    if not board.is_valid() or board.is_game_over(claim_draw=True):
        raise ValueError("Cannot analyse an invalid/terminal historical board")
    count = min(multipv, board.legal_moves.count())
    information = engine.analyse(
        board, chess.engine.Limit(depth=depth),
        multipv=count,
        info=chess.engine.INFO_SCORE | chess.engine.INFO_PV |
             chess.engine.INFO_BASIC,
    )
    if not isinstance(information, list) or len(information) != count:
        raise ValueError("Actual Rodent did not provide the requested distinct MultiPV lines")
    for item in information:
        if item.get("score") is None or not item.get("pv"):
            raise ValueError("Actual Rodent returned incomplete UCI score/PV")
    # Keep the engine's own MultiPV ordering; do not sort by target agreement.
    best = information[0]["score"].pov(board.turn).score(mate_score=30_000)
    if best is None:
        raise ValueError("Missing Rodent top-line evaluation")
    seen = set()
    candidates = []
    for rank, entry in enumerate(information):
        record = _record_candidate(board, entry, rank, int(best), count, depth)
        if record["move"] in seen:
            raise ValueError("Repeated root move in real Rodent MultiPV")
        seen.add(record["move"])
        candidates.append(record)
    return candidates


def _pgn_training_rows(cohorts: Path, base: dict, *,
                       per_player: int, per_game: int, seed: int) -> dict:
    _assert_training_manifest(cohorts, base)
    manifest = _read_json(cohorts / "manifest.json")
    grouped = defaultdict(list)
    for row in _validated_training_rows(cohorts, manifest):
        grouped[row["player"]].append(row)
    if set(grouped) != set(base["profiles"]):
        raise ValueError("Incompatible real player roster")
    if type(per_player) is not int or not 40 <= per_player <= 500:
        raise ValueError("Train size per player must be 40..500")
    return {
        slug: _bottom_k(grouped[slug], limit=per_player,
                        per_game=per_game, seed=seed)
        for slug in sorted(grouped)
    }


def _real_uci_records(root: Path, samples: dict, *, multipv: int, depth: int,
                      training: bool) -> tuple[dict, dict]:
    """No simulated engines. Enumerate PVs for actual historical FENs."""
    from .matches import validate, _open_engine, file_hash
    root = root.expanduser().resolve(strict=True)
    records = {}
    details = {"engine_sha256": None, "profile_sha256": {},
               "ucis_per_player": {}, "principal_variations": 0,
               "engine_method": ENGINE_SHA, "multipv": multipv, "depth": depth}
    for slug in sorted(samples):
        player = find_player(slug)
        executable, profiles = validate(root, player, player)
        digest = file_hash(executable)
        if details["engine_sha256"] not in (None, digest):
            raise ValueError("Rodent binary changed during feature extraction")
        details["engine_sha256"] = digest
        details["profile_sha256"][slug] = file_hash(profiles[slug])
        engine = _open_engine(root, executable, player, book=False)
        try:
            if "MultiPV" not in engine.options:
                raise ValueError("Real Rodent IV UCI does not announce MultiPV")
            options = {}
            for name, desired in (("Threads", 1), ("Hash", 16),
                                  ("UCI_LimitStrength", False),
                                  ("UseBook", False)):
                if name in engine.options:
                    options[name] = desired
            if options:
                engine.configure(options)
            observations = []
            for row in samples[slug]:
                entry = (row["policy_input"] if training else row)
                expected = (row["policy_target"]["move_uci"] if training
                            else row["move"])
                board = chess.Board(entry["fen"])
                if chess.Move.from_uci(expected) not in board.legal_moves:
                    raise ValueError("Historical source move is illegal")
                alternatives = _analyse(engine, board, multipv=multipv, depth=depth)
                observations.append({
                    "slug": slug,
                    "game_sha256": row["game_sha256"],
                    "ply": row["ply"],
                    "fen": entry["fen"],
                    "year": entry["year"],
                    "real_human_move": expected,
                    "move": expected,
                    "candidates": alternatives,
                })
                details["principal_variations"] += len(alternatives)
            records[slug] = observations
            details["ucis_per_player"][slug] = len(observations)
        finally:
            try:
                engine.quit()
            except Exception:
                engine.close()
    return records, details


def _target_index(row: dict) -> int | None:
    return next((i for i, candidate in enumerate(row["candidates"])
                 if candidate["move"] == row["real_human_move"]), None)


def _predict(weights: list[float], row: dict) -> str:
    if len(weights) != len(FEATURES) or not row["candidates"]:
        raise ValueError("Invalid learned engine-ranker weights/candidates")
    return max(
        row["candidates"],
        key=lambda record: (
            sum(w * v for w, v in zip(weights, record["features"])),
            -row["candidates"].index(record)),
    )["move"]


def _fit(records: list[dict], initial: list[float], *,
         epochs: int, learning_rate: float, regularization: float,
         seed: int) -> tuple[list[float], list[float]]:
    if not records or len(initial) != len(FEATURES):
        raise ValueError("Empty fitting records or incompatible feature vector")
    weights = initial.copy()
    gradient_history = [1.0] * len(FEATURES)
    rng = random.Random(seed)
    losses = []
    for _ in range(epochs):
        examples = list(records)
        rng.shuffle(examples)
        loss = 0.0
        for row in examples:
            target = _target_index(row)
            if target is None:
                raise ValueError("Training example without observed target inside engine PVs")
            features = [candidate["features"] for candidate in row["candidates"]]
            scores = [sum(a * b for a, b in zip(weights, vector))
                      for vector in features]
            peak = max(scores)
            exponents = [math.exp(score - peak) for score in scores]
            denom = sum(exponents)
            probabilities = [x / denom for x in exponents]
            loss += -math.log(max(1e-12, probabilities[target]))
            expected = [sum(p * vec[k] for p, vec in zip(probabilities, features))
                        for k in range(len(FEATURES))]
            for k in range(len(FEATURES)):
                gradient = (features[target][k] - expected[k]
                            - regularization * (weights[k] - initial[k]))
                gradient_history[k] += gradient * gradient
                weights[k] += learning_rate * gradient / math.sqrt(gradient_history[k])
                weights[k] = _clamp(weights[k], -8.0, 8.0)
        losses.append(round(loss / len(examples), 8))
    return [round(w, 8) for w in weights], losses


def train_multipv(cohorts: Path, v3_path: Path, rodent_root: Path,
                  output: Path, *, per_player: int = 192, per_game: int = 2,
                  seed: int = 2031, multipv: int = 4, depth: int = 3) -> dict:
    v3 = _read_model(v3_path, V3_SCHEMA)
    if not 2 <= multipv <= 8 or not 2 <= depth <= 8:
        raise ValueError("Invalid real UCI feature extraction settings")
    sampled = _pgn_training_rows(
        cohorts, v3, per_player=per_player, per_game=per_game, seed=seed)
    observed, provenance = _real_uci_records(
        rodent_root, sampled, multipv=multipv, depth=depth, training=True)
    supported = {
        slug: [row for row in rows if _target_index(row) is not None]
        for slug, rows in observed.items()
    }
    if any(len(rows) < MIN_COVERED for rows in supported.values()):
        raise ValueError("Real multiPV historical coverage too sparse for an individual model")
    pooled = [row for slug in sorted(supported) for row in supported[slug]]
    generic, pooled_loss = _fit(
        pooled, list(DEFAULT_WEIGHTS), epochs=12,
        learning_rate=0.13, regularization=0.075, seed=seed)
    personal = {}
    for index, slug in enumerate(sorted(supported)):
        weights, losses = _fit(
            supported[slug], generic, epochs=12, learning_rate=0.09,
            regularization=0.20, seed=seed + index + 1)
        real_positions = observed[slug]
        personal[slug] = {
            "weights": weights, "training_loss": losses,
            "total_real_decisions": len(real_positions),
            "historical_move_in_multipv": len(supported[slug]),
            "historical_move_coverage": len(supported[slug]) / len(real_positions),
            "distinct_real_games": len({row["game_sha256"] for row in real_positions}),
            "candidate_count": sum(len(row["candidates"]) for row in real_positions),
        }
    result = {
        "schema": SCHEMA, "status": STATUS,
        "algorithm": "HIERARCHICAL_ADAGRAD_ENGINE_MULTIPV_CONDITIONAL_LOGIT",
        "engine_analysis": provenance,
        "feature_names": list(FEATURES),
        "feature_sources": "REAL_UCI_SCORES_DEPTH_AND_PRINCIPAL_VARIATIONS",
        "candidate_policy": "TARGET_BLIND_TOP_K_FROM_NAMED_RODENT_MULTIPV",
        "target_missing_handling": "EXCLUDE_FROM_CONDITIONAL_LOSS_AND_REPORT_COVERAGE",
        "v3_model_sha256": v3["model_sha256"],
        "train_manifest_sha256": v3["train_manifest_sha256"],
        "training_files_sha256": v3["training_files_sha256"],
        "source_pgn_sha256": v3["source_pgn_sha256"],
        "players": personal,
        "generic_weights": generic,
        "generic_training_loss": pooled_loss,
        "training_seed": seed, "per_player": per_player, "per_game": per_game,
        "total_real_train_positions": sum(len(v) for v in observed.values()),
        "covered_real_train_positions": sum(len(v) for v in supported.values()),
        "final_holdout_opened": False,
        "official_personality_promotion": "DENIED",
        "limitations": [
            "Human historical targets are never injected into MultiPV candidate lists.",
            "Conditional ranking learns only decisions inside top-k; coverage reported separately.",
            "This is engine-analysis-based choice ranking, not Rodent internal weight training.",
            "PV assessments at shallow fixed depth do not certify move safety, strength or identity.",
            "All retrospective validation p-values are exploratory and unadjusted for multiplicity.",
        ],
    }
    result["model_sha256"] = _digest(result)
    _write_once(output, result)
    return result


def _read_trained(path: Path, base: dict) -> dict:
    model = _read_json(path, 8_000_000)
    signature = model.pop("model_sha256", None)
    if (model.get("schema") != SCHEMA or model.get("status") != STATUS or
            model.get("v3_model_sha256") != base["model_sha256"] or
            model.get("train_manifest_sha256") != base["train_manifest_sha256"] or
            model.get("source_pgn_sha256") != base["source_pgn_sha256"] or
            model.get("training_files_sha256") != base["training_files_sha256"] or
            model.get("candidate_policy") != "TARGET_BLIND_TOP_K_FROM_NAMED_RODENT_MULTIPV" or
            model.get("feature_names") != list(FEATURES) or
            not isinstance(signature, str) or signature != _digest(model)):
        raise ValueError("Tampered or incompatible real Rodent MultiPV model")
    model["model_sha256"] = signature
    if set(model["players"]) != set(base["profiles"]):
        raise ValueError("Engine-policy player roster mismatch")
    for weights in [model["generic_weights"],
                    *(profile["weights"] for profile in model["players"].values())]:
        if (not isinstance(weights, list) or len(weights) != len(FEATURES) or
                any(type(x) not in (float, int) or not math.isfinite(x) or
                    abs(x) > 8.0 for x in weights)):
            raise ValueError("Invalid engine-dependent learned coefficients")
    return model


def evaluate_multipv(cohorts: Path, v3_path: Path, learned_path: Path,
                     rodent_root: Path, output: Path, *,
                     archive_dir: Path | None = None,
                     per_player: int = 64, per_game: int = 2,
                     seed: int = 2032, repetitions: int = 1200) -> dict:
    v3 = _read_model(v3_path, V3_SCHEMA)
    learned = _read_trained(learned_path, v3)
    _assert_training_manifest(cohorts, v3)
    # Over-fetch held-year positions, then reject every position (including
    # differently numbered FEN clocks) appearing ANYWHERE in training.
    candidates, excluded, original_hashes = _read_eval_positions(
        cohorts, v3, arm="validation", archive_dir=archive_dir,
        per_player=per_player * 5, per_game=per_game, seed=seed)
    manifest = _read_json(cohorts / "manifest.json")
    forbidden = {
        _position_key(row["policy_input"]["fen"])
        for row in _validated_training_rows(cohorts, manifest)
    }
    samples = {}
    skipped = {}
    for slug, rows in sorted(candidates.items()):
        independent = [r for r in rows if _position_key(r["fen"]) not in forbidden]
        skipped[slug] = len(rows) - len(independent)
        chosen = _bottom_k(independent, limit=per_player,
                           per_game=per_game, seed=seed)
        if len(chosen) != per_player or len({r["game_sha256"] for r in chosen}) < 10:
            raise ValueError("Insufficient independent held-year historical games for " + slug)
        samples[slug] = chosen
    extraction = learned["engine_analysis"]
    observed, provenance = _real_uci_records(
        rodent_root, samples, multipv=extraction["multipv"],
        depth=extraction["depth"], training=False)
    if (provenance["engine_sha256"] != extraction["engine_sha256"] or
            provenance["profile_sha256"] != extraction["profile_sha256"]):
        raise ValueError("Rodent binary or personalities changed between train and validation")
    report_players = {}
    joined = defaultdict(list)
    count = Counter()
    all_rows = []
    for index, slug in enumerate(sorted(observed)):
        rows = observed[slug]
        generic = [_predict(learned["generic_weights"], row) for row in rows]
        named = [_predict(learned["players"][slug]["weights"], row) for row in rows]
        engine = [row["candidates"][0]["move"] for row in rows]
        legacy = [predict_contextual(v3, chess.Board(row["fen"]),
                                     slug, "overall") for row in rows]
        legacy_generic = [predict_contextual(v3, chess.Board(row["fen"]),
                                             slug, "overall", generic=True)
                          for row in rows]
        rotation = sorted(observed)[(index + 1) % len(observed)]
        swapped = [_predict(learned["players"][rotation]["weights"], row)
                   for row in rows]
        comparisons = {
            "multipv_engine_top1": engine,
            "trained_named": named,
            "trained_generic": generic,
            "trained_fixed_swapped": swapped,
            "v3_named": legacy,
            "v3_generic": legacy_generic,
        }
        metrics = {k: _metric(rows, v) for k, v in comparisons.items()}
        coverage = sum(_target_index(row) is not None for row in rows)
        statistics = {
            name + "_vs_engine": paired_game_inference(
                rows, choices, engine, seed=seed + index * 10 + order,
                repetitions=repetitions)
            for order, (name, choices) in enumerate([
                ("trained_named", named), ("trained_generic", generic),
                ("v3_named", legacy),
            ])
        }
        statistics["named_vs_generic"] = paired_game_inference(
            rows, named, generic, seed=seed + 100 + index,
            repetitions=repetitions)
        statistics["named_vs_fixed_swapped"] = paired_game_inference(
            rows, named, swapped, seed=seed + 200 + index,
            repetitions=repetitions)
        report_players[slug] = {
            "evaluation_positions": len(rows),
            "distinct_historical_games": len({row["game_sha256"] for row in rows}),
            "real_human_move_in_multipv": coverage,
            "real_human_move_coverage": coverage / len(rows),
            "negative_control_rotated_player": rotation,
            "metrics": metrics,
            "paired_by_real_game": statistics,
            "identity_validated": False,
        }
        for key, preds in comparisons.items():
            joined[key].extend(preds)
        all_rows.extend(rows)
        count["covered"] += coverage
    aggregate = {key: _metric(all_rows, predictions)
                 for key, predictions in joined.items()}
    report = {
        "schema": REPORT_SCHEMA,
        "status": "REAL_UCI_MULTIPV_HELD_YEAR_PAIRED_EXPLORATORY",
        "model_sha256": learned["model_sha256"],
        "v3_model_sha256": v3["model_sha256"],
        "source_pgn_sha256": original_hashes,
        "engine_binary_sha256": provenance["engine_sha256"],
        "engine_profile_sha256": provenance["profile_sha256"],
        "actual_rodent_uci": True,
        "analysis_method": ENGINE_SHA,
        "multipv": extraction["multipv"],
        "depth": extraction["depth"],
        "sample_seed": seed,
        "held_year_arm": "validation",
        "final_holdout_opened": False,
        "total_real_train_positions": learned["total_real_train_positions"],
        "total_real_validation_positions": len(all_rows),
        "historical_move_in_multipv": count["covered"],
        "historical_move_coverage": count["covered"] / len(all_rows),
        "all_train_canonical_positions_embargoed": len(forbidden),
        "validation_overlap_skipped": skipped,
        "sample_exclusions": dict(excluded),
        "by_player": report_players,
        "aggregate": aggregate,
        "candidate_policy": learned["candidate_policy"],
        "promotion": "DENIED_UNTIL_INDEPENDENT_STYLE_AND_CHESS_SAFETY_TEST",
        "limitations": [
            "All model decisions are restricted to actual target-blind engine MultiPV lines.",
            "Top-k recall constrains unconditional exact match; targets outside k are counted wrong.",
            "Candidate top-1 from MultiPV mode is not equivalent to normal single-PV engine search.",
            "Held-year data has been inspected in prior research; tests are exploratory.",
            "Game-cluster CI does not by itself correct repeated model development/multiple tests.",
            "No claim of improved Elo or faithful historical player identity.",
        ],
    }
    _write_once(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Actual Rodent IV UCI MultiPV conditional ranking learned from real PGNs")
    commands = parser.add_subparsers(dest="action", required=True)
    train = commands.add_parser("train")
    train.add_argument("--cohorts", required=True, type=Path)
    train.add_argument("--v3-model", required=True, type=Path)
    train.add_argument("--rodent-root", required=True, type=Path)
    train.add_argument("--out", required=True, type=Path)
    train.add_argument("--per-player", type=int, default=192)
    train.add_argument("--per-game", type=int, default=2)
    train.add_argument("--seed", type=int, default=2031)
    train.add_argument("--multipv", type=int, default=4)
    train.add_argument("--depth", type=int, default=3)
    ev = commands.add_parser("evaluate")
    ev.add_argument("--cohorts", required=True, type=Path)
    ev.add_argument("--v3-model", required=True, type=Path)
    ev.add_argument("--model", required=True, type=Path)
    ev.add_argument("--rodent-root", required=True, type=Path)
    ev.add_argument("--out", required=True, type=Path)
    source = ev.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    ev.add_argument("--per-player", type=int, default=64)
    ev.add_argument("--per-game", type=int, default=2)
    ev.add_argument("--seed", type=int, default=2032)
    ev.add_argument("--repetitions", type=int, default=1200)
    opts = parser.parse_args(argv)
    if opts.action == "train":
        result = train_multipv(
            opts.cohorts, opts.v3_model, opts.rodent_root, opts.out,
            per_player=opts.per_player, per_game=opts.per_game,
            seed=opts.seed, multipv=opts.multipv, depth=opts.depth)
        msg = {"status": result["status"],
               "total_historical_decisions": result["total_real_train_positions"],
               "covered_historical_decisions": result["covered_real_train_positions"],
               "model_sha256": result["model_sha256"]}
    else:
        result = evaluate_multipv(
            opts.cohorts, opts.v3_model, opts.model, opts.rodent_root,
            opts.out, archive_dir=opts.archive_dir,
            per_player=opts.per_player, per_game=opts.per_game,
            seed=opts.seed, repetitions=opts.repetitions)
        msg = {"status": result["status"],
               "validation_positions": result["total_real_validation_positions"],
               "actual_historic_coverage": result["historical_move_coverage"],
               "top1_by_approach": {k: v["top1"] for k, v in result["aggregate"].items()},
               "promotion": result["promotion"]}
    print(json.dumps(msg, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
