"""Exhaustive Fischer decision audit with six PRE-MOVE competency proxies.

All 827 accepted source games and every focal move are replayed. Forced
moves are counted but do not claim to measure skill. Every nonforced move
is evaluated at fixed Stockfish d20, plus original Fischer Rodent d16/d18.
The frozen FIT/TUNE/retrospective-TEST partitions are never reshuffled.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean

import chess
import chess.engine

from .catalog import find_player
from .fischer_baseline import BANDS, period, phase
from .fischer_deep_calibration import NODE_CAP, choose, tags
from .fischer_full_games import atomic_json, digest
from .fischer_position_calibration import game_cluster_interval
from .matches import _open_engine, file_hash
from .real85_graded_phase_attribution import _score
from .real85_neural_stream import neural_arm
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)
from .registry import registered_aliases
from .fischer_strength_calibration import choose_fit_depths, summarize_out_of_training

SCHEMA="chesssim-fischer-all-decisions-six-competencies-v1"
ARMS=("fit","tune","excluded_test")
COMPETENCIES=("opening","tactics","dynamics","defense","positional","endgames")
DEPTHS=(16,18)
REFERENCE_DEPTH=20
SHARDS=64
MIN_PAIRED_GAMES=30
MIN_SKILL_DECISIONS=100
DEVELOPMENT="fit"
TEST_DISCLOSURE="RETROSPECTIVE_PREVIOUSLY_INSPECTED_NOT_BLIND"


def _source(archive_dir=None):
    config=_player_configuration(["fischer"],None,None)["fischer"]
    _,pgn,origin=_source_bytes(config["source"],archive_dir)
    rows,scan=_scan(pgn,"fischer",config["surname"])
    ids=[r["mainline_sha256"] for r in rows]
    if len(ids)!=len(set(ids)) or not rows:
        raise ValueError("Empty or duplicate historical corpus")
    return pgn,rows,scan,origin,config["surname"]


def expected_row(row):
    """Shard assignment by stable game digest, never by game sample count."""
    h=row["mainline_sha256"]
    return int(h[:16],16)%SHARDS


def game_decisions(game,row):
    """Iterate EVERY focal decision, legal and with full history."""
    if row["color"] not in ("white","black") or not game.board().is_valid():
        raise ValueError("Invalid source game")
    board=game.board()
    focal=row["color"]=="white"
    decisions=[]
    forced=0
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("Illegal historical move")
        if board.turn==focal:
            options=board.legal_moves.count()
            if options==1:
                forced+=1
            elif options>1:
                decisions.append((board.copy(stack=True),move,phase(board),options))
            else:
                raise ValueError("No legal moves while historical game continues")
        board.push(move)
    return decisions,forced


def plan(archive_dir=None):
    pgn,rows,scan,origin,surname=_source(archive_dir)
    counts=Counter(neural_arm(r["mainline_sha256"]) for r in rows)
    if set(counts)!=set(ARMS):
        raise ValueError("Expected historical FIT/TUNE/TEST partition missing")
    shards=Counter(expected_row(r) for r in rows)
    if len(shards)!=SHARDS:
        raise ValueError("At least one game shard empty; refuse silent omission")
    return {"schema":SCHEMA,"source":origin,
            "source_snapshot":MIRROR_COMMIT if archive_dir is None else None,
            "source_pgn_sha256":sha256(pgn).hexdigest(),
            "games":len(rows),"accepted_scan":scan,
            "arms":dict(counts),"shard_game_counts":{str(i):shards[i] for i in range(SHARDS)},
            "shards":SHARDS,"player":"fischer","competencies":list(COMPETENCIES),
            "candidate_depths":list(DEPTHS),"reference_depth":REFERENCE_DEPTH,
            "requested_node_cap":NODE_CAP,"forced_decisions_scored":False,
            "test_disclosure":TEST_DISCLOSURE,"calibrated_0_110":False}


def _reference_top2(ref,board):
    infos=ref.analyse(board,chess.engine.Limit(depth=REFERENCE_DEPTH),
                      multipv=2,game=object())
    if len(infos)!=2:
        raise RuntimeError("Reference MultiPV root missing")
    result=[]
    for item in infos:
        pv=item.get("pv")
        if (not pv or pv[0] not in board.legal_moves or
                item.get("score") is None or
                type(item.get("depth")) is not int or item["depth"]<REFERENCE_DEPTH):
            raise RuntimeError("Reference depth not completed")
        score=item["score"].pov(board.turn)
        result.append({"move":pv[0],"cp":score.score(),"mate":score.mate()})
    if result[0]["move"]==result[1]["move"]:
        raise RuntimeError("MultiPV duplicate root moves")
    return result


def _stratum(board,options,reference):
    """Pre-choice strata: not an assumed ordering of human difficulty."""
    cp=reference["cp"]
    gap=reference["gap"]
    mobility="few" if options<=9 else "medium" if options<=24 else "many"
    balance=("mate" if cp is None else
             "disadvantage" if cp< -100 else
             "advantage" if cp>100 else "balanced")
    margin=("mate" if gap is None else
            "close" if gap<=15 else
            "moderate" if gap<80 else "clear")
    return f"{mobility}|{balance}|{margin}"


def _loss(best,other):
    if best["mate"] is not None or other["mate"] is not None:
        return None
    if type(best["cp"]) is not int or type(other["cp"]) is not int:
        raise ValueError("Missing cp for nonmate root")
    return best["cp"]-other["cp"]


def audit_decision(board,human,stage,options,reference,engine):
    """One original root, SAME forced-root scoring for every candidate."""
    if board.legal_moves.count()!=options or human not in board.legal_moves or options<2:
        raise ValueError("Missing legal historical decision")
    best,runner=_reference_top2(reference,board)
    gap=_loss(best,runner)
    reference_info={"cp":best["cp"],"mate":best["mate"],"gap":gap}
    contexts=tags(board,stage,{"cp":best["cp"],"mate":best["mate"]},
                  {"cp":runner["cp"],"mate":runner["mate"]})
    if any(t not in COMPETENCIES for t in contexts):
        raise RuntimeError("Unknown competency context")
    candidates={"human":human}
    telemetry={}
    for depth in DEPTHS:
        selected,stats=choose(engine,board,depth)
        candidates[f"d{depth}"]=selected
        telemetry[f"d{depth}"]=stats
    root=best["move"]
    distinct=sorted({root,*candidates.values()},key=lambda m:m.uci())
    evaluations={m:_score(reference,board,m,REFERENCE_DEPTH) for m in distinct}
    truth=evaluations[root]
    scores={}
    for name,move in candidates.items():
        e=evaluations[move]
        delta=_loss(truth,e)
        scores[name]={"cp_loss":delta,
                      "within_25cp":delta<=25 if delta is not None else None,
                      "over_100cp":delta>100 if delta is not None else None,
                      "mate_sensitive":delta is None,
                      "positive_mate_lost":truth["mate"] is not None and truth["mate"]>0 and
                          (e["mate"] is None or e["mate"]<=0),
                      "negative_mate_selected":e["mate"] is not None and e["mate"]<0,
                      "historical_match":move==human,
                      "reference_match":move==root}
    return {"phase":stage,"competencies":contexts,
            "stratum":_stratum(board,options,reference_info),
            "legal_moves":options,"root_margin_cp":gap,
            "reference_pov_cp":best["cp"],"reference_mate":best["mate"],
            "scores":scores,"search":telemetry}


def _engines(rodent,stockfish):
    root=Path(rodent).resolve(strict=True)
    sf=Path(stockfish).resolve(strict=True)
    exe=root/"rodent-iv-plain.exe"
    fischer=find_player("fischer")
    registry=root/"personalities/basic.ini"
    if registered_aliases(registry).get(fischer.rodent_alias)!=fischer.profile_filename:
        raise ValueError("Fischer must be the pinned original Rodent personality")
    paths={"rodent":exe,"stockfish":sf,
           "personality":root/"personalities"/fischer.profile_filename,"registry":registry}
    return root,exe,sf,fischer,paths


def run_shard(rodent,stockfish,out,index,archive_dir=None):
    if type(index) is not int or not 0<=index<SHARDS:
        raise ValueError("Invalid shard index")
    pgn,rows,scan,origin,surname=_source(archive_dir)
    selected=[r for r in rows if expected_row(r)==index]
    if not selected:
        raise ValueError("Empty shard")
    root,exe,sf,fischer,paths=_engines(rodent,stockfish)
    hashes={key:file_hash(path) for key,path in paths.items()}
    protocol_files=("fischer_competency_audit.py","fischer_baseline.py",
                    "fischer_deep_calibration.py","real85_graded_phase_attribution.py",
                    "real85_neural_stream.py","real_training_cohorts.py")
    basedir=Path(__file__).resolve().parent
    protocol={name:file_hash(basedir/name) for name in protocol_files}
    manifest={"schema":SCHEMA,"shard":index,"shards":SHARDS,
              "source_pgn_sha256":sha256(pgn).hexdigest(),
              "hashes":hashes,"protocol":protocol,
              "chess_version":chess.__version__,
              "reference_depth":REFERENCE_DEPTH,"candidate_depths":list(DEPTHS),
              "node_cap":NODE_CAP,
              "game_ids":sorted(r["mainline_sha256"] for r in selected)}
    out=Path(out)
    if out.is_symlink():
        raise ValueError("Symlink output")
    out.mkdir(parents=False,exist_ok=True)
    if (out/"manifest.json").exists():
        if json.loads((out/"manifest.json").read_text())!=manifest:
            raise ValueError("Incompatible resume manifest")
    else:
        if any(out.iterdir()):
            raise ValueError("Unknown checkpoint directory")
        atomic_json(out/"manifest.json",manifest)
    pending={r["mainline_sha256"] for r in selected}
    with ExitStack() as stack:
        reference=stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(sf),timeout=180.0))
        reference.configure({"Threads":1,"Hash":64})
        engine=stack.enter_context(_open_engine(root,exe,fischer,book=False))
        for game,row in _iter_verified_games(pgn,selected,surname):
            gid=row["mainline_sha256"]
            if gid not in pending:
                raise ValueError("Unexpected game in shard")
            pending.remove(gid)
            path=out/(gid+".json")
            decisions,forced=game_decisions(game,row)
            expected_positions=[(board.ply(),sha256(board.fen().encode()).hexdigest(),
                                 stage,options) for board,_,stage,options in decisions]
            if path.exists():
                doc=json.loads(path.read_text())
                if (doc["game"]!=gid or doc["arm"]!=neural_arm(gid) or
                        doc["forced"]!=forced or
                        [(r["ply"],r["fen_sha256"],r["phase"],r["legal_moves"])
                          for r in doc["decisions"]]!=expected_positions or
                        doc["nonforced"]!=len(decisions) or
                        doc["manifest_sha256"]!=digest(manifest)):
                    raise ValueError("Unverifiable checkpoint")
            else:
                measured=[]
                for board,human,stage,options in decisions:
                    entry=audit_decision(board,human,stage,options,reference,engine)
                    entry["ply"]=board.ply()
                    entry["fen_sha256"]=sha256(board.fen().encode()).hexdigest()
                    measured.append(entry)
                doc={"schema":SCHEMA,"game":gid,"arm":neural_arm(gid),
                     "year":row["date"],"period":period(row["date"]),
                     "color":row["color"],"forced":forced,
                     "nonforced":len(decisions),"decisions":measured,
                     "manifest_sha256":digest(manifest)}
                atomic_json(path,doc)
            print("FISCHER_ALL_DECISIONS",index,len(manifest["game_ids"])-len(pending),
                  len(manifest["game_ids"]),gid,doc["nonforced"],flush=True)
    if pending or hashes!={key:file_hash(path) for key,path in paths.items()} or protocol!={
            name:file_hash(basedir/name) for name in protocol_files}:
        raise ValueError("Incomplete shard or source/engine changed")
    return {"schema":SCHEMA,"shard":index,"games":len(selected),"complete":True,
            "nonforced":sum(json.loads((out/(r["mainline_sha256"]+".json")).read_text())[
                "nonforced"] for r in selected),"source":origin,"scan":scan}


def summarize_group(records):
    """Paired human-relative performance, game-cluster uncertainty."""
    values={"human":[],"d16":[],"d18":[]}
    paired={label:[] for label in DEPTHS}
    decisions=len(records)
    for row in records:
        for label in values:
            data=row["scores"][label]
            if data["cp_loss"] is not None:
                values[label].append(data["cp_loss"])
        human=row["scores"]["human"]["cp_loss"]
        for depth in DEPTHS:
            engine=row["scores"][f"d{depth}"]["cp_loss"]
            if human is not None and engine is not None:
                paired[depth].append((row["game"],engine-human))
    policies={}
    for label,losses in values.items():
        records_m=[r["scores"][label] for r in records]
        policies[label]={"decisions":len(records_m),"numeric":len(losses),
                         "mate_sensitive":sum(r["mate_sensitive"] for r in records_m),
                         "mean_signed_loss_cp":mean(losses) if losses else None,
                         "within_25cp":sum(r["within_25cp"] is True for r in records_m),
                         "over_100cp":sum(r["over_100cp"] is True for r in records_m),
                         "lost_positive_mate":sum(r["positive_mate_lost"] for r in records_m),
                         "negative_mate_selected":sum(r["negative_mate_selected"] for r in records_m),
                         "historical_match":sum(r["historical_match"] for r in records_m)}
        if label!="human":
            searches=[r["search"][label] for r in records]
            policies[label].update({
                "attained_requested_depth":sum(x["reached_requested"] for x in searches),
                "missing_reported_depth":sum(x["reported_depth"] is None for x in searches),
                "overshot_node_cap":sum(x["reported_nodes"] is not None and
                                        x["reported_nodes"]>NODE_CAP for x in searches)})
            d=int(label[1:])
            delta=[t for _,t in paired[d]]
            policies[label].update({"paired_numeric":len(delta),
                                    "mean_excess_vs_human_cp":mean(delta) if delta else None,
                                    "game_cluster_ci95_cp":game_cluster_interval(paired[d]),
                                    "distinct_paired_games":len({g for g,_ in paired[d]})})
    return {"decisions":decisions,"distinct_games":len({r["game"] for r in records}),
            "distinct_fens":len({r["fen_sha256"] for r in records}),
            "policies":policies}


def aggregate(shards,archive_dir=None):
    pgn,rows,scan,origin,surname=_source(archive_dir)
    data={r["mainline_sha256"]:r for r in rows}
    # Independent source replay determines the exact full list of
    # expected eligible decisions. A forged forced count cannot hide gaps.
    expected_decisions={}
    for game,meta in _iter_verified_games(pgn,rows,surname):
        decisions,forced=game_decisions(game,meta)
        expected_decisions[meta["mainline_sha256"]]=(
            forced,[(board.ply(),sha256(board.fen().encode()).hexdigest(),
                     stage,options) for board,_,stage,options in decisions])
    if len(expected_decisions)!=len(rows):
        raise ValueError("Incomplete independent source reconstruction")
    counts=Counter()
    collected=defaultdict(list)
    seen=set()
    depth_records=[]
    manifests=[]
    for index in range(SHARDS):
        directory=Path(shards)/f"shard-{index}"
        manifest=json.loads((directory/"manifest.json").read_text())
        expected=sorted(r["mainline_sha256"] for r in rows if expected_row(r)==index)
        if (manifest["schema"]!=SCHEMA or manifest["shard"]!=index or
                manifest["source_pgn_sha256"]!=sha256(pgn).hexdigest() or
                manifest["game_ids"]!=expected or manifest["shards"]!=SHARDS or
                manifest["reference_depth"]!=REFERENCE_DEPTH or
                manifest["candidate_depths"]!=list(DEPTHS)):
            raise ValueError("Corrupt or partial shard manifest")
        if manifests and any(manifest[key]!=manifests[0][key] for key in
                             ("hashes","protocol","chess_version","node_cap")):
            raise ValueError("Mismatched experimental environment")
        manifests.append(manifest)
        allowed={"manifest.json",*(gid+".json" for gid in expected)}
        if {f.name for f in directory.iterdir()}!=allowed:
            raise ValueError("Missing or extra game record")
        for gid in expected:
            doc=json.loads((directory/(gid+".json")).read_text())
            meta=data[gid]
            if (gid in seen or doc["schema"]!=SCHEMA or
                    doc["game"]!=gid or doc["arm"]!=neural_arm(gid) or
                    doc["color"]!=meta["color"] or doc["year"]!=meta["date"] or
                    doc["manifest_sha256"]!=digest(manifest) or
                    doc["nonforced"]!=len(doc["decisions"])):
                raise ValueError("Corrupted historical decision record")
            seen.add(gid)
            counts[doc["arm"]+":games"]+=1
            counts[doc["arm"]+":nonforced"]+=doc["nonforced"]
            counts[doc["arm"]+":forced"]+=doc["forced"]
            src_forced,src_positions=expected_decisions[gid]
            if (doc["forced"]!=src_forced or doc["nonforced"]!=len(src_positions) or
                    [(r["ply"],r["fen_sha256"],r["phase"],r["legal_moves"])
                     for r in doc["decisions"]]!=src_positions):
                raise ValueError("Game audit omitted or changed a real legal decision")
            prior=-1
            for decision in doc["decisions"]:
                if (type(decision["ply"]) is not int or decision["ply"]<=prior or
                        decision["ply"]>=meta["plies"] or decision["ply"]%2!=
                        (0 if doc["color"]=="white" else 1)):
                    raise ValueError("Duplicate, out-of-order or wrong-color focal decision")
                prior=decision["ply"]
                if (set(decision["scores"])!={"human","d16","d18"} or
                        decision["phase"] not in ("opening","middlegame","endgame") or
                        not set(decision["competencies"])<=set(COMPETENCIES)):
                    raise ValueError("Invalid candidate or competency")
                entry={**decision,"game":gid,"arm":doc["arm"],"period":doc["period"]}
                collected[doc["arm"]].append(entry)
                for depth in DEPTHS:
                    depth_records.append(decision["search"][f"d{depth}"])
    if seen!=set(data):
        raise ValueError("Not every accepted historical game was audited")
    arms={}
    for arm in ARMS:
        records=collected[arm]
        group={"all":summarize_group(records),"by_competency":{},
               "by_competency_stratum":{},
               "by_phase":{},"by_period":{},"by_stratum":{}}
        for skill in COMPETENCIES:
            subset=[x for x in records if skill in x["competencies"]]
            group["by_competency"][skill]=summarize_group(subset)
            group["by_competency_stratum"][skill]={
                key:summarize_group([v for v in subset if
                                     v["phase"]+"::"+v["stratum"]==key])
                for key in sorted({v["phase"]+"::"+v["stratum"] for v in subset})}
        for key,field,allowed in (("by_phase","phase",("opening","middlegame","endgame")),
                                  ("by_period","period",BANDS),
                                  ("by_stratum","stratum",sorted({x["stratum"] for x in records}))):
            for value in allowed:
                group[key][value]=summarize_group([r for r in records if r[field]==value])
        arms[arm]=group
    incomplete_depth=Counter()
    for stats in depth_records:
        k=str(stats["requested_depth"])
        incomplete_depth[k+":searched"]+=1
        incomplete_depth[k+":short"]+=int(not stats["reached_requested"])
        incomplete_depth[k+":missing"]+=int(stats["reported_depth"] is None)
        incomplete_depth[k+":nodes_over"]+=int(
            stats["reported_nodes"] is not None and stats["reported_nodes"]>NODE_CAP)
    experimental=choose_fit_depths(arms["fit"])
    retrospective=summarize_out_of_training(experimental,{
        "tune":arms["tune"],"excluded_test":arms["excluded_test"]})
    return {"schema":SCHEMA,"status":"ALL_FISCHER_DECISIONS_AUDITED_NOT_CALIBRATED",
            "source":origin,"source_pgn_sha256":sha256(pgn).hexdigest(),
            "games":len(rows),"counted":dict(counts),"shard_count":SHARDS,
            "engine_hashes":manifests[0]["hashes"],"protocol_hashes":manifests[0]["protocol"],
            "reference_depth":REFERENCE_DEPTH,"candidate_depths":list(DEPTHS),
            "engine_search_telemetry":dict(incomplete_depth),
            "arms":arms,"experimental_phase_budget":experimental,
            "out_of_training_retrospective_checks":retrospective,"skill_values_0_110":dict.fromkeys(COMPETENCIES),
            "test_disclosure":TEST_DISCLOSURE,
            "training_only_selection_allowed":"fit",
            "independent_blind_test_available":False,
            "career_levels_enabled":False,"official_engine_modified":False,
            "limitations":["All original accepted games, no arbitrary sample; forced choices not scored.",
                           "Category labels/position difficulty are provisional proxies, not validated skills.",
                           "FIT was used for earlier design; TUNE and outer TEST inspected previously.",
                           "Stockfish depth20 is a common reference, not ground-truth human Elo.",
                           "No common 0–110 scale until qualified cross-player anchors are supplied.",
                           "Raw source moves not redistributed."]}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest="command",required=True)
    for name in ("plan","play","aggregate"):
        q=subs.add_parser(name)
        source=q.add_mutually_exclusive_group(required=True)
        source.add_argument("--fetch",action="store_true")
        source.add_argument("--archive-dir",type=Path)
        if name=="play":
            q.add_argument("--index",required=True,type=int)
            q.add_argument("--rodent-root",required=True,type=Path)
            q.add_argument("--stockfish",required=True,type=Path)
        if name=="aggregate":
            q.add_argument("--shards",required=True,type=Path)
        if name!="plan":
            q.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.command=="plan":
        doc=plan(args.archive_dir)
    elif args.command=="play":
        doc=run_shard(args.rodent_root,args.stockfish,args.out,args.index,args.archive_dir)
    else:
        doc=aggregate(args.shards,args.archive_dir)
        if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
            raise ValueError("Refuse overwrite")
        atomic_json(args.out,doc)
    print(json.dumps(doc,sort_keys=True,allow_nan=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
