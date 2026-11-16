"""Genuine contrastive Maia3-79M LoRA training for four historical players.

Train Q/K adapters to predict genuine TRAIN moves *and* discriminate their
author from three other real-player adapters on exactly the same positions.
All embeddings/priors derive from the pinned original frozen Maia checkpoint.
Nothing reads validation/final holdout, mutates Rodent, or claims promotion.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random

import torch
import torch.nn.functional as F

from . import maia79_neural_four as base
from .contextual_policy_v3 import SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model
from .dsc_qk_lora_core import QKLoRA, model_state
from .maia_transfer_v6 import _restore_real_histories, _verify_maia_source_code
from .maia_personalization_v7 import (
    MAIA79_CHECKPOINT_SHA256, MAIA79_FILE, MAIA79_HF_REVISION, MAIA79_REPO,
    _verify_checkpoint,
)
from .real_policy_experiment import _digest, _write_once

SCHEMA = "chesssim-maia79-four-contrastive-qk-v1"
REPORT_SCHEMA = "chesssim-maia79-four-contrastive-identity-report-v1"
PLAYERS = base.PLAYERS
SEED = base.SEED
RANK = base.RANK
ELO = base.ELO
GENERIC_EPOCHS = 2
PERSONAL_EPOCHS = 3
BATCH_PER_PLAYER = 2
IDENTITY_WEIGHT = 0.7
MOVE_WEIGHT = 1.0
KL_WEIGHT = 0.05
GROUP_WEIGHT = 0.0005
LR = 0.001
PERMUTATIONS = 2000
SWAP = {slug: PLAYERS[(i+1) % len(PLAYERS)] for i, slug in enumerate(PLAYERS)}


def _target_logprob(examples: list[dict], adapter: QKLoRA | None,
                    promo: torch.Tensor) -> list[float]:
    """Log P(genuine move | same original position) for one candidate profile."""
    result: list[float] = []
    if adapter is not None:
        adapter.eval()
    with torch.no_grad():
        for start in range(0, len(examples), 8):
            batch = base._pack(examples[start:start+8])
            logits = base._adapted_logits(batch, adapter, promo)
            values = F.log_softmax(logits, dim=-1)
            result.extend(values.gather(1, batch["target"][:, None]).flatten().tolist())
    if len(result) != len(examples):
        raise ValueError("Identity log probabilities misaligned to historical decisions")
    if not all(-1e6 < v <= 1e-5 for v in result):
        raise ValueError("Invalid per-legal-move Maia identity log probability")
    return result


def contrastive_epoch(adapters: dict[str, QKLoRA],
                      examples: dict[str, list[dict]], promo: torch.Tensor,
                      generic: QKLoRA, *, epoch: int) -> dict:
    """Simultaneous 4-way training with real *balanced* FIT positions.

    Per-sample identity target enters *only the loss*. All four adapters see
    the identical frozen Maia board latent + original legal-move prior, so
    there is no direct player-name feature injected into their inference.
    """
    if set(adapters) != set(PLAYERS) or set(examples) != set(PLAYERS):
        raise ValueError("Exactly four historical profiles are required")
    lengths = [len(examples[p]) for p in PLAYERS]
    if min(lengths) == 0 or len(set(lengths)) != 1:
        raise ValueError("Each historical profile must have equal nonzero FIT decisions")
    order = {}
    for p in PLAYERS:
        order[p] = list(range(len(examples[p])))
        random.Random(SEED + epoch + PLAYERS.index(p)*1000).shuffle(order[p])
    for adapter in adapters.values():
        adapter.train()
    generic.eval()
    generic.requires_grad_(False)
    optimizer = torch.optim.AdamW(
        (param for p in PLAYERS for param in adapters[p].parameters()),
        lr=LR, weight_decay=1e-4)
    sums = defaultdict(float)
    steps = 0
    for offset in range(0, lengths[0], BATCH_PER_PLAYER):
        rows = []
        label_ids = []
        for player_id, player in enumerate(PLAYERS):
            for idx in order[player][offset:offset+BATCH_PER_PLAYER]:
                rows.append(examples[player][idx])
                label_ids.append(player_id)
        batch = base._pack(rows)
        labels = torch.tensor(label_ids, dtype=torch.long)
        optimizer.zero_grad(set_to_none=True)
        per_profile_logps = []
        move_kl = batch["prior"].new_tensor(0.)
        base_probs = F.softmax(batch["prior"], dim=-1)
        for i, player in enumerate(PLAYERS):
            logits = base._adapted_logits(batch, adapters[player], promo)
            logprobs = F.log_softmax(logits, dim=-1)
            chosen_logp = logprobs.gather(1, batch["target"][:, None]).squeeze(1)
            per_profile_logps.append(chosen_logp)
            owns = labels == i
            move_kl = move_kl + F.kl_div(
                logprobs[owns], base_probs[owns], reduction="batchmean")
        candidate_logp = torch.stack(per_profile_logps, dim=1)
        own_logp = candidate_logp.gather(1, labels[:, None]).squeeze(1)
        move_ce = -own_logp.mean()
        # Add same-row class centering for numerical stability; no player info
        # enters any adapter except through the supervised training objective.
        centered = candidate_logp - candidate_logp.mean(dim=1, keepdim=True)
        identity_ce = F.cross_entropy(centered, labels)
        group = sum(
            (a-g.detach()).square().mean()
            for p in PLAYERS
            for a, g in zip(adapters[p].parameters(), generic.parameters())
        )
        loss = (MOVE_WEIGHT * move_ce
                + IDENTITY_WEIGHT * identity_ce
                + KL_WEIGHT * move_kl/len(PLAYERS)
                + GROUP_WEIGHT * group)
        if not bool(torch.isfinite(loss)):
            raise ValueError("Nonfinite historical contrastive gradient loss")
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            [param for p in PLAYERS for param in adapters[p].parameters()], 1.0)
        optimizer.step()
        sums["total"] += float(loss.detach())
        sums["move_ce"] += float(move_ce.detach())
        sums["identity_ce"] += float(identity_ce.detach())
        sums["move_kl"] += float(move_kl.detach())
        steps += 1
    if steps == 0:
        raise ValueError("Zero contrastive optimizer steps")
    return {k: round(v / steps, 6) for k, v in sorted(sums.items())} | {"steps": steps}


def _game_blind(rows: dict[str, list[dict]],
                scores: dict[str, dict[str, list[float]]], *,
                seed: int = SEED, permutations: int = PERMUTATIONS) -> dict:
    """Blind classify each genuine game by mean logp across its moves.

    Four candidate models are scored on EXACTLY the same predecision FEN and
    observed human move. Tie -> abstain, never arbitrary class ordering.
    Randomization permutes complete game labels with balanced class totals.
    """
    if set(rows) != set(PLAYERS) or set(scores) != set(PLAYERS):
        raise ValueError("Blind classification requires exactly four known target cohorts")
    if type(permutations) is not int or permutations < 20 or permutations > 10000:
        raise ValueError("Invalid number of balanced game-label permutations")
    outcomes = []
    seen_games = set()
    confusion = {p: {q: 0 for q in (*PLAYERS, "abstain")} for p in PLAYERS}
    for player in PLAYERS:
        if set(scores[player]) != set(PLAYERS):
            raise ValueError("Missing candidate identity score")
        n = len(rows[player])
        if not n or any(len(scores[player][candidate]) != n for candidate in PLAYERS):
            raise ValueError("Inconsistent per-decision score coverage")
        grouped = defaultdict(list)
        for k, row in enumerate(rows[player]):
            game = row.get("game_sha256")
            if (not isinstance(game, str) or len(game) != 64
                    or any(c not in "0123456789abcdef" for c in game)):
                raise ValueError("Invalid blind game identity")
            grouped[game].append(k)
        for game in sorted(grouped):
            if game in seen_games:
                raise ValueError("Shared game in different player identity classes")
            seen_games.add(game)
            indexes = grouped[game]
            mean = {
                candidate: sum(scores[player][candidate][k] for k in indexes) / len(indexes)
                for candidate in PLAYERS
            }
            if not all(-1e6 < val <= 1e-5 for val in mean.values()):
                raise ValueError("Unbounded or invalid blind probability")
            maximum = max(mean.values())
            winners = [p for p in PLAYERS if maximum - mean[p] < 1e-8]
            guess = winners[0] if len(winners) == 1 else "abstain"
            confusion[player][guess] += 1
            outcomes.append((player, guess))
    totals = {p: sum(confusion[p].values()) for p in PLAYERS}
    if min(totals.values()) == 0:
        raise ValueError("Missing historical games for a blinded identity")
    correct = sum(p == guess for p, guess in outcomes)
    abstain = sum(guess == "abstain" for _, guess in outcomes)
    total = len(outcomes)
    rng = random.Random(seed)
    labels = [p for p, _ in outcomes]
    winners = [guess for _, guess in outcomes]
    exceed = 0
    for _ in range(permutations):
        assigned = labels.copy()
        rng.shuffle(assigned)
        exceed += sum(a == b for a, b in zip(assigned, winners)) >= correct
    return {
        "games": total,
        "correct": correct,
        "accuracy": round(correct/total, 6),
        "abstentions": abstain,
        "coverage": round((total-abstain)/total, 6),
        "per_player_games": totals,
        "confusion": confusion,
        "game_label_permutation_p": round((exceed+1)/(permutations+1), 6),
        "permutations": permutations,
        "chance_four_classes": 0.25,
    }


def _evaluate(cache: dict, adapters: dict[str,QKLoRA],
              generic: QKLoRA, promo: torch.Tensor) -> dict:
    details = {}
    all_blind = {}
    for arm in ("tune", "audit"):
        rows = {p: cache[p][arm] for p in PLAYERS}
        contrast_scores = {}
        ce_scores = {}
        for player in PLAYERS:
            decisions = rows[player]
            contrast_scores[player] = {
                candidate: _target_logprob(decisions, adapters["contrastive"][candidate], promo)
                for candidate in PLAYERS}
            ce_scores[player] = {
                candidate: _target_logprob(decisions, adapters["move_only"][candidate], promo)
                for candidate in PLAYERS}
        all_blind[arm] = {
            "contrastive": _game_blind(rows, contrast_scores, seed=SEED+20),
            "move_only": _game_blind(rows, ce_scores, seed=SEED+20),
            "contrastive_swapped_names": _game_blind(
                rows, {p:{candidate:contrast_scores[p][SWAP[candidate]]
                         for candidate in PLAYERS} for p in PLAYERS}, seed=SEED+20),
        }
        details[arm] = {}
        for p in PLAYERS:
            details[arm][p] = {
                "original": base.score(rows[p], None, promo),
                "generic": base.score(rows[p], generic, promo),
                "move_only_correct": base.score(rows[p], adapters["move_only"][p], promo),
                "contrastive_correct": base.score(rows[p], adapters["contrastive"][p], promo),
                "contrastive_swapped": base.score(rows[p], adapters["contrastive"][SWAP[p]], promo),
                "swapped_source": SWAP[p],
            }
    return {"blinded_identity": all_blind, "move_prediction": details}


def run(cohorts: Path, v3_path: Path, model_out: Path, report_out: Path, *,
        fit_games: int = 128, tune_games: int = 16, audit_games: int = 16,
        moves_per_game: int = 3, archive_dir: Path | None = None) -> dict:
    if model_out == report_out or any(p.exists() or p.is_symlink()
                                     for p in (model_out, report_out)):
        raise ValueError("Outputs must be separate new immutable paths")
    v3 = _read_model(v3_path, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    chosen, selection = base.choose_training(
        cohorts, fit_games=fit_games, tune_games=tune_games,
        audit_games=audit_games, moves_per_game=moves_per_game)
    _verify_maia_source_code()
    from huggingface_hub import hf_hub_download
    ckpt = Path(hf_hub_download(MAIA79_REPO, filename=MAIA79_FILE,
                                revision=MAIA79_HF_REVISION))
    ckpt_sha = _verify_checkpoint(ckpt, known_digest=MAIA79_CHECKPOINT_SHA256)
    torch.manual_seed(SEED)
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    original, cfg = base._frozen_maia(ckpt)
    selected_rows = {
        p: [row for arm in ("fit", "tune", "audit") for row in chosen[p][arm]]
        for p in PLAYERS}
    boards, sources = _restore_real_histories(cohorts, v3, selected_rows, archive_dir)
    if sources != v3["source_pgn_sha256"]:
        raise ValueError("Pinned PGN history source drift")
    cache = base._capture_original(original, cfg, boards, chosen)
    promotion = original.promo_bias_proj.weight.detach().float().clone()
    del original
    all_fit = [e for p in PLAYERS for e in cache[p]["fit"]]
    generic = QKLoRA(1024, 1024, RANK)
    generic_losses = [base.train_epoch(generic, all_fit, promotion, seed=SEED+i)
                      for i in range(GENERIC_EPOCHS)]
    # Common initialization ensures both training families get the exact same
    # pretrained features, generic baseline, and historical FIT examples.
    families = {}
    ce_loss = {}
    ordinary = {}
    for i, p in enumerate(PLAYERS):
        model = QKLoRA(1024, 1024, RANK)
        model.load_state_dict(generic.state_dict())
        ordinary[p] = model
        ce_loss[p] = [
            base.train_epoch(model, cache[p]["fit"], promotion, seed=SEED+100*(i+1)+epoch,
                             generic=generic)
            for epoch in range(PERSONAL_EPOCHS)
        ]
    families["move_only"] = ordinary
    contrastive = {}
    for p in PLAYERS:
        m = QKLoRA(1024, 1024, RANK)
        m.load_state_dict(generic.state_dict())
        contrastive[p] = m
    fit = {p: cache[p]["fit"] for p in PLAYERS}
    contrastive_losses = [
        contrastive_epoch(contrastive, fit, promotion, generic, epoch=i)
        for i in range(PERSONAL_EPOCHS)
    ]
    families["contrastive"] = contrastive
    measured = _evaluate(cache, families, generic, promotion)
    models = {
        "schema": SCHEMA,
        "status": "NEURAL_TRAINED_EXPLORATORY_IDENTITY_NOT_APPROVED",
        "maia_checkpoint_sha256": ckpt_sha,
        "maia_hf_revision": MAIA79_HF_REVISION,
        "maia_source_commit": _verify_maia_source_code(),
        "v3_model_sha256": v3["model_sha256"],
        "source_pgn_sha256": sources,
        "train_manifest_sha256": v3["train_manifest_sha256"],
        "training_files_sha256": v3["training_files_sha256"],
        "data_scope": "FOUR_REAL_HISTORICAL_PLAYERS_TRAIN_ONLY",
        "seed": SEED, "rank": RANK, "elo_scenario": ELO,
        "fit_games_per_player": fit_games,
        "fit_decisions_per_player": fit_games*moves_per_game,
        "generic": {"state": model_state(generic), "loss_by_epoch": generic_losses},
        "move_only": {p:{"state":model_state(ordinary[p]), "loss_by_epoch":ce_loss[p]}
                      for p in PLAYERS},
        "contrastive": {p:{"state":model_state(contrastive[p])} for p in PLAYERS},
        "contrastive_losses": contrastive_losses,
        "frozen_original_weights_changed": False,
        "final_holdout_opened": False,
        "official_rodent_modified": False,
        "personality_homologated": False,
    }
    models["model_sha256"] = _digest(models)
    report = {
        "schema": REPORT_SCHEMA,
        "status": "TRAIN_ONLY_CONTRASTIVE_EXPLORATORY_IDENTITY",
        "trained_model_sha256": models["model_sha256"],
        "model_input_checkpoint_sha256": ckpt_sha,
        "selection": selection,
        **measured,
        "pretraining_independence": "UNVERIFIABLE_PRETRAIN_OVERLAP",
        "data_leakage_controls": {
            "validation_opened": False,
            "final_holdout_opened": False,
            "original_weights_updated": False,
            "rodent_official_updated": False,
        },
        "personality_homologated": False,
        "limitations": [
            "Identity metrics use internal TRAIN TUNE/AUDIT, already exploratory.",
            "Exact game/position embargo does not control all opening/transposition similarity.",
            "Contrastive move-likelihood identification does not establish playing strength.",
            "External original Maia pretraining may overlap historical games/positions.",
        ],
    }
    report["report_sha256"] = _digest(report)
    _write_once(model_out, models)
    _write_once(report_out, report)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Actual Maia79 contrastive identity training")
    p.add_argument("--cohorts", type=Path, required=True)
    p.add_argument("--v3-model", type=Path, required=True)
    p.add_argument("--model-out", type=Path, required=True)
    p.add_argument("--report-out", type=Path, required=True)
    p.add_argument("--archive-dir", type=Path)
    p.add_argument("--fit-games", type=int, default=128)
    p.add_argument("--tune-games", type=int, default=16)
    p.add_argument("--audit-games", type=int, default=16)
    p.add_argument("--moves-per-game", type=int, default=3)
    args = p.parse_args(argv)
    report = run(args.cohorts, args.v3_model, args.model_out, args.report_out,
                 fit_games=args.fit_games, tune_games=args.tune_games,
                 audit_games=args.audit_games, moves_per_game=args.moves_per_game,
                 archive_dir=args.archive_dir)
    print(json.dumps({
        "status": report["status"],
        "selection": report["selection"]["selected_decisions"],
        "blind_audit": report["blinded_identity"]["audit"],
        "final_holdout_opened": report["data_leakage_controls"]["final_holdout_opened"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
