"""Fischer-specific same-position search calibration, not Elo or promotion.

Predeclared peak FIT (1970–72) and all TUNE games, compared separately.
One nonforced decision per material/ply phase per game. No outer TEST.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median
import random
from contextlib import ExitStack

import chess
import chess.engine

from .catalog import Player, find_player
from .fischer_baseline import phase, period
from .matches import _open_engine, file_hash
from .real85_graded_phase_attribution import _score
from .real85_neural_stream import neural_arm
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)
from .registry import registered_aliases

SCHEMA = "chesssim-fischer-position-calibration-v1"
SALT = "fischer-position-calibration-191-v1"
DEPTHS = (4, 6, 8)
ARMS = ("peak_fit", "tune")
PHASES = ("opening", "middlegame", "endgame")
CANDIDATES = tuple(f"{profile}_d{depth}" for profile in ("fischer", "simple") for depth in DEPTHS)


def cohort(row: dict) -> str | None:
    arm = neural_arm(row["mainline_sha256"])
    if arm == "tune":
        return "tune"
    if arm == "fit" and period(row["date"]) == "1970_1972":
        return "peak_fit"
    return None


def sample(game, row):
    arm = cohort(row)
    if arm not in ARMS:
        raise ValueError("Position calibration cannot read excluded games")
    board = game.board()
    if not board.is_valid() or row["color"] not in ("white", "black"):
        raise ValueError("Invalid board or focal color")
    chosen = {}
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("Illegal historical move")
        if board.turn == (row["color"] == "white") and board.legal_moves.count() > 1:
            stage = phase(board)
            key = sha256(f"{SALT}:{row['mainline_sha256']}:{board.ply()}".encode()).hexdigest()
            if stage not in chosen or key < chosen[stage][0]:
                chosen[stage] = (key, board.copy(stack=True), move)
        board.push(move)
    return [(stage, item[1], item[2]) for stage, item in sorted(chosen.items())]


def evaluate(board, human, reference, engines):
    """Every policy chooses independently before identical reference scoring."""
    if human not in board.legal_moves or board.legal_moves.count() < 2:
        raise ValueError("Require a legal nonforced human decision")
    if set(engines) != {"fischer", "simple"}:
        raise ValueError("Both unchanged original personality controls required")
    choices = {"human": human}
    for profile, engine in engines.items():
        for depth in DEPTHS:
            result = engine.play(board, chess.engine.Limit(depth=depth), game=object())
            if result.move is None or result.move not in board.legal_moves:
                raise ValueError("Illegal or absent Rodent move")
            choices[f"{profile}_d{depth}"] = result.move
    best = _score(reference, board, None, 16)
    # Use a forced-root search even for the reference's preferred move.
    # All candidates receive exactly the same scoring procedure.
    scores = {}
    for move in sorted({chess.Move.from_uci(best["move"]), *choices.values()}, key=lambda m:m.uci()):
        scores[move] = _score(reference, board, move, 16)
    ref = scores[chess.Move.from_uci(best["move"])]
    context = ["all"]
    if board.is_check():
        context.append("in_check")
    if ref["cp"] is not None and ref["cp"] <= -100:
        context.append("disadvantage_proxy")
    if ref["cp"] is not None and ref["cp"] >= 100:
        context.append("advantage_proxy")
    if ref["cp"] is not None and abs(ref["cp"]) < 100 and not board.is_check():
        context.append("balanced_not_in_check_proxy")
    output = {}
    for label, move in choices.items():
        score = scores[move]
        numeric = ref["mate"] is None and score["mate"] is None
        output[label] = {"human_match":move == human,"reference_match":move.uci() == best["move"],
                         "signed_gap_cp":ref["cp"]-score["cp"] if numeric else None,
                         "reference_mate":ref["mate"],"move_mate":score["mate"]}
    return context, output


def game_cluster_interval(pairs):
    """Exploratory percentile bootstrap of game clusters, not independent FENs."""
    groups = defaultdict(list)
    for game, delta in pairs:
        groups[game].append(delta)
    if len(groups) < 20:
        return None
    blocks = [(sum(groups[g]),len(groups[g])) for g in sorted(groups)]
    rng = random.Random(191)
    draws = []
    for _ in range(1000):
        selected = rng.choices(blocks,k=len(blocks))
        draws.append(sum(t for t,n in selected)/sum(n for t,n in selected))
    draws.sort()
    return [draws[25],draws[974]]


def summarize(rows):
    """Matched denominators and same-game cluster uncertainty not position IID."""
    result = {}
    for label in ("human", *CANDIDATES):
        entries = [r["scores"][label] for r in rows]
        gaps = [e["signed_gap_cp"] for e in entries if e["signed_gap_cp"] is not None]
        paired = [(r["scores"]["human"]["signed_gap_cp"], r["scores"][label]["signed_gap_cp"])
                  for r in rows if r["scores"]["human"]["signed_gap_cp"] is not None and
                  r["scores"][label]["signed_gap_cp"] is not None]
        delta = [b-a for a,b in paired]
        clusters = [(r["game"],r["scores"][label]["signed_gap_cp"]-r["scores"]["human"]["signed_gap_cp"])
                    for r in rows if r["scores"]["human"]["signed_gap_cp"] is not None and
                    r["scores"][label]["signed_gap_cp"] is not None]
        n = len(entries)
        result[label] = {
            "decisions":n,"human_matches":sum(e["human_match"] for e in entries),
            "reference_matches":sum(e["reference_match"] for e in entries),
            "human_match_rate":sum(e["human_match"] for e in entries)/n if n else None,
            "nonmate_count":len(gaps),"mate_sensitive_count":n-len(gaps),
            "mean_signed_gap_cp":mean(gaps) if gaps else None,
            "median_signed_gap_cp":median(gaps) if gaps else None,
            "gap_over_100cp":sum(g>100 for g in gaps),
            "paired_nonmate_count":len(paired),
            "mean_paired_excess_gap_vs_human_cp":mean(delta) if delta else None,
            "paired_game_cluster_ci95_cp":game_cluster_interval(clusters),
            "paired_distinct_games":len({g for g,d in clusters}),
            "paired_more_than_100cp_worse_than_human":sum(d>100 for d in delta),
            "paired_more_than_100cp_better_than_human":sum(d < -100 for d in delta),
            "lost_positive_mate":sum(e["reference_mate"] is not None and e["reference_mate"]>0
                                     and (e["move_mate"] is None or e["move_mate"]<=0)
                                     for e in entries),
        }
    return {"games":len({r["game"] for r in rows}),"positions":len(rows),
            "distinct_fens":len({r["fen"] for r in rows}),"policies":result}


def run(rodent, stockfish, archive_dir=None):
    root = Path(rodent).resolve(strict=True)
    exe = root/"rodent-iv-plain.exe"
    sf = Path(stockfish).resolve(strict=True)
    aliases = registered_aliases(root/"personalities/basic.ini")
    fischer = find_player("fischer")
    if aliases.get(fischer.rodent_alias)!=fischer.profile_filename or aliases.get("Simple")!="simple.txt":
        raise ValueError("Require original registered Fischer and Simple")
    simple = Player(number=0,name="Rodent IV Simple",slug="control-simple",group="baseline",
                    rodent_alias="Simple",rodent_file="simple.txt")
    files = {"rodent":exe,"stockfish":sf,"fischer":root/"personalities"/fischer.profile_filename,
             "simple":root/"personalities/simple.txt"}
    hashes = {k:file_hash(v) for k,v in files.items()}
    conf = _player_configuration(["fischer"],None,None)["fischer"]
    _, pgn, origin = _source_bytes(conf["source"],archive_dir)
    rows, scan = _scan(pgn,"fischer",conf["surname"])
    selected = [r for r in rows if cohort(r) is not None]
    expected = {a:sum(cohort(r)==a for r in selected) for a in ARMS}
    if not all(expected.values()):
        raise ValueError("Both predeclared peak FIT and TUNE panels are required")
    data = defaultdict(list)
    seen = set()
    with ExitStack() as stack:
        ref=stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(sf),timeout=120.0))
        ref.configure({"Threads":1,"Hash":64})
        engines = {label:stack.enter_context(_open_engine(root,exe,p,book=False))
                   for label,p in (("fischer",fischer),("simple",simple))}
        engine_id=dict(ref.id)
        for game,row in _iter_verified_games(pgn,selected,conf["surname"]):
            arm=cohort(row)
            digest=row["mainline_sha256"]
            if digest in seen or arm not in ARMS:
                raise ValueError("Unexpected duplicate or reserved game")
            seen.add(digest)
            for stage,board,move in sample(game,row):
                contexts, scores=evaluate(board,move,ref,engines)
                data[arm].append({"game":digest,"fen":board.fen(),"phase":stage,
                                  "contexts":contexts,"scores":scores})
    if len(seen)!=len(selected) or hashes!={k:file_hash(v) for k,v in files.items()}:
        raise ValueError("Source or engine changed during audit")
    if any(not data[a] for a in ARMS):
        raise ValueError("Missing observed panel")
    report={}
    for arm in ARMS:
        report[arm]={"all":summarize(data[arm]),
                     "by_phase":{p:summarize([r for r in data[arm] if r["phase"]==p]) for p in PHASES},
                     "by_context":{c:summarize([r for r in data[arm] if c in r["contexts"]])
                                   for c in ("in_check","disadvantage_proxy","advantage_proxy",
                                             "balanced_not_in_check_proxy")}}
    return {"schema":SCHEMA,"status":"FISCHER_PAIRED_POSITION_DIAGNOSTIC_NOT_ELO",
            "source":origin,"source_snapshot":MIRROR_COMMIT if archive_dir is None else None,
            "source_pgn_sha256":sha256(pgn).hexdigest(),"scan":scan,"hashes":hashes,
            "reference_engine":engine_id,"reference_depth":16,"reference_threads":1,
            "reference_hash_mb":64,"candidate_depths":list(DEPTHS),"book_enabled":False,
            "expected_games":expected,"outer_test_decisions_evaluated":0,
            "historically_validated":False,"intrinsic_elo":None,
            "competencies_scaled":False,"panels":report,
            "limitations":["FIT enriched for predeclared 1970-1972 peak; TUNE all years and small.",
                           "Context tags are proxies, not isolated competencies.",
                           "Fixed depth forced-root CP scores are noisy and signed; not human Elo.",
                           "No independent outer test; no neural intervention or promotion.",
                           "Same phase/position panel across all candidates; opening repetition remains.",
                           "No source moves or positions published."]}


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
