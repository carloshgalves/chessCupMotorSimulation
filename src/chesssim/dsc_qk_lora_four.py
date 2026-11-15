"""Four-player DSC150 frozen-latent Q/K LoRA, with blind 8-move games.

Scientific boundaries: source/labels are TRAIN-year only; whole games and
canonical FEN conflicts are embargoed across FIT/TUNE/AUDIT. All ranks,
seeds and game-window sizes are fixed in issue #161 BEFORE this experiment.
The external DSC base is frozen. No official Rodent/arena integration.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import resource
import time

import chess

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model,
    paired_game_inference,
)
from .dsc_four_player_identity import PLAYERS
from .dsc_qk_lora_core import (
    RANKS, QKLoRA, load_state, log_loss, model_state, predict, restore,
    snapshot, train_epoch,
)
from .maia_transfer_v6 import _restore_real_histories
from .real_gate_v4 import _position_key
from .real_policy_experiment import (
    _digest, _metric, _read_json, _validated_training_rows, _write_once,
)
from .science_multimodel_cpu import (
    DSC_FIDE_SCENARIO, DSC_SOURCE_COMMIT, DSC150_WEIGHTS_SHA256,
    _dsc_preflight,
)

SCHEMA = "chesssim-dsc150-four-qk-lora-sequence-v1"
CHECKPOINT_SCHEMA = "chesssim-dsc150-original-frozen-qk-lora-only-v1"
SEED = 3719
SALT = "chesssim-latent-qk-v1-2026-11-15"
WINDOW = 8
GAMES = {"fit": 48, "tune": 12, "audit": 12}
GENERIC_EPOCHS = 3
PERSONAL_EPOCHS = 4
TUNE_NLL_MINIMUM = 0.005
BOOTSTRAP_REPETITIONS = 2000


def arm_for_game(digest: str) -> str:
    if (not isinstance(digest, str) or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)):
        raise ValueError("Expected exact SHA-256 historical trajectory")
    bucket = int(sha256((SALT + "|" + digest).encode()).hexdigest()[:12], 16) % 10
    return "fit" if bucket < 7 else "tune" if bucket == 7 else "audit"


def _order(*fields: object) -> str:
    return sha256(("|".join(str(f) for f in (SALT, SEED, *fields))).encode()).hexdigest()


def _game_window(game_rows: list[dict]) -> list[dict] | None:
    """One deterministic window of eight consecutive focal-player moves."""
    rows = sorted(game_rows, key=lambda row: row["ply"])
    if not rows:
        return None
    windows = []
    for start in range(len(rows) - WINDOW + 1):
        candidate = rows[start:start + WINDOW]
        if (all(candidate[i + 1]["ply"] - candidate[i]["ply"] == 2
                for i in range(WINDOW - 1))
                and len({_position_key(r["fen"]) for r in candidate}) == WINDOW):
            windows.append(candidate)
    if not windows:
        return None
    return min(windows, key=lambda w: (_order(w[0]["game_sha256"], w[0]["ply"]),
                                      w[0]["ply"]))


def choose_windows(cohorts: Path) -> tuple[dict, dict]:
    """Full-source cross-arm FEN embargo BEFORE sampling game windows."""
    manifest = _read_json(Path(cohorts) / "manifest.json")
    source = {
        slug: {arm: defaultdict(list) for arm in GAMES}
        for slug in PLAYERS
    }
    conflict_bits = defaultdict(int)
    seen = Counter()
    bits = {"fit": 1, "tune": 2, "audit": 4}
    for raw in _validated_training_rows(cohorts, manifest):
        slug = raw["player"]
        if slug not in source:
            raise ValueError("Non-pilot historical player in four-player corpus")
        if raw["ply"] < 21:
            continue
        arm = arm_for_game(raw["game_sha256"])
        fen = raw["policy_input"]["fen"]
        conflict_bits[_position_key(fen)] |= bits[arm]
        row = {
            "player": slug, "fen": fen, "move": raw["policy_target"]["move_uci"],
            "game_sha256": raw["game_sha256"], "ply": raw["ply"],
        }
        source[slug][arm][row["game_sha256"]].append(row)
        seen[(slug, arm)] += 1
    conflicting_fens = {fen for fen, value in conflict_bits.items()
                        if value not in (1, 2, 4)}
    windows = {slug: {} for slug in PLAYERS}
    counts = {}
    all_games = set()
    # Sorting names is part of the protocol for shared Karpov/Kasparov PGNs.
    for slug in sorted(PLAYERS):
        counts[slug] = {}
        for arm, n_games in GAMES.items():
            eligible = []
            for game, rows in source[slug][arm].items():
                filtered = [r for r in rows
                            if _position_key(r["fen"]) not in conflicting_fens]
                window = _game_window(filtered)
                if window is not None:
                    eligible.append((game, window))
            eligible.sort(key=lambda pair: (_order(slug, arm, pair[0]), pair[0]))
            chosen = []
            for game, window in eligible:
                if game in all_games:
                    continue
                chosen.append(window)
                all_games.add(game)
                if len(chosen) == n_games:
                    break
            if len(chosen) != n_games:
                raise ValueError(f"UNDERPOWERED_{slug}_{arm}: "
                                 f"needed {n_games} games, got {len(chosen)}")
            windows[slug][arm] = chosen
            counts[slug][arm] = {
                "source_decisions": seen[(slug, arm)],
                "eligible_game_windows": len(eligible),
                "sampled_games": len(chosen),
                "sampled_decisions": sum(len(window) for window in chosen),
            }
    if len(all_games) != 4 * sum(GAMES.values()):
        raise ValueError("Historical shared-game collision after sample assignment")
    selected_fens = defaultdict(set)
    for slug in PLAYERS:
        for arm, groups in windows[slug].items():
            for group in groups:
                for row in group:
                    selected_fens[_position_key(row["fen"])].add(arm)
    if any(len(arms) > 1 for arms in selected_fens.values()):
        raise ValueError("Canonical historical FEN leaked across experimental arms")
    return windows, {
        "counts": counts,
        "source_canonical_conflicts_embargoed": len(conflicting_fens),
        "total_unique_source_games": len(all_games),
        "selector": "SHA256 frozen game split, game-rank and eight consecutive focal moves",
        "seed": SEED, "window": WINDOW,
    }


def _original_latents(cohorts: Path, v3: dict, windows: dict,
                      archive_dir: Path | None) -> tuple[dict, dict, dict]:
    """Run pinned DSC unchanged, intercept ln_f before original policy Q/K."""
    import torch
    torch.manual_seed(SEED)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    provenance = _dsc_preflight()
    rows = {
        slug: [row for arm in GAMES for group in windows[slug][arm] for row in group]
        for slug in PLAYERS
    }
    histories, digests = _restore_real_histories(cohorts, v3, rows, archive_dir)
    if digests != v3["source_pgn_sha256"]:
        raise ValueError("Original historical PGN snapshot drifted")
    from chessds_dsc.predictor import DSC, PositionInput
    from chessds_dsc.encoding import legal_indices
    dsc = DSC.load("dsc-150m", device="cpu")
    if dsc.mode != "history":
        raise ValueError("Cannot use a FEN-only DSC checkpoint")
    dsc.model.eval()
    for param in dsc.model.parameters():
        param.requires_grad_(False)
    policy = dsc.model.policy
    width = policy.q.in_features
    policy_width = policy.q.out_features
    if (policy.k.in_features != width or policy.k.out_features != policy_width
            or tuple(policy.promo.weight.shape) != (3, policy_width)):
        raise ValueError("Pinned original DSC PolicyHead dimensions changed")
    promo_weight = policy.promo.weight.detach().clone().to("cpu", dtype=torch.float32)
    scale = float(policy.scale)
    cache = {slug: {} for slug in PLAYERS}
    time_start = time.monotonic()
    for slug in PLAYERS:
        for arm in GAMES:
            games = windows[slug][arm]
            examples = []
            for group in games:
                for start in range(0, len(group), 4):
                    batch_rows = group[start:start + 4]
                    boards = [
                        histories[slug][(row["game_sha256"], row["ply"])]
                        for row in batch_rows
                    ]
                    if any(not board.is_valid() or board.fen() != row["fen"]
                           for row, board in zip(batch_rows, boards)):
                        raise ValueError("Original reconstructed history/FEN mismatch")
                    items = [
                        PositionInput(
                            board, white_elo=DSC_FIDE_SCENARIO,
                            black_elo=DSC_FIDE_SCENARIO,
                            time_control="classical", rating_system="fide",
                            use_history=True)
                        for board in boards
                    ]
                    observed = []
                    def capture(_module, _inputs, output):
                        observed.append(output.detach().to("cpu").clone())
                    hook = dsc.model.ln_f.register_forward_hook(capture)
                    try:
                        with torch.no_grad():
                            predictions = dsc.predict_batch(items)
                    finally:
                        hook.remove()
                    if len(observed) != 1 or len(predictions) != len(batch_rows):
                        raise ValueError("Latent hook not aligned to original DSC prediction")
                    latents = observed[0]
                    if tuple(latents.shape) != (len(batch_rows), 64, width):
                        raise ValueError("Unexpected frozen DSC latent shape")
                    with torch.no_grad():
                        q_values = policy.q(latents).detach().cpu()
                        k_values = policy.k(latents).detach().cpu()
                    for index, (row, board, pred) in enumerate(zip(batch_rows, boards, predictions)):
                        if pred.inputs.fen != row["fen"] or pred.inputs.history_boards < 1:
                            raise ValueError("PGN move-stack was not included in DSC model")
                        legal = legal_indices(board)
                        moves = [move for move, _ in legal]
                        if (set(moves) != set(pred.moves)
                                or row["move"] not in moves
                                or abs(sum(pred.moves.values()) - 1.0) > 0.002):
                            raise ValueError("Invalid original full legal-move probability output")
                        scores = [float(pred.moves[uci]) for uci in moves]
                        if any(not math.isfinite(p) or p <= 0 for p in scores):
                            raise ValueError("DSC original emitted invalid legal-move probability")
                        examples.append({
                            "latent": latents[index].detach().half().clone(),
                            "q0": q_values[index].detach().half().clone(),
                            "k0": k_values[index].detach().half().clone(),
                            "indices": torch.tensor([idx for _, idx in legal], dtype=torch.long),
                            "prior_logp": torch.tensor([math.log(p) for p in scores], dtype=torch.float32),
                            "moves": moves, "target": moves.index(row["move"]),
                            "game_sha256": row["game_sha256"],
                        })
            if len(examples) != WINDOW * GAMES[arm]:
                raise ValueError("Original PGN/latent count mismatch")
            cache[slug][arm] = examples
    elapsed = round(time.monotonic() - time_start, 3)
    del dsc
    return cache, digests, {
        "width": width, "policy_width": policy_width, "scale": scale,
        "promo_weight": promo_weight, "original_inference_seconds": elapsed,
        "source_code_commit": provenance["code_commit"],
    }


def _best_tune(models: list[tuple[tuple, object]], examples: list[dict],
               promo, scale) -> tuple[tuple, object, float]:
    candidates = []
    for label, model in models:
        nll = log_loss(predict(examples, model, promo, scale))
        candidates.append((nll, label, model))
    nll, label, model = min(candidates, key=lambda entry: (entry[0], entry[1]))
    return label, model, nll


def _metrics(rows: list[dict], predicted: list[dict]) -> dict:
    if len(rows) != len(predicted) or not rows:
        raise ValueError("Unaligned real game evaluation")
    correct = 0
    top3 = 0
    nll = 0.0
    first = []
    for row, forecast in zip(rows, predicted):
        if row["move"] != forecast["moves"][forecast["target"]]:
            raise ValueError("Historical true move changed before scoring")
        ranks = sorted(range(len(forecast["moves"])),
                       key=lambda i: (-forecast["probs"][i], i))
        first_move = forecast["moves"][ranks[0]]
        first.append(first_move)
        correct += first_move == row["move"]
        top3 += forecast["target"] in ranks[:3]
        nll -= math.log(max(forecast["probs"][forecast["target"]], 1e-40))
    out = _metric(rows, first)
    out.update({
        "top1_correct": correct, "top3_correct": top3,
        "top3": top3 / len(rows), "mean_log_loss_nats": nll / len(rows),
    })
    return out


def _blind(rows: list[dict], scores_by_identity: dict[str, list[dict]],
           windows: list[list[dict]], seed: int) -> dict:
    """Evaluate game identity from 8 true-move likelihoods; no player metadata as input."""
    if set(scores_by_identity) != set(PLAYERS):
        raise ValueError("Four-name blind test requires four frozen candidates")
    total = sum(len(g) for g in windows)
    if len(rows) != total or any(len(g) != WINDOW for g in windows):
        raise ValueError("8-move blind game windows not aligned")
    if any(len(pred) != total for pred in scores_by_identity.values()):
        raise ValueError("Incomplete identical-board blind controls")
    matrix = {slug: {other: 0 for other in PLAYERS} for slug in PLAYERS}
    move_matrix = {slug: {other: 0 for other in PLAYERS} for slug in PLAYERS}
    observed = []
    cursor = 0
    for game in windows:
        truths = game
        label = truths[0]["player"]
        if label not in PLAYERS or len({r["player"] for r in game}) != 1:
            raise ValueError("Mixed human player in a blind game window")
        likelihoods = {}
        for candidate in PLAYERS:
            preds = scores_by_identity[candidate][cursor:cursor + WINDOW]
            likelihoods[candidate] = math.fsum(
                math.log(max(pred["probs"][pred["target"]], 1e-40))
                for pred in preds) / WINDOW
        guessed = min(PLAYERS, key=lambda p: (-likelihoods[p], p))
        matrix[label][guessed] += 1
        for i in range(WINDOW):
            move_guess = min(
                PLAYERS,
                key=lambda p: (-scores_by_identity[p][cursor+i]["probs"][
                    scores_by_identity[p][cursor+i]["target"]], p))
            move_matrix[label][move_guess] += 1
        observed.append((label, guessed, truths[0]["game_sha256"]))
        cursor += WINDOW
    if len({game for _, _, game in observed}) != len(observed):
        raise ValueError("Repeated original game in blind confidence interval")
    by_player = {p: [(label, pred) for label, pred, _ in observed if label == p]
                 for p in PLAYERS}
    count = {slug: len(by_player[slug]) for slug in PLAYERS}
    if any(v != GAMES["audit"] for v in count.values()):
        raise ValueError("Underpowered or unbalanced four-way game blind test")
    balanced = sum(matrix[p][p] / count[p] for p in PLAYERS) / 4
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP_REPETITIONS):
        value = 0.0
        for slug in PLAYERS:
            group = by_player[slug]
            value += sum(group[rng.randrange(len(group))][1] == slug
                         for _j in group) / len(group) / 4
        draws.append(value)
    draws.sort()
    # Permute hidden truth labels while preserving exactly 12 games/player.
    perm_rng = random.Random(seed + 1)
    labels = [label for label, _, _ in observed]
    guesses = [pred for _, pred, _ in observed]
    more_extreme = 0
    for _ in range(BOOTSTRAP_REPETITIONS):
        shuffled = labels[:]
        perm_rng.shuffle(shuffled)
        statistic = sum(sum(int(a == b) for a, b in zip(shuffled, guesses)
                            if a == slug) / count[slug] for slug in PLAYERS) / 4
        more_extreme += statistic >= balanced - 1e-12
    return {
        "sequence_length": WINDOW, "games": len(observed),
        "decisions": total, "coverage": 1.0,
        "sequence_correct": sum(matrix[p][p] for p in PLAYERS),
        "sequence_balanced_accuracy": balanced,
        "sequence_balanced_game_bootstrap_95_ci": [
            draws[int(0.025 * len(draws))],
            draws[min(len(draws)-1, math.ceil(0.975 * len(draws))-1)],
        ],
        "sequence_game_permutation_one_sided_p":
            (more_extreme + 1) / (BOOTSTRAP_REPETITIONS + 1),
        "sequence_confusion_matrix": matrix,
        "single_move_correct": sum(move_matrix[p][p] for p in PLAYERS),
        "single_move_balanced_accuracy":
            sum(move_matrix[p][p] / (count[p] * WINDOW) for p in PLAYERS) / 4,
        "single_move_confusion_matrix": move_matrix,
        "nominal_random_chance": 0.25,
        "majority_game_baseline": max(count.values()) / len(observed),
        "negative_control_rotated_identity_accuracy":
            sum(matrix[slug][PLAYERS[(PLAYERS.index(slug)+1)%4]]
                for slug in PLAYERS) / len(observed),
        "note": "Historic move is used only to score blind likelihood; not to fit AUDIT weights",
    }


def run(cohorts: Path, v3_path: Path, model_out: Path, report_out: Path, *,
        archive_dir: Path | None) -> dict:
    import torch
    torch.manual_seed(SEED)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    v3 = _read_model(v3_path, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    if set(v3["profiles"]) != set(PLAYERS):
        raise ValueError("Only the four registered historical players may be modeled")
    windows, partition = choose_windows(cohorts)
    cache, source, frozen = _original_latents(cohorts, v3, windows, archive_dir)
    promo = frozen.pop("promo_weight")
    scale = frozen["scale"]
    width, policy_width = frozen["width"], frozen["policy_width"]
    all_fit = [ex for slug in PLAYERS for ex in cache[slug]["fit"]]
    all_tune = [ex for slug in PLAYERS for ex in cache[slug]["tune"]]
    generic_by_rank = {}
    generic_choices = []
    start = time.monotonic()
    for rank in RANKS:
        torch.manual_seed(SEED + rank)
        model = QKLoRA(width, policy_width, rank)
        best_state, best_score, best_epoch = None, math.inf, None
        for epoch in range(1, GENERIC_EPOCHS + 1):
            train_epoch(model, all_fit, promo, scale, seed=SEED+rank+epoch)
            score = log_loss(predict(all_tune, model, promo, scale))
            if score < best_score:
                best_score, best_epoch, best_state = score, epoch, snapshot(model)
        generic_by_rank[rank] = restore(width, policy_width, rank, best_state)
        generic_choices.append(((rank, best_epoch), generic_by_rank[rank]))
    generic_label, generic, _ = _best_tune(generic_choices, all_tune, promo, scale)
    named = {}
    choices = {}
    for index, slug in enumerate(PLAYERS):
        selections = []
        tune = cache[slug]["tune"]
        ref_loss = log_loss(predict(tune, generic, promo, scale))
        for rank in RANKS:
            base = generic_by_rank[rank]
            candidate = restore(width, policy_width, rank, snapshot(base))
            best_state, best_loss, best_epoch = None, math.inf, None
            for epoch in range(1, PERSONAL_EPOCHS + 1):
                train_epoch(candidate, cache[slug]["fit"], promo, scale,
                            seed=SEED+index*100+rank*10+epoch, generic=base)
                value = log_loss(predict(tune, candidate, promo, scale))
                if value < best_loss:
                    best_state, best_loss, best_epoch = snapshot(candidate), value, epoch
            selections.append((best_loss, rank, best_epoch,
                               restore(width, policy_width, rank, best_state)))
        selected_loss, selected_rank, selected_epoch, selected_model = min(
            selections, key=lambda z: (z[0], z[1], z[2]))
        enabled = selected_loss <= ref_loss - TUNE_NLL_MINIMUM
        named[slug] = selected_model if enabled else generic
        choices[slug] = {
            "fit_decisions": len(cache[slug]["fit"]),
            "tune_decisions": len(tune),
            "tune_selected_rank": selected_rank if enabled else None,
            "tune_selected_epoch": selected_epoch if enabled else None,
            "tune_nll_generic": ref_loss,
            "tune_nll_candidate": selected_loss,
            "tune_activation_threshold_nats": TUNE_NLL_MINIMUM,
            "personal_head_activated_on_tune_only": enabled,
        }
    training_time = round(time.monotonic() - start, 3)
    output_models = {"generic": {"rank": generic.rank, "epoch": generic_label[1],
                                 "parameters": model_state(generic)}}
    for slug in PLAYERS:
        output_models[slug] = (
            {"rank": named[slug].rank, "parameters": model_state(named[slug])}
            if choices[slug]["personal_head_activated_on_tune_only"] else
            {"rank": None, "parameters": None, "uses_generic": True}
        )
    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "status": "RESEARCH_ONLY_NOT_APPROVED",
        "base_model": "dsc-150m",
        "base_weights_sha256": DSC150_WEIGHTS_SHA256,
        "base_code_commit": DSC_SOURCE_COMMIT,
        "training_manifest_sha256": v3["train_manifest_sha256"],
        "source_pgn_sha256": source,
        "seed": SEED,
        "training_program": {"ranks": list(RANKS), "generic_epochs": GENERIC_EPOCHS,
                             "personal_epochs": PERSONAL_EPOCHS},
        "tune_selected": choices, "lora_only": output_models,
        "holdout_opened": False, "official_promotion": "DENIED",
    }
    checkpoint["checkpoint_sha256"] = _digest(checkpoint)
    _write_once(model_out, checkpoint)

    # AUDIT is first accessed for scoring only after rank/epoch are frozen.
    flat_rows = []
    flat_cache = []
    flat_games = []
    for slug in sorted(PLAYERS):
        groups = windows[slug]["audit"]
        examples = cache[slug]["audit"]
        assert len(examples) == len(groups)*WINDOW
        for index, group in enumerate(groups):
            flat_games.append(group)
            flat_rows.extend(group)
            flat_cache.extend(examples[index*WINDOW:(index+1)*WINDOW])
    baseline = predict(flat_cache, None, promo, scale)
    generic_pred = predict(flat_cache, generic, promo, scale)
    identity_predictions = {
        slug: predict(flat_cache, named[slug], promo, scale)
        for slug in PLAYERS
    }
    blind = _blind(flat_rows, identity_predictions, flat_games, SEED+1000)
    by_player = {}
    for index, slug in enumerate(sorted(PLAYERS)):
        positions = [i for i, row in enumerate(flat_rows) if row["player"] == slug]
        rows = [flat_rows[i] for i in positions]
        original = [baseline[i] for i in positions]
        common = [generic_pred[i] for i in positions]
        own = [identity_predictions[slug][i] for i in positions]
        wrong = PLAYERS[(PLAYERS.index(slug)+1) % 4]
        swapped = [identity_predictions[wrong][i] for i in positions]
        own_moves = [pred["moves"][max(range(len(pred["moves"])),
                                   key=lambda j: pred["probs"][j])]
                     for pred in own]
        generic_moves = [pred["moves"][max(range(len(pred["moves"])),
                                       key=lambda j: pred["probs"][j])]
                         for pred in common]
        wrong_moves = [pred["moves"][max(range(len(pred["moves"])),
                                     key=lambda j: pred["probs"][j])]
                       for pred in swapped]
        by_player[slug] = {
            **choices[slug], "audit_games": len(set(row["game_sha256"] for row in rows)),
            "audit_decisions": len(rows),
            "original": _metrics(rows, original),
            "generic": _metrics(rows, common),
            "correct": _metrics(rows, own),
            "swapped": _metrics(rows, swapped),
            "swapped_from": wrong,
            "correct_minus_generic": paired_game_inference(
                rows, own_moves, generic_moves, seed=SEED+201+index,
                repetitions=BOOTSTRAP_REPETITIONS),
            "correct_minus_swapped": paired_game_inference(
                rows, own_moves, wrong_moves, seed=SEED+301+index,
                repetitions=BOOTSTRAP_REPETITIONS),
            "official_personality": "DENIED",
        }
    low = blind["sequence_balanced_game_bootstrap_95_ci"][0]
    evidence = low > 0.25 and blind["sequence_game_permutation_one_sided_p"] <= 0.05
    evidence = evidence and all(
        by_player[slug]["correct"]["mean_log_loss_nats"]
        < by_player[slug]["generic"]["mean_log_loss_nats"]
        and by_player[slug]["correct"]["top1"]
        >= by_player[slug]["generic"]["top1"] - 0.02
        and by_player[slug]["correct_minus_swapped"][
            "paired_sign_flip_two_sided_p"] <= 0.0125
        for slug in PLAYERS
    )
    report = {
        "schema": SCHEMA,
        "status": "TRAIN_YEAR_DEVELOPMENT_NOT_FINAL_CONFIRMATION",
        "players": by_player,
        "blind_sequence": blind,
        "overall_original": _metrics(flat_rows, baseline),
        "overall_generic": _metrics(flat_rows, generic_pred),
        "overall_correct": _metrics(
            flat_rows, [identity_predictions[row["player"]][i]
                        for i, row in enumerate(flat_rows)]),
        "source_split": partition,
        "original_backbone": {"weights_sha256": DSC150_WEIGHTS_SHA256,
                              "code_commit": DSC_SOURCE_COMMIT,
                              "scenario": "FIDE_2400_CLASSICAL"},
        "head_checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "training_seconds": training_time,
        "original_inference_seconds": frozen["original_inference_seconds"],
        "peak_python_process_rss_kib_linux": resource.getrusage(
            resource.RUSAGE_SELF).ru_maxrss,
        "research_identity_evidence": "CANDIDATE_ONLY" if evidence else "DENIED",
        "official_promotion": "DENIED",
        "holdout_opened": False, "validation_opened": False,
        "external_pretraining_decontaminated": False,
        "limitations": [
            "TRAIN-year disjoint audit does not substitute an independent historical final holdout",
            "External DSC pretraining may include historical PGNs",
            "The DSC input uses fixed 2400 FIDE/classical scenarios, not actual recorded ratings",
            "The low-rank Q/K adapter is a style hypothesis, not a certified human chess personality",
            "No independent Stockfish safety acceptance or tournament integration was performed",
        ],
    }
    report["report_sha256"] = _digest(report)
    _write_once(report_out, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Genuine DSC frozen Q/K LoRA with four-player blind sequences")
    for name in ("cohorts", "v3-model", "model-out", "report-out"):
        parser.add_argument("--" + name, required=True, type=Path)
    paths = parser.add_mutually_exclusive_group(required=True)
    paths.add_argument("--fetch", action="store_true")
    paths.add_argument("--archive-dir", type=Path)
    args = parser.parse_args(argv)
    report = run(args.cohorts, args.v3_model, args.model_out, args.report_out,
                 archive_dir=args.archive_dir)
    print(json.dumps({
        "status": report["status"],
        "blind_sequence": report["blind_sequence"],
        "head_hash": report["head_checkpoint_sha256"],
        "research_identity": report["research_identity_evidence"],
        "official_promotion": report["official_promotion"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
