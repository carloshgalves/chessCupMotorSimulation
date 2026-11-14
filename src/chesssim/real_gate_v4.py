"""V4: game-disjoint calibrated choice between real Rodent UCI and V3.

A measured strategy change, not a promise of 100% historical fidelity.
V3 weights are fitted on genuine historical training games. Gate thresholds
are fitted on OTHER, never-fitted training games, with a second game-disjoint
audit partition. The final test/holdout arm is never read.

Rodent's actual UCI bestmove is the default. A policy can displace it only
when an independently audited player-specific threshold shows positive
game-macro historical choice agreement on reserved *training-arm* games.
No engine weights are changed, and no game result enters the training signal.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path
import re

import chess

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _board_context,
    _read_model, contextual_features, paired_game_inference,
    predict_contextual,
)
from .real_policy_experiment import (
    _bottom_k, _digest, _metric, _read_eval_positions, _read_json,
    _rodent_predictions, _validated_training_rows, _write_once,
)

GATE_SCHEMA = "chesssim-real-rodent-external-decision-gate-v4"
GATE_STATUS = "CALIBRATED_FROM_REAL_TRAIN_ARM_PGNS_NO_STYLE_VALIDATION"
REPORT_SCHEMA = "chesssim-real-rodent-gate-paired-validation-v1"
# Predeclared grid: a fixed statistical experiment, NOT chosen on validation.
THRESHOLDS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
INTERVENTION_PENALTY = 0.01  # game-macro regularizer for any external override


def _render_train_row(row: dict) -> dict:
    policy, target = row["policy_input"], row["policy_target"]
    return {"player": row["player"], "fen": policy["fen"],
            "move": target["move_uci"], "game_sha256": row["game_sha256"],
            "career_band": policy["career_band"], "year": policy["year"],
            "ply": row["ply"]}


def _calibration_samples(cohorts: Path, model: dict, *,
                         per_player: int, per_game: int,
                         seed: int) -> tuple[dict[str, list[dict]], dict]:
    """Reconstruct the EXACT V3 fitted games, then embargo them globally.

    Other decisions in the same game, from any player's PGN, are forbidden.
    Also embargo every FEN used in fitting (across all players).
    """
    _assert_training_manifest(cohorts, model)
    if not isinstance(per_player, int) or not 30 <= per_player <= 400:
        raise ValueError("Calibration decisions per player must be 30..400")
    if not isinstance(per_game, int) or not 1 <= per_game <= 3:
        raise ValueError("Calibration decisions per game must be 1..3")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("Calibration seed must be a nonnegative integer")
    manifest = _read_json(cohorts / "manifest.json")
    grouped = defaultdict(list)
    for row in _validated_training_rows(cohorts, manifest):
        grouped[row["player"]].append(row)
    if set(grouped) != set(model["profiles"]):
        raise ValueError("Real training players disagree with fitted V3 model")
    fitted_games, fitted_fens = set(), set()
    actual_counts = {}
    # Replay deterministic minhash *exactly* as original V3 fit.
    for slug in sorted(grouped):
        selected = _bottom_k(
            grouped[slug], limit=model["max_per_player"],
            per_game=model["max_per_game"], seed=model["seed"])
        if len(selected) != model["profiles"][slug]["observations"]:
            raise ValueError("Cannot reconstruct the fitted-game sample")
        actual_counts[slug] = len(selected)
        for row in selected:
            fitted_games.add(row["game_sha256"])
            fitted_fens.add(row["policy_input"]["fen"])
    unfit = {}
    excluded = Counter()
    for slug in sorted(grouped):
        spare = []
        for row in grouped[slug]:
            if row["game_sha256"] in fitted_games:
                excluded["fitted_trajectory"] += 1
            elif row["policy_input"]["fen"] in fitted_fens:
                excluded["fitted_fen"] += 1
            else:
                spare.append(row)
        sampled = _bottom_k(spare, limit=per_player, per_game=per_game, seed=seed)
        if len({x["game_sha256"] for x in sampled}) < 20:
            raise ValueError(f"Insufficient unused actual historical games: {slug}")
        unfit[slug] = [_render_train_row(row) for row in sampled]
    return unfit, {
        "fitted_observations_reconstructed": actual_counts,
        "fitted_games_globally_embargoed": len(fitted_games),
        "fitted_fens_globally_embargoed": len(fitted_fens),
        "rejected": dict(excluded),
        "min_calibration_games": min(len({x["game_sha256"] for x in v})
                                     for v in unfit.values()),
    }


def _style_lift_and_proposal(model: dict, board: chess.Board,
                             slug: str, band: str,
                             rodent_move: str) -> tuple[str, float]:
    """Named-vs-generic log-score preference for policy over Rodent.

    Positive lift: trained named policy prefers its move over Rodent more
    strongly than the generic policy. Style-lift is NOT a tactical safety
    guarantee; it requires real-game gate calibration.
    """
    if model.get("schema") != V3_SCHEMA:
        raise ValueError("Gate requires trained contextual V3")
    if slug not in model["profiles"]:
        raise ValueError("Untrained player")
    board_move = chess.Move.from_uci(rodent_move)
    if board_move not in board.legal_moves:
        raise ValueError("Rodent UCI proposed an illegal move")
    proposed = predict_contextual(model, board, slug, band)
    candidate = chess.Move.from_uci(proposed)
    if proposed == rodent_move:
        return proposed, 0.0
    profile = model["profiles"][slug]
    named = profile["career_weights"].get(band, profile["weights"])
    generic = model["generic_weights"]
    ctx = _board_context(board)
    a = contextual_features(board, candidate, context=ctx, assumed_legal=True)
    b = contextual_features(board, board_move, context=ctx, assumed_legal=True)
    delta = (sum(named.get(k, 0.0) for k in a)
             - sum(named.get(k, 0.0) for k in b)
             - sum(generic.get(k, 0.0) for k in a)
             + sum(generic.get(k, 0.0) for k in b))
    if not math.isfinite(delta):
        raise ValueError("Invalid V3 style lift")
    return proposed, delta


def _choose(rodent_move: str, named_move: str, lift: float,
            threshold: float | None) -> str:
    if (threshold is None or named_move == rodent_move or
            not math.isfinite(lift) or lift <= threshold):
        return rodent_move
    return named_move


def _paired_counts(rows: list[dict], predictions: list[str],
                   rodent: list[str]) -> dict:
    stats = paired_game_inference(rows, predictions, rodent,
                                  seed=2029, repetitions=1000)
    return stats


def _split_games(rows: list[dict], *, seed: int, slug: str) -> tuple[list[int], list[int]]:
    by_game = sorted({r["game_sha256"] for r in rows},
                     key=lambda g: sha256(
                         f"{seed}|{slug}|{g}".encode()).digest())
    if len(by_game) < 20:
        raise ValueError("Calibration requires at least 20 distinct games")
    pivot = max(12, min(len(by_game) - 8, int(len(by_game) * 0.65)))
    tuning = set(by_game[:pivot])
    a, b = [], []
    for index, row in enumerate(rows):
        (a if row["game_sha256"] in tuning else b).append(index)
    if len({rows[i]["game_sha256"] for i in b}) < 8:
        raise ValueError("Insufficient game-disjoint gate audit")
    return a, b


def _game_macro_gain(rows, predictions, rodent) -> float:
    values = defaultdict(list)
    overrides = 0
    for row, ours, baseline in zip(rows, predictions, rodent):
        values[row["game_sha256"]].append(
            int(ours == row["move"]) - int(baseline == row["move"]))
        overrides += int(ours != baseline)
    if not values:
        raise ValueError("No real gate fitting positions")
    return (sum(sum(v)/len(v) for v in values.values()) / len(values)
            - INTERVENTION_PENALTY * overrides / len(rows))


def fit_gate(cohorts: Path, v3_path: Path, rodent_root: Path,
             out: Path, *, per_player: int = 128,
             per_game: int = 2, seed: int = 2028,
             depth: int = 2, seconds: float = 0.12) -> dict:
    """Train threshold using only unused real TRAIN-arm games.

    No V3 training example, training game, or FEN enters gate calibration.
    A distinct, pre-designated game cluster audits the threshold. The audit
    is acceptance evidence for this narrow gate, not final test evidence.
    """
    model = _read_model(v3_path, V3_SCHEMA)
    sampled, split_info = _calibration_samples(
        cohorts, model, per_player=per_player, per_game=per_game, seed=seed)
    slugs = sorted(sampled)
    flat = [row for slug in slugs for row in sampled[slug]]
    engines, hashes = _rodent_predictions(
        rodent_root, flat, slugs, depth=depth, time_per_position=seconds)
    players = {}
    offset = 0
    for slug in slugs:
        rows = sampled[slug]
        rodent_moves = engines[slug][offset:offset + len(rows)]
        offset += len(rows)
        proposals, lifts = [], []
        for row, engine_move in zip(rows, rodent_moves):
            move, lift = _style_lift_and_proposal(
                model, chess.Board(row["fen"]), slug,
                "overall", engine_move)
            proposals.append(move)
            lifts.append(lift)
        tune_ids, audit_ids = _split_games(rows, seed=seed, slug=slug)

        def scoped(indices, threshold):
            rs = [rows[i] for i in indices]
            bs = [rodent_moves[i] for i in indices]
            ps = [_choose(rodent_moves[i], proposals[i], lifts[i], threshold)
                  for i in indices]
            return rs, ps, bs

        # The baseline is a candidate (None) with zero intervention penalty;
        # any intervention needs measured positive historical agreement.
        scores = {}
        for threshold in (None, *THRESHOLDS):
            rs, ours, original = scoped(tune_ids, threshold)
            scores[str(threshold)] = round(_game_macro_gain(rs, ours, original), 8)
        # Only positive alternatives can be proposed. Tie -> fewer overrides,
        # then larger threshold (conservative). No validation data seen.
        candidates = [threshold for threshold in THRESHOLDS
                      if scores[str(threshold)] > 0]
        suggested = (max(candidates, key=lambda t: (scores[str(t)], t))
                     if candidates else None)
        audit_rows, audit_moves, audit_rodent = scoped(audit_ids, suggested)
        audit = _paired_counts(audit_rows, audit_moves, audit_rodent)
        audit_interventions = sum(a != b for a, b in zip(audit_moves, audit_rodent))
        accepted = bool(suggested is not None and audit_interventions > 0 and
                        audit["bootstrap_95_ci"][0] > 0.0)
        selected = suggested if accepted else None
        full = [_choose(b, p, lift, selected)
                for b, p, lift in zip(rodent_moves, proposals, lifts)]
        players[slug] = {
            "threshold": selected,
            "tuning_proposed_threshold": suggested,
            "tuning_penalized_gain_by_threshold": scores,
            "tuning_games": len({rows[i]["game_sha256"] for i in tune_ids}),
            "audit_games": audit["games"],
            "audit_positions": audit["positions"],
            "audit_game_cluster_statistics": audit,
            "audit_overrides_for_proposal": audit_interventions,
            "decision_rule": (
                "ACCEPT_AUDIT_LOWER_BOUND_POSITIVE" if accepted
                else "DENY_NOT_REPLICATED_ON_GAME_DISJOINT_AUDIT"),
            "calibration_positions": len(rows),
            "calibration_games": len({r["game_sha256"] for r in rows}),
            "calibration_overrides_if_enabled": sum(
                x != y for x, y in zip(full, rodent_moves)),
            "calibration_exact_match": {
                "gate": sum(a == r["move"] for a, r in zip(full, rows)),
                "rodent": sum(a == r["move"] for a, r in zip(rodent_moves, rows)),
            },
        }
    result = {
        "schema": GATE_SCHEMA,
        "status": GATE_STATUS,
        "method": "GAME_DISJOINT_STYLE_LIFT_THRESHOLD_WITH_SEPARATE_AUDIT",
        "base_model_sha256": model["model_sha256"],
        "source_pgn_sha256": model["source_pgn_sha256"],
        "train_manifest_sha256": model["train_manifest_sha256"],
        "engine_binary_sha256": hashes["exe"],
        "engine_profile_sha256": {slug: hashes[slug] for slug in slugs},
        "rodent_generic_profile_sha256": hashes["simple_profile"],
        "engine_depth": depth, "engine_seconds_per_position": seconds,
        "calibrated_policy_era": "overall",
        "rodent_opening_books": False,
        "seed": seed, "per_player": per_player, "per_game": per_game,
        "threshold_grid": list(THRESHOLDS),
        "override_penalty": INTERVENTION_PENALTY,
        "calibration": split_info,
        "players": players,
        "default_action": "RODENT_BESTMOVE",
        "engine_weights_changed": False,
        "training_arm": "train_only_out_of_fit_games",
        "final_holdout_opened": False,
        "style_identity_validated": False,
        "limitations": [
            "Gate acceptance uses training-arm audit, not independent final held-out tests.",
            "V3 base weights already fitted on the other training games; splits reconstructed exactly.",
            "The same historical corpus drives multiple research iterations; inference is exploratory.",
            "Only historical top-1 imitation was optimized; tactical safety and Elo are not validated.",
            "UCI bestmoves depend on engine binary, depth, time, hash and personality.",
            "A None threshold means fail-closed back to unmodified Rodent bestmove.",
        ],
    }
    result["gate_sha256"] = _digest(result)
    _write_once(out, result)
    return result


def load_gate(path: Path, model: dict, *,
              engine_binary_sha256: str | None = None,
              profile_sha256: dict | None = None) -> dict:
    """Validate signed decision-gate artifact. Optional exact binary check."""
    gate = _read_json(path, 8_000_000)
    digest = gate.pop("gate_sha256", None)
    if (gate.get("schema") != GATE_SCHEMA or
            gate.get("status") != GATE_STATUS or
            not isinstance(digest, str) or digest != _digest(gate)):
        raise ValueError("Tampered or unsupported real Rodent gate artifact")
    gate["gate_sha256"] = digest
    if (model.get("schema") != V3_SCHEMA or
            model.get("model_sha256") != gate["base_model_sha256"] or
            model.get("train_manifest_sha256") != gate["train_manifest_sha256"] or
            model.get("source_pgn_sha256") != gate["source_pgn_sha256"]):
        raise ValueError("Gate must be attached to its exact real V3 model")
    if (gate.get("calibrated_policy_era") != "overall" or
            gate.get("training_arm") != "train_only_out_of_fit_games" or
            gate.get("final_holdout_opened") is not False or
            gate.get("engine_weights_changed") is not False or
            gate.get("style_identity_validated") is not False):
        raise ValueError("Unsupported gate provenance/claims")
    if (gate.get("default_action") != "RODENT_BESTMOVE" or
            gate.get("threshold_grid") != list(THRESHOLDS) or
            gate.get("override_penalty") != INTERVENTION_PENALTY):
        raise ValueError("Unsupported gate selection contract")
    if (not isinstance(gate.get("players"), dict) or
            set(gate["players"]) != set(model["profiles"])):
        raise ValueError("Gate roster differs from fitted V3")
    for slug, p in gate["players"].items():
        threshold = p.get("threshold")
        if threshold is not None and (threshold not in THRESHOLDS or
                                      p.get("decision_rule") !=
                                      "ACCEPT_AUDIT_LOWER_BOUND_POSITIVE" or
                                      p["audit_game_cluster_statistics"]["bootstrap_95_ci"][0] <= 0):
            raise ValueError("Gate has an unjustified active threshold: " + slug)
        if threshold is None and p.get("decision_rule") != "DENY_NOT_REPLICATED_ON_GAME_DISJOINT_AUDIT":
            raise ValueError("Gate denies without expected reason")
    if engine_binary_sha256 is not None and gate["engine_binary_sha256"] != engine_binary_sha256:
        raise ValueError("Rodent binary differs from the calibrated gate")
    if profile_sha256 is not None:
        for slug, digest in profile_sha256.items():
            if gate["engine_profile_sha256"].get(slug) != digest:
                raise ValueError("Rodent personality differs from calibrated gate: " + slug)
    return gate


def gate_move(gate: dict, v3: dict, board: chess.Board, slug: str,
              era: str, rodent_uci: str) -> tuple[str, dict]:
    """Pure inference, no mutations and no online adjustment."""
    if slug not in gate["players"]:
        raise ValueError("Gate does not cover current player")
    move, lift = _style_lift_and_proposal(v3, board, slug, era, rodent_uci)
    threshold = gate["players"][slug]["threshold"]
    played = _choose(rodent_uci, move, lift, threshold)
    return played, {
        "proposal": move, "style_lift": round(lift, 8),
        "gate_threshold": threshold, "engine_bestmove": rodent_uci,
        "decision": ("EXTERNAL_GATE_OVERRIDE" if played != rodent_uci
                     else "GATE_RETAINED_RODENT"),
    }


def evaluate_gate(cohorts: Path, v3_path: Path, gate_path: Path,
                  out: Path, *, archive_dir: Path | None = None,
                  seed: int = 2027, per_player: int = 120,
                  per_game: int = 2, rodent_root: Path,
                  depth: int = 2, seconds: float = 0.12,
                  repetitions: int = 1500,
                  curve_models: dict[int, Path] | None = None) -> dict:
    model = _read_model(v3_path, V3_SCHEMA)
    gate = load_gate(gate_path, model)
    _assert_training_manifest(cohorts, model)
    if depth != gate["engine_depth"] or seconds != gate["engine_seconds_per_position"]:
        raise ValueError("UCI search settings differ from calibrated gate")
    samples, excluded, hashes = _read_eval_positions(
        cohorts, model, arm="validation", archive_dir=archive_dir,
        seed=seed, per_player=per_player, per_game=per_game)
    slugs = sorted(samples)
    all_rows = [row for slug in slugs for row in samples[slug]]
    engine, digests = _rodent_predictions(
        rodent_root, all_rows, slugs, depth=depth, time_per_position=seconds)
    load_gate(gate_path, model, engine_binary_sha256=digests["exe"],
              profile_sha256={s: digests[s] for s in slugs})
    extra_models = {}
    for sample_size, path in sorted((curve_models or {}).items()):
        if (type(sample_size) is not int or sample_size < 20 or
                sample_size >= model["max_per_player"]):
            raise ValueError("Curve sizes must be smaller than the gate V3 training size")
        item = _read_model(path, V3_SCHEMA)
        _assert_training_manifest(cohorts, item)
        if (item["max_per_player"] != sample_size or
                item["max_per_game"] != model["max_per_game"] or
                item["seed"] != model["seed"] or
                item["source_pgn_sha256"] != model["source_pgn_sha256"]):
            raise ValueError("Learning curve models are not from matched historical samples")
        extra_models[sample_size] = item
    result_players = {}
    all_curve = defaultdict(lambda: Counter())
    offset = 0
    for index, slug in enumerate(slugs):
        rows = samples[slug]
        base = engine[slug][offset:offset + len(rows)]
        generic_engine = engine["generic_rodent"][offset:offset + len(rows)]
        offset += len(rows)
        proposals = []
        gate_moves = []
        names = []
        generic_v3 = []
        for row, original in zip(rows, base):
            board = chess.Board(row["fen"])
            custom, audit = gate_move(
                gate, model, board, slug, "overall", original)
            gate_moves.append(custom)
            proposals.append(audit)
            names.append(audit["proposal"])
            generic_v3.append(predict_contextual(
                model, board, slug, "overall", generic=True))
        curves = {}
        for size, curve in [*sorted(extra_models.items()),
                            (model["max_per_player"], model)]:
            preds = [
                predict_contextual(curve, chess.Board(row["fen"]),
                                   slug, "overall")
                for row in rows]
            curves[str(size)] = _metric(rows, preds)
            all_curve[str(size)]["right"] += curves[str(size)]["exact_match"]
            all_curve[str(size)]["total"] += len(rows)
        paired = {
            "gate_vs_rodent": paired_game_inference(
                rows, gate_moves, base,
                seed=seed + index, repetitions=repetitions),
            "v3_vs_rodent": paired_game_inference(
                rows, names, base,
                seed=seed + 100 + index, repetitions=repetitions),
            "gate_vs_v3": paired_game_inference(
                rows, gate_moves, names,
                seed=seed + 200 + index, repetitions=repetitions),
            "v3_named_vs_generic": paired_game_inference(
                rows, names, generic_v3,
                seed=seed + 300 + index, repetitions=repetitions),
        }
        band = defaultdict(Counter)
        for row, ours, v3_move, engine_move in zip(rows, gate_moves, names, base):
            counts = band[row["career_band"]]
            counts["decisions"] += 1
            counts["gate_match"] += int(ours == row["move"])
            counts["v3_match"] += int(v3_move == row["move"])
            counts["rodent_match"] += int(engine_move == row["move"])
        result_players[slug] = {
            "validation_games": len({r["game_sha256"] for r in rows}),
            "validation_decisions": len(rows),
            "gate_threshold": gate["players"][slug]["threshold"],
            "gate_audit_decision": gate["players"][slug]["decision_rule"],
            "gate_overrides": sum(a != b for a, b in zip(gate_moves, base)),
            "measured": {
                "gate": _metric(rows, gate_moves),
                "v3_named": _metric(rows, names),
                "v3_generic": _metric(rows, generic_v3),
                "rodent_named": _metric(rows, base),
                "rodent_simple": _metric(rows, generic_engine),
            },
            "paired_game_statistics": paired,
            "learning_curve": curves,
            "by_career_band": {k: dict(v) for k, v in sorted(band.items())},
            "identity_validated": False,
        }
    report = {
        "schema": REPORT_SCHEMA,
        "status": "REAL_HISTORIC_GAME_DISJOINT_GATE_EXPLORATORY",
        "validation_arm": "validation", "final_holdout_opened": False,
        "v3_sha256": model["model_sha256"], "gate_sha256": gate["gate_sha256"],
        "source_pgn_sha256": hashes,
        "engine_sha256": digests["exe"],
        "engine_profile_sha256": {s: digests[s] for s in slugs},
        "rodent_generic_simple_sha256": digests["simple_profile"],
        "actual_rodent_uci": True,
        "training_decisions": model["training_observations_used"],
        "gate_train_arm_only": True,
        "sample_seed": seed, "per_player_limit": per_player,
        "per_game_limit": per_game, "rodent_depth": depth,
        "rodent_seconds_per_position": seconds,
        "calibrated_policy_era": "overall",
        "total_validation_decisions": len(all_rows),
        "excluded_validation": dict(excluded),
        "by_player": result_players,
        "learning_curve": {k: {
            "correct": v["right"], "total": v["total"],
            "top1": v["right"] / v["total"]}
            for k, v in sorted(all_curve.items(), key=lambda x: int(x[0]))},
        "promotion": "DENIED_NO_INDEPENDENT_STYLE_AND_SAFETY_EVIDENCE",
        "caveats": [
            "Validation has been inspected in prior PRs: curves are descriptive and exploratory.",
            "Calibration audits are out-of-fit but share the training source and are not a final holdout.",
            "Comparisons use game-cluster bootstrap, not independent ply-level tests.",
            "Search depth 2 and 0.12 s are smoke settings, not validated tactical safety.",
            "Learning curves have nested bottom-k samples by per-game rank but no full hyperparameter sweep.",
            "Top-1 exact imitation does not establish Elo or historical psychological identity.",
        ],
    }
    _write_once(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real-game-disjoint Rodent/V3 learned override with a safely declining gate")
    sub = parser.add_subparsers(dest="action", required=True)
    fit = sub.add_parser("fit")
    fit.add_argument("--cohorts", type=Path, required=True)
    fit.add_argument("--model", type=Path, required=True)
    fit.add_argument("--rodent-root", type=Path, required=True)
    fit.add_argument("--out", type=Path, required=True)
    fit.add_argument("--per-player", type=int, default=128)
    fit.add_argument("--per-game", type=int, default=2)
    fit.add_argument("--seed", type=int, default=2028)
    fit.add_argument("--rodent-depth", type=int, default=2)
    fit.add_argument("--rodent-seconds", type=float, default=0.12)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--cohorts", type=Path, required=True)
    ev.add_argument("--model", type=Path, required=True)
    ev.add_argument("--gate", type=Path, required=True)
    ev.add_argument("--rodent-root", type=Path, required=True)
    ev.add_argument("--out", type=Path, required=True)
    source = ev.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    ev.add_argument("--curve", action="append", default=[], metavar="SIZE:MODEL_JSON")
    ev.add_argument("--per-player", type=int, default=120)
    ev.add_argument("--per-game", type=int, default=2)
    ev.add_argument("--seed", type=int, default=2027)
    ev.add_argument("--rodent-depth", type=int, default=2)
    ev.add_argument("--rodent-seconds", type=float, default=0.12)
    ev.add_argument("--repetitions", type=int, default=1500)
    args = parser.parse_args(argv)
    if args.action == "fit":
        result = fit_gate(
            args.cohorts, args.model, args.rodent_root, args.out,
            per_player=args.per_player, per_game=args.per_game,
            seed=args.seed, depth=args.rodent_depth, seconds=args.rodent_seconds)
        info = {"status": result["status"], "gate_sha256": result["gate_sha256"],
                "acceptance": {slug: p["decision_rule"] for slug, p in result["players"].items()}}
    else:
        curves = {}
        for item in args.curve:
            match = re.fullmatch(r"([0-9]+):(.+)", item)
            if not match:
                raise ValueError("Expected --curve SIZE:MODEL_JSON")
            size = int(match[1])
            if size in curves:
                raise ValueError("Repeated learning-curve size")
            curves[size] = Path(match[2])
        result = evaluate_gate(
            args.cohorts, args.model, args.gate, args.out,
            archive_dir=args.archive_dir,
            seed=args.seed, per_player=args.per_player, per_game=args.per_game,
            rodent_root=args.rodent_root,
            depth=args.rodent_depth, seconds=args.rodent_seconds,
            repetitions=args.repetitions, curve_models=curves)
        info = {
            "status": result["status"],
            "validation_decisions": result["total_validation_decisions"],
            "learning_curve": result["learning_curve"],
            "promotion": result["promotion"],
            "gate_overrides": {s: v["gate_overrides"] for s, v in result["by_player"].items()},
        }
    print(json.dumps(info, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
