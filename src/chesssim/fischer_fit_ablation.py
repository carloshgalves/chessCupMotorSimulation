"""Matched Fischer-only book/residual ablation on every development game (FIT/TUNE).

Experimental position choices, not an Arena policy, Elo or promotion test.
The reference and intervention guard run in separate Stockfish processes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.engine

from .catalog import find_player
from .fischer_deep_calibration import (
    REFERENCE_DEPTH, NODE_CAP, COMPETENCIES, choose, reference_choices, tags,
)
from .fischer_position_calibration import SALT as POSITION_SALT, summarize, PHASES
from .fischer_baseline import BANDS, period, phase
from .matches import _open_engine, file_hash
from .opening_books import SCHEMA as BOOK_SCHEMA, position_token, select_book_move
from .real85_graded_phase_attribution import _score
from .real85_neural_guarded_uci import NeuralGuardedPolicy, _load_player
from .real85_neural_stream import neural_arm, verify
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)
from .registry import registered_aliases

SCHEMA = "chesssim-fischer-fit-ablation-v2"
ARMS = ("fit", "tune")
DEPTHS = (12,16)
MODES = ("base","book","residual","both")
CANDIDATES = tuple(f"d{d}_{mode}" for d in DEPTHS for mode in MODES)
BOOK_SEED = 2033
CP_BUDGET = 25


def select_all_development(rows):
    """Every accepted FIT/TUNE trajectory, no cap by year, number or order."""
    chosen = [r for r in rows if neural_arm(r["mainline_sha256"]) in ARMS]
    identities = [r["mainline_sha256"] for r in chosen]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate development trajectory")
    if {neural_arm(s) for s in identities} != set(ARMS):
        raise ValueError("Both development arms are required")
    return chosen


def sample_game(game, row):
    """Replay ALL focal decisions; audit one fixed nonforced root per phase.

    Preserve the #193/#195 position salt for direct within-game comparisons.
    Games with no nonforced decision are counted but not engine-scored.
    """
    digest = row["mainline_sha256"]
    if neural_arm(digest) not in ARMS:
        raise ValueError("Reserved source trajectory")
    board = game.board()
    if not board.is_valid() or row["color"] not in ("white", "black"):
        raise ValueError("Invalid historical board or focal color")
    chosen = {}
    counts = Counter()
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("Illegal historical move")
        if board.turn == (row["color"] == "white"):
            stage = phase(board)
            counts["focal_decisions"] += 1
            if board.legal_moves.count() == 1:
                counts["forced"] += 1
            else:
                counts["nonforced"] += 1
                counts[f"eligible_{stage}"] += 1
                key = sha256(f"{POSITION_SALT}:{digest}:{board.ply()}".encode()).hexdigest()
                if stage not in chosen or key < chosen[stage][0]:
                    chosen[stage] = (key, board.copy(stack=True), move)
        board.push(move)
    return [(s,item[1],item[2]) for s,item in sorted(chosen.items())], counts


def fit_book(pgn, rows, surname):
    selected = [r for r in rows if neural_arm(r["mainline_sha256"]) == "fit"]
    votes = {side:defaultdict(Counter) for side in ("white","black")}
    seen = set()
    for game,row in _iter_verified_games(pgn,selected,surname):
        digest = row["mainline_sha256"]
        if digest in seen or neural_arm(digest) != "fit":
            raise ValueError("Duplicate or reserved book input")
        seen.add(digest)
        board = game.board()
        for move in game.mainline_moves():
            if board.ply() >= 40:
                break
            if board.turn == (row["color"] == "white"):
                votes[row["color"]][position_token(board)][move.uci()] += 1
            board.push(move)
    if seen != {r["mainline_sha256"] for r in selected} or not seen:
        raise ValueError("Incomplete FIT book")
    return {"schema":BOOK_SCHEMA,"player":"fischer","policy":{"max_plies":40},
            "source":{"pgn_sha256":sha256(pgn).hexdigest(),"accepted_games":len(seen),
                      "provenance":"REAL85_NEURAL_FIT_ONLY"},
            "positions":{side:dict(v) for side,v in votes.items()}}


class FreshAnalysis:
    """Avoid search-order effects from the guard's previous root analyses."""
    def __init__(self, engine):
        self.engine=engine

    def analyse(self, board, limit, **kwargs):
        # Includes initial MultiPV and forced-root confirmation.
        kwargs["game"]=object()
        return self.engine.analyse(board,limit,**kwargs)


def guard_book(board, base, book, guard):
    proposed=select_book_move(book,board,seed=BOOK_SEED,game_number=1,min_count=2)
    if proposed is None:
        return base,"no_book_support"
    if proposed==base:
        return base,"book_agreement"
    best=_score(guard,board,None,14)
    base_score=_score(guard,board,base,14)
    proposed_score=_score(guard,board,proposed,14)
    if any(s["mate"] is not None for s in (best,base_score,proposed_score)):
        return base,"book_mate_guard"
    if (best["cp"]-proposed_score["cp"]>CP_BUDGET or
            base_score["cp"]-proposed_score["cp"]>CP_BUDGET):
        return base,"book_cp_guard"
    return proposed,"book_override"


def evaluate(board,human,stage,engine,guard,reference,book,policy):
    if human not in board.legal_moves or board.legal_moves.count()<2:
        raise ValueError("Require nonforced legal historical choice")
    choices={"human":human}
    outcomes={}
    searches={}
    for depth in DEPTHS:
        base,telemetry=choose(engine,board,depth)
        choices[f"d{depth}_base"]=base
        searches[str(depth)]=telemetry
        if board.ply()<40:
            learned,reason=guard_book(board,base,book,guard)
            residual=base
            residual_reason="opening_no_residual"
        else:
            learned,reason=base,"outside_book"
            decision=policy.select(board,slug="fischer",engine_move=base,
                                   round_number=1,ply=board.ply()+1)
            residual=decision.move
            residual_reason=decision.audit["outcome"]
            if residual not in board.legal_moves or ((residual!=base) !=
                    (residual_reason=="EXTERNAL_POLICY_OVERRIDE")):
                raise ValueError("Residual decision and intervention trace disagree")
        choices[f"d{depth}_book"]=learned
        choices[f"d{depth}_residual"]=residual
        # Explicitly mutually exclusive: book window and neural window.
        choices[f"d{depth}_both"]=learned if board.ply()<40 else residual
        outcomes[str(depth)]={"book":reason,"residual":residual_reason}
    best,other=reference_choices(reference,board)
    preferred=chess.Move.from_uci(best["move"])
    scores={m:_score(reference,board,m,REFERENCE_DEPTH)
            for m in sorted({preferred,*choices.values()},key=lambda m:m.uci())}
    ref=scores[preferred]
    values={}
    for label,move in choices.items():
        score=scores[move]
        numeric=ref["mate"] is None and score["mate"] is None
        values[label]={"human_match":move==human,"reference_match":move==preferred,
                       "signed_gap_cp":ref["cp"]-score["cp"] if numeric else None,
                       "reference_mate":ref["mate"],"move_mate":score["mate"]}
        if label!="human":
            base_label=label.split("_")[0]+"_base"
            base=choices[base_label]
            base_score=scores[base]
            values[label]["changed"]=move!=base
            values[label]["audit_delta_cp"]=(
                score["cp"]-base_score["cp"] if score["mate"] is None and
                base_score["mate"] is None else None)
            values[label]["lost_positive_mate_vs_base"]=(
                base_score["mate"] is not None and base_score["mate"]>0 and
                (score["mate"] is None or score["mate"]<=0))
            values[label]["introduced_negative_mate_vs_base"]=(
                score["mate"] is not None and score["mate"]<0 and
                (base_score["mate"] is None or base_score["mate"]>=0))
    return tags(board,stage,best,other),values,outcomes,searches


def cell(rows):
    result=summarize(rows,CANDIDATES)
    result["interventions"]={}
    for label in CANDIDATES:
        changes=[r["scores"][label] for r in rows if r["scores"][label]["changed"]]
        deltas=[v["audit_delta_cp"] for v in changes if v["audit_delta_cp"] is not None]
        result["interventions"][label]={"changed":len(changes),
            "nonmate_changes":len(deltas),"mate_sensitive_changes":len(changes)-len(deltas),
            "worse_over_25cp":sum(d < -25 for d in deltas),
            "worse_over_100cp":sum(d < -100 for d in deltas),
            "lost_positive_mate_vs_base":sum(v["lost_positive_mate_vs_base"] for v in changes),
            "introduced_negative_mate_vs_base":sum(v["introduced_negative_mate_vs_base"] for v in changes)}
    result["outcomes"]={str(d):{mode:dict(Counter(r["outcomes"][str(d)][mode] for r in rows))
                               for mode in ("book","residual")} for d in DEPTHS}
    result["search"]={str(d):{"searches":len(rows),
        "reached_requested":sum(r["searches"][str(d)]["reached_requested"] for r in rows),
        "depth_missing":sum(r["searches"][str(d)]["reported_depth"] is None for r in rows)}
        for d in DEPTHS}
    return result


def run(rodent,stockfish,inputs,weights,archive_dir=None):
    root=Path(rodent).resolve(strict=True)
    exe=root/"rodent-iv-plain.exe"
    sf=Path(stockfish).resolve(strict=True)
    inputs=Path(inputs).resolve(strict=True)
    weights=Path(weights).resolve(strict=True)
    fischer=find_player("fischer")
    if registered_aliases(root/"personalities/basic.ini").get(fischer.rodent_alias)!=fischer.profile_filename:
        raise ValueError("Require registered original Fischer")
    conf=_player_configuration(["fischer"],None,None)["fischer"]
    _,pgn,origin=_source_bytes(conf["source"],archive_dir)
    rows,scan=_scan(pgn,"fischer",conf["surname"])
    source_hash=sha256(pgn).hexdigest()
    checked=verify(inputs)
    manifest_path=inputs/"manifest.json"
    manifest=json.loads(manifest_path.read_text())
    if checked["status"]!="VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES" or set(manifest["players"])!={"fischer"}:
        raise ValueError("Require verified Fischer-only input")
    summary=manifest["players"]["fischer"]
    if summary["source_pgn_sha256"]!=source_hash:
        raise ValueError("Historical source mismatch")
    for arm in ("fit","tune"):
        shard=inputs/summary["shards"][arm]["file"]
        actual={json.loads(line)["game_sha256"]:json.loads(line)["focal_color"]
                for line in shard.read_text().splitlines()}
        expected={r["mainline_sha256"]:r["color"] for r in rows
                  if neural_arm(r["mainline_sha256"])==arm}
        if actual!=expected:
            raise ValueError("Input identities or focal colors differ from source")
    model_report_path=weights/"report.json"
    model_report=json.loads(model_report_path.read_text())
    checkpoint_path=weights/model_report["players"]["fischer"]["checkpoint_file"]
    cp=_load_player(weights,"fischer")
    if (cp["input_manifest_sha256"]!=file_hash(manifest_path) or
            cp["fit_shard_sha256"]!=summary["shards"]["fit"]["sha256"]):
        raise ValueError("Model not bound to verified input")
    book=fit_book(pgn,rows,conf["surname"])
    selected=select_all_development(rows)
    expected={a:sum(neural_arm(r["mainline_sha256"])==a for r in selected) for a in ARMS}
    if expected != {"fit":summary["fit_games"],"tune":summary["tune_games"]}:
        raise ValueError("Development game selection does not cover the verified manifest")
    panel_hash=sha256("\n".join(sorted(r["mainline_sha256"] for r in selected)).encode()).hexdigest()
    files={"rodent":exe,"stockfish":sf,"profile":root/"personalities"/fischer.profile_filename,
           "registry":root/"personalities/basic.ini","input_manifest":manifest_path,
           "model_report":model_report_path,"checkpoint":checkpoint_path,
           "fit_shard":inputs/summary["shards"]["fit"]["file"],
           "tune_shard":inputs/summary["shards"]["tune"]["file"]}
    hashes={k:file_hash(v) for k,v in files.items()}
    data=defaultdict(list)
    coverage={a:Counter() for a in ARMS}
    period_coverage={a:{band:Counter() for band in BANDS} for a in ARMS}
    seen=set()
    with ExitStack() as stack:
        reference=stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(sf),timeout=180.0))
        guard=stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(sf),timeout=180.0))
        for sf_engine in (reference,guard):
            sf_engine.configure({"Threads":1,"Hash":64})
        engine=stack.enter_context(_open_engine(root,exe,fischer,book=False))
        policy=NeuralGuardedPolicy("fischer",cp,FreshAnalysis(guard),depth=10,confirmation_depth=14,
                                   cp_budget=CP_BUDGET,min_ply=41)
        for game,row in _iter_verified_games(pgn,selected,conf["surname"]):
            digest=row["mainline_sha256"]
            arm=neural_arm(digest)
            band=period(row["date"])
            if digest in seen or arm not in expected:
                raise ValueError("Duplicate or excluded position game")
            seen.add(digest)
            observations,counts=sample_game(game,row)
            for counter in (coverage[arm],period_coverage[arm][band]):
                counter.update(counts)
                counter["games_replayed"] += 1
                counter["games_with_nonforced_choice"] += int(bool(observations))
                counter["games_without_nonforced_choice"] += int(not observations)
                counter["positions_scored"] += len(observations)
            for stage,board,human in observations:
                labels,values,outcomes,searches=evaluate(board,human,stage,engine,guard,reference,book,policy)
                data[arm].append({"game":digest,"fen":board.fen(),"phase":stage,"period":band,
                                  "tags":labels,"scores":values,"outcomes":outcomes,"searches":searches})
            if len(seen)%25==0 or len(seen)==len(selected):
                print(f"FISCHER_PROGRESS {len(seen)}/{len(selected)} games; "
                      f"{sum(len(x) for x in data.values())} scored positions",flush=True)
    if seen!={r["mainline_sha256"] for r in selected} or hashes!={k:file_hash(v) for k,v in files.items()}:
        raise ValueError("Incomplete panel or changed files")
    for a in ARMS:
        cov=coverage[a]
        if (cov["games_replayed"] != expected[a] or
                cov["focal_decisions"] != summary[f"{a}_focal_decisions_all_phases"] or
                cov["forced"] + cov["nonforced"] != cov["focal_decisions"] or
                cov["positions_scored"] != len(data[a]) or
                cov["games_with_nonforced_choice"] != len({r["game"] for r in data[a]}) or
                cov["games_with_nonforced_choice"]+cov["games_without_nonforced_choice"] != expected[a]):
            raise ValueError("Incomplete game/decision audit coverage")
    panels={a:{"all":cell(data[a]),"by_phase":{p:cell([r for r in data[a] if r["phase"]==p]) for p in PHASES},
               "by_period":{band:cell([r for r in data[a] if r["period"]==band]) for band in BANDS},
               "competency_proxies":{c:cell([r for r in data[a] if c in r["tags"]]) for c in COMPETENCIES}}
            for a in expected}
    if any(not q["all"]["positions"] for q in panels.values()):
        raise ValueError("Empty panel")
    return {"schema":SCHEMA,"status":"FISCHER_ABLATION_DIAGNOSTIC_NOT_PROMOTION",
            "source":origin,"source_snapshot":MIRROR_COMMIT if archive_dir is None else None,
            "source_pgn_sha256":source_hash,"panel_sha256":panel_hash,"hashes":hashes,"scan":scan,
            "model_sha256":policy.model["model_sha256"],
            "book_sha256":sha256(json.dumps(book,sort_keys=True).encode()).hexdigest(),
            "book_fit_games":book["source"]["accepted_games"],
            "book_positions":sum(len(v) for v in book["positions"].values()),
            "expected_games":expected,"coverage":coverage,"coverage_by_period":period_coverage,
            "all_source_games":len(rows),"excluded_test_games":len(rows)-len(selected),
            "position_sampling":"one deterministic nonforced choice per available phase per EVERY development game",
            "depth_caps":list(DEPTHS),"node_cap":NODE_CAP,
            "reference_depth":REFERENCE_DEPTH,"guard_depth":10,"confirmation_depth":14,
            "cp_budget":CP_BUDGET,"outer_test_decisions_evaluated":0,"historically_validated":False,
            "intrinsic_elo":None,"competency_values":dict.fromkeys(COMPETENCIES),"panels":panels,
            "limitations":["All FIT/TUNE games, one nonforced root per phase; not every focal decision engine-scored.",
                           "FIT is in-sample; TUNE previously inspected; reserved TEST excluded from tuning.",
                           "Intervention guard is shallower than fixed d18 audit; violations disclosed.",
                           "Book and residual disjoint by ply; book min count 2, seed 2033.",
                           "Context labels are overlapping proxies, not calibrated competency scores.",
                           "No source positions or moves published, no Arena activation."]}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    src=p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch",action="store_true")
    src.add_argument("--archive-dir",type=Path)
    for name in ("rodent-root","stockfish","inputs","weights","out"):
        p.add_argument("--"+name,type=Path,required=True)
    a=p.parse_args(argv)
    if a.out.exists() or a.out.is_symlink() or not a.out.parent.is_dir():
        raise ValueError("Output must be new")
    report=run(a.rodent_root,a.stockfish,a.inputs,a.weights,a.archive_dir)
    with a.out.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,sort_keys=True,indent=2,allow_nan=False)
        handle.write("\n")
    print(json.dumps(report,sort_keys=True,allow_nan=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
