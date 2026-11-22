"""Evidence-preserving recovery of the 450-ply censored Fischer UCI games.

Original 3,616 case files and the #197 protocol remain immutable.
Only incomplete cases continue from the exact legal move history.
Do not score or fabricate draws until every position reaches a legal terminal.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path

import chess

from . import fischer_full_games as original
from .catalog import Player, find_player
from .fischer_deep_calibration import NODE_CAP, choose
from .matches import _open_engine, file_hash
from .registry import registered_aliases

SCHEMA="chesssim-fischer-cap-recovery-v1"
SOURCE_RUN_ID=44415124944
COUNT=32
ORIGINAL_CAP=450
EXTENDED_CAP=1800
EXPECTED_CENSORED=22


def hash_bytes(blob: bytes) -> str:
    return sha256(blob).hexdigest()


def atomic_replace(path: Path, value: dict) -> None:
    """Atomic durable checkpoint; unlike original, PARTIAL is replaceable."""
    if path.is_symlink():
        raise ValueError("Refuse symlink checkpoint")
    temp=path.with_suffix(".tmp")
    if temp.exists() or temp.is_symlink():
        raise ValueError("Stale recovery temporary file")
    with temp.open("x",encoding="utf-8") as handle:
        json.dump(value,handle,sort_keys=True,allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def originals(shards: Path, archive_dir=None):
    """Full replay of original evidence and its hashes before selecting repairs."""
    report=original.aggregate(shards,COUNT,archive_dir=archive_dir)
    if (report["schema"]!=original.SCHEMA or
            report["observed_cases"]!=report["expected_cases"] or
            report["expected_cases"]!=3616 or
            report["status"]!="INCOMPLETE_DO_NOT_SCORE" or
            report["scores"] is not None or report["incomplete"]!=EXPECTED_CENSORED or
            report["max_continuation_plies"]!=ORIGINAL_CAP or
            report["excluded_test_games"]!=121):
        raise ValueError("Unexpected or scored source benchmark")
    panel,_,*_=original.load_source(archive_dir)
    roots={r["id"]:r for r in panel}
    censored=[]
    all_records=[]
    for i in range(COUNT):
        directory=Path(shards)/f"shard-{i}"
        for case in original.shard_cases(panel,i,COUNT):
            path=directory/(case["id"]+".json")
            blob=path.read_bytes()
            rec=json.loads(blob)
            # original.aggregate already replayed and validated ALL 3,616
            # source files. Recheck every censored one, which is extended.
            # Avoid replaying 3,594 terminal move histories a second time.
            if rec["status"]!="COMPLETE":
                original.validate_case(rec,case,roots[case["root"]])
            item=(i,case,rec,hash_bytes(blob))
            all_records.append(item)
            if rec["status"]=="INCOMPLETE":
                if (rec["termination"]!="PLY_CAP_INCOMPLETE" or
                        len(rec["moves"])!=ORIGINAL_CAP or rec["result"]!="*"):
                    raise ValueError("Censored case is not the known ply-cap condition")
                censored.append(item)
    if len(censored)!=EXPECTED_CENSORED or len(all_records)!=3616:
        raise ValueError("Inconsistent original case census")
    return report,panel,roots,all_records,censored


def scan(shards: Path, archive_dir=None):
    report,_,_,records,censored=originals(shards,archive_dir)
    indices=sorted({i for i,_,_,_ in censored})
    pairs=defaultdict(dict)
    completed=defaultdict(Counter)
    for _,case,record,_ in records:
        key=f'{case["arm"]}|{case["opponent_depth"]}|{case["color"]}|{case["mode"]}'
        if record["status"]=="COMPLETE":
            score=points(case,record["result"])
            completed[key]["games"]+=1
            completed[key]["points_twice"]+=int(2*score)
        else:
            score=None
            completed[key]["censored"]+=1
        pairs[(case["arm"],case["opponent_depth"],case["root"],case["color"])][case["mode"]]=score
    bounds=defaultdict(lambda:{"pairs":0,"censored_pairs":0,"lower_total":0.0,"upper_total":0.0})
    for (arm,depth,root,color),modes in pairs.items():
        if set(modes)!=set(original.MODES):
            raise ValueError("Unpaired source records")
        book,base=modes["book"],modes["original"]
        entry=bounds[f"{arm}|{depth}"]
        entry["pairs"]+=1
        entry["censored_pairs"]+=int(book is None or base is None)
        entry["lower_total"]+=(0.0 if book is None else book)-(1.0 if base is None else base)
        entry["upper_total"]+=(1.0 if book is None else book)-(0.0 if base is None else base)
    for entry in bounds.values():
        entry["lower_mean"]=entry.pop("lower_total")/entry["pairs"]
        entry["upper_mean"]=entry.pop("upper_total")/entry["pairs"]
    return {"schema":SCHEMA,"status":"ONLY_PLY_CAP_RECOVERY_REQUIRED",
            "source_run_id":SOURCE_RUN_ID,"original_cases":report["observed_cases"],
            "original_complete":report["observed_cases"]-len(censored),
            "original_censored":len(censored),"indices":indices,
            "extended_cap":EXTENDED_CAP,"no_source_test_used":True,
            "descriptive_completed_cells":dict(completed),
            "censored_paired_effect_bounds":dict(bounds),
            "original_panel_sha256":report["panel_sha256"],
            "original_protocol_hashes":report["protocol_code_hashes"]}


def reconstruct(root: dict, initial: dict):
    board=original.initial_board(root)
    for encoded in initial["moves"]:
        if board.is_game_over(claim_draw=True):
            raise ValueError("Original continuation moved after terminal")
        move=chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal original continuation")
        board.push(move)
    if board.outcome(claim_draw=True):
        raise ValueError("Source should be censored, not terminal")
    return board


def validate_patch(initial: dict, patch: dict, root: dict, digest: str):
    if (patch.get("schema")!=SCHEMA or patch.get("case")!=initial["case"] or
            patch.get("original_sha256")!=digest or
            patch.get("source_run_id")!=SOURCE_RUN_ID or
            patch.get("original_plies")!=ORIGINAL_CAP or
            patch.get("extended_cap")!=EXTENDED_CAP or
            not isinstance(patch.get("extra_moves"),list)):
        raise ValueError("Invalid recovery binding")
    board=reconstruct(root,initial)
    moves=patch["extra_moves"]
    if len(moves)>EXTENDED_CAP-ORIGINAL_CAP:
        raise ValueError("Continuation exceeded registered recovery budget")
    for encoded in moves:
        if board.is_game_over(claim_draw=True):
            raise ValueError("Moves after terminal")
        move=chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal recovered move")
        board.push(move)
    outcome=board.outcome(claim_draw=True)
    wanted=("COMPLETE",outcome.result(),outcome.termination.name) if outcome else (
        ("PARTIAL" if len(moves)==EXTENDED_CAP-ORIGINAL_CAP else "IN_PROGRESS"),
        "*",("EXTENDED_CAP_INCOMPLETE" if len(moves)==EXTENDED_CAP-ORIGINAL_CAP
             else "RECOVERY_IN_PROGRESS"))
    if (patch.get("status"),patch.get("result"),patch.get("termination"))!=wanted:
        raise ValueError("Incorrect recovery adjudication")
    if not outcome and len(moves)<EXTENDED_CAP-ORIGINAL_CAP and patch["status"]!="IN_PROGRESS":
        raise ValueError("Unfinished continuation must remain IN_PROGRESS")
    if not outcome and len(moves)==EXTENDED_CAP-ORIGINAL_CAP and patch["status"]!="PARTIAL":
        raise ValueError("Extended cap must remain unscored")
    for name in ("reached","missing","nodes_exceeded"):
        counter=patch.get(name)
        if not isinstance(counter,dict) or not set(counter)<= {"fischer","simple"} or any(
                type(v) is not int or v<0 or v>len(moves) for v in counter.values()):
            raise ValueError("Invalid recovered UCI telemetry")
    return patch


def extend_case(initial,root,original_sha,fischer,simple,prior=None,on_progress=None):
    board=reconstruct(root,initial)
    focal_white=initial["case"]["color"]=="white"
    extra=list(prior["extra_moves"]) if prior else []
    reached=Counter(prior["reached"]) if prior else Counter()
    missing=Counter(prior["missing"]) if prior else Counter()
    exceeded=Counter(prior["nodes_exceeded"]) if prior else Counter()
    if prior:
        validate_patch(initial,prior,root,original_sha)
        for encoded in extra:
            if board.is_game_over(claim_draw=True):
                raise ValueError("Terminal checkpoint contains surplus moves")
            board.push(chess.Move.from_uci(encoded))
    def record():
        outcome=board.outcome(claim_draw=True)
        status=("COMPLETE" if outcome else
                "PARTIAL" if ORIGINAL_CAP+len(extra)==EXTENDED_CAP else
                "IN_PROGRESS")
        return {"schema":SCHEMA,"source_run_id":SOURCE_RUN_ID,
                "case":initial["case"],"original_sha256":original_sha,
                "original_plies":ORIGINAL_CAP,"extended_cap":EXTENDED_CAP,
                "extra_moves":list(extra),"status":status,
                "result":outcome.result() if outcome else "*",
                "termination":outcome.termination.name if outcome else
                    ("EXTENDED_CAP_INCOMPLETE" if status=="PARTIAL" else "RECOVERY_IN_PROGRESS"),
                "reached":dict(reached),"missing":dict(missing),
                "nodes_exceeded":dict(exceeded)}
    while not board.is_game_over(claim_draw=True) and ORIGINAL_CAP+len(extra)<EXTENDED_CAP:
        focal=board.turn==focal_white
        role="fischer" if focal else "simple"
        depth=16 if focal else initial["case"]["opponent_depth"]
        move,meta=choose(fischer if focal else simple,board,depth)
        reached[role]+=int(meta["reached_requested"])
        missing[role]+=int(meta["reported_depth"] is None)
        exceeded[role]+=int(meta["reported_nodes"] is not None and meta["reported_nodes"]>NODE_CAP)
        if move not in board.legal_moves:
            raise ValueError("Illegal recovery UCI move")
        board.push(move)
        extra.append(move.uci())
        if on_progress and len(extra)%10==0:
            on_progress(validate_patch(initial,record(),root,original_sha))
    return validate_patch(initial,record(),root,original_sha)


def repair(original_dir: Path, out: Path, rodent_root: Path, archive_dir=None):
    # This shard's all cases have exactly the same source protocol hashes
    # as the unchanged original module. It cannot be silently "updated".
    panel,metadata,pgn,rows,surname=original.load_source(archive_dir)
    name=Path(original_dir).name
    if not name.startswith("shard-") or not name[6:].isdigit():
        raise ValueError("Require original shard-N directory")
    index=int(name[6:])
    if not 0<=index<COUNT:
        raise ValueError("Invalid source shard")
    roots={r["id"]:r for r in panel}
    manifest=original.load_json(Path(original_dir)/"manifest.json")
    expected={**metadata,"hashes":manifest["hashes"],
              "book_sha256":original.digest(original.fit_book(pgn,rows,surname)),
              "shard_index":index,"shard_count":COUNT,
              "case_ids":[c["id"] for c in original.shard_cases(panel,index,COUNT)]}
    if manifest!=expected:
        raise ValueError("Original shard or protocol metadata changed")
    censored=[]
    for case in original.shard_cases(panel,index,COUNT):
        path=Path(original_dir)/(case["id"]+".json")
        raw=path.read_bytes()
        record=json.loads(raw)
        original.validate_case(record,case,roots[case["root"]])
        if record["status"]=="INCOMPLETE":
            censored.append((record,roots[case["root"]],hash_bytes(raw)))
    if not censored:
        raise ValueError("No incomplete games in this shard")
    root=Path(rodent_root).resolve(strict=True)
    exe=root/"rodent-iv-plain.exe"
    profile=find_player("fischer")
    registry=root/"personalities/basic.ini"
    aliases=registered_aliases(registry)
    if aliases.get(profile.rodent_alias)!=profile.profile_filename or aliases.get("Simple")!="simple.txt":
        raise ValueError("Missing original Rodent aliases")
    files={"rodent":exe,"fischer":root/"personalities"/profile.profile_filename,
           "simple":root/"personalities/simple.txt","registry":registry}
    if any(file_hash(f)!=manifest["hashes"][key] for key,f in files.items()):
        raise ValueError("Binary or personality changed since original UCI benchmark")
    original_manifest_sha=hash_bytes((Path(original_dir)/"manifest.json").read_bytes())
    recovery_manifest={"schema":SCHEMA,"source_run_id":SOURCE_RUN_ID,
        "original_manifest_sha256":original_manifest_sha,
        "original_protocol_hashes":manifest["protocol_code_hashes"],
        "original_engine_hashes":manifest["hashes"],
        "recovery_code_sha256":file_hash(Path(__file__)),
        "shard_index":index,"extended_cap":EXTENDED_CAP,
        "censored_case_ids":[r["case"]["id"] for r,_,_ in censored]}
    out=Path(out)
    if out.is_symlink():
        raise ValueError("Symlink recovery output")
    out.mkdir(parents=False,exist_ok=True)
    saved=out/"recovery-manifest.json"
    if saved.exists():
        if original.load_json(saved)!=recovery_manifest:
            raise ValueError("Incompatible prior recovery evidence")
    else:
        if any(out.iterdir()):
            raise ValueError("Unrecognized recovery files")
        original.atomic_json(saved,recovery_manifest)
    simple=Player(number=0,name="Rodent IV Simple",slug="control-simple",
                  group="baseline",rodent_alias="Simple",rodent_file="simple.txt")
    with ExitStack() as stack:
        focal=stack.enter_context(_open_engine(root,exe,profile,book=False))
        baseline=stack.enter_context(_open_engine(root,exe,simple,book=False))
        for i,(initial,position,old_hash) in enumerate(censored,1):
            file=out/(initial["case"]["id"]+".json")
            prior=(validate_patch(initial,original.load_json(file),position,old_hash)
                   if file.exists() else None)
            if prior is not None and prior["status"] in ("COMPLETE","PARTIAL"):
                patch=prior
            else:
                patch=extend_case(initial,position,old_hash,focal,baseline,prior,
                                  lambda checkpoint:atomic_replace(file,checkpoint))
                atomic_replace(file,patch)
            print("FISCHER_RECOVERY",index,i,len(censored),patch["status"],
                  len(patch["extra_moves"]),flush=True)
    if any(file_hash(f)!=manifest["hashes"][key] for key,f in files.items()):
        raise ValueError("Original engine changed during continuation")
    return {"schema":SCHEMA,"source_run_id":SOURCE_RUN_ID,"shard":index,
            "cases":len(censored),"completed":sum(
                original.load_json(out/(r["case"]["id"]+".json"))["status"]=="COMPLETE"
                for r,_,_ in censored)}


def points(case,result):
    return 0.5 if result=="1/2-1/2" else float(
        (result=="1-0")==(case["color"]=="white"))


def finalize(shards: Path, repairs: Path, archive_dir=None):
    source,panel,roots,records,censored=originals(shards,archive_dir)
    recovery_index={i for i,_,_,_ in censored}
    report={}
    combined=[]
    for i,case,base,old_hash in records:
        if base["status"]=="COMPLETE":
            combined.append((case,base["result"]))
            continue
        directory=Path(repairs)/f"repair-shard-{i}"
        manifest=original.load_json(directory/"recovery-manifest.json")
        old_manifest=Path(shards)/f"shard-{i}"/"manifest.json"
        if (manifest.get("schema")!=SCHEMA or manifest.get("source_run_id")!=SOURCE_RUN_ID
                or manifest.get("original_manifest_sha256")!=hash_bytes(old_manifest.read_bytes())
                or manifest.get("original_protocol_hashes")!=source["protocol_code_hashes"]
                or manifest.get("original_engine_hashes")!=source["hashes"] or
                manifest.get("shard_index")!=i or manifest.get("extended_cap")!=EXTENDED_CAP
                or manifest.get("recovery_code_sha256")!=file_hash(Path(__file__))):
            raise ValueError("Recovery manifest does not bind original evidence")
        patch=validate_patch(base,original.load_json(directory/(case["id"]+".json")),
                             roots[case["root"]],old_hash)
        if patch["status"]!="COMPLETE":
            raise RuntimeError("Recovered game still lacks legal terminal result")
        combined.append((case,patch["result"]))
        report[case["id"]]={"shard":i,"extra_plies":len(patch["extra_moves"]),
                            "termination":patch["termination"]}
    if len(combined)!=source["observed_cases"] or len(report)!=EXPECTED_CENSORED:
        raise ValueError("Not all original cases accounted for")
    for i in recovery_index:
        directory=Path(repairs)/f"repair-shard-{i}"
        ids={case["id"] for index,case,_,_ in censored if index==i}
        manifest=original.load_json(directory/"recovery-manifest.json")
        if set(manifest["censored_case_ids"])!=ids:
            raise ValueError("Recovery manifest incomplete")
        if {p.name for p in directory.iterdir()}!={"recovery-manifest.json",*(x+".json" for x in ids)}:
            raise ValueError("Unexpected recovery file")
    scores=defaultdict(Counter)
    matched=defaultdict(dict)
    for case,result in combined:
        key=f'{case["arm"]}|{case["opponent_depth"]}|{case["color"]}|{case["mode"]}'
        cell=scores[key]
        cell["games"]+=1
        p=points(case,result)
        cell["wins"]+=int(p==1)
        cell["draws"]+=int(p==.5)
        cell["losses"]+=int(p==0)
        matched[(case["arm"],case["root"],case["opponent_depth"],case["color"])][case["mode"]]=p
    paired=defaultdict(list)
    for (arm,root,depth,color),modes in matched.items():
        if set(modes)!=set(original.MODES):
            raise ValueError("Missing paired control")
        paired[f"{arm}|{depth}"].append((root,modes["book"]-modes["original"]))
    paired_stats={name:{"pairs":len(values),
                        "distinct_roots":len({root for root,_ in values}),
                        "mean_book_minus_original":sum(v for _,v in values)/len(values),
                        "root_cluster_ci95":original.game_cluster_interval(values)}
                  for name,values in paired.items()}
    return {"schema":SCHEMA,"status":"ALL_3616_GAMES_TERMINAL_AFTER_EVIDENCE_PRESERVING_RECOVERY",
            "source_run_id":SOURCE_RUN_ID,"source_panel_sha256":source["panel_sha256"],
            "source_protocol_hashes":source["protocol_code_hashes"],
            "original_complete":len(records)-len(censored),"recovered":len(report),
            "observed_cases":len(combined),"scores":dict(scores),
            "paired_book_minus_original":paired_stats,
            "recovery_termination_counts":dict(Counter(r["termination"] for r in report.values())),
            "outer_test_decisions_evaluated":0,"intrinsic_elo":None,
            "historically_validated":False,"official_engine_changed":False,
            "limitations":["Extended continuations are a preregistered recovery of capped games.",
                           "No historical-human Elo or independent holdout established.",
                           "FIT openings used for book training; TUNE previously inspected.",
                           "No automatic promotion or official Arena effects."]}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="command",required=True)
    for name in ("scan","repair","finalize"):
        q=sub.add_parser(name)
        q.add_argument("--shards",type=Path,required=name!="repair")
        q.add_argument("--original-dir",type=Path,required=name=="repair")
        q.add_argument("--repairs",type=Path,required=name=="finalize")
        q.add_argument("--out",type=Path,required=name!="scan")
        q.add_argument("--rodent-root",type=Path,required=name=="repair")
        q.add_argument("--archive-dir",type=Path)
    args=ap.parse_args(argv)
    if args.command=="scan":
        doc=scan(args.shards,args.archive_dir)
    elif args.command=="repair":
        doc=repair(args.original_dir,args.out,args.rodent_root,args.archive_dir)
    else:
        doc=finalize(args.shards,args.repairs,args.archive_dir)
        if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
            raise ValueError("Refusing to overwrite recovery summary")
        original.atomic_json(args.out,doc)
    print(json.dumps(doc,sort_keys=True,allow_nan=False),flush=True)
    if args.command=="repair" and doc["completed"]!=doc["cases"]:
        return 2
    return 0 if doc.get("status") not in ("PARTIAL","INCOMPLETE_DO_NOT_SCORE") else 2


if __name__=="__main__":
    raise SystemExit(main())
