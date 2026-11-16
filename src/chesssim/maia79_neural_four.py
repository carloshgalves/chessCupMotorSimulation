"""Neural Maia3-79M Q/K low-rank adaptation from genuine four-player TRAIN PGNs.

This is actual gradient-based fitting to the original Maia policy head,
not the V7 semantic/rank adapter. Frozen pretrained weights and source files
are never modified. Only a private, bounded TRAIN FIT/TUNE/AUDIT is used.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from hashlib import sha256
import json
import math
from pathlib import Path
import random
from types import SimpleNamespace

import torch
import torch.nn.functional as F

from .contextual_policy_v3 import SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model
from .dsc_qk_lora_core import QKLoRA, model_state
from .maia_personalization_v7 import (
    MAIA79_CHECKPOINT_SHA256, MAIA79_FILE, MAIA79_HF_REVISION, MAIA79_REPO,
    _verify_checkpoint,
)
from .maia_transfer_v6 import _restore_real_histories, _verify_maia_source_code
from .real_gate_v4 import _position_key
from .real_policy_experiment import _digest, _read_json, _validated_training_rows, _write_once

SCHEMA = "chesssim-maia79-four-neural-qk-fit-v1"
REPORT_SCHEMA = "chesssim-maia79-four-neural-qk-training-report-v1"
PLAYERS = ("fischer", "karpov", "kasparov", "tal")
SEED = 4261
SALT = "maia79-four-neural-qk-v1"
RANK = 4
ELO = 2400  # Model's input scenario, NOT inferred historic FIDE rating.
KL_WEIGHT = 0.05
LR = 0.001
EPOCHS_GENERIC = 2
EPOCHS_PLAYER = 3
MAX_GAMES = 128
MAX_MOVES = 4


def game_arm(game: str) -> str:
    if not isinstance(game, str) or len(game) != 64 or any(
            c not in "0123456789abcdef" for c in game):
        raise ValueError("Expected verified 64-char game trajectory digest")
    bucket = int(sha256((SALT + "|" + game).encode()).hexdigest()[:12], 16) % 10
    return "fit" if bucket < 7 else "tune" if bucket == 7 else (
        "audit" if bucket == 8 else "unused")


def _order(*parts: object) -> str:
    return sha256("|".join(map(str, (SALT, SEED, *parts))).encode()).hexdigest()


def choose_training(cohorts: Path, *, fit_games: int = 12, tune_games: int = 4,
                    audit_games: int = 4, moves_per_game: int = 2) -> tuple[dict, dict]:
    """Deterministic global game separation and cross-arm canonical FEN embargo."""
    sizes = {"fit": fit_games, "tune": tune_games, "audit": audit_games}
    if any(type(n) is not int or not 1 <= n <= MAX_GAMES for n in sizes.values()):
        raise ValueError("Game budget must be 1..128 per arm")
    if type(moves_per_game) is not int or not 1 <= moves_per_game <= MAX_MOVES:
        raise ValueError("Moves per game must be 1..4")
    manifest = _read_json(Path(cohorts) / "manifest.json")
    if set(manifest["players"]) != set(PLAYERS):
        raise ValueError("Four historical players required, no substituted identities")
    groups = {s: {arm: defaultdict(list) for arm in sizes} for s in PLAYERS}
    positions = defaultdict(set)
    source_counts = defaultdict(int)
    for raw in _validated_training_rows(cohorts, manifest):
        slug, game, ply = raw["player"], raw["game_sha256"], raw["ply"]
        if slug not in groups or ply < 21:
            continue
        arm = game_arm(game)
        fen = raw["policy_input"]["fen"]
        key = _position_key(fen)
        positions[key].add(arm)
        source_counts[(slug, arm)] += 1
        if arm in sizes:
            groups[slug][arm][game].append({
                "player": slug, "fen": fen, "move": raw["policy_target"]["move_uci"],
                "ply": ply, "game_sha256": game,
            })
    # Even a position shared with unused TRAIN games is forbidden in FIT.
    conflicts = {key for key, arms in positions.items() if len(arms) != 1}
    selected = {s: {} for s in PLAYERS}
    chosen_games = set()
    for slug in PLAYERS:
        for arm, count in sizes.items():
            candidates = []
            for game, rows in groups[slug][arm].items():
                valid = [r for r in rows if _position_key(r["fen"]) not in conflicts]
                valid.sort(key=lambda r: (_order(game, r["ply"]), r["ply"]))
                if len(valid) >= moves_per_game:
                    candidates.append((game, valid[:moves_per_game]))
            candidates.sort(key=lambda item: (_order(slug, arm, item[0]), item[0]))
            selected[slug][arm] = []
            for game, rows in candidates:
                if game in chosen_games:
                    continue
                selected[slug][arm].extend(rows)
                chosen_games.add(game)
                if len(selected[slug][arm]) == count * moves_per_game:
                    break
            if len(selected[slug][arm]) != count * moves_per_game:
                raise ValueError(f"UNDERPOWERED_{slug}_{arm}: expected "
                                 f"{count} distinct games x {moves_per_game} moves")
    by_pos = defaultdict(set)
    for slug, arms in selected.items():
        for arm, rows in arms.items():
            for row in rows:
                by_pos[_position_key(row["fen"])].add(arm)
    if any(len(arms) > 1 for arms in by_pos.values()):
        raise AssertionError("TRAIN sub-arm position embargo failed")
    return selected, {
        "source_training_decisions": {
            slug: {arm: source_counts[(slug, arm)]
                   for arm in ("fit", "tune", "audit", "unused")}
            for slug in PLAYERS
        },
        "source_cross_arm_position_conflicts": len(conflicts),
        "selected_unique_games": len(chosen_games),
        "selected_decisions": sum(len(rows) for a in selected.values()
                                  for rows in a.values()),
        "per_player": {slug: {arm: {"games": len(rows)//moves_per_game,
                                      "decisions": len(rows)}
                              for arm, rows in selected[slug].items()}
                       for slug in PLAYERS},
        "split": "SHA256 trajectory global / canonical position embargo",
        "seed": SEED,
    }


def _frozen_maia(checkpoint: Path):
    """Strict original checkpoint load, with all trainable backbone params frozen."""
    from maia3.models import MAIA3Model
    from maia3.model_registry import resolve_model_spec
    spec = resolve_model_spec("maia3-79m")
    cfg = SimpleNamespace(**spec.config, device="cpu")
    raw = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = raw["model_state_dict"] if isinstance(raw, dict) and "model_state_dict" in raw else raw
    if not isinstance(state, dict):
        raise ValueError("Original Maia checkpoint does not contain a tensor state dict")
    renamed = {k.replace("smolgen", "gab"): v for k, v in state.items()}
    model = MAIA3Model(cfg)
    missing, unexpected = model.load_state_dict(renamed, strict=False)
    if missing or unexpected:
        raise ValueError(f"Original Maia checkpoint incompatible: missing={missing[:5]}, "
                         f"unexpected={unexpected[:5]}")
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    if (model.proj_sq_from.in_features != 1024
            or model.proj_sq_to.in_features != 1024
            or model.proj_sq_from.out_features != 1024
            or model.proj_sq_to.out_features != 1024
            or tuple(model.promo_bias_proj.weight.shape) != (4, 1024)):
        raise ValueError("Expected pinned Maia3-79M policy head dimensions")
    return model, cfg


def historical_tokens(board, cfg):
    """Exactly replay prior board states; never include the future target move."""
    from maia3.dataset import get_historical_tokens, tokenize_board
    history = deque(maxlen=cfg.history)
    replay = board.root()
    history.append(tokenize_board(replay))
    for move in board.move_stack:
        replay.push(move)
        history.append(tokenize_board(replay))
    if replay.fen() != board.fen():
        raise ValueError("History reconstruction does not match original board")
    return get_historical_tokens(
        history, cfg, base=0.0, inc=0.0, clk_left_before=0.0, clk_ponder=0.0)


def _capture_original(model, cfg, boards: dict, selected: dict) -> dict:
    """Extract original policy logits and final frozen 64x1024 latents, TRAIN only."""
    import chess
    from maia3.dataset import get_legal_moves_mask
    from maia3.utils import get_all_possible_moves, mirror_move
    move_map = {s: i for i, s in enumerate(get_all_possible_moves())}
    if len(move_map) != 4352:
        raise ValueError("Unexpected original Maia move vocabulary")
    cache = {slug: {} for slug in PLAYERS}
    for slug in PLAYERS:
        for arm in ("fit", "tune", "audit"):
            examples = []
            for row in selected[slug][arm]:
                board = boards[slug][(row["game_sha256"], row["ply"])]
                if (board.fen() != row["fen"] or not board.is_valid()
                        or board.is_game_over(claim_draw=False)):
                    raise ValueError("TRAIN PGN history/position mismatch")
                target = (mirror_move(row["move"]) if board.turn == chess.BLACK
                          else row["move"])
                legal = get_legal_moves_mask(board, move_map)
                if target not in move_map or not bool(legal[move_map[target]]):
                    raise ValueError("Human historic target absent from original Maia legal mask")
                ids = torch.nonzero(legal, as_tuple=False).flatten()
                tokens = historical_tokens(board, cfg).unsqueeze(0)
                captured = []
                def hook(_module, _inputs, output):
                    captured.append(output.detach().clone())
                handle = model.transformer.register_forward_hook(hook)
                try:
                    with torch.inference_mode():
                        raw, _, _ = model(
                            tokens, torch.tensor([ELO], dtype=torch.long),
                            torch.tensor([ELO], dtype=torch.long))
                finally:
                    handle.remove()
                if len(captured) != 1 or tuple(captured[0].shape) != (1, 64, 1024):
                    raise ValueError("Original Maia transformer latent shape changed")
                latent = captured[0][0]
                with torch.inference_mode():
                    from_q = model.proj_sq_from(latent)
                    to_k = model.proj_sq_to(latent)
                    prior = F.log_softmax(raw[0, ids].float(), dim=0)
                if not bool(torch.isfinite(prior).all()):
                    raise ValueError("Original Maia probability not finite")
                examples.append({
                    "latent": latent.detach().half().clone(),
                    "q0": from_q.detach().half().clone(),
                    "k0": to_k.detach().half().clone(),
                    "ids": ids.cpu().clone(),
                    "prior": prior.cpu().clone(),
                    "target": (ids == move_map[target]).nonzero(as_tuple=False).item(),
                    "game_sha256": row["game_sha256"],
                })
            cache[slug][arm] = examples
    return cache


def _pack(examples: list[dict]) -> dict:
    if not examples:
        raise ValueError("Cannot train/evaluate on empty historical examples")
    n = len(examples)
    length = max(len(e["ids"]) for e in examples)
    ids = torch.zeros((n, length), dtype=torch.long)
    prior = torch.full((n, length), -1e9)
    mask = torch.zeros((n, length), dtype=torch.bool)
    target = []
    for i, e in enumerate(examples):
        k = len(e["ids"])
        if not 0 <= e["target"] < k or k == 0:
            raise ValueError("Invalid historical legal-move label")
        if (len(e["prior"]) != k or not bool(torch.isfinite(e["prior"]).all())
                or len(set(e["ids"].tolist())) != k or bool((e["ids"] < 0).any())):
            raise ValueError("Malformed historical Maia original legal-move prior")
        ids[i, :k] = e["ids"]
        prior[i, :k] = e["prior"]
        mask[i, :k] = True
        target.append(e["target"])
    return {
        "x": torch.stack([e["latent"].float() for e in examples]),
        "q": torch.stack([e["q0"].float() for e in examples]),
        "k": torch.stack([e["k0"].float() for e in examples]),
        "ids": ids, "prior": prior, "mask": mask,
        "target": torch.tensor(target, dtype=torch.long),
    }


def _adapted_logits(batch: dict, adapter: QKLoRA | None,
                    frozen_promo_weight: torch.Tensor) -> torch.Tensor:
    """Adjust Maia 4352 logits using original Q/K and the promotion offset."""
    prior = batch["prior"]
    if adapter is None:
        return prior.masked_fill(~batch["mask"], -1e9)
    ids, x, q0, k0 = batch["ids"], batch["x"], batch["q"], batch["k"]
    if (x.ndim != 3 or x.shape[1:] != (64, 1024)
            or q0.shape != x.shape or k0.shape != x.shape
            or frozen_promo_weight.shape != (4, 1024)
            or torch.any(ids[batch["mask"]] >= 4352)):
        raise ValueError("Unrecognized original Maia79 Q/K or legal move vocabulary")
    # index 0..4095: source*64+dest; index 4096..4351:
    # ((source_file*8 + dest_file)*4 + promotion) from rank 7 to rank 8.
    promotion = ids >= 4096
    z = (ids - 4096).clamp(min=0)
    from_sq = torch.where(promotion, 48 + z // 32, ids // 64)
    to_sq = torch.where(promotion, 56 + (z // 4) % 8, ids % 64)
    dim = q0.shape[-1]
    gather_from = from_sq.unsqueeze(-1).expand(-1, -1, dim)
    gather_to = to_sq.unsqueeze(-1).expand(-1, -1, dim)
    dq, dk = adapter.deltas(x)
    frozen_q = q0.gather(1, gather_from)
    frozen_k = k0.gather(1, gather_to)
    new_q = frozen_q + dq.gather(1, gather_from)
    new_k = frozen_k + dk.gather(1, gather_to)
    delta = ((new_q * new_k) - (frozen_q * frozen_k)).sum(dim=-1) / math.sqrt(dim)
    # Maia promo_bias_proj receives policy K, NOT the frozen transformer x.
    # Consequently K-LoRA must also update its promotion offset.
    promo_k = dk.gather(1, gather_to)
    promo_options = F.linear(promo_k, frozen_promo_weight) * math.sqrt(dim)
    promo_delta = promo_options.gather(2, (z % 4).unsqueeze(-1)).squeeze(-1)
    delta = delta + torch.where(promotion, promo_delta, 0.0)
    return (prior + delta).masked_fill(~batch["mask"], -1e9)


def train_epoch(adapter: QKLoRA, examples: list[dict], promo: torch.Tensor,
                *, seed: int, generic: QKLoRA | None = None) -> float:
    rng = random.Random(seed)
    order = list(range(len(examples)))
    rng.shuffle(order)
    optimizer = torch.optim.AdamW(adapter.parameters(), lr=LR, weight_decay=1e-4)
    adapter.train()
    if generic is not None:
        generic.eval()
        generic.requires_grad_(False)
    losses = []
    for i in range(0, len(order), 8):
        b = _pack([examples[j] for j in order[i:i+8]])
        optimizer.zero_grad(set_to_none=True)
        scores = _adapted_logits(b, adapter, promo)
        objective = F.cross_entropy(scores, b["target"])
        objective = objective + KL_WEIGHT * F.kl_div(
            F.log_softmax(scores, dim=-1),
            F.softmax(b["prior"], dim=-1), reduction="batchmean")
        if generic is not None:
            objective = objective + 0.0005 * sum(
                (p - g.detach()).square().mean()
                for p, g in zip(adapter.parameters(), generic.parameters()))
        if not torch.isfinite(objective):
            raise ValueError("Non-finite Maia neural training objective")
        objective.backward()
        torch.nn.utils.clip_grad_norm_(adapter.parameters(), 1.0)
        optimizer.step()
        losses.append(float(objective.detach()))
    return sum(losses) / len(losses)


def score(examples: list[dict], adapter: QKLoRA | None, promo: torch.Tensor) -> dict:
    correct, loss = 0, 0.0
    if adapter is not None:
        adapter.eval()
    with torch.no_grad():
        for i in range(0, len(examples), 8):
            batch = _pack(examples[i:i+8])
            logits = _adapted_logits(batch, adapter, promo)
            probabilities = F.log_softmax(logits, dim=-1)
            correct += int((logits.argmax(dim=-1) == batch["target"]).sum().item())
            loss -= float(probabilities.gather(1, batch["target"][:, None]).sum().item())
    return {"decisions": len(examples), "games": len({e["game_sha256"] for e in examples}),
            "top1_correct": correct, "top1": round(correct / len(examples), 6),
            "log_loss": round(loss / len(examples), 6)}


def run(cohorts: Path, v3_file: Path, out: Path, report_out: Path, *,
        fit_games: int = 12, tune_games: int = 4, audit_games: int = 4,
        moves_per_game: int = 2, archive_dir: Path | None = None) -> dict:
    if out == report_out:
        raise ValueError("Model and report outputs must be different")
    if any(p.exists() or p.is_symlink() for p in (out, report_out)):
        raise FileExistsError("Historical outputs are immutable; choose new paths")
    v3 = _read_model(v3_file, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    selection, counts = choose_training(cohorts, fit_games=fit_games,
        tune_games=tune_games, audit_games=audit_games, moves_per_game=moves_per_game)
    _verify_maia_source_code()
    from huggingface_hub import hf_hub_download
    checkpoint = Path(hf_hub_download(MAIA79_REPO, filename=MAIA79_FILE,
                                     revision=MAIA79_HF_REVISION))
    checkpoint_sha = _verify_checkpoint(checkpoint, known_digest=MAIA79_CHECKPOINT_SHA256)
    torch.manual_seed(SEED)
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    model, cfg = _frozen_maia(checkpoint)
    examples_for_history = {
        slug: [r for arm in ("fit", "tune", "audit") for r in selection[slug][arm]]
        for slug in PLAYERS}
    boards, sources = _restore_real_histories(
        cohorts, v3, examples_for_history, archive_dir)
    if sources != v3["source_pgn_sha256"]:
        raise ValueError("Original PGN bytes changed during history restoration")
    cache = _capture_original(model, cfg, boards, selection)
    promo = model.promo_bias_proj.weight.detach().float().clone()
    del model
    generic_fit = [e for slug in PLAYERS for e in cache[slug]["fit"]]
    generic = QKLoRA(1024, 1024, RANK)
    generic_losses = [
        train_epoch(generic, generic_fit, promo, seed=SEED + epoch)
        for epoch in range(EPOCHS_GENERIC)]
    named = {}
    profiles, metrics = {}, {}
    for idx, slug in enumerate(PLAYERS):
        adapter = QKLoRA(1024, 1024, RANK)
        adapter.load_state_dict(generic.state_dict())
        losses = [train_epoch(adapter, cache[slug]["fit"], promo,
                             seed=SEED + 100*(idx+1) + epoch, generic=generic)
                  for epoch in range(EPOCHS_PLAYER)]
        named[slug] = adapter
        profiles[slug] = {
            "fit_decisions": len(cache[slug]["fit"]),
            "fit_distinct_games": len({e["game_sha256"] for e in cache[slug]["fit"]}),
            "optimizer": "AdamW", "learning_rate": LR, "rank": RANK,
            "epochs": EPOCHS_PLAYER, "fit_training_loss_by_epoch": losses,
            "state": model_state(adapter),
        }
    for i, slug in enumerate(PLAYERS):
        other = PLAYERS[(i+1) % len(PLAYERS)]
        metrics[slug] = {}
        for arm in ("tune", "audit"):
            rows = cache[slug][arm]
            metrics[slug][arm] = {
                "original": score(rows, None, promo),
                "generic": score(rows, generic, promo),
                "named": score(rows, named[slug], promo),
                "swapped": score(rows, named[other], promo),
                "swapped_source": other,
            }
    adapter_json = {
        "schema": SCHEMA, "status": "TRAINED_NEURAL_QK_EXPLORATORY_NOT_HOMOLOGATED",
        "original_backbone": "maia3-79m",
        "maia_source_commit": _verify_maia_source_code(),
        "maia_checkpoint_sha256": checkpoint_sha,
        "maia_hf_revision": MAIA79_HF_REVISION,
        "rank": RANK, "elo_scenario": ELO, "seed": SEED,
        "data_scope": "FOUR_HISTORICAL_PLAYERS_TRAIN_ONLY_FIT",
        "v3_model_sha256": v3["model_sha256"],
        "train_manifest_sha256": v3["train_manifest_sha256"],
        "training_files_sha256": v3["training_files_sha256"],
        "source_pgn_sha256": sources,
        "generic": {"state": model_state(generic),
                    "training_losses": generic_losses},
        "players": profiles,
        "final_holdout_opened": False,
        "official_rodent_modified": False,
        "personality_homologated": False,
    }
    adapter_json["model_sha256"] = _digest(adapter_json)
    report = {
        "schema": REPORT_SCHEMA, "status": "EXPLORATORY_TRAIN_ONLY_NEURAL_MAIA79",
        "model_sha256": adapter_json["model_sha256"],
        "original_checkpoint_sha256": checkpoint_sha,
        "selection": counts, "metrics": metrics,
        "adaptation": "FROZEN_79M_LATENTS_QK_LOW_RANK_BACKPROP",
        "original_maia79_weights_changed": False,
        "validation_opened": False, "final_holdout_opened": False,
        "official_rodent_modified": False, "personality_homologated": False,
        "external_pretrain_overlap": "UNVERIFIABLE_PRETRAIN_OVERLAP",
        "limitations": [
            "TRAIN sub-arm tune/audit is exploratory; previously viewed project results are not independent confirmation",
            "This run trains only lightweight Q/K matrices, not all Maia79 weights",
            "Full PGN source re-read is limited to reconstructing selected TRAIN game histories; no reserved decision is evaluated",
            "Identity differences require separate independent, game-paired evidence before promotion",
        ],
    }
    report["report_sha256"] = _digest(report)
    _write_once(out, adapter_json)
    _write_once(report_out, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Train real Maia3-79M Q/K on four historical players")
    parser.add_argument("--cohorts", type=Path, required=True)
    parser.add_argument("--v3-model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--fit-games", type=int, default=12)
    parser.add_argument("--tune-games", type=int, default=4)
    parser.add_argument("--audit-games", type=int, default=4)
    parser.add_argument("--moves-per-game", type=int, default=2)
    args = parser.parse_args(argv)
    result = run(args.cohorts, args.v3_model, args.out, args.report,
                 fit_games=args.fit_games, tune_games=args.tune_games,
                 audit_games=args.audit_games, moves_per_game=args.moves_per_game,
                 archive_dir=args.archive_dir)
    print(json.dumps({"status": result["status"],
                      "selected_decisions": result["selection"]["selected_decisions"],
                      "models": list(result["metrics"]),
                      "final_holdout_opened": result["final_holdout_opened"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
