"""Fischer-only source inventory and FIT decision-quality diagnostic.

No ratings are inferred from centipawns, no TEST decisions evaluated, no
external source trajectories published. The career bands are preregistered,
not selected after inspecting move quality.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median

import chess
import chess.engine

from .matches import file_hash
from .opening_books import SCHEMA as BOOK_SCHEMA, position_token
from .real85_neural_stream import neural_arm
from .real85_graded_phase_attribution import _score
from .real85_playable import _material_endgame
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)

SCHEMA = "chesssim-fischer-individual-baseline-v1"
SLUG = "fischer"
BANDS = ("through_1957", "1958_1962", "1963_1969", "1970_1972", "1973_1991", "1992_plus", "unknown")
PHASES = ("opening", "middlegame", "endgame")
SALT = "fischer-baseline-191-v1"


def period(year: int | None) -> str:
    if year is None:
        return "unknown"
    for stop, label in zip((1957, 1962, 1969, 1972, 1991), BANDS):
        if year <= stop:
            return label
    return "1992_plus"


def phase(board: chess.Board) -> str:
    if _material_endgame(board):
        return "endgame"
    return "opening" if board.ply() < 40 else "middlegame"


def inventory(rows: list[dict]) -> dict:
    by_year = defaultdict(Counter)
    by_period = defaultdict(Counter)
    for row in rows:
        year = row["date"]
        arm = neural_arm(row["mainline_sha256"])
        for cell in (by_year[str(year) if year is not None else "unknown"],
                     by_period[period(year)]):
            cell["distinct_games"] += 1
            cell[arm] += 1
            cell[row["color"]] += 1
            cell["with_focal_elo_header"] += int(str(row["focal_elo"]).isdigit())
            cell["with_time_control_header"] += int(row["time_control"] not in ("", "?", "-"))
    return {"distinct_games":len(rows), "by_year":dict(sorted(by_year.items())),
            "by_period":{b:by_period[b] for b in BANDS},
            "coverage_note":"Pinned source only; not a count of every game ever played."}


def fit_observations(game, row: dict, style: dict, votes: dict | None = None) -> list[tuple[str, chess.Board, chess.Move]]:
    """All legal FIT decisions contribute style counts; one min-hash draw per phase/game."""
    digest = row["mainline_sha256"]
    if neural_arm(digest) != "fit":
        raise ValueError("Only globally assigned FIT games may be analysed")
    board = game.board()
    if not board.is_valid() or row["color"] not in ("white", "black"):
        raise ValueError("Invalid historical initial board or color")
    color = row["color"] == "white"
    chosen = {}
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("Illegal source move")
        if board.turn == color:
            stage = phase(board)
            hist = style[stage]
            if votes is not None and board.ply() < 40:
                votes[row["color"]][position_token(board)][move.uci()] += 1
            hist["decisions"] += 1
            hist["captures"] += int(board.is_capture(move))
            hist["checks"] += int(board.gives_check(move))
            hist["castles"] += int(board.is_castling(move))
            hist["promotions"] += int(move.promotion is not None)
            hist["in_check"] += int(board.is_check())
            legal_count = board.legal_moves.count()
            hist["legal_moves_total"] += legal_count
            hist["forced_only_move"] += int(legal_count == 1)
            key = sha256(f"{SALT}:{digest}:{board.ply()}".encode()).hexdigest()
            if legal_count > 1 and (stage not in chosen or key < chosen[stage][0]):
                chosen[stage] = (key, board.copy(stack=True), move)
        board.push(move)
    return [(stage, item[1], item[2]) for stage, item in sorted(chosen.items())]


def quality(engine, board: chess.Board, move: chess.Move, depth: int) -> dict:
    best = _score(engine, board, None, depth)
    actual = best if best["move"] == move.uci() else _score(engine, board, move, depth)
    both_cp = best["mate"] is None and actual["mate"] is None
    gap = best["cp"] - actual["cp"] if both_cp else None
    return {"top1": best["move"] == move.uci(), "signed_gap_cp":gap,
            "mate_sensitive":not both_cp, "best_mate":best["mate"],
            "actual_mate":actual["mate"]}


def summarize(rows: list[dict]) -> dict:
    gaps = [r["signed_gap_cp"] for r in rows if r["signed_gap_cp"] is not None]
    n = len(rows)
    return {"sampled_decisions":n, "distinct_games":len({r["game"] for r in rows}),
            "distinct_fens":len({r["fen"] for r in rows}),
            "top1_matches":sum(r["top1"] for r in rows),
            "top1_rate":sum(r["top1"] for r in rows)/n if n else None,
            "nonmate_count":len(gaps), "mate_sensitive_count":n-len(gaps),
            "mean_signed_gap_cp":mean(gaps) if gaps else None,
            "median_signed_gap_cp":median(gaps) if gaps else None,
            "gap_over_100cp":sum(g > 100 for g in gaps),
            "negative_gap_count":sum(g < 0 for g in gaps)}


def run(stockfish: Path, *, archive_dir: Path | None = None, depth: int = 12,
        book_out: Path | None = None) -> dict:
    if type(depth) is not int or depth not in (12, 16):
        raise ValueError("Depth must be 12 or 16")
    stockfish = Path(stockfish).resolve(strict=True)
    executable_hash = file_hash(stockfish)
    config = _player_configuration([SLUG], None, None)[SLUG]
    _, pgn, source = _source_bytes(config["source"], archive_dir)
    rows, scan = _scan(pgn, SLUG, config["surname"])
    if not rows:
        raise ValueError("No accepted Fischer games")
    census = inventory(rows)
    fit = [r for r in rows if neural_arm(r["mainline_sha256"]) == "fit"]
    if not fit:
        raise ValueError("No FIT games")
    metrics = defaultdict(list)
    style = defaultdict(lambda: defaultdict(Counter))
    observed_games = set()
    votes = {side:defaultdict(Counter) for side in ("white","black")}
    engine = chess.engine.SimpleEngine.popen_uci(str(stockfish), timeout=120.0)
    try:
        if "Threads" in engine.options:
            engine.configure({"Threads":1})
        if "Hash" in engine.options:
            engine.configure({"Hash":64})
        engine_id = dict(engine.id)
        for game, row in _iter_verified_games(pgn, fit, config["surname"]):
            band = period(row["date"])
            digest = row["mainline_sha256"]
            observed_games.add(digest)
            for stage, board, move in fit_observations(game,row,style[band],votes):
                result = quality(engine,board,move,depth)
                result.update(game=digest,fen=board.fen())
                metrics[(band,stage)].append(result)
    finally:
        try:
            engine.quit()
        finally:
            engine.close()
    if len(observed_games) != len(fit) or file_hash(stockfish) != executable_hash:
        raise ValueError("Incomplete FIT audit or changed engine")
    total = [r for group in metrics.values() for r in group]
    if not total:
        raise ValueError("No sampled legal FIT decisions")
    book = {"schema":BOOK_SCHEMA,"player":SLUG,"match_name":config["name"],
            "policy":{"max_plies":40,"never_engine_verified":True},
            "source":{"pgn_sha256":sha256(pgn).hexdigest(),
                      "snapshot_commit":MIRROR_COMMIT if archive_dir is None else None,
                      "accepted_games":len(fit),"source_games_total":len(rows),
                      "train_ratio":len(fit)/len(rows),
                      "provenance":"REAL85_NEURAL_FIT_ONLY"},
            "positions":{side:dict(votes[side]) for side in votes}}
    book_hash = None
    if book_out is not None:
        with Path(book_out).open("x",encoding="utf-8") as handle:
            json.dump(book,handle,sort_keys=True,allow_nan=False)
            handle.write("\n")
        book_hash = file_hash(book_out)
    competencies = {key:None for key in ("opening","tactics","dynamics","defense","positional","endgames")}
    return {
        "schema":SCHEMA,"status":"FISCHER_FIT_DIAGNOSTIC_NOT_ELO_OR_PROMOTION",
        "player":SLUG,"source":source,"source_snapshot":MIRROR_COMMIT if archive_dir is None else None,
        "source_pgn_sha256":sha256(pgn).hexdigest(),"scan":scan,"inventory":census,
        "fit_games_audited":len(observed_games),"sample_protocol":SALT,
        "analysis":{"engine_id":engine_id,"engine_sha256":executable_hash,"depth":depth,
                    "threads":1,"hash_mb":64,"sampling":"one min-hash focal position with >1 legal move per game per phase",
                    "overall":summarize(total),
                    "nonopening":summarize([r for (b,s),group in metrics.items()
                                            if s!="opening" for r in group]),
                    "by_phase":{stage:summarize([r for (b,s),group in metrics.items()
                                                 if s==stage for r in group]) for stage in PHASES},
                    "by_period_phase":{f"{b}|{s}":summarize(metrics[(b,s)])
                                       for b in BANDS for s in PHASES}},
        "fit_style_counts":{b:dict(style[b]) for b in BANDS},
        "fit_repertoire":{"positions":{s:len(votes[s]) for s in votes},
                          "decisions":sum(sum(c.values()) for v in votes.values() for c in v.values()),
                          "book_sha256":book_hash,"tune_used":False,"test_used":False},
        "competencies":{"scale_proposed_max":110,"values":competencies,
                        "status":"UNSCALED_NO_REFERENCE_ANCHORS"},
        "gameplay_initial_rating":2500,"intrinsic_elo":None,"historical_validated":False,
        "outer_test_decisions_evaluated":0,"tune_decisions_evaluated":0,
        "limitations":[
            "Inventory aggregates all source metadata; engine and style use FIT only.",
            "Earlier year HOLDOUT may overlap REAL85 FIT; not independent of prior project experiments.",
            "Source includes mixed or unknown time controls; not a classical-only performance claim.",
            "Material endgame predicate and opening cutoff are proxies; tactical/positional/dynamic competence not yet isolated.",
            "Signed depth-limited forced-root gaps can be negative; mates separate; no CP-to-Elo conversion.",
            "Correlated positions and historical opening recall limit cross-era comparison.",
            "Single-legal-move decisions count in style and book but not decision-quality sampling.",
            "No raw PGN, move sequence or FEN is included in the report.",
        ],
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    parser.add_argument("--stockfish", type=Path, required=True)
    parser.add_argument("--depth", type=int, choices=(12,16), default=12)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--book-out", type=Path)
    args=parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
        raise ValueError("Output must be a new file in an existing directory")
    if args.book_out is not None and (args.book_out.exists() or args.book_out.is_symlink() or
            args.book_out.resolve() == args.out.resolve() or not args.book_out.parent.is_dir()):
        raise ValueError("Book output must be distinct and new")
    report=run(args.stockfish,archive_dir=args.archive_dir,depth=args.depth,book_out=args.book_out)
    with args.out.open("x",encoding="utf-8") as stream:
        json.dump(report,stream,sort_keys=True,indent=2,allow_nan=False)
        stream.write("\n")
    print(json.dumps(report,sort_keys=True,allow_nan=False),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
