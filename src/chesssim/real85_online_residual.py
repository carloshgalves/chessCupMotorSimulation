"""CPU-only, genuinely gradient-trained experimental historical move-style residual.

A sparse low-rank bilinear neural scorer is optimized by pairwise logistic
backpropagation against legal alternative moves from ALL real85 FIT games.
The original UCI Rodent IV, Maia and DSC binaries/weights remain untouched.
This is NOT a Maia/DSC fine-tune, historical Elo or a tactically safe player.

The streaming source must pass real85_neural_stream.verify before training.
Game identities, exact arm, SHA256 shards, legal chess trajectories and FIT
coverage are checked upstream. TUNE moves are used for diagnostics only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path

import chess

from .matches import file_hash
from .real85_neural_stream import (
    SCHEMA as INPUT_SCHEMA, GAME_SCHEMA, neural_arm, verify,
)

SCHEMA = "chesssim-real85-online-lowrank-style-residual-v1"
REPORT_SCHEMA = "chesssim-real85-online-residual-training-report-v1"
RANK = 6
SEED = 261169
LR = 0.015
DECAY = 0.000001
MAX_JSON_BYTES = 32 * 1024 * 1024
PIECE_WEIGHT = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5,
                chess.QUEEN: 9}


def _hashed_vector(domain: str, name: str, seed: int, rank: int) -> list[float]:
    raw = sha256(f"{seed}|{domain}|{name}".encode()).digest()
    return [((int.from_bytes(raw[2*i:2*i+2], "big") / 65535) - 0.5) * 0.08
            for i in range(rank)]


def _initial(seed: int = SEED) -> dict:
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("seed must be a bounded integer")
    return {"rank": RANK, "seed": seed, "context": {}, "action": {}, "bias": {}}


def _emb(model: dict, field: str, token: str, *,
         create: bool = False) -> list[float]:
    table = model[field]
    if token not in table:
        vector = _hashed_vector(field, token, model["seed"], model["rank"])
        if create:
            table[token] = vector
        return vector
    return table[token]


def _context(board: chess.Board) -> tuple[str, ...]:
    remaining = sum(PIECE_WEIGHT[piece.piece_type]
                    for piece in board.piece_map().values()
                    if piece.piece_type in PIECE_WEIGHT)
    phase = ("endgame" if remaining <= 16 else
             "opening" if board.ply() < 40 else "middlegame")
    focal = sum(PIECE_WEIGHT.get(p.piece_type, int(p.piece_type == chess.PAWN))
                for p in board.piece_map().values() if p.color == board.turn)
    enemy = sum(PIECE_WEIGHT.get(p.piece_type, int(p.piece_type == chess.PAWN))
                for p in board.piece_map().values() if p.color != board.turn)
    edge = max(-4, min(4, focal - enemy))
    return (f"phase:{phase}", f"material-lead:{edge}",
            f"fullmove-bin:{min(10, board.fullmove_number // 5)}",
            f"can-castle:{int(board.has_castling_rights(board.turn))}")


def _action(board: chess.Board, move: chess.Move) -> tuple[str, ...]:
    piece = board.piece_type_at(move.from_square)
    if piece is None or move not in board.legal_moves:
        raise ValueError("Illegal source or candidate action")
    origin = (move.from_square if board.turn == chess.WHITE
              else chess.square_mirror(move.from_square))
    target = (move.to_square if board.turn == chess.WHITE
              else chess.square_mirror(move.to_square))
    target_piece = board.piece_type_at(move.to_square)
    if board.is_en_passant(move):
        target_piece = chess.PAWN
    df = chess.square_file(target) - chess.square_file(origin)
    dr = chess.square_rank(target) - chess.square_rank(origin)
    return (f"piece:{piece}", f"from:{origin}", f"to:{target}",
            f"file_delta:{df}", f"rank_delta:{dr}",
            f"capture:{target_piece or 0}", f"promotion:{move.promotion or 0}",
            f"castle:{int(board.is_castling(move))}")


def _score(model: dict, context: tuple[str, ...],
           features: tuple[str, ...]) -> float:
    width = model["rank"]
    c = [0.0] * width
    for token in context:
        vec = _emb(model, "context", token)
        for i in range(width):
            c[i] += vec[i]
    a = [0.0] * width
    for token in features:
        vec = _emb(model, "action", token)
        for i in range(width):
            a[i] += vec[i]
    return sum(c[i]*a[i] for i in range(width)) + sum(
        model["bias"].get(token, 0.0) for token in features)


def _pair(model: dict, board: chess.Board, preferred: chess.Move,
          other: chess.Move, *, update: bool) -> tuple[float, bool]:
    context = _context(board)
    positive = _action(board, preferred)
    negative = _action(board, other)
    rank = model["rank"]
    delta = Counter(positive)
    delta.subtract(negative)
    delta = {token: amount for token,amount in delta.items() if amount}
    ctx = [0.0] * rank
    act = [0.0] * rank
    for token in context:
        vec = _emb(model, "context", token, create=update)
        for i in range(rank):
            ctx[i] += vec[i]
    for token, weight in delta.items():
        vec = _emb(model, "action", token, create=update)
        for i in range(rank):
            act[i] += weight * vec[i]
    margin = sum(ctx[i]*act[i] for i in range(rank)) + sum(
        weight*model["bias"].get(token, 0.0) for token,weight in delta.items())
    logloss = max(0.0, -margin) + math.log1p(math.exp(-abs(margin)))
    if update:
        grad = 1.0 / (1.0 + math.exp(max(-40.0, min(40.0, margin))))
        for token in context:
            u = model["context"][token]
            for i in range(rank):
                u[i] += LR*(grad*act[i] - DECAY*u[i])
        for token, weight in delta.items():
            v = model["action"][token]
            for i in range(rank):
                v[i] += LR*(grad*weight*ctx[i] - DECAY*v[i])
            prior = model["bias"].get(token, 0.0)
            model["bias"][token] = prior + LR*(grad*weight - DECAY*prior)
    return logloss, margin > 0.0


def _alternative(board: chess.Board, chosen: chess.Move,
                 game_sha: str, ply: int, seed: int) -> chess.Move | None:
    legal = sorted((m for m in board.legal_moves if m != chosen),
                   key=lambda m: m.uci())
    if not legal:
        return None
    h = sha256(f"{seed}:{game_sha}:{ply}".encode()).digest()
    return legal[int.from_bytes(h[:8], "big") % len(legal)]


def _game(model: dict, row: dict, *, update: bool) -> dict:
    if row.get("schema") != GAME_SCHEMA or row.get("arm") != (
            "fit" if update else "tune"):
        raise ValueError("Invalid stream arm or game schema")
    digest = row["game_sha256"]
    if neural_arm(digest) != row["arm"]:
        raise ValueError("FIT/TUNE leak in neural optimization")
    board = chess.Board(row["initial_fen"])
    focal = row["focal_color"] == "white"
    counts = Counter()
    loss = 0.0
    correct = 0
    for ply, uci in enumerate(row["uci_moves"], 1):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("Illegal real chess trajectory")
        if board.turn == focal:
            counts["focal_decisions"] += 1
            alt = _alternative(board, move, digest, ply, model["seed"])
            if alt is None:
                counts["forced_without_gradient"] += 1
            else:
                phase = _context(board)[0].split(":")[1]
                counts[f"decisions_{phase}"] += 1
                sample_loss, hit = _pair(model, board, move, alt, update=update)
                loss += sample_loss
                correct += int(hit)
                counts["gradient_steps" if update else "tune_comparisons"] += 1
        board.push(move)
    if counts["focal_decisions"] != row["focal_decisions"]:
        raise RuntimeError("Focal move count mismatch")
    return {"counts":counts, "loss":loss, "correct":correct}


def _fingerprint(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _atomic_json(path: Path, record: dict) -> None:
    if path.is_symlink():
        raise ValueError("Refuse symbolic link checkpoint")
    record = dict(record)
    if record.get("schema") == SCHEMA:
        record.pop("payload_sha256", None)
        record["payload_sha256"] = _fingerprint(record)
    tmp = path.with_name("." + path.name + ".tmp")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_symlink() or not tmp.is_file():
            raise ValueError("Unsafe interrupted temporary checkpoint")
        tmp.unlink()
    with tmp.open("x", encoding="utf-8", newline="") as writer:
        json.dump(record, writer, sort_keys=True, separators=(",", ":"),
                  ensure_ascii=False, allow_nan=False)
        writer.write("\n")
        writer.flush()
        os.fsync(writer.fileno())
    os.replace(tmp, path)


def _read_checkpoint(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Missing or unsafe neural checkpoint")
    state = json.loads(path.read_text(encoding="utf-8"))
    claimed = state.pop("payload_sha256", None)
    if (state.get("schema") != SCHEMA or
            not isinstance(claimed, str) or claimed != _fingerprint(state)):
        raise ValueError("Neural checkpoint integrity digest mismatch")
    state["payload_sha256"] = claimed
    model = state.get("model", {})
    if (not isinstance(model, dict) or model.get("rank") != RANK or
            model.get("seed") != state.get("seed") or
            state.get("optimizer") != {"learning_rate":LR, "decay":DECAY, "rank":RANK}):
        raise ValueError("Neural checkpoint optimizer or model metadata mismatch")
    for family in ("context", "action"):
        weights = model.get(family)
        if not isinstance(weights, dict) or len(weights) > 100000:
            raise ValueError("Invalid learned neural feature table")
        for token, vector in weights.items():
            if (not isinstance(token, str) or not isinstance(vector, list) or
                    len(vector) != RANK or any(
                        type(n) not in (float, int) or
                        not math.isfinite(n) or abs(n) > 10000
                        for n in vector)):
                raise ValueError("Invalid learned neural embedding")
    biases = model.get("bias")
    if (not isinstance(biases, dict) or len(biases) > 100000 or any(
            not isinstance(k, str) or type(v) not in (float, int) or
            not math.isfinite(v) or abs(v) > 10000
            for k,v in biases.items())):
        raise ValueError("Invalid neural action bias")
    for name in ("completed_games","focal_decisions","gradient_steps","forced_without_gradient"):
        if type(state.get(name)) is not int or state[name] < 0:
            raise ValueError("Invalid neural checkpoint progress")
    if (state["gradient_steps"] + state["forced_without_gradient"] !=
            state["focal_decisions"] or
            not isinstance(state.get("phase_decisions"), dict) or
            sum(state["phase_decisions"].values()) != state["gradient_steps"] or
            type(state.get("sum_fit_loss")) not in (float,int) or
            not math.isfinite(state["sum_fit_loss"]) or state["sum_fit_loss"] < 0):
        raise ValueError("Neural checkpoint training counts are inconsistent")
    return state


def _tune(model: dict, root: Path, row: dict) -> dict:
    losses, matches, comparisons, decisions, games = 0.0, 0, 0, 0, 0
    path = root / row["shards"]["tune"]["file"]
    before = sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
    with path.open(encoding="utf-8") as source:
        for line in source:
            outcome = _game(model, json.loads(line), update=False)
            losses += outcome["loss"]
            matches += outcome["correct"]
            comparisons += outcome["counts"]["tune_comparisons"]
            decisions += outcome["counts"]["focal_decisions"]
            games += 1
    if sha256(json.dumps(model, sort_keys=True).encode()).hexdigest() != before:
        raise RuntimeError("TUNE altered learned parameters or feature tables")
    if games != row["tune_games"] or decisions != row["tune_focal_decisions_all_phases"]:
        raise RuntimeError("Neural TUNE game/decision accounting failed")
    return {
        "games":games, "decisions":decisions, "comparisons":comparisons,
        "mean_pairwise_logloss":round(losses/comparisons, 8) if comparisons else None,
        "pairwise_preference_rate":round(matches/comparisons, 8) if comparisons else None,
        "fit_weights_mutated_by_tune":False,
        "diagnostic_only_not_independent_final_test":True,
    }


def train(inputs: Path, out: Path, *, players: list[str] | None = None,
          resume: bool = False, max_fit_games: int | None = None,
          checkpoint_every: int = 200, seed: int = SEED) -> dict:
    """Train every eligible focal FIT decision unless an explicit partial cap.

    max_fit_games is strictly a debug/partial-run budget, never a completion
    shortcut. Resume continues exactly from checkpointed game count.
    """
    if type(checkpoint_every) is not int or not 1 <= checkpoint_every <= 10000:
        raise ValueError("checkpoint_every must be 1..10000")
    if max_fit_games is not None and (
            type(max_fit_games) is not int or not 1 <= max_fit_games <= 100000):
        raise ValueError("max_fit_games must be a positive integer")
    _initial(seed)
    inputs = Path(inputs).resolve(strict=True)
    checked = verify(inputs)
    if checked["status"] != "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES":
        raise ValueError("Only fully verified, present real FIT/TUNE inputs may train")
    manifest_file = inputs / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if manifest["schema"] != INPUT_SCHEMA:
        raise ValueError("Unsupported input schema")
    chosen = list(players or manifest["players"])
    if len(chosen) != len(set(chosen)) or not chosen or any(
            manifest["players"].get(slug, {}).get("status") !=
            "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING" for slug in chosen):
        raise ValueError("All selected players need verified and sufficient real training input")
    out = Path(out)
    if out.is_symlink() or (out.exists() and (not resume or not out.is_dir())):
        raise ValueError("Refuse existing output without explicit resume")
    if resume and not out.is_dir():
        raise ValueError("Resume needs an existing output directory")
    if not resume:
        if not out.parent.is_dir():
            raise ValueError("Output parent missing")
        out.mkdir()
    training_rows = {}
    manifest_hash = file_hash(manifest_file)
    for slug in chosen:
        cohort = manifest["players"][slug]
        fit = cohort["shards"]["fit"]
        filepath = inputs / fit["file"]
        checkpoint_file = out / f"{slug}.checkpoint.json"
        if resume:
            state = _read_checkpoint(checkpoint_file)
            if (state.get("schema") != SCHEMA or state.get("player") != slug or
                    state.get("input_manifest_sha256") != manifest_hash or
                    state.get("fit_shard_sha256") != fit["sha256"] or
                    state.get("seed") != seed):
                raise ValueError("Resume checkpoint does not match exact player/input/seed")
        else:
            state = {
                "schema":SCHEMA,"player":slug,"seed":seed,
                "optimizer":{"learning_rate":LR,"decay":DECAY,"rank":RANK},
                "input_manifest_sha256":manifest_hash,
                "fit_shard_sha256":fit["sha256"],
                "completed_games":0,"focal_decisions":0,
                "gradient_steps":0,"forced_without_gradient":0,
                "phase_decisions":{},
                "sum_fit_loss":0.0, "model":_initial(seed),
            }
        start = state["completed_games"]
        if type(start) is not int or not 0 <= start <= fit["games"]:
            raise ValueError("Invalid resume game index")
        target = (fit["games"] if max_fit_games is None else
                  min(fit["games"], max_fit_games))
        if target < start:
            raise ValueError("Partial cap predates saved checkpoint")
        with filepath.open(encoding="utf-8") as stream:
            for index,line in enumerate(stream):
                if index < start:
                    continue
                if index >= target:
                    break
                record = json.loads(line)
                if record["player"] != slug or record["source_pgn_sha256"] != cohort["source_pgn_sha256"]:
                    raise ValueError("Training row player or source mismatch")
                observation = _game(state["model"], record, update=True)
                cnt = observation["counts"]
                state["completed_games"] += 1
                state["focal_decisions"] += cnt["focal_decisions"]
                state["gradient_steps"] += cnt["gradient_steps"]
                state["forced_without_gradient"] += cnt["forced_without_gradient"]
                state["sum_fit_loss"] += observation["loss"]
                for key,count in cnt.items():
                    if key.startswith("decisions_"):
                        phases = state["phase_decisions"]
                        phases[key] = phases.get(key, 0) + count
                if state["completed_games"] % checkpoint_every == 0:
                    _atomic_json(checkpoint_file, state)
        if state["completed_games"] != target:
            raise RuntimeError("Training corpus shorter than committed FIT count")
        _atomic_json(checkpoint_file, state)
        completed = target == fit["games"]
        if completed and state["focal_decisions"] != fit["focal_decisions"]:
            raise RuntimeError("Not all historical FIT move decisions entered gradient stage")
        tune = _tune(state["model"], inputs, cohort) if completed else None
        training_rows[slug] = {
            "status":("FITTED_ALL_REAL_FOCAL_MOVES_CPU" if completed else
                      "PARTIAL_FIT_NEEDS_RESUME"),
            "fit_games_processed":target,
            "fit_games_available":fit["games"],
            "focal_decisions_processed":state["focal_decisions"],
            "fit_focal_decisions_available":fit["focal_decisions"],
            "gradient_steps":state["gradient_steps"],
            "forced_without_gradient":state["forced_without_gradient"],
            "phase_decisions":state["phase_decisions"],
            "mean_fit_pairwise_logloss":(
                round(state["sum_fit_loss"]/state["gradient_steps"], 8)
                if state["gradient_steps"] else None),
            "tune":tune,
            "checkpoint_file":checkpoint_file.name,
            "checkpoint_sha256":file_hash(checkpoint_file),
        }
    report = {
        "schema":REPORT_SCHEMA,
        "status":("ALL_REQUESTED_PLAYER_RESIDUALS_FITTED" if all(
            x["status"] == "FITTED_ALL_REAL_FOCAL_MOVES_CPU"
            for x in training_rows.values()) else "PARTIAL_NEURAL_FIT_RESUME_REQUIRED"),
        "input_manifest_sha256":manifest_hash,
        "learner":"CPU_SPARSE_BILINEAR_RANK6_ONLINE_PAIRWISE_GRADIENTS",
        "seed":seed, "learning_rate":LR, "rank":RANK,
        "neural_weights_actually_updated":any(
            row["gradient_steps"] > 0 for row in training_rows.values()),
        "maia_dsc_backbone_finetuned":False,
        "uci_playable_and_tactical_safe":False,
        "old_year_holdout_used":False, "inspected_test_used":False,
        "players":training_rows,
        "warning":"Experimental style residual trained on real moves; requires independent UCI and tactical safety gates before deployment.",
    }
    _atomic_json(out/"report.json",report)
    return report


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="CPU train real85 full-history neural style residual")
    p.add_argument("--inputs",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--players",nargs="+")
    p.add_argument("--resume",action="store_true")
    p.add_argument("--max-fit-games",type=int)
    p.add_argument("--checkpoint-every",type=int,default=200)
    p.add_argument("--seed",type=int,default=SEED)
    args=p.parse_args(argv)
    r=train(args.inputs,args.out,players=args.players,resume=args.resume,
            max_fit_games=args.max_fit_games,
            checkpoint_every=args.checkpoint_every,seed=args.seed)
    print(json.dumps({"status":r["status"],"players":{
        slug:{"games":row["fit_games_processed"],
              "decisions":row["focal_decisions_processed"],
              "updates":row["gradient_steps"],
              "tune":row["tune"]} for slug,row in r["players"].items()}},
        sort_keys=True),flush=True)
    return 0 if r["status"]=="ALL_REQUESTED_PLAYER_RESIDUALS_FITTED" else 2


if __name__=="__main__":
    raise SystemExit(main())
