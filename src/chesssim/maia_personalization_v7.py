"""V7: FULL historical TRAIN corpus, lightweight per-player Maia3-79M adapter.

The original Maia3 79M weights stay frozen. An opportunity-adjusted,
shrunk, player-conditional *rank residual* learns from every eligible
historical TRAIN game (not a 1,600-move subset). Game-disjoint tuning/audit
and held-year validation compare the personalised 79M to the SAME 79M
without adaptation and the published 5M original on identical real PGNs.

This does not fine-tune 79M parameters, claim historical Elo, or establish
an intrinsic move-matching ceiling. Candidate moves come from original Maia3
UCI MultiPV=20 without consulting the human target.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys

import chess
import chess.engine

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model,
    paired_game_inference,
)
from .maia_transfer_v6 import (
    MAIA_CODE_COMMIT, MAIA_HF_REVISION, MAIA_CHECKPOINT_SHA256,
    _restore_real_histories, _samp_validation, _verify_maia_source_code,
)
from .real_gate_v4 import _position_key
from .real_policy_experiment import (
    _bottom_k, _digest, _metric, _read_json, _validated_training_rows,
    _write_once,
)

SCHEMA = "chesssim-historical-all-train-maia79-personal-adapter-v7"
REPORT_SCHEMA = "chesssim-historical-maia79-personal-benchmark-v7"
MAIA79_REPO = "UofTCSSLab/Maia3-79M"
MAIA79_FILE = "maia3-79m.pt"
MIN_PLY = 21
SPLIT_MOD = 10
FIT_BUCKETS = frozenset(range(8))  # 80% train-year games, rest reserved for gate fitting/auditing
TUNE_BUCKET = 8
AUDIT_BUCKET = 9
REGULARIZATION = 60.0
MAX_ABS_WEIGHT = 2.0
MULTIPV = 20
STRENGTHS = (0.0, 0.5, 1.0, 2.0, 4.0)  # fixed BEFORE first validation
MAIA_ELO = 2400   # scenario, not an FIDE -> Lichess mapping
PENALTY = 0.008


def _arm(slug: str, game_id: str) -> int:
    # Same PGN trajectory always maps to same bucket, even if exported
    # under multiple player files. Do not depend on player name.
    if not re.fullmatch(r"[0-9a-f]{64}", game_id):
        raise ValueError("Invalid historical mainline identity")
    return int.from_bytes(sha256(("maia-v7-split|" + game_id).encode()).digest()[:8],
                          "big") % SPLIT_MOD


def _features(board: chess.Board, move: chess.Move, *,
              assumed_legal: bool = False) -> tuple[str, ...]:
    """Small chess-semantic style dictionary; no outcome or future labels."""
    piece = board.piece_at(move.from_square)
    if piece is None or (not assumed_legal and move not in board.legal_moves):
        raise ValueError("Illegal game or candidate move")
    symbol = piece.symbol().upper()
    target_file = chess.square_file(move.to_square)
    target_rank = chess.square_rank(move.to_square)
    # Relative advancement is from the mover's perspective.
    forward_rank = target_rank if board.turn else 7 - target_rank
    capture = board.is_capture(move)
    return (
        "piece=" + symbol,
        "capture=" + str(int(capture)),
        "castle=" + str(int(board.is_castling(move))),
        "promotion=" + (chess.piece_symbol(move.promotion) if move.promotion else "-"),
        "file=" + str(target_file),
        "rank_relative=" + str(forward_rank),
        "piece_rank=" + symbol + ":" + str(forward_rank),
        "central=" + str(int(move.to_square in (
            chess.D4, chess.E4, chess.D5, chess.E5))),
        "pawn_double=" + str(int(
            piece.piece_type == chess.PAWN and
            abs(chess.square_rank(move.to_square) -
                chess.square_rank(move.from_square)) == 2)),
    )


def _canonical_key(row: dict) -> str:
    return _position_key(row["policy_input"]["fen"])


def _valid_train(row: dict) -> bool:
    return row["ply"] >= MIN_PLY


def _build_profile(selected: Counter, expected: Counter,
                   positions: int, games: int) -> dict:
    if positions == 0 or games == 0:
        return {"status": "UNDERPOWERED_NO_TRAIN_DECISIONS",
                "weights": {}, "decisions": positions, "distinct_games": games}
    weights = {}
    for key in sorted(set(selected) | set(expected)):
        s, e = selected[key], expected[key]
        if s + e < 20:
            continue
        # Opportunity-adjusted empirical log ratio with shrinkage and
        # bounded amplitude. Historical human move labels are used ONLY here.
        advantage = math.log((s + REGULARIZATION) / (e + REGULARIZATION))
        weights[key] = round(max(-MAX_ABS_WEIGHT, min(MAX_ABS_WEIGHT, advantage)), 8)
    return {"status": "REAL_TRAIN_FIT_NOT_STYLE_VALIDATED",
            "weights": weights, "decisions": positions, "distinct_games": games,
            "unique_semantic_features": len(weights),
            "regularization_pseudocount": REGULARIZATION}


def train_full_corpus(cohorts: Path, v3_path: Path, output: Path) -> dict:
    """No artificial move cap, no per-game cap: process ALL eligible FIT rows."""
    v3 = _read_model(v3_path, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    manifest = _read_json(cohorts / "manifest.json")
    choices = defaultdict(Counter)
    opportunities = defaultdict(Counter)
    counters = defaultdict(Counter)
    games = defaultdict(set)
    all_games = defaultdict(set)
    splits = defaultdict(lambda: defaultdict(set))
    # Source files in this manifest are genuine only; the underlying
    # validator rejects unpinned or cross-arm policy targets.
    for row in _validated_training_rows(cohorts, manifest):
        slug = row["player"]
        game = row["game_sha256"]
        bucket = _arm(slug, game)
        all_games[slug].add(game)
        splits[slug][str(bucket)].add(game)
        if not _valid_train(row):
            counters[slug]["below_min_ply"] += 1
            continue
        counters[slug]["all_training_arm_decisions"] += 1
        if bucket not in FIT_BUCKETS:
            counters[slug]["reserved_tune_audit_decisions"] += 1
            continue
        board = chess.Board(row["policy_input"]["fen"])
        actual = chess.Move.from_uci(row["policy_target"]["move_uci"])
        if actual not in board.legal_moves:
            raise ValueError("Historical target is not legal at source FEN")
        if board.is_game_over(claim_draw=True):
            counters[slug]["terminal_positions_skipped"] += 1
            continue
        legal = list(board.legal_moves)
        per_opportunity = 1.0 / len(legal)
        for move in legal:
            for feature in _features(board, move, assumed_legal=True):
                opportunities[slug][feature] += per_opportunity
        for feature in _features(board, actual, assumed_legal=True):
            choices[slug][feature] += 1
        counters[slug]["fitted_decisions"] += 1
        games[slug].add(game)
    roster = set(manifest["players"])
    if roster != set(counters):
        raise ValueError("Insufficient train-only PGN inputs for one or more players")
    profiles = {}
    for slug in sorted(roster):
        profile = _build_profile(
            choices[slug], opportunities[slug],
            counters[slug]["fitted_decisions"], len(games[slug]))
        profile["eligible_train_arm_games"] = len(all_games[slug])
        profile["fit_tune_audit_games"] = {
            k: len(value) for k, value in sorted(splits[slug].items())
        }
        profile["counts"] = dict(counters[slug])
        profile["all_source_accepted_games"] = manifest["sources"][slug]["accepted_games"]
        profile["train_year_games_manifest"] = (
            manifest["players"][slug]["partition_by_whole_year"]["train"]["games"])
        profiles[slug] = profile
    model = {
        "schema": SCHEMA,
        "status": "ALL_ELIGIBLE_REAL_TRAIN_GAMES_FROZEN_MAIA79_RANK_ADAPTER",
        "training": "OPPORTUNITY_NORMALIZED_BAYES_SHRUNK_CATEGORY_LOG_LIFT",
        "pretrained_backbone": "MAIA3_79M_FROZEN_ORIGINAL_WEIGHTS_NOT_FINETUNED",
        "training_policy": "ALL_ELIGIBLE_FIT_DECISIONS_NO_PER_GAME_OR_MOVE_CAP",
        "game_split": "SHA256_TRAJECTORY_BUCKETS_0_TO_7_FIT_8_TUNE_9_AUDIT",
        "minimum_ply": MIN_PLY,
        "maia_elo_scenario": MAIA_ELO,
        "model_features": "TRAIN_ONLY_CHOICE_VS_LEGAL_OPPORTUNITY",
        "train_manifest_sha256": v3["train_manifest_sha256"],
        "training_files_sha256": v3["training_files_sha256"],
        "source_pgn_sha256": v3["source_pgn_sha256"],
        "v3_model_sha256": v3["model_sha256"],
        "profiles": profiles,
        "sum_fitted_decisions": sum(x["decisions"] for x in profiles.values()),
        "sum_distinct_fitted_games_by_player": sum(
            x["distinct_games"] for x in profiles.values()),
        "holdout_opened": False,
        "official_personality_promotion": "DENIED",
        "limitations": [
            "No gradient step changes the 79-million pretrained Maia weights.",
            "Player specific re-ranking weights use ALL eligible historical FIT arm choices.",
            "Tune and audit TRAIN games are intentionally excluded from fitting to prevent leakage.",
            "Held-year validation and final holdout are excluded from training.",
            "Historic FIDE rating is not converted to Maia online Elo.",
        ],
    }
    model["model_sha256"] = _digest(model)
    _write_once(output, model)
    return model


def _load_adapter(path: Path, v3: dict) -> dict:
    model = _read_json(path, 8_000_000)
    signature = model.pop("model_sha256", None)
    if (model.get("schema") != SCHEMA or
            model.get("v3_model_sha256") != v3["model_sha256"] or
            model.get("train_manifest_sha256") != v3["train_manifest_sha256"] or
            model.get("training_files_sha256") != v3["training_files_sha256"] or
            model.get("source_pgn_sha256") != v3["source_pgn_sha256"] or
            signature != _digest(model) or
            set(model.get("profiles", {})) != set(v3["profiles"])):
        raise ValueError("Tampered or incompatible all-real-games Maia adapter")
    for p in model["profiles"].values():
        if not isinstance(p.get("weights"), dict):
            raise ValueError("Invalid learned style weights")
        if any(not isinstance(key, str) or type(w) not in (float, int)
               or not math.isfinite(w) or abs(w) > MAX_ABS_WEIGHT
               for key, w in p["weights"].items()):
            raise ValueError("Out-of-range semantic weight in model")
    model["model_sha256"] = signature
    return model


def _style_score(board: chess.Board, move: chess.Move, weights: dict) -> float:
    return sum(weights.get(feature, 0) for feature in _features(
        board, move, assumed_legal=True))


def _rerank(board: chess.Board, candidates: list[str], weights: dict,
            strength: float) -> str:
    if strength not in STRENGTHS or not candidates or len(set(candidates)) != len(candidates):
        raise ValueError("Invalid Maia candidate list or unregistered personalization strength")
    parsed = [chess.Move.from_uci(uci) for uci in candidates]
    if any(move not in board.legal_moves for move in parsed):
        raise ValueError("Illegal original Maia UCI candidate")
    # UCI exposes policy RANK (not logits). It would be misleading to treat
    # Maia's separate WDL cp info as move probabilities. Explicit ordinal prior.
    ranked = [
        (-math.log(i + 1) + strength * _style_score(board, move, weights),
         -i, move.uci())
        for i, move in enumerate(parsed)
    ]
    return max(ranked)[2]


def _calibration_samples(cohorts: Path, v3: dict, model: dict,
                         limit: int, seed: int) -> tuple[dict, dict]:
    manifest = _read_json(cohorts / "manifest.json")
    fitted_positions = set()
    tune, audit = defaultdict(list), defaultdict(list)
    rows = list(_validated_training_rows(cohorts, manifest))
    for row in rows:
        if _valid_train(row) and _arm(row["player"], row["game_sha256"]) in FIT_BUCKETS:
            fitted_positions.add(_canonical_key(row))
    for row in rows:
        if not _valid_train(row) or _canonical_key(row) in fitted_positions:
            continue
        arm = _arm(row["player"], row["game_sha256"])
        if arm not in (TUNE_BUCKET, AUDIT_BUCKET):
            continue
        sample = {
            "player": row["player"], "fen": row["policy_input"]["fen"],
            "move": row["policy_target"]["move_uci"],
            "year": row["year"], "game_sha256": row["game_sha256"],
            "ply": row["ply"],
        }
        (tune if arm == TUNE_BUCKET else audit)[row["player"]].append(sample)
    final_tune, final_audit = {}, {}
    for slug in sorted(model["profiles"]):
        tune_samples = _bottom_k(
            tune[slug], limit=min(limit, len(tune[slug])),
            per_game=2, seed=seed)
        # Audit should not re-use the EXACT canonical board used in tune.
        tune_keys = {_position_key(item["fen"]) for item in tune_samples}
        independent = [row for row in audit[slug]
                       if _position_key(row["fen"]) not in tune_keys]
        audit_samples = _bottom_k(
            independent, limit=min(limit, len(independent)),
            per_game=2, seed=seed + 1)
        final_tune[slug] = tune_samples
        final_audit[slug] = audit_samples
    return final_tune, final_audit


def _verify_checkpoint(path: Path, *, known_digest: str | None) -> str:
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Missing genuine Maia checkpoint")
    actual = sha256(path.read_bytes()).hexdigest()
    if known_digest and actual != known_digest:
        raise ValueError("Original Maia checkpoint bytes changed")
    return actual


def _open_maia(label: str, checkpoint: Path) -> chess.engine.SimpleEngine:
    cmd = [sys.executable, "-m", "maia3.uci", "--model", label,
           "--checkpoint-path", str(checkpoint),
           "--device", "cpu", "--no-use-amp", "--use-uci-history",
           "--temperature", "0", "--top-p", "1",
           "--multipv", str(MULTIPV)]
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}
    engine = chess.engine.SimpleEngine.popen_uci(cmd, timeout=240, env=env)
    if not {"Elo", "MultiPV"} <= set(engine.options):
        engine.quit()
        raise ValueError("Not the original Maia-3 UCI engine")
    engine.configure({"Elo": MAIA_ELO})
    return engine


def _original_maia_candidates(engine: chess.engine.SimpleEngine,
                              board: chess.Board) -> list[str]:
    if not board.is_valid() or board.is_game_over(claim_draw=True):
        raise ValueError("Invalid/terminal human historical position")
    count = min(MULTIPV, board.legal_moves.count())
    rows = engine.analyse(
        board, chess.engine.Limit(nodes=1), multipv=count,
        info=chess.engine.INFO_SCORE | chess.engine.INFO_PV)
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Original Maia3 did not return required UCI MultiPV")
    moves = []
    for row in rows:
        pv = row.get("pv")
        if not pv or pv[0] not in board.legal_moves:
            raise ValueError("Illegal/incomplete Maia3 candidate")
        moves.append(pv[0].uci())
    if len(set(moves)) != len(moves):
        raise ValueError("Duplicate original Maia3 UCI suggestions")
    return moves


def _infer_samples(cohorts: Path, v3: dict, samples: dict,
                   archive_dir: Path | None,
                   engines: dict) -> dict:
    history, hashes = _restore_real_histories(
        cohorts, v3, samples, archive_dir)
    if hashes != v3["source_pgn_sha256"]:
        raise ValueError("Real PGN source SHA drift during Maia benchmark")
    out = {}
    for slug in sorted(samples):
        observed = []
        for row in samples[slug]:
            board = history[slug][(row["game_sha256"], row["ply"])]
            if board.fen() != row["fen"]:
                raise ValueError("True historical Maia board mismatch")
            record = {**row, "maia5_candidates": _original_maia_candidates(
                engines["maia3-5m"], board),
                "maia79_candidates": _original_maia_candidates(
                    engines["maia3-79m"], board)}
            observed.append(record)
        out[slug] = observed
    return out


def _model_proposals(row: dict, weights: dict) -> dict:
    board = chess.Board(row["fen"])
    return {
        str(k): _rerank(board, row["maia79_candidates"], weights, k)
        for k in STRENGTHS
    }


def _calibrate(rows_tune: list[dict], rows_audit: list[dict],
               weights: dict, seed: int) -> dict:
    base_tune = [r["maia79_candidates"][0] for r in rows_tune]
    ranked_tune = {s: [
        _rerank(chess.Board(row["fen"]), row["maia79_candidates"], weights, s)
        for row in rows_tune] for s in STRENGTHS}
    def opportunity_score(proposals: list[str]) -> float:
        if not rows_tune:
            return -1.0
        by_game = defaultdict(list)
        changes = 0
        for row, pred, b in zip(rows_tune, proposals, base_tune):
            by_game[row["game_sha256"]].append(int(pred == row["move"]) -
                                                int(b == row["move"]))
            changes += pred != b
        return (sum(sum(x)/len(x) for x in by_game.values())/len(by_game)
                - PENALTY * changes/len(rows_tune))
    choice = max(STRENGTHS, key=lambda s: (opportunity_score(ranked_tune[s]), -s))
    # The adapter is enabled only if improvements generalize from TUNE to
    # different AUDIT games; 0 is the safe original Maia79 fallback.
    distinct_tune = len({r["game_sha256"] for r in rows_tune})
    distinct_audit = len({r["game_sha256"] for r in rows_audit})
    results = {"chosen_tune_strength": choice,
               "tuning_games": distinct_tune, "audit_games": distinct_audit}
    if distinct_tune < 8 or distinct_audit < 8 or choice == 0:
        return {**results, "activated_strength": 0.0, "audit": None,
                "decision": "DENY_INSUFFICIENT_INDEPENDENT_GAMES_OR_GAIN"}
    personal = [
        _rerank(chess.Board(row["fen"]), row["maia79_candidates"], weights, choice)
        for row in rows_audit]
    baseline = [r["maia79_candidates"][0] for r in rows_audit]
    proof = paired_game_inference(
        rows_audit, personal, baseline, seed=seed, repetitions=1000)
    replacements = sum(a != b for a, b in zip(personal, baseline))
    accepted = (replacements >= 3 and proof["bootstrap_95_ci"][0] > 0)
    return {
        **results, "activated_strength": choice if accepted else 0.0,
        "audit": proof, "audit_overrides": replacements,
        "decision": ("ACCEPT_GAME_DISJOINT_GAIN" if accepted
                     else "DENY_NOT_REPLICATED_ON_INDEPENDENT_GAMES"),
    }


def benchmark(cohorts: Path, v3_path: Path, adapter_path: Path, output: Path,
              *, maia79_revision: str, archive_dir: Path | None,
              validation_per_player: int, tune_per_player: int,
              seed: int = 2038) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", maia79_revision):
        raise ValueError("Maia79 checkpoint requires frozen 40-char HF revision")
    if not 10 <= validation_per_player <= 10000 or not 10 <= tune_per_player <= 500:
        raise ValueError("Invalid historical sample; full-corpus training has no cap")
    v3 = _read_model(v3_path, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    model = _load_adapter(adapter_path, v3)
    _verify_maia_source_code()
    from huggingface_hub import hf_hub_download
    ckpt79 = Path(hf_hub_download(MAIA79_REPO, filename=MAIA79_FILE,
                                  revision=maia79_revision))
    ckpt5 = Path(hf_hub_download(
        "UofTCSSLab/Maia3-5M", filename="maia3-5m.pt",
        revision=MAIA_HF_REVISION))
    digests = {
        "maia3-5m": _verify_checkpoint(ckpt5, known_digest=MAIA_CHECKPOINT_SHA256),
        "maia3-79m": _verify_checkpoint(ckpt79, known_digest=None),
    }
    tune, audit = _calibration_samples(
        cohorts, v3, model, tune_per_player, seed)
    validation, excluded, sources = _samp_validation(
        cohorts, v3, validation_per_player, seed+1, archive_dir)
    engines = {}
    try:
        engines["maia3-5m"] = _open_maia("maia3-5m", ckpt5)
        engines["maia3-79m"] = _open_maia("maia3-79m", ckpt79)
        fit_observations = _infer_samples(
            cohorts, v3, {slug: tune[slug] + audit[slug] for slug in sorted(tune)},
            archive_dir, engines)
        val_observations = _infer_samples(
            cohorts, v3, validation, archive_dir, engines)
    finally:
        for eng in engines.values():
            try:
                eng.quit()
            except Exception:
                eng.close()
    results = {}
    combined_rows = []
    total_predictions = defaultdict(list)
    for i, slug in enumerate(sorted(model["profiles"])):
        observed = fit_observations[slug]
        tune_ids = {(x["game_sha256"], x["ply"]) for x in tune[slug]}
        tune_rows = [r for r in observed if (r["game_sha256"], r["ply"]) in tune_ids]
        audit_rows = [r for r in observed if (r["game_sha256"], r["ply"]) not in tune_ids]
        personal = model["profiles"][slug]
        gate = _calibrate(tune_rows, audit_rows, personal["weights"], seed+i)
        rows = val_observations[slug]
        p5 = [r["maia5_candidates"][0] for r in rows]
        p79 = [r["maia79_candidates"][0] for r in rows]
        unapproved = [
            _rerank(chess.Board(r["fen"]), r["maia79_candidates"],
                    personal["weights"], 1.0) for r in rows]
        approved = [
            _rerank(chess.Board(r["fen"]), r["maia79_candidates"],
                    personal["weights"], gate["activated_strength"]) for r in rows]
        predictions = {"maia3_5m": p5, "maia3_79m": p79,
                       "maia3_79m_style_probe": unapproved,
                       "maia3_79m_game_audited": approved}
        results[slug] = {
            "source_games": personal["all_source_accepted_games"],
            "train_year_games": personal["train_year_games_manifest"],
            "fitted_distinct_games": personal["distinct_games"],
            "fitted_real_decisions": personal["decisions"],
            "tune_audit": gate,
            "validation_games": len({r["game_sha256"] for r in rows}),
            "validation_decisions": len(rows),
            "metrics": {k: _metric(rows, v) for k, v in predictions.items()},
            "game_paired_vs_frozen_79m": {
                name: paired_game_inference(
                    rows, pred, p79, seed=seed+i*10+j,
                    repetitions=1000)
                for j, (name, pred) in enumerate(predictions.items())
                if name != "maia3_79m"
            },
            "observed_not_in_fit_for_trained_adapter": True,
        }
        combined_rows.extend(rows)
        for key, pred in predictions.items():
            total_predictions[key].extend(pred)
    aggregate = {
        name: _metric(combined_rows, pred)
        for name, pred in total_predictions.items()
    }
    aggregate_stats = {
        name: paired_game_inference(
            combined_rows, pred, total_predictions["maia3_79m"],
            seed=seed+600+j, repetitions=2000)
        for j, (name, pred) in enumerate(sorted(total_predictions.items()))
        if name != "maia3_79m"
    }
    report = {
        "schema": REPORT_SCHEMA,
        "status": "REAL_FROZEN_79M_PERSONALIZATION_CANDIDATE_NOT_STYLE_VALIDATED",
        "maia_code_commit": MAIA_CODE_COMMIT,
        "maia79_repo": MAIA79_REPO, "maia79_hf_revision": maia79_revision,
        "maia_checkpoints_sha256": digests, "maia79_backbone_frozen": True,
        "adapter_sha256": model["model_sha256"],
        "source_pgn_sha256": sources,
        "train_manifest_sha256": v3["train_manifest_sha256"],
        "train_full_fit_decisions": model["sum_fitted_decisions"],
        "validation_arm": "validation",
        "validation_positions": len(combined_rows),
        "validation_real_games": len({r["game_sha256"] for r in combined_rows}),
        "sample_exclusions": excluded,
        "per_player": results, "aggregate": aggregate,
        "aggregate_paired_game_vs_79m": aggregate_stats,
        "maia_elo_is_scenario_not_FIDE_rating": True,
        "test_final_holdout_opened": False,
        "official_personality_promotion": "DENIED",
        "limitations": [
            "79M backbone is pretrained and frozen; personal rank residual learned from real historical choices.",
            "Original Maia3 MultiPV reports rank; its UCI score cp is a separate WDL head, not policy log-probability.",
            "Reranking is restricted to top-20 legal Maia suggestions without injecting human targets.",
            "Non-approved style probe is descriptive only; gated model must pass game-independent audit.",
            "Published Maia3 online figures cannot be directly compared to this OTB historical corpus.",
            "External Maia pretraining has not been independently decontaminated against historic games.",
            "These validation years were already inspected; inference remains exploratory.",
        ],
    }
    report["report_sha256"] = _digest(report)
    _write_once(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train all real player TRAIN decisions; CPU Maia3-79M historical personalization")
    commands = parser.add_subparsers(dest="action", required=True)
    fit = commands.add_parser("fit")
    fit.add_argument("--cohorts", required=True, type=Path)
    fit.add_argument("--v3-model", required=True, type=Path)
    fit.add_argument("--out", required=True, type=Path)
    ev = commands.add_parser("benchmark")
    ev.add_argument("--cohorts", required=True, type=Path)
    ev.add_argument("--v3-model", required=True, type=Path)
    ev.add_argument("--adapter", required=True, type=Path)
    ev.add_argument("--out", required=True, type=Path)
    ev.add_argument("--maia79-revision", required=True)
    source = ev.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    ev.add_argument("--validation-per-player", type=int, default=96)
    ev.add_argument("--tune-per-player", type=int, default=48)
    ev.add_argument("--seed", type=int, default=2038)
    args = parser.parse_args(argv)
    if args.action == "fit":
        result = train_full_corpus(
            args.cohorts, args.v3_model, args.out)
        print(json.dumps({
            "status": result["status"],
            "all_fit_decisions": result["sum_fitted_decisions"],
            "distinct_fit_games_by_player": {
                slug: data["distinct_games"] for slug,data in result["profiles"].items()},
        }, indent=2, ensure_ascii=False))
    else:
        result = benchmark(
            args.cohorts, args.v3_model, args.adapter, args.out,
            maia79_revision=args.maia79_revision,
            archive_dir=args.archive_dir,
            validation_per_player=args.validation_per_player,
            tune_per_player=args.tune_per_player, seed=args.seed)
        print(json.dumps({
            "status": result["status"], "validation": result["validation_positions"],
            "overall": result["aggregate"],
            "gates": {slug:v["tune_audit"]["decision"]
                      for slug,v in result["per_player"].items()},
        }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
