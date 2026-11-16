"""Frozen DSC-150M latent Q/K LoRA; no mutation of original weights.

All chess data enter through the pinned original DSC predictor and its
intermediate LayerNorm output. Only small A/B matrices receive gradients.
Original policy logits are preserved as an exact softmax *prior*; LoRA
changes attention logits by a reparameterized Q/K delta. The frozen policy
promotion offset is adjusted consistently when K changes.

This module does not import PGNs, inspect final holdout or authorize engines.
"""
from __future__ import annotations

import copy
import math

import torch
import torch.nn as nn
import torch.nn.functional as F


RANKS = (2, 4, 8)
KL_WEIGHT = 0.03
GROUP_WEIGHT = 0.0005
LEARNING_RATE = 0.001
BATCH_SIZE = 8


class QKLoRA(nn.Module):
    """Trainable low-rank deltas for original DSC PolicyHead.q/k only."""

    def __init__(self, width: int, policy_width: int, rank: int) -> None:
        super().__init__()
        if rank not in RANKS or width <= 0 or policy_width <= 0:
            raise ValueError("Unregistered LoRA rank or invalid frozen head dimensions")
        self.rank = rank
        self.aq = nn.Parameter(torch.empty(rank, width))
        self.bq = nn.Parameter(torch.zeros(policy_width, rank))
        self.ak = nn.Parameter(torch.empty(rank, width))
        self.bk = nn.Parameter(torch.zeros(policy_width, rank))
        nn.init.normal_(self.aq, mean=0.0, std=0.02)
        nn.init.normal_(self.ak, mean=0.0, std=0.02)

    def deltas(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # LoRA orientation: B @ A (policy_width, width), scaled by 1/r.
        dq = F.linear(F.linear(x, self.aq), self.bq) / self.rank
        dk = F.linear(F.linear(x, self.ak), self.bk) / self.rank
        return dq, dk


def pack(examples: list[dict]) -> dict[str, torch.Tensor]:
    """Pad legal moves, never the human target; preserve original policy order."""
    if not examples:
        raise ValueError("Empty frozen latent batch")
    lengths = [len(item["indices"]) for item in examples]
    if min(lengths) <= 0:
        raise ValueError("No legal chess moves in a historical example")
    max_legal = max(lengths)
    n = len(examples)
    ids = torch.zeros((n, max_legal), dtype=torch.long)
    prior = torch.full((n, max_legal), -1e9, dtype=torch.float32)
    mask = torch.zeros((n, max_legal), dtype=torch.bool)
    for i, ex in enumerate(examples):
        count = lengths[i]
        ids[i, :count] = ex["indices"]
        prior[i, :count] = ex["prior_logp"]
        mask[i, :count] = True
        if not 0 <= ex["target"] < count:
            raise ValueError("Historic target out of legal candidate range")
    return {
        "x": torch.stack([ex["latent"].float() for ex in examples]),
        "q": torch.stack([ex["q0"].float() for ex in examples]),
        "k": torch.stack([ex["k0"].float() for ex in examples]),
        "ids": ids,
        "prior": prior,
        "mask": mask,
        "target": torch.tensor([ex["target"] for ex in examples], dtype=torch.long),
    }


def logits(batch: dict[str, torch.Tensor], adapter: QKLoRA | None,
           promotion_weight: torch.Tensor, scale: float) -> torch.Tensor:
    """Exact Q/K low-rank delta on each legal move, plus underpromotions.

    The original frozen legal-move log probability is used as the base.
    No chess label or player name enters this function.
    """
    original = batch["prior"]
    if adapter is None:
        return original.masked_fill(~batch["mask"], -1e9)
    x, q0, k0 = batch["x"], batch["q"], batch["k"]
    if x.shape[1] != 64 or q0.shape[1] != 64 or k0.shape != q0.shape:
        raise ValueError("Original DSC spatial latent/projection dimensions changed")
    if x.shape[-1] != adapter.aq.shape[1] or q0.shape[-1] != adapter.bq.shape[0]:
        raise ValueError("Adapter is incompatible with original DSC Q/K")
    if (promotion_weight.shape != (3, q0.shape[-1]) or
            not isinstance(scale, (int, float)) or scale <= 0):
        raise ValueError("Frozen PolicyHead promotion/scale configuration changed")
    idx = batch["ids"]
    start = (idx // 4) // 64
    end = (idx // 4) % 64
    channel = idx % 4
    if torch.any(idx[batch["mask"]] >= 16384):
        raise ValueError("DSC oriented move index out of range")
    dq, dk = adapter.deltas(x)
    dim = q0.shape[-1]
    fs = start.unsqueeze(-1).expand(-1, -1, dim)
    ts = end.unsqueeze(-1).expand(-1, -1, dim)
    frozen_q = q0.gather(1, fs)
    frozen_k = k0.gather(1, ts)
    new_q = frozen_q + dq.gather(1, fs)
    new_k = frozen_k + dk.gather(1, ts)
    delta = ((new_q * new_k) - (frozen_q * frozen_k)).sum(dim=-1) * scale
    # Original policy.promo(k) affects R/B/N promotions (channels 1/2/3),
    # not queen promotions (channel 0). The base prior contains its bias;
    # only the frozen promo projection applied to delta-K is added here.
    promo_rows = promotion_weight[(channel - 1).clamp(min=0)]
    delta_prom = (dk.gather(1, ts) * promo_rows).sum(dim=-1)
    delta = delta + torch.where(channel > 0, delta_prom, 0.0)
    return (original + delta).masked_fill(~batch["mask"], -1e9)


def loss(batch: dict[str, torch.Tensor], adapter: QKLoRA,
         promo_weight: torch.Tensor, scale: float,
         generic: QKLoRA | None = None) -> torch.Tensor:
    scores = logits(batch, adapter, promo_weight, scale)
    ce = F.cross_entropy(scores, batch["target"])
    original_prob = F.softmax(batch["prior"], dim=-1)
    kl = F.kl_div(F.log_softmax(scores, dim=-1), original_prob, reduction="batchmean")
    reg = scores.new_tensor(0.0)
    if generic is not None:
        # Generic head is frozen while fitting the named individual.
        for param, base in zip(adapter.parameters(), generic.parameters()):
            reg = reg + torch.mean((param - base.detach()).square())
    return ce + KL_WEIGHT * kl + GROUP_WEIGHT * reg


def train_epoch(model: QKLoRA, examples: list[dict],
                promotion_weight: torch.Tensor, scale: float,
                seed: int, generic: QKLoRA | None = None) -> None:
    import random
    rng = random.Random(seed)
    order = list(range(len(examples)))
    rng.shuffle(order)
    if generic is not None:
        generic.eval()
        for param in generic.parameters():
            param.requires_grad_(False)
    optimizer = torch.optim.AdamW(
        (param for param in model.parameters() if param.requires_grad),
        lr=LEARNING_RATE, weight_decay=1e-4)
    model.train()
    for start in range(0, len(order), BATCH_SIZE):
        batch = pack([examples[j] for j in order[start:start + BATCH_SIZE]])
        optimizer.zero_grad(set_to_none=True)
        objective = loss(batch, model, promotion_weight, scale, generic)
        if not torch.isfinite(objective):
            raise ValueError("Non-finite LoRA loss on historical FIT")
        objective.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()


def predict(examples: list[dict], model: QKLoRA | None,
            promotion_weight: torch.Tensor, scale: float,
            batch_size: int = 8) -> list[dict]:
    """Return per-legal-move probabilities in fixed UCI ordering."""
    result = []
    if model is not None:
        model.eval()
    with torch.no_grad():
        for start in range(0, len(examples), batch_size):
            batch = pack(examples[start:start + batch_size])
            values = logits(batch, model, promotion_weight, scale)
            probabilities = F.softmax(values, dim=-1).cpu().tolist()
            for ex, prob in zip(examples[start:start + batch_size], probabilities):
                result.append({
                    "moves": ex["moves"],
                    "probs": prob[:len(ex["moves"])],
                    "target": ex["target"],
                    "game_sha256": ex["game_sha256"],
                })
    return result


def model_state(model: QKLoRA) -> dict:
    """Only LoRA matrices are serialized, never DSC/PGN tensors."""
    return {name: [[round(float(v), 6) for v in row]
                   for row in param.detach().cpu().tolist()]
            for name, param in model.named_parameters()}


def load_state(width: int, policy_width: int, rank: int, state: dict) -> QKLoRA:
    model = QKLoRA(width, policy_width, rank)
    if set(state) != {"aq", "bq", "ak", "bk"}:
        raise ValueError("Invalid Q/K LoRA-only state")
    with torch.no_grad():
        for name, param in model.named_parameters():
            value = torch.tensor(state[name], dtype=torch.float32)
            if value.shape != param.shape or not torch.isfinite(value).all():
                raise ValueError("Invalid personal LoRA matrix dimensions/values")
            param.copy_(value)
    return model


def snapshot(model: QKLoRA) -> dict[str, torch.Tensor]:
    return copy.deepcopy(model.state_dict())


def restore(width: int, policy_width: int, rank: int,
            state: dict[str, torch.Tensor]) -> QKLoRA:
    model = QKLoRA(width, policy_width, rank)
    model.load_state_dict(state)
    return model


def log_loss(predictions: list[dict]) -> float:
    if not predictions:
        raise ValueError("Empty TUNE evaluation")
    return math.fsum(-math.log(max(item["probs"][item["target"]], 1e-40))
                     for item in predictions) / len(predictions)
