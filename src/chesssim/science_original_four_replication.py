"""Pre-registered independent-from-V8-VALIDATION comparison of original engines.

Original DSC-150M vs original Maia3-79M on 128 distinct FIT-year games
per Fischer/Karpov/Kasparov/Tal. FIT rows have been used by the DIFFERENT
V7 style adapter, so this is not an independent personalization test.
Neither original model has been refitted by the ChessCup project.
Historical final HOLDOUT and previously inspected VALIDATION stay closed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import time

import chess

from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model,
    paired_game_inference,
)
from .dsc_four_player_identity import PLAYERS, _open_dsc
from .maia_personalization_v7 import (
    MAIA79_HF_REVISION, MAIA79_REPO, MAIA79_FILE,
    MAIA79_CHECKPOINT_SHA256, _arm, FIT_BUCKETS,
    _open_maia, _verify_checkpoint, _original_maia_candidates,
)
from .maia_transfer_v6 import _restore_real_histories, _verify_maia_source_code
from .real_gate_v4 import _position_key
from .real_policy_experiment import (
    _bottom_k, _digest, _metric, _validated_training_rows,
    _read_json, _write_once,
)
from .science_multimodel_cpu import (
    DSC_SOURCE_COMMIT, DSC150_WEIGHTS_SHA256, _candidates,
    _dsc_preflight, _peak_rss_kib,
)

SCHEMA = "chesssim-fixed-original-dsc-vs-maia-fit-replication-four-v1"
SEED = 3109
PAIRED_SEED = 4109
COUNT = 128


def _choose_fit_replication(cohorts: Path, limit: int = COUNT) -> dict:
    if not 2 <= limit <= 128:
        raise ValueError("Unregistered sample size")
    manifest = _read_json(cohorts / "manifest.json")
    by_player = defaultdict(list)
    for row in _validated_training_rows(cohorts, manifest):
        if row["player"] not in PLAYERS:
            raise ValueError("Unexpected player in original fitting corpus")
        if _arm(row["player"], row["game_sha256"]) not in FIT_BUCKETS or row["ply"] < 21:
            continue
        by_player[row["player"]].append({
            "player": row["player"],
            "fen": row["policy_input"]["fen"],
            "move": row["policy_target"]["move_uci"],
            "year": row["year"],
            "game_sha256": row["game_sha256"],
            "ply": row["ply"],
        })
    selected = {}
    observed_games, observed_fens = set(), set()
    for slug in sorted(PLAYERS):
        ranked = _bottom_k(by_player[slug], limit=limit*4, per_game=1, seed=SEED)
        qualified=[]
        for row in ranked:
            canonical=_position_key(row["fen"])
            if (row["game_sha256"] in observed_games
                    or canonical in observed_fens):
                continue
            qualified.append(row)
            observed_games.add(row["game_sha256"])
            observed_fens.add(canonical)
            if len(qualified)==limit:
                break
        if len(qualified)!=limit:
            raise ValueError("Insufficient globally unique historical FIT games: "+slug)
        selected[slug]=qualified
    if len(observed_games)!=4*limit or len(observed_fens)!=4*limit:
        raise ValueError("Duplicate global historical game/position in replication")
    return selected


def run(cohorts: Path, v3_path: Path, out: Path, *,
        archive_dir: Path | None) -> dict:
    v3=_read_model(v3_path,V3_SCHEMA)
    _assert_training_manifest(cohorts,v3)
    if set(v3["profiles"])!=set(PLAYERS):
        raise ValueError("Unexpected historical four-player experimental cohort")
    selected=_choose_fit_replication(cohorts,COUNT)
    board_map, source_hashes=_restore_real_histories(cohorts,v3,selected,archive_dir)
    if source_hashes!=v3["source_pgn_sha256"]:
        raise ValueError("Real historical source changed")
    rows=[r for slug in sorted(selected) for r in selected[slug]]
    boards=[
        board_map[slug][(r["game_sha256"],r["ply"])]
        for slug in sorted(selected) for r in selected[slug]
    ]
    if any(b.fen()!=r["fen"] for b,r in zip(boards,rows)):
        raise ValueError("History/pre-move FEN inconsistency")
    _dsc_preflight()
    _verify_maia_source_code()
    from huggingface_hub import hf_hub_download
    checkpoint=Path(hf_hub_download(
        MAIA79_REPO,filename=MAIA79_FILE,revision=MAIA79_HF_REVISION))
    _verify_checkpoint(checkpoint,known_digest=MAIA79_CHECKPOINT_SHA256)
    t0=time.perf_counter()
    dsc_engine=_open_dsc()
    try:
        dsc=[_candidates(dsc_engine,board) for board in boards]
        dsc_ram=_peak_rss_kib(dsc_engine)
    finally:
        dsc_engine.quit()
    dsc_seconds=round(time.perf_counter()-t0,3)
    t1=time.perf_counter()
    maia_engine=_open_maia("maia3-79m",checkpoint)
    try:
        maia=[_original_maia_candidates(maia_engine,board) for board in boards]
        maia_ram=_peak_rss_kib(maia_engine)
    finally:
        maia_engine.quit()
    maia_seconds=round(time.perf_counter()-t1,3)
    a=[x[0] for x in dsc]
    b=[x[0] for x in maia]
    paired=paired_game_inference(
        rows,a,b,seed=PAIRED_SEED,repetitions=2000)
    supported=(
        paired["macro_gain_candidate_minus_control"]>0 and
        paired["bootstrap_95_ci"][0]>0 and
        paired["paired_sign_flip_two_sided_p"]<=0.05)
    by_player={}
    for slug in sorted(PLAYERS):
        index=[i for i,r in enumerate(rows) if r["player"]==slug]
        focused=[rows[i] for i in index]
        moves_dsc=[dsc[i] for i in index]
        moves_maia=[maia[i] for i in index]
        by_player[slug]={
            "games":len({r["game_sha256"] for r in focused}),
            "dsc":_metric(focused,[x[0] for x in moves_dsc]),
            "maia79":_metric(focused,[x[0] for x in moves_maia]),
            "dsc_top3_correct":sum(r["move"] in p[:3] for r,p in zip(focused,moves_dsc)),
            "maia79_top3_correct":sum(r["move"] in p[:3] for r,p in zip(focused,moves_maia)),
            "paired_descriptive":paired_game_inference(
                focused,[x[0] for x in moves_dsc],
                [x[0] for x in moves_maia],
                seed=PAIRED_SEED+1+len(by_player),repetitions=2000),
        }
    result={
        "schema":SCHEMA,
        "status":"ORIGINAL_MODEL_FIT_YEAR_REPLICATION_NOT_FINAL_HOLDOUT",
        "pre_registration_issue":157,
        "player_set":list(sorted(PLAYERS)),
        "sample_seed":SEED,
        "one_per_globally_distinct_game":True,
        "min_ply":21,
        "games":len({r["game_sha256"] for r in rows}),
        "decisions":len(rows),
        "per_player":by_player,
        "overall":{"dsc_top1":_metric(rows,a),
                   "maia79_top1":_metric(rows,b),
                   "dsc_top3_correct":sum(r["move"] in c[:3] for r,c in zip(rows,dsc)),
                   "maia79_top3_correct":sum(r["move"] in c[:3] for r,c in zip(rows,maia)),
                   "paired":paired,"supports_dsc_superiority":supported},
        "engines":{
            "dsc":{"code_commit":DSC_SOURCE_COMMIT,
                   "weights_sha256":DSC150_WEIGHTS_SHA256,
                   "scenario":"FIDE_2400_CLASSICAL",
                   "seconds":dsc_seconds,"peak_rss_kib":dsc_ram},
            "maia79":{"weights_sha256":MAIA79_CHECKPOINT_SHA256,
                      "revision":MAIA79_HF_REVISION,
                      "scenario":"LICHESS_2400",
                      "seconds":maia_seconds,"peak_rss_kib":maia_ram}},
        "train_manifest_sha256":v3["train_manifest_sha256"],
        "source_pgn_sha256":source_hashes,
        "validation_opened":False,"final_holdout_opened":False,
        "personality_promoted":False,
        "limitations":[
            "Checkpoints were selected after inspecting V8 validation, but not these new FIT-year games.",
            "FIT rows were used to train a separate V7 adapter; no personalized inference is evaluated here.",
            "Original Maia/DSC upstream pretraining has not been decontaminated against historic source PGNs.",
            "DSC FIDE and Maia Lichess Elo=2400 are not equivalent player skill scales.",
            "No claim of four distinct personal style engines, Elo, external dataset replication, or final holdout.",
        ],
    }
    result["report_sha256"]=_digest(result)
    _write_once(out,result)
    return result


def main(argv=None)->int:
    p=argparse.ArgumentParser(description="Genuine 512-game original-model DSC/Maia replication")
    for name in ("cohorts","v3-model","out"):
        p.add_argument("--"+name,required=True,type=Path)
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch",action="store_true")
    src.add_argument("--archive-dir",type=Path)
    args=p.parse_args(argv)
    report=run(args.cohorts,args.v3_model,args.out,
               archive_dir=args.archive_dir)
    print(json.dumps({
        "status":report["status"],"decisions":report["decisions"],
        "H1":report["overall"]["paired"],
        "supported":report["overall"]["supports_dsc_superiority"],
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
