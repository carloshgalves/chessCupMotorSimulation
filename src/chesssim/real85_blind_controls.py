"""Within-TRAIN TUNE paired, wrong-name historical neural-style controls.

No model is updated. All models are evaluated against EXACTLY the same
historical decisions and same deterministic legal alternatives for each
source game, even when games are shared across historical players.

Differences are not assumed to exist; CI succeeds when models are
indistinguishable because the research question is empirical.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import mean

import chess

from .matches import file_hash
from .real85_neural_stream import (
    GAME_SCHEMA, verify, neural_arm,
)
from .real85_online_residual import (
    SCHEMA as MODEL_SCHEMA, REPORT_SCHEMA, _alternative, _initial,
    _pair, _read_checkpoint, SEED,
)

SCHEMA = "chesssim-real85-frozen-identity-controls-v1"


def paired_interval(games: list[float], *, reps: int = 500,
                    seed: int = 20261116) -> dict:
    """Nonparametric bootstrap over independent-by-player TEST-free TUNE games."""
    if (type(reps) is not int or not 100 <= reps <= 5000 or
            type(seed) is not int or seed < 0):
        raise ValueError("Invalid paired bootstrap budget")
    if not games:
        return {"n_games":0, "mean_difference":None,
                "confidence_95":None,"status":"NO_COMPARABLE_GAMES"}
    rng = random.Random(seed)
    n = len(games)
    draws = sorted(mean(games[rng.randrange(n)] for _ in range(n))
                   for _ in range(reps))
    return {
        "n_games":n,
        "mean_difference":round(mean(games),8),
        "confidence_95":[round(draws[int((reps-1)*0.025)],8),
                         round(draws[int((reps-1)*0.975)],8)],
        "status":"DESCRIPTIVE_GAME_BOOTSTRAP_NOT_FINAL_HOLDOUT",
    }


def _load_models(weights: Path, roster: list[str], original_manifest: str) -> dict:
    result=json.loads((weights/"report.json").read_text())
    if (result.get("schema")!=REPORT_SCHEMA or
            result.get("status")!="ALL_REQUESTED_PLAYER_RESIDUALS_FITTED" or
            result.get("input_manifest_sha256")!=original_manifest or
            result.get("seed")!=SEED or
            result.get("maia_dsc_backbone_finetuned") is not False):
        raise ValueError("Expected full FIT model set with exact source manifest and seed")
    if set(roster) - set(result.get("players",{})):
        raise ValueError("Missing source-locked trained historical neural model")
    models={}
    for slug in roster:
        row=result["players"][slug]
        path=weights/row["checkpoint_file"]
        if (path.parent!=weights or path.is_symlink() or
                file_hash(path)!=row["checkpoint_sha256"] or
                row["status"]!="FITTED_ALL_REAL_FOCAL_MOVES_CPU" or
                row["fit_games_processed"]!=row["fit_games_available"] or
                row["focal_decisions_processed"]!=row["fit_focal_decisions_available"]):
            raise ValueError("Unsafe or incomplete learned player checkpoint")
        state=_read_checkpoint(path)
        if (state["schema"]!=MODEL_SCHEMA or state["player"]!=slug or
                state["input_manifest_sha256"]!=original_manifest or
                state["completed_games"]!=row["fit_games_processed"] or
                state["focal_decisions"]!=row["focal_decisions_processed"] or
                state["gradient_steps"]!=row["gradient_steps"]):
            raise ValueError("Trained checkpoint and report disagree")
        models[slug]=state["model"]
    return models


def _game(row: dict, models: dict[str,dict], *, slug: str,
          generic: dict) -> dict:
    """Actual source game, identical FENs and alternative moves for all models."""
    if (row.get("schema")!=GAME_SCHEMA or row.get("player")!=slug or
            row.get("arm")!="tune" or neural_arm(row["game_sha256"])!="tune"):
        raise ValueError("FIT/TEST trajectory admitted to blind TUNE")
    board=chess.Board(row["initial_fen"])
    focal=row["focal_color"]=="white"
    losses={name:0.0 for name in models}
    losses["generic-untrained"]=0.0
    decisions=0
    alternatives=0
    for ply,uci in enumerate(row["uci_moves"],1):
        real=chess.Move.from_uci(uci)
        if real not in board.legal_moves:
            raise ValueError("Illegal historical TUNE move")
        if board.turn==focal:
            decisions+=1
            negative=_alternative(board,real,row["game_sha256"],ply,SEED)
            if negative is not None:
                alternatives+=1
                for name,model in models.items():
                    loss,_=_pair(model,board,real,negative,update=False)
                    losses[name]+=loss
                base_loss,_=_pair(generic,board,real,negative,update=False)
                losses["generic-untrained"]+=base_loss
        board.push(real)
    if decisions!=row["focal_decisions"]:
        raise ValueError("Historical TUNE focal decisions mismatch")
    return {"decisions":decisions,"comparisons":alternatives,"losses":losses}


def audit(inputs: Path, weights: Path, out: Path, *,
          players: list[str] | None = None,
          bootstrap_reps: int = 500) -> dict:
    inputs=Path(inputs).resolve(strict=True)
    weights=Path(weights).resolve(strict=True)
    manifest=inputs/"manifest.json"
    m=json.loads(manifest.read_text())
    tested=verify(inputs)
    if tested["status"]!="VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES":
        raise ValueError("Incomplete original real85 stream")
    roster=list(players or ("fischer","tal","karpov","kasparov"))
    if (len(roster)<2 or len(set(roster))!=len(roster) or
            any(m["players"].get(slug,{}).get("status")!=
                "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING" for slug in roster)):
        raise ValueError("Blind control requires >=2 complete real historical profiles")
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Must publish a new review report")
    models=_load_models(weights,roster,file_hash(manifest))
    generic=_initial(SEED)
    baseline_model_hashes={slug:sha256(json.dumps(model,sort_keys=True).encode()).hexdigest()
                           for slug,model in models.items()}
    baseline_model_hashes["generic-untrained"]=sha256(
        json.dumps(generic,sort_keys=True).encode()).hexdigest()
    report={}
    for slug in roster:
        shard=inputs/m["players"][slug]["shards"]["tune"]["file"]
        sample_games=0
        sample_decisions=0
        sample_comparisons=0
        own_minus_wrong=[]
        own_minus_untrained=[]
        individual_loss={name:[] for name in (*roster,"generic-untrained")}
        blind_predictions={}
        ties=0
        with shard.open(encoding="utf-8") as stream:
            for line in stream:
                row=json.loads(line)
                game=_game(row,models,slug=slug,generic=generic)
                sample_games+=1
                sample_decisions+=game["decisions"]
                sample_comparisons+=game["comparisons"]
                if not game["comparisons"]:
                    continue
                losses={name:value/game["comparisons"]
                        for name,value in game["losses"].items()}
                for name in individual_loss:
                    individual_loss[name].append(losses[name])
                own=losses[slug]
                others=[losses[name] for name in roster if name!=slug]
                own_minus_wrong.append(mean(others)-own)
                own_minus_untrained.append(losses["generic-untrained"]-own)
                smallest=min(losses[name] for name in roster)
                winning=[name for name in roster if abs(losses[name]-smallest)<1e-12]
                if len(winning)!=1:
                    ties+=1
                else:
                    winning=winning[0]
                    blind_predictions[winning]=blind_predictions.get(winning,0)+1
        expected=m["players"][slug]
        if (sample_games!=expected["tune_games"] or
                sample_decisions!=expected["tune_focal_decisions_all_phases"] or
                sample_comparisons<=0):
            raise RuntimeError("Blind TUNE game/move population differs from source")
        report[slug]={
            "tune_games":sample_games,
            "tune_focal_decisions":sample_decisions,
            "legal_pairwise_comparisons":sample_comparisons,
            "comparable_game_count":len(own_minus_wrong),
            "per_model_mean_game_logloss":{
                name:round(mean(vals),8) if vals else None
                for name,vals in individual_loss.items()},
            "mean_wrong_minus_correct_logloss":paired_interval(
                own_minus_wrong,reps=bootstrap_reps,seed=20261116),
            "untrained_minus_correct_logloss":paired_interval(
                own_minus_untrained,reps=bootstrap_reps,seed=20261117),
            "blind_prediction_counts":blind_predictions,
            "tied_game_predictions":ties,
            "correct_game_predictions":blind_predictions.get(slug,0),
            "warning":"Overlapping historical styles are acceptable; nonpositive uplift is reported, never hidden.",
        }
    for slug,model in (*models.items(),("generic-untrained",generic)):
        now=sha256(json.dumps(model,sort_keys=True).encode()).hexdigest()
        if now!=baseline_model_hashes[slug]:
            raise RuntimeError(f"Blind TUNE evaluated with parameter mutation: {slug}")
    result={
        "schema":SCHEMA,
        "status":"PAIRED_REAL_TUNE_ONLY_NO_HISTORICAL_IDENTITY_CLAIM",
        "frozen_manifest_sha256":file_hash(manifest),
        "model_report_sha256":file_hash(weights/"report.json"),
        "actual_full_training_weights_changed":False,
        "maia_dsc_checkpoint_changed":False,
        "official_rodent_changed":False,
        "outer_test_used":False,"old_year_holdout_used":False,
        "all_models_same_positions_and_legal_negative_per_player":True,
        "bootstrap_unit":"independent_TUNE_game_not_individual_decision",
        "players":report,
    }
    with out.open("x",encoding="utf-8",newline="") as stream:
        json.dump(result,stream,sort_keys=True,ensure_ascii=False,allow_nan=False)
        stream.write("\n")
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description="Frozen correct vs swapped historical neural TUNE controls")
    p.add_argument("--inputs",type=Path,required=True)
    p.add_argument("--weights",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--players",nargs="+",default=["fischer","tal","karpov","kasparov"])
    p.add_argument("--bootstrap-reps",type=int,default=500)
    a=p.parse_args(argv)
    r=audit(a.inputs,a.weights,a.out,players=a.players,
            bootstrap_reps=a.bootstrap_reps)
    print(json.dumps({"status":r["status"],"players":{
        slug:{"paired_uplift":v["mean_wrong_minus_correct_logloss"],
              "correct_blind_games":v["correct_game_predictions"],
              "comparable_games":v["comparable_game_count"]}
        for slug,v in r["players"].items()}},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
