"""Fischer d16 vs graded Simple on every distinct development opening.

All eligible FIT/TUNE source games accounted for. Identical 12-ply prefixes
within an arm share one crossover, not pseudo-replicates. Preserve full
prefix history. No outer TEST, residual, rating or Arena promotion.
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
import chess.engine

from .catalog import Player, find_player
from .fischer_deep_calibration import NODE_CAP, choose
from .fischer_fit_ablation import fit_book, guard_book, select_all_development
from .fischer_position_calibration import game_cluster_interval
from .matches import _open_engine, file_hash
from .real85_neural_stream import neural_arm
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)
from .registry import registered_aliases

SCHEMA = "chesssim-fischer-full-games-v1"
START_PLIES = 12
FOCAL_DEPTH = 16
OPPONENT_DEPTHS = (12, 16)
MODES = ("original", "book")
COLORS = ("white", "black")
MAX_CONTINUATION_PLIES = 450


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def source_panel(pgn, rows, surname):
    selected=select_all_development(rows)
    sources={r["mainline_sha256"]:r for r in selected}
    groups=defaultdict(list)
    exclusions=Counter()
    seen=set()
    for game,row in _iter_verified_games(pgn,selected,surname):
        gid=row["mainline_sha256"]
        arm=neural_arm(gid)
        if gid in seen or gid not in sources or arm not in ("fit","tune"):
            raise ValueError("Duplicate or reserved source game")
        seen.add(gid)
        if game.board().fen()!=chess.STARTING_FEN:
            exclusions[f"{arm}:nonstandard_start"]+=1
            continue
        moves=list(game.mainline_moves())
        if len(moves)<START_PLIES:
            exclusions[f"{arm}:short_game"]+=1
            continue
        board=chess.Board()
        prefix=[]
        for move in moves[:START_PLIES]:
            if move not in board.legal_moves:
                raise ValueError("Illegal source opening")
            board.push(move)
            prefix.append(move.uci())
        if board.is_game_over(claim_draw=True):
            exclusions[f"{arm}:terminal_start"]+=1
            continue
        groups[(arm,tuple(prefix))].append(gid)
    if seen!=set(sources):
        raise ValueError("Incomplete source opening coverage")
    panel=[]
    for (arm,prefix), games in sorted(groups.items()):
        panel.append({"arm":arm,"prefix":list(prefix),"id":digest([arm,list(prefix)]),
                      "source_games":sorted(games)})
    if not panel or sum(len(x["source_games"]) for x in panel)+sum(exclusions.values())!=len(sources):
        raise ValueError("Development source counts do not reconcile")
    return panel,dict(exclusions)


def cases(panel):
    for root in panel:
        for depth in OPPONENT_DEPTHS:
            for color in COLORS:
                for mode in MODES:
                    yield {"root":root["id"],"arm":root["arm"],"opponent_depth":depth,
                           "color":color,"mode":mode,
                           "id":digest([root["id"],depth,color,mode])}


def shard_cases(panel,index,count):
    if type(index) is not int or type(count) is not int or not 1<=count<=256 or not 0<=index<count:
        raise ValueError("Invalid shard")
    # Keep all eight crossover games for each root together; exact deterministic
    # partition even across different source orderings.
    ordered=sorted(panel,key=lambda r:r["id"])
    return list(cases([root for i,root in enumerate(ordered) if i%count==index]))


def load_source(archive_dir=None):
    conf=_player_configuration(["fischer"],None,None)["fischer"]
    _,pgn,origin=_source_bytes(conf["source"],archive_dir)
    rows,scan=_scan(pgn,"fischer",conf["surname"])
    panel,excluded=source_panel(pgn,rows,conf["surname"])
    metadata={"schema":SCHEMA,"source":origin,"source_snapshot":MIRROR_COMMIT if archive_dir is None else None,
              "source_pgn_sha256":sha256(pgn).hexdigest(),"panel_sha256":digest(panel),
              "all_source_games":len(rows),"development_games":dict(Counter(
                  neural_arm(r["mainline_sha256"]) for r in rows if neural_arm(r["mainline_sha256"]) in ("fit","tune"))),
              "excluded_test_games":sum(neural_arm(r["mainline_sha256"])=="excluded_test" for r in rows),
              "eligible_source_games":sum(len(x["source_games"]) for x in panel),
              "source_exclusions":excluded,"distinct_prefixes_by_arm":dict(Counter(x["arm"] for x in panel)),
              "distinct_prefixes_global":len({tuple(x["prefix"]) for x in panel}),
              "distinct_start_fens_global":len({initial_board(x).fen() for x in panel}),
              "expected_cases":len(list(cases(panel))),"focal_depth":FOCAL_DEPTH,
              "opponent_depths":list(OPPONENT_DEPTHS),"node_cap":NODE_CAP,
              "max_continuation_plies":MAX_CONTINUATION_PLIES,
              "outer_test_decisions_evaluated":0,"official_personalities_changed":False,
              "historically_validated":False,"intrinsic_elo":None,
              "limitations":["Continuations after 12 historical plies, not standard-start ratings.",
                             "Identical prefixes grouped within arm; transpositions and between-arm overlap remain.",
                             "Unclocked depth/node budgets, not human rating anchors.",
                             "FIT in-sample, TUNE already inspected; no independent validation."]}
    return panel,metadata,pgn,rows,conf["surname"]


def initial_board(root):
    board=chess.Board()
    for encoded in root["prefix"]:
        move=chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal source prefix")
        board.push(move)
    if board.ply()!=START_PLIES or board.is_game_over(claim_draw=True):
        raise ValueError("Invalid source root")
    return board


def play_case(case,root,engine,opponent,guard,book):
    board=initial_board(root)
    # choose() starts each turn with fresh UCI game identity; the verified
    # board still contains the full previous move stack for repetition.
    focal_white=case["color"]=="white"
    moves=[]
    interventions=Counter()
    book_trace=[]
    depth_reached=Counter()
    depth_missing=Counter()
    nodes_missing=Counter()
    nodes_exceeded=Counter()
    for _ in range(MAX_CONTINUATION_PLIES):
        if board.is_game_over(claim_draw=True):
            break
        focal=board.turn==focal_white
        role="fischer" if focal else "simple"
        base,telemetry=choose(engine if focal else opponent,board,
                              FOCAL_DEPTH if focal else case["opponent_depth"])
        depth_reached[role]+=int(telemetry["reached_requested"])
        depth_missing[role]+=int(telemetry["reported_depth"] is None)
        nodes=telemetry["reported_nodes"]
        nodes_missing[role]+=int(nodes is None)
        nodes_exceeded[role]+=int(nodes is not None and nodes>NODE_CAP)
        move=base
        if focal and case["mode"]=="book" and board.ply()<40:
            move,reason=guard_book(board,base,book,guard)
            interventions[reason]+=1
            book_trace.append({"index":len(moves),"base":base.uci(),"reason":reason})
        if move not in board.legal_moves:
            raise ValueError("Illegal continuation choice")
        moves.append(move.uci())
        board.push(move)
    outcome=board.outcome(claim_draw=True)
    return {"case":case,"moves":moves,"result":outcome.result() if outcome else "*",
            "termination":outcome.termination.name if outcome else "PLY_CAP_INCOMPLETE",
            "status":"COMPLETE" if outcome else "INCOMPLETE",
            "interventions":dict(interventions),"book_trace":book_trace,"depth_reached":dict(depth_reached),
            "depth_missing":dict(depth_missing),"nodes_missing":dict(nodes_missing),
            "nodes_exceeded":dict(nodes_exceeded)}


def validate_case(record,case,root):
    if record["case"]!=case or record["case"]["root"]!=root["id"]:
        raise ValueError("Result does not bind exact planned case")
    board=initial_board(root)
    moves=record["moves"]
    if not isinstance(moves,list) or not 1<=len(moves)<=MAX_CONTINUATION_PLIES:
        raise ValueError("Invalid continuation length")
    turns=Counter()
    focal_white=case["color"]=="white"
    focal_book_turns=0
    if not isinstance(record["book_trace"],list):
        raise ValueError("Invalid opening trace")
    trace=iter(record["book_trace"])
    sentinel=object()
    observed=Counter()
    for index,encoded in enumerate(moves):
        if board.is_game_over(claim_draw=True):
            raise ValueError("Moves after terminal position")
        move=chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal recorded move")
        focal=board.turn==focal_white
        turns["fischer" if focal else "simple"]+=1
        if focal and board.ply()<40 and case["mode"]=="book":
            focal_book_turns+=1
            event=next(trace,None)
            if (not isinstance(event,dict) or event.get("index")!=index or
                    not isinstance(event.get("base"),str)):
                raise ValueError("Missing matching opening trace")
            base=chess.Move.from_uci(event["base"])
            if base not in board.legal_moves or ((base!=move)!=(event["reason"]=="book_override")):
                raise ValueError("Invalid recorded book intervention")
            observed[event["reason"]]+=1
        board.push(move)
    outcome=board.outcome(claim_draw=True)
    wanted=(outcome.result(),outcome.termination.name,"COMPLETE") if outcome else (
        "*","PLY_CAP_INCOMPLETE","INCOMPLETE")
    if (record["result"],record["termination"],record["status"])!=wanted:
        raise ValueError("False terminal result")
    if not outcome and len(moves)!=MAX_CONTINUATION_PLIES:
        raise ValueError("Truncated continuation")
    for role,n in turns.items():
        a=record["depth_reached"].get(role,0)
        b=record["depth_missing"].get(role,0)
        if type(a) is not int or type(b) is not int or min(a,b)<0 or a+b>n:
            raise ValueError("Invalid search telemetry")
    allowed={"no_book_support","book_agreement","book_mate_guard","book_cp_guard","book_override"}
    events=record["interventions"]
    if (not set(events)<=allowed or any(type(v) is not int or v<0 for v in events.values()) or
            sum(events.values())!=focal_book_turns or dict(observed)!=events or
            next(trace,sentinel) is not sentinel):
        raise ValueError("Invalid book intervention coverage")
    for role,n in turns.items():
        a=record["nodes_missing"].get(role,0)
        b=record["nodes_exceeded"].get(role,0)
        if type(a) is not int or type(b) is not int or min(a,b)<0 or a+b>n:
            raise ValueError("Invalid node telemetry")
    if any(set(record[key])-set(turns) for key in ("depth_reached","depth_missing","nodes_missing","nodes_exceeded")):
        raise ValueError("Unexpected telemetry role")
    return record


def atomic_json(path,doc):
    if path.exists() or path.is_symlink():
        raise ValueError("Refusing to overwrite evidence")
    tmp=path.with_suffix(".tmp")
    if tmp.exists() or tmp.is_symlink():
        raise ValueError("Stale temporary output; inspect before resuming")
    with tmp.open("x",encoding="utf-8") as f:
        json.dump(doc,f,sort_keys=True,allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp,path)  # atomic creation, refuses overwrite even under race
    finally:
        tmp.unlink()
    directory=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or symbolic evidence")
    return json.loads(path.read_text())


def run_shard(rodent,stockfish,out,index,count,archive_dir=None):
    panel,metadata,pgn,rows,surname=load_source(archive_dir)
    planned=shard_cases(panel,index,count)
    if not planned:
        raise ValueError("Empty shard; reduce shard count, not game coverage")
    roots={r["id"]:r for r in panel}
    book=fit_book(pgn,rows,surname)
    root=Path(rodent).resolve(strict=True)
    sf=Path(stockfish).resolve(strict=True)
    exe=root/"rodent-iv-plain.exe"
    focal=find_player("fischer")
    registry=root/"personalities/basic.ini"
    aliases=registered_aliases(registry)
    if aliases.get(focal.rodent_alias)!=focal.profile_filename or aliases.get("Simple")!="simple.txt":
        raise ValueError("Require original registered profiles")
    simple=Player(number=0,name="Rodent IV Simple",slug="control-simple",group="baseline",
                  rodent_alias="Simple",rodent_file="simple.txt")
    files={"rodent":exe,"stockfish":sf,"fischer":root/"personalities"/focal.profile_filename,
           "simple":root/"personalities/simple.txt","registry":registry}
    hashes={k:file_hash(v) for k,v in files.items()}
    spec={**metadata,"hashes":hashes,"book_sha256":digest(book),"shard_index":index,
          "shard_count":count,"case_ids":[c["id"] for c in planned]}
    out=Path(out)
    if out.is_symlink():
        raise ValueError("Symbolic output")
    out.mkdir(parents=False,exist_ok=True)
    spec_path=out/"manifest.json"
    binding=digest(spec)
    if spec_path.exists():
        if load_json(spec_path)!=spec:
            raise ValueError("Resume manifest differs")
    else:
        if any(out.iterdir()):
            raise ValueError("Unknown output directory")
        atomic_json(spec_path,spec)
    with ExitStack() as stack:
        guard=stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(sf),timeout=180.0))
        guard.configure({"Threads":1,"Hash":64})
        engine=stack.enter_context(_open_engine(root,exe,focal,book=False))
        opponent=stack.enter_context(_open_engine(root,exe,simple,book=False))
        for i,case in enumerate(planned,1):
            path=out/(case["id"]+".json")
            if path.exists():
                record=load_json(path)
                if record.get("manifest_sha256")!=binding:
                    raise ValueError("Checkpoint not bound to current manifest")
                validate_case(record,case,roots[case["root"]])
            else:
                result=play_case(case,roots[case["root"]],engine,opponent,guard,book)
                result["manifest_sha256"]=binding
                atomic_json(path,validate_case(result,case,roots[case["root"]]))
            print(f"FISCHER_SHARD {index}/{count} {i}/{len(planned)}",flush=True)
    if hashes!={k:file_hash(v) for k,v in files.items()}:
        raise ValueError("Engine or profile changed during run")
    return spec


def aggregate(shards, count, archive_dir=None):
    panel,metadata,pgn,rows,surname=load_source(archive_dir)
    book_hash=digest(fit_book(pgn,rows,surname))
    roots={r["id"]:r for r in panel}
    seen=set()
    records=[]
    hashes=None
    for idx in range(count):
        directory=Path(shards)/f"shard-{idx}"
        manifest=load_json(directory/"manifest.json")
        planned=shard_cases(panel,idx,count)
        expected={**metadata,"hashes":manifest["hashes"],"book_sha256":book_hash,
                  "shard_index":idx,"shard_count":count,"case_ids":[c["id"] for c in planned]}
        if manifest!=expected or (hashes is not None and manifest["hashes"]!=hashes):
            raise ValueError("Shard manifests differ")
        hashes=manifest["hashes"]
        if set(hashes)!={"rodent","stockfish","fischer","simple","registry"} or any(
                not isinstance(x,str) or len(x)!=64 or any(c not in "0123456789abcdef" for c in x)
                for x in hashes.values()):
            raise ValueError("Invalid shard engine provenance")
        binding=digest(manifest)
        if set(p.name for p in directory.iterdir())!={"manifest.json",*(c["id"]+".json" for c in planned)}:
            raise ValueError("Missing, extra or partial case files")
        for case in planned:
            if case["id"] in seen:
                raise ValueError("Duplicate case")
            seen.add(case["id"])
            record=load_json(directory/(case["id"]+".json"))
            if record.get("manifest_sha256")!=binding:
                raise ValueError("Case not bound to exact shard manifest")
            records.append(validate_case(record,case,roots[case["root"]]))
    if seen!={c["id"] for c in cases(panel)}:
        raise ValueError("Incomplete crossover")
    scores=defaultdict(Counter)
    telemetry=defaultdict(Counter)
    matched=defaultdict(dict)
    for rec in records:
        c=rec["case"]
        cell=scores[f'{c["arm"]}|{c["opponent_depth"]}|{c["color"]}|{c["mode"]}']
        cell["games"]+=1
        tele=telemetry[f\'{c["arm"]}|{c["opponent_depth"]}|{c["color"]}|{c["mode"]}\']
        for kind in ("depth_reached","depth_missing","nodes_missing","nodes_exceeded"):
            for role,n in rec[kind].items():
                tele[f"{role}:{kind}"]+=n
        for reason,n in rec["interventions"].items():
            tele[f"book:{reason}"]+=n
        if rec["status"]!="COMPLETE":
            cell["incomplete"]+=1
            continue
        result=rec["result"]
        points=0.5 if result=="1/2-1/2" else float((result=="1-0")==(c["color"]=="white"))
        cell["wins"]+=int(points==1)
        cell["draws"]+=int(points==0.5)
        cell["losses"]+=int(points==0)
        matched[(c["arm"],c["root"],c["opponent_depth"],c["color"])][c["mode"]]=points
    incomplete=sum(r["status"]!="COMPLETE" for r in records)
    pairs=defaultdict(list)
    if not incomplete:
        for (arm,root,depth,color),modes in matched.items():
            if set(modes)!=set(MODES):
                raise ValueError("Missing paired original/book case")
            pairs[f"{arm}|{depth}"].append((root,modes["book"]-modes["original"]))
    paired={key:{"pairs":len(values),"roots":len({root for root,_ in values}),
                 "mean_score_delta":sum(v for _,v in values)/len(values),
                 "root_cluster_ci95":game_cluster_interval(values)}
            for key,values in pairs.items()}
    return {**metadata,"hashes":hashes,"book_sha256":book_hash,
            "status":"ALL_CONTINUATIONS_COMPLETE" if not incomplete else "INCOMPLETE_DO_NOT_SCORE",
            "observed_cases":len(records),"incomplete":incomplete,
            "search_and_book_telemetry":dict(telemetry),
            "scores":dict(scores) if not incomplete else None,
            "paired_book_minus_original":paired if not incomplete else None,
            "note":"Raw WDL descriptive; no human Elo or calibration promotion."}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="cmd",required=True)
    for name in ("plan","play","aggregate"):
        a=sub.add_parser(name)
        src=a.add_mutually_exclusive_group(required=True)
        src.add_argument("--fetch",action="store_true")
        src.add_argument("--archive-dir",type=Path)
        if name!="plan":
            a.add_argument("--count",type=int,required=True)
        if name=="play":
            a.add_argument("--index",type=int,required=True)
            a.add_argument("--rodent-root",type=Path,required=True)
            a.add_argument("--stockfish",type=Path,required=True)
            a.add_argument("--out",type=Path,required=True)
        if name=="aggregate":
            a.add_argument("--shards",type=Path,required=True)
            a.add_argument("--out",type=Path,required=True)
    a=p.parse_args(argv)
    if a.cmd=="plan":
        _,report,*_=load_source(a.archive_dir)
    elif a.cmd=="play":
        report=run_shard(a.rodent_root,a.stockfish,a.out,a.index,a.count,a.archive_dir)
    else:
        if not 1<=a.count<=256:
            raise ValueError("Invalid shard count")
        report=aggregate(a.shards,a.count,a.archive_dir)
        atomic_json(a.out,report)
    print(json.dumps(report,sort_keys=True,allow_nan=False),flush=True)
    return 2 if report.get("status")=="INCOMPLETE_DO_NOT_SCORE" else 0


if __name__=="__main__":
    raise SystemExit(main())
