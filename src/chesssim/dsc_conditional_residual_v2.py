"""CPU-only, genuine DSC-full-softmax conditional-logit residual training.

Four historical individuals are fitted on new TRAIN-year, globally game-
separated FIT data. TUNE chooses fixed candidate scales; AUDIT is read once
per PR run. VALIDATION and final HOLDOUT are never opened. The 150M
original DSC backbone remains frozen; only 256-feature personal residuals
are learned and always experimental.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import random

import chess

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest,
    _read_model, paired_game_inference,
)
from .dsc_four_player_identity import PLAYERS
from .maia_personalization_v7 import _features as chess_features
from .maia_transfer_v6 import _restore_real_histories
from .real_gate_v4 import _position_key
from .real_policy_experiment import (
    _bottom_k, _digest, _metric, _read_json,
    _validated_training_rows, _write_once,
)
from .science_multimodel_cpu import (
    DSC_FIDE_SCENARIO, DSC_SOURCE_COMMIT, DSC150_WEIGHTS_SHA256,
    _dsc_preflight,
)

SCHEMA = "chesssim-dsc150-four-trainable-conditional-residual-v2"
MODEL_SCHEMA = "chesssim-dsc150-four-residual-checkpoint-v2"
SEED = 4261
FIT_PER_PLAYER = 512
TUNE_PER_PLAYER = 48
AUDIT_PER_PLAYER = 48
WIDTH = 256
EPOCHS = 12
ALPHAS = (0.0, 0.25, 0.5, 1.0, 2.0)
BLIND_ALPHA = 1.0
L2 = 0.008


def _arm(game: str) -> str:
    if (not isinstance(game, str) or len(game) != 64 or
            any(c not in "0123456789abcdef" for c in game)):
        raise ValueError("Invalid complete historical game trajectory SHA")
    bucket = int.from_bytes(
        sha256(("dsc-residual-v2|" + game).encode()).digest()[:8], "big") % 10
    return "fit" if bucket <= 6 else "tune" if bucket == 7 else "audit"


def _histogram(cohorts: Path) -> tuple[dict, dict]:
    """No target rows from VALIDATION/HOLDOUT; global FEN-arm embargo."""
    manifest = _read_json(cohorts / "manifest.json")
    by_arm = {slug: {a: [] for a in ("fit", "tune", "audit")}
              for slug in PLAYERS}
    arm_bits = {"fit": 1, "tune": 2, "audit": 4}
    canonical_arms: dict[str, int] = defaultdict(int)
    counted = Counter()
    for row in _validated_training_rows(cohorts, manifest):
        slug = row["player"]
        if slug not in by_arm:
            raise ValueError("Unexpected historical player in training data")
        if row["ply"] < 21:
            continue
        arm = _arm(row["game_sha256"])
        canonical = _position_key(row["policy_input"]["fen"])
        canonical_arms[canonical] |= arm_bits[arm]
        by_arm[slug][arm].append({
            "player": slug,
            "game_sha256": row["game_sha256"],
            "ply": row["ply"],
            "year": row["policy_input"]["year"],
            "fen": row["policy_input"]["fen"],
            "move": row["policy_target"]["move_uci"],
            "_canonical": canonical,
        })
        counted[(slug, arm)] += 1
    conflict = {key for key, bits in canonical_arms.items()
                if bits not in (1, 2, 4)}
    counts = {}
    chosen = {}
    global_sets = {a: set() for a in arm_bits}
    for slug in sorted(PLAYERS):
        chosen[slug] = {}
        counts[slug] = {}
        for arm, limit, per_game in (
            ("fit", FIT_PER_PLAYER, 2),
            ("tune", TUNE_PER_PLAYER, 1),
            ("audit", AUDIT_PER_PLAYER, 1),
        ):
            candidates = [
                {k: v for k, v in r.items() if k != "_canonical"}
                for r in by_arm[slug][arm] if r["_canonical"] not in conflict
            ]
            # Explicit budget based on genuine source availability. Do not
            # borrow a different split or re-sample by observed model score.
            drawn = _bottom_k(candidates, limit=limit, per_game=per_game,
                              seed=SEED)
            if len(drawn) != limit:
                raise ValueError("Insufficient disjoint source decisions in "
                                 + slug + ":" + arm)
            chosen[slug][arm] = drawn
            global_sets[arm].update(r["game_sha256"] for r in drawn)
            counts[slug][arm] = {
                "source_candidate_decisions": counted[(slug, arm)],
                "after_global_position_arm_embargo": len(candidates),
                "sampled_decisions": len(drawn),
                "sampled_games": len({r["game_sha256"] for r in drawn}),
            }
    for arm in global_sets:
        if any(global_sets[arm] & global_sets[other]
               for other in global_sets if other != arm):
            raise ValueError("Source game exposed to two training-year arms")
    for slug in PLAYERS:
        for arm in ("fit", "tune", "audit"):
            rows = chosen[slug][arm]
            if len({(r["game_sha256"], r["ply"]) for r in rows}) != len(rows):
                raise ValueError("Duplicate sampled move")
    return chosen, {"candidate_counts": counts,
                    "globally_embargoed_canonical_positions": len(conflict),
                    "partition":"sha256(game, dsc-residual-v2) mod10; FIT 0..6 TUNE7 AUDIT8..9"}


def _indices(board: chess.Board, move: chess.Move) -> tuple[int, ...]:
    """Sparse deterministic hashed chess semantics; only pre-target state."""
    piece = board.piece_at(move.from_square)
    if piece is None or move not in board.legal_moves:
        raise ValueError("Invalid conditional-logit legal candidate")
    semantic = list(chess_features(board, move, assumed_legal=True))
    material = sum(len(board.pieces(p,chess.WHITE)) +
                   len(board.pieces(p,chess.BLACK))
                   for p in chess.PIECE_TYPES)
    phase = "opening" if material >= 27 else "middle" if material >= 16 else "late"
    rank = chess.square_rank(move.to_square)
    file = chess.square_file(move.to_square)
    semantic += [
        "phase:" + phase,
        "phase_piece:" + phase + ":" + piece.symbol().upper(),
        "phase_capture:" + phase + ":" + str(board.is_capture(move)),
        "target_quadrant:" + str(file//2) + ":" + str(rank//2),
        "file_displacement:" + str(abs(chess.square_file(move.from_square)-file)),
        "rank_displacement:" + str(abs(chess.square_rank(move.from_square)-rank)),
    ]
    # Signed feature hashing keeps model size fixed independent of corpus.
    result = []
    for token in semantic:
        digest = sha256(token.encode("utf-8")).digest()
        result.append(int.from_bytes(digest[:4],"big") % WIDTH)
    return tuple(result)


def _encode(row: dict, distribution: dict[str, float]) -> dict:
    board = chess.Board(row["fen"])
    legal = {move.uci() for move in board.legal_moves}
    if (legal != set(distribution) or
            any(type(p) not in (float, int) or not math.isfinite(p) or p <= 0
                for p in distribution.values()) or
            not 0.999 <= sum(distribution.values()) <= 1.001):
        raise ValueError("Not genuine complete legal-move DSC probabilities")
    if row["move"] not in legal:
        raise ValueError("Historic human move is illegal")
    moves = sorted(legal)
    return {
        "features": [_indices(board,chess.Move.from_uci(move)) for move in moves],
        "offsets": [math.log(distribution[move]) for move in moves],
        "moves": moves,
        "target": moves.index(row["move"]),
    }


def _scores(example: dict, weights: list[float], *,
            generic: list[float] | None = None,
            alpha: float = 1.0) -> list[float]:
    if len(weights) != WIDTH or (generic is not None and len(generic) != WIDTH):
        raise ValueError("Invalid personal feature-head vector")
    anchor = generic if generic is not None else [0.0] * WIDTH
    return [
        off + sum((anchor[j] + alpha*(weights[j]-anchor[j]))
                  for j in features)
        for off, features in zip(example["offsets"],example["features"])
    ]


def _distribution(example: dict, weights: list[float], *,
                  generic: list[float] | None = None,
                  alpha: float = 1.0) -> list[float]:
    values=_scores(example,weights,generic=generic,alpha=alpha)
    big=max(values)
    exp=[math.exp(v-big) for v in values]
    total=sum(exp)
    return [v/total for v in exp]


def _prediction(example: dict, weights: list[float],
                generic: list[float], alpha: float) -> str:
    probabilities=_distribution(example,weights,generic=generic,alpha=alpha)
    idx=min(range(len(probabilities)),key=lambda i:(-probabilities[i],i))
    return example["moves"][idx]


def _fit(examples: list[dict], seed: int, *,
         generic: list[float] | None = None) -> list[float]:
    """Bounded SGD on actual human labels with original DSC log-prob offset.

    FIT-only targets contribute to gradient. No neural weight is modified.
    """
    if len(examples) < 300:
        raise ValueError("Insufficient real FIT observations")
    anchor = generic[:] if generic is not None else [0.0]*WIDTH
    weights = anchor[:]
    rng=random.Random(seed)
    order=list(range(len(examples)))
    for epoch in range(EPOCHS):
        rng.shuffle(order)
        rate=0.045 / (1 + epoch * 0.25)
        for index in order:
            sample=examples[index]
            prob=_distribution(sample,weights)
            gradient=defaultdict(float)
            for features, p in zip(sample["features"],prob):
                for j in features:
                    gradient[j] -= p
            for j in sample["features"][sample["target"]]:
                gradient[j] += 1.0
            # Smooth shrinkage toward generic prior; size is bounded.
            for j in range(WIDTH):
                weights[j] *= (1-rate*L2)
                weights[j] += rate * L2 * anchor[j]
            for j, g in gradient.items():
                weights[j] = max(-3.0,min(3.0,weights[j]+rate*g))
    return [round(v,8) for v in weights]


def _tune_alpha(rows: list[dict], examples: list[dict],
                individual: list[float], generic: list[float]) -> float:
    if len(rows) != len(examples) or len({r["game_sha256"] for r in rows}) < 30:
        raise ValueError("Insufficient disjoint TUNE games")
    base=[_prediction(e,generic,generic,0) for e in examples]
    results={}
    for alpha in ALPHAS:
        modified=[_prediction(e,individual,generic,alpha) for e in examples]
        per_game=defaultdict(list)
        for row,a,b in zip(rows,modified,base):
            per_game[row["game_sha256"]].append(
                int(a==row["move"])-int(b==row["move"]))
        gain=sum(sum(v)/len(v) for v in per_game.values())/len(per_game)
        changes=sum(a!=b for a,b in zip(modified,base))
        logloss=-sum(math.log(max(
            _distribution(e,individual,generic=generic,alpha=alpha)[e["target"]],1e-15))
            for e in examples)/len(examples)
        results[alpha]=(gain-0.008*changes/len(rows),-logloss,-alpha)
    return max(ALPHAS,key=lambda a:results[a])


def _blind(rows: list[dict], examples: list[dict], weights: dict,
           generic: list[float], seed: int) -> dict:
    if len(rows) != len(examples):
        raise ValueError("Incompatible blind game labels")
    matrix={p:{q:0 for q in PLAYERS} for p in PLAYERS}
    groups=defaultdict(list)
    for row, example in zip(rows,examples):
        probs={
            player:_distribution(example,weights[player],
                                 generic=generic,alpha=BLIND_ALPHA)[example["target"]]
            for player in PLAYERS
        }
        found=min(PLAYERS,key=lambda p:(-probs[p],p))
        matrix[row["player"]][found]+=1
        groups[row["game_sha256"]].append((row["player"],int(found==row["player"])))
    counts={p:sum(matrix[p].values()) for p in PLAYERS}
    if min(counts.values()) < 30:
        return {"status":"UNDERPOWERED_IDENTITY","counts":counts}
    correct=sum(matrix[p][p] for p in PLAYERS)
    balanced=sum(matrix[p][p]/counts[p] for p in PLAYERS)/4
    clusters=[groups[g] for g in sorted(groups)]
    rng=random.Random(seed)
    samples=[]
    for _ in range(2000):
        num=Counter();den=Counter()
        for _j in clusters:
            for slug,hit in clusters[rng.randrange(len(clusters))]:
                num[slug]+=hit;den[slug]+=1
        if all(den[s] for s in PLAYERS):
            samples.append(sum(num[s]/den[s] for s in PLAYERS)/4)
    if len(samples)<1980:
        raise ValueError("Bootstrap unstable from paired historical games")
    samples.sort()
    return {
        "status":"BLIND_REAL_HISTORICAL_AUDIT_DEVELOPMENT",
        "n":len(rows),"globally_distinct_games":len(clusters),
        "correct":correct,"accuracy":correct/len(rows),
        "balanced_accuracy":balanced,"nominal_chance":0.25,
        "majority_class_baseline":max(counts.values())/len(rows),
        "per_player_n":counts,"confusion_matrix":matrix,
        "balanced_game_bootstrap_95_ci":[
            samples[int(.025*len(samples))],
            samples[min(len(samples)-1,math.ceil(.975*len(samples))-1)]],
    }


def _get_original_probabilities(cohorts: Path, v3: dict, sampled: dict,
                                archive_dir: Path | None) -> tuple[dict,dict]:
    histories,hashes=_restore_real_histories(cohorts,v3,sampled,archive_dir)
    if hashes!=v3["source_pgn_sha256"]:
        raise ValueError("Source PGN hash changed during FIT")
    _dsc_preflight()
    from chessds_dsc.predictor import DSC, PositionInput
    model=DSC.load("dsc-150m",device="cpu")
    all_out={}
    for slug in PLAYERS:
        rows=sampled[slug]
        out=[]
        for start in range(0,len(rows),8):
            chunk=rows[start:start+8]
            boards=[histories[slug][(r["game_sha256"],r["ply"])]
                    for r in chunk]
            inputs=[
                PositionInput(
                    board,white_elo=DSC_FIDE_SCENARIO,
                    black_elo=DSC_FIDE_SCENARIO,
                    time_control="classical",rating_system="fide",
                    use_history=True)
                for board in boards
            ]
            predictions=model.predict_batch(inputs)
            if len(predictions)!=len(chunk):
                raise ValueError("DSC batch did not provide every position")
            for row,board,prediction in zip(chunk,boards,predictions):
                if (prediction.inputs.fen != row["fen"] or
                        prediction.inputs.history_boards < 1):
                    raise ValueError("DSC did not encode true PGN history")
                out.append(_encode(row,dict(prediction.moves)))
        all_out[slug]=out
    del model
    return all_out,hashes


def run(cohorts:Path,v3_path:Path,model_out:Path,report_out:Path, *,
        archive_dir: Path | None) -> dict:
    v3=_read_model(v3_path,V3_SCHEMA)
    _assert_training_manifest(cohorts,v3)
    if set(v3["profiles"])!=set(PLAYERS):
        raise ValueError("Not the fixed historical four-player roster")
    portions, embargo=_histogram(cohorts)
    sampled={slug:sum((portions[slug][arm] for arm in ("fit","tune","audit")),[])
             for slug in PLAYERS}
    raw,hashes=_get_original_probabilities(cohorts,v3,sampled,archive_dir)
    datasets={}
    for slug in PLAYERS:
        cursor=0; datasets[slug]={}
        for arm in ("fit","tune","audit"):
            length=len(portions[slug][arm])
            datasets[slug][arm]=raw[slug][cursor:cursor+length]
            cursor+=length
        if cursor!=len(raw[slug]):
            raise ValueError("Misaligned model predictions and historical arms")
    pooled=[e for slug in PLAYERS for e in datasets[slug]["fit"]]
    generic=_fit(pooled,SEED+100)
    individual={slug:_fit(datasets[slug]["fit"],SEED+101+i,generic=generic)
                for i,slug in enumerate(PLAYERS)}
    checkpoint={
        "schema":MODEL_SCHEMA,"status":"TRAINED_ON_REAL_FIT_NOT_APPROVED",
        "backbone":"DSC-150M_ORIGINAL_FROZEN",
        "original_dsc_sha256":DSC150_WEIGHTS_SHA256,
        "original_dsc_code_commit":DSC_SOURCE_COMMIT,
        "train_manifest_sha256":v3["train_manifest_sha256"],
        "source_pgn_sha256":hashes,
        "seed":SEED,"width":WIDTH,"epochs":EPOCHS,"l2":L2,
        "generic_weights":generic,"individual_weights":individual,
        "fit_decisions":{s:len(datasets[s]["fit"]) for s in PLAYERS},
        "final_holdout_opened":False,"official_promotion":"DENIED",
    }
    checkpoint["checkpoint_sha256"]=_digest(checkpoint)
    _write_once(model_out,checkpoint)
    result={}
    all_rows=[];all_encoded=[]
    all_personal=[];all_generic=[];all_dsc=[]
    for index,slug in enumerate(PLAYERS):
        rows=portions[slug]["audit"]
        examples=datasets[slug]["audit"]
        tune=_tune_alpha(portions[slug]["tune"],datasets[slug]["tune"],
                          individual[slug],generic)
        original=[_prediction(e,[0.0]*WIDTH,[0.0]*WIDTH,0) for e in examples]
        pooled_pred=[_prediction(e,generic,generic,0) for e in examples]
        personal=[_prediction(e,individual[slug],generic,tune) for e in examples]
        wrong=PLAYERS[(index+1)%4]
        swapped=[_prediction(e,individual[wrong],generic,BLIND_ALPHA)
                 for e in examples]
        fixed=[_prediction(e,individual[slug],generic,BLIND_ALPHA)
               for e in examples]
        vs_generic=paired_game_inference(
            rows,personal,pooled_pred,seed=SEED+300+index,repetitions=2000)
        vs_wrong=paired_game_inference(
            rows,fixed,swapped,seed=SEED+400+index,repetitions=2000)
        result[slug]={
            "fit_decisions":len(datasets[slug]["fit"]),
            "tune_games":len({r["game_sha256"] for r in portions[slug]["tune"]}),
            "audit_games":len({r["game_sha256"] for r in rows}),
            "chosen_tune_alpha":tune,
            "dsc_original":_metric(rows,original),
            "trained_generic":_metric(rows,pooled_pred),
            "individual":_metric(rows,personal),
            "swapped_fixed_alpha":_metric(rows,swapped),
            "fixed_correct":_metric(rows,fixed),
            "paired_individual_vs_generic":vs_generic,
            "fixed_correct_vs_swapped":vs_wrong,
            "personalization_gate": "DENIED",
        }
        all_rows.extend(rows);all_encoded.extend(examples)
        all_dsc.extend(original);all_generic.extend(pooled_pred)
        all_personal.extend(personal)
    blind=_blind(all_rows,all_encoded,individual,generic,SEED+500)
    aggregate=paired_game_inference(
        all_rows,all_personal,all_generic,seed=SEED+550,repetitions=2000)
    passed_blind=(blind["status"]=="BLIND_REAL_HISTORICAL_AUDIT_DEVELOPMENT"
                  and blind["balanced_game_bootstrap_95_ci"][0] > 0.25)
    for slug in PLAYERS:
        g=result[slug]
        passed=(passed_blind and g["chosen_tune_alpha"] > 0
                and g["paired_individual_vs_generic"]["bootstrap_95_ci"][0] > 0
                and g["fixed_correct_vs_swapped"]["bootstrap_95_ci"][0] > 0)
        g["personalization_gate"]="RESEARCH_CANDIDATE_ONLY" if passed else "DENIED"
    report={
        "schema":SCHEMA,"status":"DEVELOPMENT_TRAIN_YEAR_AUDIT_NOT_FINAL",
        "players":result,"blind_identity":blind,
        "aggregate_personal_vs_generic":aggregate,
        "original_dsc":_metric(all_rows,all_dsc),
        "trained_generic":_metric(all_rows,all_generic),
        "trained_personal":_metric(all_rows,all_personal),
        "real_split":embargo,
        "original_dsc_sha256":DSC150_WEIGHTS_SHA256,
        "trained_weights_sha256":checkpoint["checkpoint_sha256"],
        "train_manifest_sha256":v3["train_manifest_sha256"],
        "source_pgn_sha256":hashes,
        "seed":SEED,"fit_limit_per_player":FIT_PER_PLAYER,
        "tune_limit_per_player":TUNE_PER_PLAYER,
        "audit_limit_per_player":AUDIT_PER_PLAYER,
        "full_corpus_processed":False,
        "official_personality_promotion":"DENIED",
        "holdout_opened":False,"validation_opened":False,
        "limitations":[
            "Neural DSC-150M backbone unchanged, only a 256-feature residual head was fitted.",
            "512 FIT historical decisions sampled per player for CPU budget; not the entire historical corpus.",
            "This is a new train-year developmental split, not independent chronological HOLDOUT.",
            "Original pretraining of DSC may include the same historical PGNs.",
            "Game-disjoint samples may contain position transpositions within an arm.",
            "FIDE 2400 is a fixed scenario; no claim of matching historical personal Elo.",
            "Research candidate gate cannot authorize tournament integration or override Rodent.",
        ],
    }
    report["report_sha256"]=_digest(report)
    _write_once(report_out,report)
    return report


def main(argv=None)->int:
    p=argparse.ArgumentParser(description="Four-player real conditional-logit residual")
    for name in ("cohorts","v3-model","model-out","report-out"):
        p.add_argument("--"+name,required=True,type=Path)
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch",action="store_true")
    src.add_argument("--archive-dir",type=Path)
    args=p.parse_args(argv)
    report=run(args.cohorts,args.v3_model,args.model_out,args.report_out,
               archive_dir=args.archive_dir)
    print(json.dumps({
        "checkpoint":report["trained_weights_sha256"],
        "blind_identity":report["blind_identity"],
        "personal_gates":{s:p["personalization_gate"] for s,p in report["players"].items()},
        "aggregate":report["aggregate_personal_vs_generic"],
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
