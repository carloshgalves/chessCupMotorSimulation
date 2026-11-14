"""Contextual historical choice modelling and paired real-game statistical audit.

V3 is a new artifact schema: it never silently changes existing V2 models.
All supervised targets come from PR #136 train-only REAL PGN decision records;
no game outcomes or post-game metadata enter training. The evaluation replays
the exact pinned source PGNs, uses only validation years and keeps holdout
unopened. Results are exploratory; repeated validation is model development.
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

from .real_policy_experiment import (
    _bottom_k, _digest, _features, _read_eval_positions,
    _read_json, _rodent_predictions, _train_logit, _validated_training_rows,
    _write_once, predict as predict_v2,
)

SCHEMA = "chesssim-real-contextual-conditional-choice-v3"
STATUS = "TRAINED_ON_REAL_HUMAN_PGNS_NOT_STYLE_VALIDATED"
REPORT_SCHEMA = "chesssim-real-contextual-paired-validation-v1"
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
         chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def _bin(value: int, boundaries: tuple[int, ...]) -> str:
    for i, upper in enumerate(boundaries):
        if value <= upper:
            return str(i)
    return str(len(boundaries))


def _board_context(board: chess.Board) -> dict:
    if not board.is_valid():
        raise ValueError("Illegal pre-decision board")
    actor = board.turn
    own = sum(VALUE[p.piece_type] for p in board.piece_map().values()
              if p.color == actor)
    enemy = sum(VALUE[p.piece_type] for p in board.piece_map().values()
                if p.color != actor)
    phase = ("end" if own + enemy <= 20 else
             "middle" if own + enemy < 55 else "high_material")
    king = board.king(actor)
    if king is None:
        raise ValueError("Board lacks moving-side king")
    king_ring = [king, *list(chess.SquareSet(chess.BB_KING_ATTACKS[king]))]
    threats = sum(board.is_attacked_by(not actor, sq) for sq in king_ring)
    own_pawns = list(board.pieces(chess.PAWN, actor))
    center_pawns = sum(
        chess.square_file(sq) in (2, 3, 4, 5) for sq in own_pawns)
    return {
        "phase": phase,
        "material": _bin(own - enemy, (-5, -2, 2, 5)),
        "mobility": _bin(board.legal_moves.count(), (10, 20, 30, 40)),
        "king_ring": _bin(threats, (0, 1, 3)),
        "king_file": str(chess.square_file(king)),
        "own_center_pawns": _bin(center_pawns, (0, 1, 2, 3)),
        "move_side": "white" if actor else "black",
    }


def contextual_features(board: chess.Board, move: chess.Move,
                        *, context: dict | None = None,
                        assumed_legal: bool = False) -> tuple[str, ...]:
    """Position-move interactions available strictly at decision time.

    The candidate post-move position is legal to compute: it is a hypothetical
    consequence of each possible action, not future moves or game outcomes.
    """
    if not assumed_legal and move not in board.legal_moves:
        raise ValueError("Illegal candidate move")
    ctx = context or _board_context(board)
    original = _features(board, move, assumed_legal=True)
    moving = board.piece_at(move.from_square)
    if moving is None:
        raise ValueError("Missing mover")
    side = board.turn
    target = chess.square_name(move.to_square)
    piece = moving.symbol().upper()
    is_capture = board.is_capture(move)
    is_check = board.gives_check(move)
    threatened_before = board.is_attacked_by(not side, move.from_square)
    pinned = board.is_pinned(side, move.from_square)
    board.push(move)
    try:
        target_attacked = board.is_attacked_by(not side, move.to_square)
        target_defended = board.is_attacked_by(side, move.to_square)
        opp_mobility = _bin(board.legal_moves.count(), (10, 20, 30, 40))
        # Relative rank is independent of player color.
        relative_rank = (chess.square_rank(move.to_square)
                         if side == chess.WHITE else
                         7 - chess.square_rank(move.to_square))
    finally:
        board.pop()
    phase = ctx["phase"]
    tokens = [
        "v3:phase:" + phase,
        "v3:material:" + ctx["material"],
        "v3:mobility:" + ctx["mobility"],
        "v3:king_ring:" + ctx["king_ring"],
        "v3:center_pawns:" + ctx["own_center_pawns"],
        "v3:phase_piece:" + phase + ":" + piece,
        "v3:phase_target:" + phase + ":" + piece + ":" + target,
        "v3:phase_file:" + phase + ":" + target[0],
        "v3:phase_capture:" + phase + ":" + str(is_capture),
        "v3:phase_check:" + phase + ":" + str(is_check),
        "v3:material_piece:" + ctx["material"] + ":" + piece,
        "v3:material_capture:" + ctx["material"] + ":" + str(is_capture),
        "v3:king_pressure_piece:" + ctx["king_ring"] + ":" + piece,
        "v3:king_pressure_check:" + ctx["king_ring"] + ":" + str(is_check),
        "v3:piece_forward_rank:" + piece + ":" + str(relative_rank),
        "v3:piece_attacked_destination:" + piece + ":" + str(target_attacked),
        "v3:piece_defended_destination:" + piece + ":" + str(target_defended),
        "v3:piece_exposed:" + piece + ":" + str(target_attacked and not target_defended),
        "v3:save_threatened:" + piece + ":" + str(threatened_before and not target_attacked),
        "v3:from_pin:" + str(pinned),
        "v3:capture_risk:" + str(is_capture) + ":" + str(target_attacked),
        "v3:opponent_reply_mobility:" + opp_mobility,
    ]
    return original + tuple(tokens)


def _encoded(row: dict):
    board = chess.Board(row["policy_input"]["fen"])
    ctx = _board_context(board)
    legal = sorted(board.legal_moves, key=lambda move: move.uci())
    try:
        target = chess.Move.from_uci(row["policy_target"]["move_uci"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid real training UCI target") from exc
    if target not in legal:
        raise ValueError("Historic move is not legal in the supplied FEN")
    features = [contextual_features(board, move, context=ctx, assumed_legal=True) for move in legal]
    return features, legal.index(target)


def _assert_training_manifest(cohorts: Path, model: dict) -> None:
    manifest = _read_json(cohorts / "manifest.json")
    if sha256((cohorts / "manifest.json").read_bytes()).hexdigest() != model["train_manifest_sha256"]:
        raise ValueError("Training partition manifest changed after fitting")
    hashes = model.get("training_files_sha256")
    files = manifest.get("training_files", [])
    if not isinstance(hashes, dict) or set(files) != set(hashes):
        raise ValueError("Missing training JSONL SHA-256 commitments")
    import re
    for rel, digest in hashes.items():
        if (not isinstance(rel, str) or
                re.fullmatch(r"train/[a-z0-9-]+/[0-9]{4}\.jsonl", rel) is None or
                not isinstance(digest, str) or
                re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError("Unsafe training commitment")
        path = cohorts / rel
        if (path.is_symlink() or any(p.is_symlink() for p in path.parents)
                or not path.is_file()):
            raise ValueError("Missing or symbolic training data")
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Training data mutated after fitting")


def fit_contextual(cohorts: Path, out: Path, *, max_per_player: int = 1600,
                   max_per_game: int = 3, seed: int = 2026) -> dict:
    """Hierarchical conditional logistic regression with real legal alternatives."""
    manifest = _read_json(cohorts / "manifest.json")
    grouped = defaultdict(list)
    for row in _validated_training_rows(cohorts, manifest):
        grouped[row["player"]].append(row)
    if not grouped or set(grouped) != set(manifest["players"]):
        raise ValueError("Missing real player-year train-only observations")
    chosen = {}
    for slug in sorted(grouped):
        subset = _bottom_k(grouped[slug], limit=max_per_player,
                           per_game=max_per_game, seed=seed)
        if len(subset) < 20:
            raise ValueError("Insufficient actual historical decisions for " + slug)
        chosen[slug] = [{
            "game": row["game_sha256"],
            "year": row["policy_input"]["year"],
            "band": row["policy_input"]["career_band"],
            "choice": _encoded(row),
        } for row in subset]
    pooled = [row["choice"] for slug in sorted(chosen) for row in chosen[slug]]
    generic, generic_loss = _train_logit(pooled, epochs=3, rate=0.20,
                                        shrinkage=0.035)
    profiles = {}
    for slug in sorted(chosen):
        own = chosen[slug]
        personal, loss = _train_logit([r["choice"] for r in own], prior=generic,
                                      epochs=3, rate=0.15, shrinkage=0.07)
        career = {}
        career_loss = {}
        for band in sorted({r["band"] for r in own}):
            selections = [r["choice"] for r in own if r["band"] == band]
            if len(selections) >= 50:
                weights, curve = _train_logit(
                    selections, prior=personal, epochs=2,
                    rate=0.10, shrinkage=0.12)
                career[band] = weights
                career_loss[band] = curve
        profiles[slug] = {
            "weights": personal, "career_weights": career,
            "training_loss": loss, "career_training_loss": career_loss,
            "observations": len(own),
            "distinct_training_games": len({r["game"] for r in own}),
            "training_years": sorted({r["year"] for r in own}),
            "training_band_counts": dict(sorted(Counter(
                r["band"] for r in own).items())),
        }
    result = {
        "schema": SCHEMA,
        "status": STATUS,
        "method": "HIERARCHICAL_ADAGRAD_CONDITIONAL_LOGIT_V3_REAL_POSITION_MOVE_INTERACTIONS",
        "feature_version": "CONTEXTUAL_MOVE_V3",
        "train_manifest_sha256": sha256((cohorts / "manifest.json").read_bytes()).hexdigest(),
        "source_pgn_sha256": {slug: row["pgn_sha256"]
                              for slug, row in manifest["sources"].items()},
        "training_files_sha256": {
            rel: sha256((cohorts / rel).read_bytes()).hexdigest()
            for rel in manifest["training_files"]
        },
        "split_policy": manifest["partition_policy"],
        "training_examples_in_manifest": manifest["training_examples_exported"],
        "training_observations_used": len(pooled),
        "max_per_player": max_per_player, "max_per_game": max_per_game,
        "seed": seed, "generic_weights": generic,
        "generic_training_loss": generic_loss,
        "profiles": profiles,
        "model_promotion": "DENIED_PENDING_INDEPENDENT_STYLE_VALIDATION",
        "limitations": [
            "Conditional historical move imitation; no evidence of chess strength or identity.",
            "Training uses PGN-provided real pre-move positions and UCI targets, not results.",
            "Phase/material/king context are hand-engineered proxies, not engine analysis.",
            "Do not select hyperparameters using the final holdout.",
        ],
    }
    result["model_sha256"] = _digest(result)
    _write_once(out, result)
    return result


def predict_contextual(model: dict, board: chess.Board, slug: str, band: str,
                       *, generic: bool = False) -> str:
    if model.get("schema") != SCHEMA:
        raise ValueError("Wrong model schema for contextual v3 inference")
    profile = model["profiles"].get(slug)
    if profile is None:
        raise ValueError("Player not trained in contextual model")
    weights = (model["generic_weights"] if generic else
               profile["career_weights"].get(band, profile["weights"]))
    moves = sorted(board.legal_moves, key=lambda move: move.uci())
    if not moves:
        raise ValueError("No legal moves")
    ctx = _board_context(board)
    best_score = -math.inf
    best_move = None
    for move in moves:
        score = sum(weights.get(key, 0.0) for key in
                    contextual_features(board, move, context=ctx, assumed_legal=True))
        if score > best_score:
            best_score, best_move = score, move
    return best_move.uci()


def paired_game_inference(rows: list[dict], candidate: list[str],
                          baseline: list[str], *, seed: int = 2026,
                          repetitions: int = 2000) -> dict:
    """Game-cluster bootstrap CI and paired sign-flip randomization test.

    Position observations within the same historical game are not IID.
    Macro differences are averaged over games, then games (not positions)
    are resampled. P-values are exploratory and unadjusted for multiplicity.
    """
    if (not rows or len(rows) != len(candidate) or
            len(rows) != len(baseline)):
        raise ValueError("Expected identical matched decision samples")
    if (type(repetitions) is not int or not 100 <= repetitions <= 10000 or
            type(seed) is not int):
        raise ValueError("Invalid statistical repetitions or seed")
    games = defaultdict(list)
    for row, a, b in zip(rows, candidate, baseline):
        board = chess.Board(row["fen"])
        if (chess.Move.from_uci(a) not in board.legal_moves or
                chess.Move.from_uci(b) not in board.legal_moves):
            raise ValueError("Invalid candidate UCI in statistical comparison")
        games[row["game_sha256"]].append(
            int(a == row["move"]) - int(b == row["move"]))
    values = [sum(v)/len(v) for _, v in sorted(games.items())]
    n = len(values)
    if n < 2:
        raise ValueError("At least two distinct real games required for inference")
    observed = sum(values) / n
    rng = random.Random(seed)
    bootstrap = [sum(values[rng.randrange(n)] for _ in range(n))/n
                 for _ in range(repetitions)]
    bootstrap.sort()
    ci = [bootstrap[int(0.025 * repetitions)],
          bootstrap[min(repetitions - 1, math.ceil(0.975 * repetitions) - 1)]]
    exceed = sum(
        abs(sum(v * (1 if rng.getrandbits(1) else -1)
                for v in values)/n) >= abs(observed) - 1e-12
        for _ in range(repetitions))
    p = (exceed + 1) / (repetitions + 1)
    return {
        "unit": "DISTINCT_HISTORICAL_GAME",
        "games": n,
        "positions": len(rows),
        "macro_gain_candidate_minus_control": round(observed, 8),
        "bootstrap_95_ci": [round(x, 8) for x in ci],
        "paired_sign_flip_two_sided_p": round(p, 8),
        "bootstrap_resamples": repetitions,
        "sign_flip_samples": repetitions,
        "seed": seed,
        "caveat": "Exploratory; no multiplicity correction or claim of game independence.",
    }


def _read_model(path: Path, schema: str) -> dict:
    model = _read_json(path, 8_000_000)
    digest = model.pop("model_sha256", None)
    if (model.get("schema") != schema or not isinstance(digest, str) or
            digest != _digest(model)):
        raise ValueError("Incorrect, tampered or untrusted model")
    model["model_sha256"] = digest
    return model


def evaluate_contextual(cohorts: Path, model_path: Path, legacy_path: Path,
                        out: Path, *, archive_dir: Path | None = None,
                        seed: int = 2027, per_player: int = 80,
                        per_game: int = 2, rodent_root: Path | None = None,
                        rodent_depth: int = 2,
                        rodent_seconds: float = 0.12,
                        repetitions: int = 2000) -> dict:
    """Validation-only paired comparison. Deliberately no holdout CLI."""
    model = _read_model(model_path, SCHEMA)
    from .real_policy_experiment import POLICY_SCHEMA
    legacy = _read_model(legacy_path, POLICY_SCHEMA)
    _assert_training_manifest(cohorts, model)
    _assert_training_manifest(cohorts, legacy)
    if (model["train_manifest_sha256"] != legacy["train_manifest_sha256"] or
            model["source_pgn_sha256"] != legacy["source_pgn_sha256"] or
            model["seed"] != legacy["seed"] or
            model["max_per_player"] != legacy["max_per_player"] or
            model["max_per_game"] != legacy["max_per_game"] or
            set(model["profiles"]) != set(legacy["profiles"])):
        raise ValueError("V2 and V3 must have exactly matched training sources and sample selectors")
    samples, excluded, hashes = _read_eval_positions(
        cohorts, model, arm="validation", archive_dir=archive_dir,
        seed=seed, per_player=per_player, per_game=per_game)
    slugs = sorted(samples)
    flattened = [row for slug in slugs for row in samples[slug]]
    rodent, engine_hashes = ({}, {})
    if rodent_root is not None:
        rodent, engine_hashes = _rodent_predictions(
            rodent_root, flattened, slugs,
            depth=rodent_depth, time_per_position=rodent_seconds)
    summaries = {}
    offset = 0
    for slug in slugs:
        rows = samples[slug]
        def predict_all(fn, profile, generic=False):
            return [fn(model if fn == predict_contextual else legacy,
                       chess.Board(row["fen"]), profile, row["career_band"],
                       generic=generic) for row in rows]
        predictions = {
            "contextual_named": predict_all(predict_contextual, slug),
            "contextual_generic": predict_all(predict_contextual, slug, generic=True),
            "legacy_named": predict_all(predict_v2, slug),
            "legacy_generic": predict_all(predict_v2, slug, generic=True),
        }
        for other in slugs:
            if other != slug:
                predictions["contextual_swapped_" + other] = predict_all(
                    predict_contextual, other)
        if rodent:
            for engine_name, choices in rodent.items():
                predictions["rodent_" + engine_name] = choices[offset:offset + len(rows)]
        offset += len(rows)
        from .real_policy_experiment import _metric
        scored = {name: _metric(rows, moves) for name, moves in predictions.items()}
        contrasts = {}
        controls = ["contextual_generic", "legacy_named",
                    "rodent_generic_rodent", "rodent_" + slug]
        for i, ctrl in enumerate(controls):
            if ctrl not in predictions:
                continue
            contrasts["contextual_named_vs_" + ctrl] = paired_game_inference(
                rows, predictions["contextual_named"], predictions[ctrl],
                seed=seed + i * 1000 + slugs.index(slug), repetitions=repetitions)
        other_profiles = [key for key in predictions if
                          key.startswith("contextual_swapped_")]
        best_other = max(other_profiles, key=lambda k: (
            scored[k]["macro_by_game"], k)) if other_profiles else None
        # Fixed cyclic label rotation avoids selecting the best of the other
        # identities from the validation data for this inferential contrast.
        rotated_slug = slugs[(slugs.index(slug) + 1) % len(slugs)]
        rotated_name = "contextual_swapped_" + rotated_slug
        if rotated_name in predictions:
            contrasts["contextual_named_vs_fixed_rotation"] = paired_game_inference(
                rows, predictions["contextual_named"], predictions[rotated_name],
                seed=seed + 9990 + slugs.index(slug), repetitions=repetitions)
        by_band = defaultdict(Counter)
        by_year = defaultdict(Counter)
        for i, row in enumerate(rows):
            target = row["move"]
            band, year = row["career_band"], str(row["year"])
            for grouped in (by_band[band], by_year[year]):
                grouped["positions"] += 1
                grouped["v3_named_correct"] += int(
                    predictions["contextual_named"][i] == target)
                grouped["v3_generic_correct"] += int(
                    predictions["contextual_generic"][i] == target)
                grouped["v2_named_correct"] += int(
                    predictions["legacy_named"][i] == target)
                if rodent:
                    grouped["rodent_named_correct"] += int(
                        predictions["rodent_" + slug][i] == target)
        summaries[slug] = {
            "sample": {"decisions": len(rows),
                       "distinct_games": len({r["game_sha256"] for r in rows}),
                       "years": sorted({r["year"] for r in rows}),
                       "by_band": {key: dict(val) for key, val in sorted(by_band.items())},
                       "by_year": {key: dict(val) for key, val in sorted(by_year.items())}},
            "fixed_rotation_negative_control": rotated_slug,
            "models": scored,
            "paired_game_statistics": contrasts,
            "negative_control_best_swapped_profile": best_other,
            "named_exceeds_best_swapped_macro": (
                scored["contextual_named"]["macro_by_game"] >
                scored[best_other]["macro_by_game"] if best_other else None),
            "historical_style_validated": False,
        }
    report = {
        "schema": REPORT_SCHEMA,
        "status": "REAL_PGN_PAIRED_VALIDATION_EXPLORATORY_NO_IDENTITY_CLAIM",
        "validation_arm": "validation",
        "final_holdout_opened": False,
        "contextual_model_sha256": model["model_sha256"],
        "legacy_model_sha256": legacy["model_sha256"],
        "historical_source_sha256": hashes,
        "training_observations": model["training_observations_used"],
        "evaluation_decisions": len(flattened),
        "excluded": dict(excluded),
        "seed": seed, "per_player_limit": per_player,
        "per_game_limit": per_game, "stats_repetitions": repetitions,
        "rodent_engine": (
            {"status": "ACTUAL_RODENT_UCI", "exe_sha256": engine_hashes["exe"],
             "profile_sha256": {p: engine_hashes[p] for p in slugs},
             "generic_personality": "Simple",
             "simple_sha256": engine_hashes["simple_profile"],
             "depth": rodent_depth, "seconds_per_position": rodent_seconds,
             "opening_book": False}
            if rodent else {"status": "NOT_REQUESTED"}),
        "by_historical_player": summaries,
        "promotion": "DENIED_UNTIL_HOLDOUT_AND_INDEPENDENT_STYLE_REPLICATION",
        "limitations": [
            "Retrospective year-held validation, not unseen final testing.",
            "Hyperparameters selected in prior iterations after inspecting validation; significance is exploratory.",
            "Exact full FEN overlap is embargoed. Similar openings and semantically equal positions may remain.",
            "Paired sign-flips presume game-level exchangeability; same era/opponent effects may remain.",
            "P-values are not adjusted for multiple testing and do not prove historical identity.",
            "Fixed cyclic label rotation is an exploratory negative control chosen by name ordering.",
            "Best swapped profile chosen on this evaluation set is descriptive, not a calibrated null.",
            "Year and career-band breakdowns can be sparse and must not be read as standalone tests.",
            "Top1 and game-macro agreement measure human choice matching, not engine strength.",
        ],
    }
    _write_once(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit contextual chess policies on real PGNs and audit paired decisions")
    sub = parser.add_subparsers(dest="action", required=True)
    train = sub.add_parser("train")
    train.add_argument("--cohorts", required=True, type=Path)
    train.add_argument("--out", required=True, type=Path)
    train.add_argument("--max-per-player", type=int, default=1600)
    train.add_argument("--max-per-game", type=int, default=3)
    train.add_argument("--seed", type=int, default=2026)
    audit = sub.add_parser("evaluate")
    audit.add_argument("--cohorts", required=True, type=Path)
    audit.add_argument("--model", required=True, type=Path)
    audit.add_argument("--legacy-model", required=True, type=Path)
    audit.add_argument("--out", required=True, type=Path)
    source = audit.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    audit.add_argument("--per-player", type=int, default=80)
    audit.add_argument("--per-game", type=int, default=2)
    audit.add_argument("--seed", type=int, default=2027)
    audit.add_argument("--repetitions", type=int, default=2000)
    audit.add_argument("--rodent-root", type=Path)
    audit.add_argument("--rodent-depth", type=int, default=2)
    audit.add_argument("--rodent-seconds", type=float, default=0.12)
    opts = parser.parse_args(argv)
    if opts.action == "train":
        result = fit_contextual(
            opts.cohorts, opts.out, max_per_player=opts.max_per_player,
            max_per_game=opts.max_per_game, seed=opts.seed)
        summary = {"status": result["status"],
                   "model_sha256": result["model_sha256"],
                   "real_training_decisions": result["training_observations_used"]}
    else:
        result = evaluate_contextual(
            opts.cohorts, opts.model, opts.legacy_model, opts.out,
            archive_dir=opts.archive_dir,
            seed=opts.seed, per_player=opts.per_player,
            per_game=opts.per_game, rodent_root=opts.rodent_root,
            rodent_depth=opts.rodent_depth,
            rodent_seconds=opts.rodent_seconds,
            repetitions=opts.repetitions)
        summary = {"status": result["status"],
                   "evaluation_decisions": result["evaluation_decisions"],
                   "promotion": result["promotion"],
                   "rodent_status": result["rodent_engine"]["status"],
                   "profiles": {slug: {
                       "contextual_top1": data["models"]["contextual_named"]["top1"],
                       "legacy_top1": data["models"]["legacy_named"]["top1"],
                       "generic_top1": data["models"]["contextual_generic"]["top1"],
                   } for slug, data in result["by_historical_player"].items()}}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
