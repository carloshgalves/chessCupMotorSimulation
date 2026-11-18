"""Fischer-only exploratory depth sweep, with pre-move context proxies.

Pilot on 24 hash-selected peak FIT games and all 23 TUNE games. All
candidates share a root and an independent Stockfish d18 reference.
Depth caps are NOT claimed as completed depths if the node budget binds.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from math import isfinite

import chess
import chess.engine

from .catalog import Player, find_player
from .fischer_position_calibration import cohort, sample, summarize, PHASES
from .matches import _open_engine, file_hash
from .real85_graded_phase_attribution import _score
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)
from .registry import registered_aliases

SCHEMA = "chesssim-fischer-deep-calibration-v1"
DEPTHS = (8, 12, 16)
REFERENCE_DEPTH = 18
NODE_CAP = 5_000_000
PEAK_LIMIT = 24
SALT = "fischer-deep-competencies-v1"
CANDIDATES = tuple(f"{p}_d{d}" for p in ("fischer", "simple") for d in DEPTHS)
COMPETENCIES = ("opening", "tactics", "dynamics", "defense", "positional", "endgames")


def select_panel(rows):
    eligible = {"peak_fit": [], "tune": []}
    for row in rows:
        arm = cohort(row)
        if arm in eligible:
            eligible[arm].append(row)
    if len(eligible["peak_fit"]) < PEAK_LIMIT or not eligible["tune"]:
        raise ValueError("Insufficient historical games for preregistered panel")
    peak = sorted(eligible["peak_fit"], key=lambda r: (
        sha256(f"{SALT}:{r['mainline_sha256']}".encode()).hexdigest(),
        r["mainline_sha256"]))[:PEAK_LIMIT]
    return peak + eligible["tune"]


def tags(board, phase, best, runner_up):
    """Pre-move, overlapping proxies. Never condition on human/candidate move."""
    labels = []
    if phase == "opening":
        labels.append("opening")
    if phase == "endgame":
        labels.append("endgames")
    cp, other = best["cp"], runner_up["cp"]
    # Only-choice-like advantage, not a proof of a tactical combination.
    clear = cp is not None and other is not None and cp - other >= 80
    if clear or (best["mate"] is not None and best["mate"] > 0 and
                 (runner_up["mate"] is None or runner_up["mate"] <= 0)):
        labels.append("tactics")
    if board.is_check() or (cp is not None and -300 <= cp <= -80):
        labels.append("defense")
    imbalance = sum(abs(len(board.pieces(p, chess.WHITE))-len(board.pieces(p, chess.BLACK)))
                    for p in (chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN))
    if phase == "middlegame" and imbalance >= 2:
        labels.append("dynamics")
    if (phase == "middlegame" and not board.is_check() and cp is not None
            and other is not None and abs(cp) <= 100 and 0 <= cp-other <= 35):
        labels.append("positional")
    return labels


def reference_choices(engine, board):
    infos = engine.analyse(board, chess.engine.Limit(depth=REFERENCE_DEPTH),
                           multipv=2, game=object())
    if len(infos) != 2:
        raise RuntimeError("Reference must return two distinct complete variations")
    result = []
    for info in infos:
        pv=info.get("pv")
        if (not pv or pv[0] not in board.legal_moves or
                type(info.get("depth")) is not int or info["depth"] < REFERENCE_DEPTH or
                info.get("score") is None):
            raise RuntimeError("Incomplete reference MultiPV search")
        pov=info["score"].pov(board.turn)
        result.append({"move":pv[0].uci(),"cp":pov.score(),"mate":pov.mate()})
    if result[0]["move"]==result[1]["move"]:
        raise RuntimeError("Duplicate MultiPV root moves")
    return result


def choose(engine, board, depth):
    started=perf_counter()
    result=engine.play(board,chess.engine.Limit(depth=depth,nodes=NODE_CAP),
                       game=object(),info=chess.engine.INFO_ALL)
    if result.move is None or result.move not in board.legal_moves:
        raise RuntimeError("Illegal or missing UCI choice")
    elapsed=perf_counter()-started
    info=result.info
    reached=info.get("depth")
    nodes=info.get("nodes")
    seconds=info.get("time")
    if type(reached) is not int or reached < 1:
        reached=None
    if type(nodes) is not int or nodes < 0:
        nodes=None
    if type(seconds) not in (float,int) or not isfinite(seconds) or seconds < 0:
        seconds=None
    return result.move,{"requested_depth":depth,"reported_depth":reached,
                        "reached_requested":reached is not None and reached>=depth,
                        "reported_nodes":nodes,"reported_seconds":seconds,"elapsed_seconds":elapsed}


def evaluate(board,human,phase,reference,engines):
    if human not in board.legal_moves or board.legal_moves.count()<2:
        raise ValueError("Require nonforced legal human decision")
    if set(engines)!={"fischer","simple"}:
        raise ValueError("Require both engine controls")
    choices={"human":human}
    telemetry={}
    for p in ("fischer","simple"):
        for depth in DEPTHS:
            label=f"{p}_d{depth}"
            choices[label],telemetry[label]=choose(engines[p],board,depth)
    best,other=reference_choices(reference,board)
    labels=tags(board,phase,best,other)
    preferred=chess.Move.from_uci(best["move"])
    scores={move:_score(reference,board,move,REFERENCE_DEPTH)
            for move in sorted({preferred,*choices.values()},key=lambda m:m.uci())}
    ref=scores[preferred]
    values={}
    for label,move in choices.items():
        score=scores[move]
        numeric=ref["mate"] is None and score["mate"] is None
        values[label]={"human_match":move==human,"reference_match":move==preferred,
                       "signed_gap_cp":ref["cp"]-score["cp"] if numeric else None,
                       "reference_mate":ref["mate"],"move_mate":score["mate"]}
    return labels,values,telemetry


def telemetry_summary(rows):
    result={}
    for label in CANDIDATES:
        items=[r["telemetry"][label] for r in rows]
        depths=[x["reported_depth"] for x in items if x["reported_depth"] is not None]
        nodes=[x["reported_nodes"] for x in items if x["reported_nodes"] is not None]
        seconds=[x["reported_seconds"] for x in items if x["reported_seconds"] is not None]
        result[label]={"searches":len(items),"depth_reported":len(depths),
                       "reached_requested":sum(x["reached_requested"] for x in items),
                       "below_requested":sum(x["reported_depth"] is not None and
                                             not x["reached_requested"] for x in items),
                       "depth_missing":len(items)-len(depths),
                       "median_reported_depth":median(depths) if depths else None,
                       "nodes_reported":len(nodes),"median_reported_nodes":median(nodes) if nodes else None,
                       "node_cap_exceeded":sum(n>NODE_CAP for n in nodes),
                       "max_reported_nodes":max(nodes) if nodes else None,
                       "median_elapsed_seconds":median([x["elapsed_seconds"] for x in items]) if items else None,
                       "seconds_reported":len(seconds),"median_reported_seconds":median(seconds) if seconds else None}
    return result


def run(rodent,stockfish,archive_dir=None):
    root=Path(rodent).resolve(strict=True)
    exe=root/"rodent-iv-plain.exe"
    sf=Path(stockfish).resolve(strict=True)
    fischer=find_player("fischer")
    aliases=registered_aliases(root/"personalities/basic.ini")
    if aliases.get(fischer.rodent_alias)!=fischer.profile_filename or aliases.get("Simple")!="simple.txt":
        raise ValueError("Require registered Fischer and Simple")
    simple=Player(number=0,name="Rodent IV Simple",slug="control-simple",group="baseline",
                  rodent_alias="Simple",rodent_file="simple.txt")
    files={"rodent":exe,"stockfish":sf,"fischer":root/"personalities"/fischer.profile_filename,
           "simple":root/"personalities/simple.txt","registry":root/"personalities/basic.ini"}
    hashes={k:file_hash(v) for k,v in files.items()}
    conf=_player_configuration(["fischer"],None,None)["fischer"]
    _,pgn,origin=_source_bytes(conf["source"],archive_dir)
    rows,scan=_scan(pgn,"fischer",conf["surname"])
    selected=select_panel(rows)
    expected={a:sum(cohort(r)==a for r in selected) for a in ("peak_fit","tune")}
    panel_digest=sha256("\n".join(sorted(r["mainline_sha256"] for r in selected)).encode()).hexdigest()
    seen=set()
    data=defaultdict(list)
    with ExitStack() as stack:
        ref=stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(sf),timeout=180.0))
        ref.configure({"Threads":1,"Hash":64})
        engines={k:stack.enter_context(_open_engine(root,exe,p,book=False))
                 for k,p in (("fischer",fischer),("simple",simple))}
        engine_id=dict(ref.id)
        for game,row in _iter_verified_games(pgn,selected,conf["surname"]):
            arm=cohort(row)
            digest=row["mainline_sha256"]
            if digest in seen or arm not in expected:
                raise ValueError("Duplicate or reserved game")
            seen.add(digest)
            for stage,board,human in sample(game,row):
                labels,values,telemetry=evaluate(board,human,stage,ref,engines)
                data[arm].append({"game":digest,"fen":board.fen(),"phase":stage,
                                  "tags":labels,"scores":values,"telemetry":telemetry})
    if seen!={r["mainline_sha256"] for r in selected} or hashes!={k:file_hash(v) for k,v in files.items()}:
        raise ValueError("Incomplete panel or changed engine")
    panels={}
    for arm in expected:
        if not data[arm]:
            raise ValueError("Empty panel")
        def cell(items):
            return {**summarize(items,CANDIDATES),"search":telemetry_summary(items)}
        panels[arm]={"all":cell(data[arm]),
                     "by_phase":{p:cell([r for r in data[arm] if r["phase"]==p]) for p in PHASES},
                     "competency_proxies":{c:cell([r for r in data[arm] if c in r["tags"]])
                                           for c in COMPETENCIES}}
    return {"schema":SCHEMA,"status":"DEEP_BUDGET_DIAGNOSTIC_NOT_COMPETENCY_CALIBRATION",
            "player":"fischer","source":origin,"source_snapshot":MIRROR_COMMIT if archive_dir is None else None,
            "source_pgn_sha256":sha256(pgn).hexdigest(),"scan":scan,"hashes":hashes,
            "panel_sha256":panel_digest,"expected_games":expected,"candidate_depth_caps":list(DEPTHS),
            "candidate_node_cap":NODE_CAP,"reference_engine":engine_id,"reference_depth":REFERENCE_DEPTH,
            "reference_threads":1,"reference_hash_mb":64,"book_enabled":False,
            "outer_test_decisions_evaluated":0,"historically_validated":False,"intrinsic_elo":None,
            "competency_values":dict.fromkeys(COMPETENCIES),"panels":panels,
            "limitations":["Depth caps with node limits; incomplete/missing depth reported, not silently relabeled.",
                           "Engine nodes/time are UCI-reported; observed overshoots and missing counts disclosed.",
                           "Wall-clock latency measured on the CI host, not the user laptop.",
                           "Pilot subset; TUNE exploratory, not confirmatory.",
                           "Overlapping heuristic competency tags, not validated skill labels or score anchors.",
                           "No book or neural intervention; no deployment or Elo claim.",
                           "Reference d18 MultiPV classification and forced-root scoring may disagree.",
                           "No source positions or moves published."]}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch",action="store_true")
    src.add_argument("--archive-dir",type=Path)
    p.add_argument("--rodent-root",required=True,type=Path)
    p.add_argument("--stockfish",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=p.parse_args(argv)
    if a.out.exists() or a.out.is_symlink() or not a.out.parent.is_dir():
        raise ValueError("Output must be new")
    report=run(a.rodent_root,a.stockfish,a.archive_dir)
    with a.out.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,sort_keys=True,indent=2,allow_nan=False)
        handle.write("\n")
    print(json.dumps(report,sort_keys=True,allow_nan=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
