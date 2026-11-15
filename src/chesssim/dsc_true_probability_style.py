"""Actual DSC-150M move-probability adapter; exploratory TRAIN-year TUNE/AUDIT.

No change to external DSC weights. Original legal-move probabilities come from
the pinned ChessDS DSC Python API, not MultiPV rank or WDL. Personal weights
were fitted ONLY on FIT historical human moves; TUNE/AUDIT do not update them.
This protocol follows a previously inspected train-year audit and is therefore
new DEVELOPMENT, not new independent/final confirmation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import random

import chess

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest,
    _read_model, paired_game_inference,
)
from .dsc_four_player_identity import (
    PLAYERS, _centered_weights, _independent_split,
)
from .maia_personalization_v7 import (
    _calibration_samples, _features, _load_adapter,
)
from .maia_transfer_v6 import _restore_real_histories
from .real_policy_experiment import _digest, _metric, _write_once
from .science_multimodel_cpu import (
    DSC_FIDE_SCENARIO, DSC_SOURCE_COMMIT,
    DSC150_WEIGHTS_SHA256, _dsc_preflight,
)

SCHEMA="chesssim-dsc-original-probability-personalization-four-v1"
ALPHAS=(0.0, 0.25, 0.5, 1.0, 2.0)
BLIND_ALPHA=1.0
SEED=2042


def _probabilities(original: dict[str,float], board: chess.Board,
                   weights: dict[str,float], alpha: float) -> dict[str,float]:
    """The original REAL normalized DSC legal-move probability is the prior."""
    if alpha not in ALPHAS and alpha != BLIND_ALPHA:
        raise ValueError("Unknown preregistered residual strength")
    legal = {m.uci() for m in board.legal_moves}
    if set(original) != legal or not original:
        raise ValueError("Incomplete DSC legal move distribution")
    if any(type(p) not in (float,int) or not math.isfinite(p) or p <= 0
           for p in original.values()):
        raise ValueError("Invalid or truncated original model probability")
    if not 0.999 <= sum(original.values()) <= 1.001:
        raise ValueError("DSC policy is not normalized")
    logits = {}
    for uci,p in original.items():
        move=chess.Move.from_uci(uci)
        bias=sum(weights.get(f,0.0) for f in
                 _features(board,move,assumed_legal=True))
        logits[uci] = math.log(p) + alpha*bias
    greatest=max(logits.values())
    values={uci:math.exp(x-greatest) for uci,x in logits.items()}
    total=sum(values.values())
    return {uci:value/total for uci,value in values.items()}


def _best(distribution: dict[str,float]) -> str:
    return min(distribution, key=lambda move: (-distribution[move], move))


def _tune_alpha(rows: list[dict], original: list[dict[str,float]],
                weights: dict[str,float]) -> float:
    if len(rows)!=len(original) or len(rows)<24:
        raise ValueError("Underpowered tuning set")
    by_alpha={}
    for alpha in ALPHAS:
        by_game=defaultdict(list)
        loss=0.0
        changes=0
        for row,base in zip(rows,original):
            board=chess.Board(row["fen"])
            adapted=_probabilities(base,board,weights,alpha)
            pred=_best(adapted)
            raw=_best(base)
            by_game[row["game_sha256"]].append(
                int(pred==row["move"])-int(raw==row["move"]))
            loss -= math.log(max(adapted[row["move"]],1e-12))
            changes+=pred!=raw
        gain=sum(sum(x)/len(x) for x in by_game.values())/len(by_game)
        # Explicit trade-off: stronger top1 performance first, then
        # lower log-loss and fewer interventions at the same top1 gain.
        by_alpha[alpha]=(gain-0.008*changes/len(rows),
                         -loss/len(rows), -alpha)
    return max(ALPHAS,key=lambda a:by_alpha[a])


def _blind(rows: list[dict], base: list[dict[str,float]],
           weights: dict, seed: int) -> dict:
    if len(rows)!=len(base):
        raise ValueError("Unaligned blind identity boards")
    confusion={s:{c:0 for c in PLAYERS} for s in PLAYERS}
    groups=defaultdict(list)
    for row, original in zip(rows,base):
        board=chess.Board(row["fen"])
        scores={
            player:_probabilities(original,board,weights[player],
                                  BLIND_ALPHA)[row["move"]]
            for player in PLAYERS
        }
        predicted=min(PLAYERS,key=lambda p:(-scores[p],p))
        confusion[row["player"]][predicted]+=1
        groups[row["game_sha256"]].append(
            (row["player"],int(predicted==row["player"])))
    counts={s:sum(confusion[s].values()) for s in PLAYERS}
    if any(count<30 for count in counts.values()):
        return {"status":"UNDERPOWERED","counts":counts}
    balanced=sum(confusion[s][s]/counts[s] for s in PLAYERS)/4
    majority=max(counts.values())/len(rows)
    rng=random.Random(seed)
    clusters=[groups[g] for g in sorted(groups)]
    draws=[]
    for _ in range(2000):
        sizes=Counter()
        wins=Counter()
        for _j in clusters:
            for slug,hit in clusters[rng.randrange(len(clusters))]:
                sizes[slug]+=1
                wins[slug]+=hit
        if all(sizes[s] for s in PLAYERS):
            draws.append(sum(wins[s]/sizes[s] for s in PLAYERS)/4)
    if len(draws)<1980:
        raise ValueError("Global-game bootstrap unstable")
    draws.sort()
    return {
        "status":"DIAGNOSTIC_4_WAY_BLIND_CLASSIFIER",
        "n":len(rows),"globally_distinct_games":len(clusters),
        "balanced_accuracy":balanced,"nominal_chance":0.25,
        "majority_class_baseline":majority,
        "confusion":confusion,
        "balanced_game_bootstrap_95_ci":[draws[int(.025*len(draws))],
                          draws[min(len(draws)-1,math.ceil(.975*len(draws))-1)]],
        "per_player_counts":counts,
    }


def run(cohorts: Path, v3_path: Path, adapter_path: Path,
        out: Path, *, archive_dir:Path | None) -> dict:
    v3=_read_model(v3_path,V3_SCHEMA)
    _assert_training_manifest(cohorts,v3)
    adapter=_load_adapter(adapter_path,v3)
    if set(adapter["profiles"]) != set(PLAYERS) or adapter["sum_fitted_decisions"] < 100000:
        raise ValueError("Only genuine original four-player full FIT model accepted")
    weights=_centered_weights(adapter["profiles"])
    tune,audit=_calibration_samples(cohorts,v3,adapter,48,SEED)
    tune,audit=_independent_split(tune,audit)
    if any(len(tune[s])<40 or len(audit[s])<30 for s in PLAYERS):
        raise ValueError("Too few disjoint real fit-year game decisions")
    sampled={s:tune[s]+audit[s] for s in PLAYERS}
    boards,sources=_restore_real_histories(cohorts,v3,sampled,archive_dir)
    if sources!=v3["source_pgn_sha256"]:
        raise ValueError("PGN source drift")
    _dsc_preflight()
    from chessds_dsc.predictor import DSC, PositionInput
    from chessds_dsc.registry import resolve
    entry=resolve("dsc-150m")
    if entry.weights_sha256!=DSC150_WEIGHTS_SHA256:
        raise ValueError("Unverified original DSC model")
    neural=DSC.load("dsc-150m",device="cpu")
    original={}
    # Batch original full policy probabilities, one checkpoint in memory.
    for slug in PLAYERS:
        predictions=[]
        current=sampled[slug]
        for begin in range(0,len(current),8):
            chunk=current[begin:begin+8]
            items=[
                PositionInput(
                    boards[slug][(r["game_sha256"],r["ply"])],
                    white_elo=DSC_FIDE_SCENARIO,
                    black_elo=DSC_FIDE_SCENARIO,
                    time_control="classical",rating_system="fide",
                    use_history=True)
                for r in chunk
            ]
            answers=neural.predict_batch(items)
            for r,item,pred in zip(chunk,items,answers):
                board=item.board
                if pred.inputs.fen!=r["fen"] or pred.inputs.history_boards<1:
                    raise ValueError("DSC history/FEN mismatch")
                move_prob=dict(pred.moves)
                _probabilities(move_prob,board,{},0)
                predictions.append(move_prob)
        original[slug]=predictions
    del neural
    reports={}
    all_rows,all_baseline,all_style,all_probs=[],[],[],[]
    for i,slug in enumerate(PLAYERS):
        cut=len(tune[slug])
        chosen=_tune_alpha(tune[slug],original[slug][:cut],weights[slug])
        rows=audit[slug]
        probabilities=original[slug][cut:]
        true_style=[
            _best(_probabilities(p,chess.Board(r["fen"]),weights[slug],chosen))
            for r,p in zip(rows,probabilities)]
        diagnostic=[
            _best(_probabilities(p,chess.Board(r["fen"]),weights[slug],BLIND_ALPHA))
            for r,p in zip(rows,probabilities)]
        generic=[_best(p) for p in probabilities]
        other=PLAYERS[(i+1)%4]
        wrong=[
            _best(_probabilities(p,chess.Board(r["fen"]),weights[other],BLIND_ALPHA))
            for r,p in zip(rows,probabilities)]
        paired=paired_game_inference(rows,true_style,generic,
                                     seed=SEED+200+i,repetitions=2000)
        contrast=paired_game_inference(rows,diagnostic,wrong,
                                      seed=SEED+250+i,repetitions=2000)
        reports[slug]={
            "tune_decisions":cut,"audit_decisions":len(rows),
            "audit_distinct_games":len({r["game_sha256"] for r in rows}),
            "alpha_selected_train_tune_only":chosen,
            "dsc_original":_metric(rows,generic),
            "personal_adjustment":_metric(rows,true_style),
            "fixed_alpha_style":_metric(rows,diagnostic),
            "wrong_label":_metric(rows,wrong),
            "paired_vs_original":paired,
            "fixed_alpha_paired_correct_vs_wrong":contrast,
            "experimental_gate": (
                chosen>0 and paired["bootstrap_95_ci"][0]>0
                and contrast["bootstrap_95_ci"][0]>0),
        }
        all_rows.extend(rows)
        all_baseline.extend(generic)
        all_style.extend(true_style)
        all_probs.extend(probabilities)
    blind=_blind(all_rows,all_probs,weights,SEED+300)
    gain=paired_game_inference(all_rows,all_style,all_baseline,
                                seed=SEED+400,repetitions=2000)
    result={
        "schema":SCHEMA,
        "status":"REAL_DSC_PROBABILITY_RERANKER_DEVELOPMENT_NOT_FINAL",
        "source_pgn_sha256":sources,
        "model_checkpoint_sha256":DSC150_WEIGHTS_SHA256,
        "model_code_commit":DSC_SOURCE_COMMIT,
        "v7_fit_model_sha256":adapter["model_sha256"],
        "sum_genuine_fit_decisions":adapter["sum_fitted_decisions"],
        "alpha_grid":list(ALPHAS),"blind_fixed_alpha":BLIND_ALPHA,
        "players":reports,"audit_vs_original":gain,
        "blind_identity":blind,
        "official_personality_promotion":"DENIED",
        "final_holdout_opened":False,
        "limitations":[
            "All normalised moves are original DSC-150M full softmax, not rank proxy.",
            "The residuals remain those of earlier FIT semantic weights, not LoRA or full fine-tuning.",
            "Previously inspected TRAIN-year audit used in this developmental follow-up.",
            "External pretraining not decontaminated; original Maïa/DSC not compared here.",
            "DSC 2400 FIDE rating is a scenario, not a historical rating assignment.",
            "No chess gameplay, external Stockfish risk certification or identity approval.",
        ],
    }
    result["report_sha256"]=_digest(result)
    _write_once(out,result)
    return result


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Real DSC-150M full softmax four-player style development")
    for n in ("cohorts","v3-model","adapter","out"):
        p.add_argument("--"+n,required=True,type=Path)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch",action="store_true")
    source.add_argument("--archive-dir",type=Path)
    args=p.parse_args(argv)
    result=run(args.cohorts,args.v3_model,args.adapter,args.out,
               archive_dir=args.archive_dir)
    print(json.dumps({"status":result["status"],
                      "blind":result["blind_identity"],
                      "players":{s:r["experimental_gate"] for s,r in result["players"].items()}},
                     sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
