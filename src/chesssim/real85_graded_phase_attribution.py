"""Root-matched, phase-aware diagnosis of actual graded UCI chess decisions.

A candidate's centipawn gap is descriptive, not proof that it caused a
game result, nor a ground-truth human playing strength. For historical
book moves we obtain a GENUINE original Rodent counterfactual: the book
move is NEVER mislabelled as Rodent's own search output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from .catalog import load_roster
from .matches import _open_engine, file_hash
from .real85_graded_openings import SCHEMA as GAMES_SCHEMA, FOCAL_DEPTHS, validate
from .real85_playable import _material_endgame
from .registry import registered_aliases

SCHEMA = "chesssim-real85-graded-phase-root-attribution-v1"


def phase(board: chess.Board) -> str:
    """Chess-position material endgame, never mere ply-based endgame guessing."""
    if board.ply() < 40:
        return "opening"
    return "endgame" if _material_endgame(board) else "middlegame"


def sample(results: Path, pgn: Path, *, max_per_phase_source: int = 1,
           focal_depth: int = 4) -> list[dict]:
    if (type(max_per_phase_source) is not int or
            not 1 <= max_per_phase_source <= 4):
        raise ValueError("Preselected phase-source sample limit must be 1..4")
    results = Path(results)
    checked = validate(results.parent, focal_depth=focal_depth)
    if checked["total_complete_games"] != 96 or pgn.resolve() != (results.parent/"games.pgn").resolve():
        raise ValueError("Only complete and joined source-locked 96-game evidence is accepted")
    doc = json.loads(results.read_text())
    if doc.get("schema") != GAMES_SCHEMA:
        raise ValueError("Incorrect actual UCI game schema")
    observations=[]
    with pgn.open(encoding="utf-8") as source:
        for game_index,record in enumerate(doc["games"]):
            game=chess.pgn.read_game(source)
            if game is None or game.errors:
                raise ValueError("Missing audited real chess game")
            board=game.board()
            by_ply={entry["ply"]:entry for entry in record["opening_trace"]}
            budget=Counter()
            for move,event in zip(game.mainline_moves(),record["policy_trace"]):
                ply=board.ply()+1
                focal_turn=board.turn==(record["color"]=="white")
                if focal_turn:
                    kind=("repertoire" if ply in by_ply else
                          "neural" if event["outcome"]=="EXTERNAL_POLICY_OVERRIDE" else
                          "rodent")
                    stage=phase(board)
                    key=(stage,kind)
                    if budget[key]<max_per_phase_source:
                        budget[key]+=1
                        observations.append({
                            "game_index":game_index, "player":record["player"],
                            "opponent_depth":record["opponent_depth"],
                            "opening_index":record["opening_index"],
                            "arm":record["arm"],"color":record["color"],
                            "source":kind,"phase":stage,"ply":ply,
                            "fen":board.fen(),
                            "fen_sha256":sha256(board.fen().encode()).hexdigest(),
                            "played_move":move.uci(),
                            # For a book move the field called "rodent_move" in
                            # the old policy trace is really the BOOK move.
                            # The authentic Rodent baseline is queried later.
                            "original_rodent_move":(
                                None if kind=="repertoire" else event["rodent_move"]),
                        })
                board.push(move)
        if chess.pgn.read_game(source) is not None:
            raise ValueError("Unmatched experimental PGN")
    return observations


def _score(engine,board: chess.Board,move: chess.Move | None,depth: int) -> dict:
    if move is not None and move not in board.legal_moves:
        raise ValueError("Counterfactual move illegal in original historical-FEN board")
    kwargs = {"root_moves":[move]} if move is not None else {}
    info=engine.analyse(board,chess.engine.Limit(depth=depth),
                        info=chess.engine.INFO_SCORE|chess.engine.INFO_PV,
                        game=object(),**kwargs)
    pv=info.get("pv")
    if (not pv or pv[0] not in board.legal_moves or
            (move is not None and pv[0]!=move) or info.get("score") is None or
            type(info.get("depth")) is not int or info["depth"]<depth):
        raise RuntimeError("Stockfish did not complete the required original-root search")
    pov=info["score"].pov(board.turn)
    mate=pov.mate()
    cp=pov.score() if mate is None else None
    if mate is None and type(cp) is not int:
        raise RuntimeError("Invalid original-root centipawn evaluation")
    return {"move":pv[0].uci(),"cp":cp,"mate":mate}


def evaluate(results: Path, pgn: Path, stockfish: Path,
             rodent_root: Path, out: Path, *, depth: int = 14,
             max_per_phase_source: int = 1, focal_depth: int = 4) -> dict:
    if type(depth) is not int or not 12 <= depth <= 20:
        raise ValueError("Independent diagnostic depth must be 12..20")
    results,pgn,out=Path(results),Path(pgn),Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Phase-audit artifact must not overwrite earlier evidence")
    doc=json.loads(results.read_text())
    observations=sample(results,pgn,max_per_phase_source=max_per_phase_source,
                        focal_depth=focal_depth)
    sfpath=Path(stockfish).resolve(strict=True)
    root=Path(rodent_root).resolve(strict=True)
    exe=root/"rodent-iv-plain.exe"
    if (not sfpath.is_file() or not exe.is_file() or
            file_hash(sfpath)!=doc["stockfish_sha256"] or
            file_hash(exe)!=doc["rodent_original_sha256"]):
        raise ValueError("Independent engines differ from real played UCI sources")
    roster={p.slug:p for p in load_roster()}
    aliases=registered_aliases(root/"personalities/basic.ini")
    for p in roster.values():
        if p.slug in doc["players"] and aliases.get(p.rodent_alias)!=p.profile_filename:
            raise ValueError("Original Rodent personality registry differs")
    profiles={}
    with pgn.open(encoding="utf-8") as handle:
        for record in doc["games"]:
            g=chess.pgn.read_game(handle)
            slug=record["player"]
            actual=(g.headers["WhiteProfileSHA256"] if record["color"]=="white"
                    else g.headers["BlackProfileSHA256"])
            if file_hash(root/"personalities"/roster[slug].profile_filename)!=actual:
                raise ValueError("Original Rodent personality hash changed before attribution")
            profiles[slug]=roster[slug]
    sf=chess.engine.SimpleEngine.popen_uci(str(sfpath),timeout=120.0)
    original={}
    cache={}
    try:
        for row in observations:
            slug=row["player"]
            board=chess.Board(row["fen"])
            actual=chess.Move.from_uci(row["played_move"])
            if slug not in original:
                original[slug]=_open_engine(root,exe,profiles[slug],book=False)
            if row["source"]=="repertoire":
                result=original[slug].play(board,chess.engine.Limit(depth=focal_depth),
                                           game=object())
                baseline=result.move
                if baseline not in board.legal_moves:
                    raise ValueError("Original Rodent counterfactual returned illegal book baseline")
            else:
                baseline=chess.Move.from_uci(row["original_rodent_move"])
                if baseline not in board.legal_moves:
                    raise ValueError("Missing authentic original UCI Rodent bestmove")
            # The focal depth is committed before the games. A separate
            # unbooked depth-8 Rodent search on the SAME FEN isolates how much
            # stronger original search alone would alter the decision.
            deeper=original[slug].play(board,chess.engine.Limit(depth=8),
                                       game=object()).move
            if deeper not in board.legal_moves:
                raise ValueError("Original depth-8 counterfactual was illegal")
            def cached(move):
                key=(board.fen(), move.uci() if move is not None else "-")
                if key not in cache:
                    cache[key]=_score(sf,board,move,depth)
                return cache[key]
            best=cached(None)
            played=cached(actual)
            orig=cached(baseline)
            deep=cached(deeper)
            affected=any(value["mate"] is not None
                         for value in (best,played,orig,deep))
            row["stockfish_best_move"]=best["move"]
            row["original_rodent_move"]=baseline.uci()
            row["stockfish_best_cp"]=best["cp"]
            row["stockfish_played_cp"]=played["cp"]
            row["stockfish_original_rodent_cp"]=orig["cp"]
            row["original_rodent_depth8_move"]=deeper.uci()
            row["stockfish_depth8_rodent_cp"]=deep["cp"]
            row["mate_affected"]=affected
            row["played_gap_from_best_cp"]=(
                None if affected else best["cp"]-played["cp"])
            row["rodent_gap_from_best_cp"]=(
                None if affected else best["cp"]-orig["cp"])
            row[f"depth8_rodent_minus_depth{focal_depth}_cp"]=(
                None if affected else deep["cp"]-orig["cp"])
            row["policy_minus_rodent_cp"]=(
                None if affected or row["source"]=="rodent" else
                played["cp"]-orig["cp"])
    finally:
        for engine in original.values():
            try:
                engine.quit()
            finally:
                engine.close()
        try:
            sf.quit()
        finally:
            sf.close()
    groups=defaultdict(list)
    for row in observations:
        groups[(row["player"],str(row["opponent_depth"]),row["phase"],
                row["source"])].append(row)
    summary={}
    for key,rows in sorted(groups.items()):
        cp=[row["played_gap_from_best_cp"] for row in rows if not row["mate_affected"]]
        compared=[row["policy_minus_rodent_cp"] for row in rows
                  if row["policy_minus_rodent_cp"] is not None]
        stronger=[row[f"depth8_rodent_minus_depth{focal_depth}_cp"] for row in rows
                  if row[f"depth8_rodent_minus_depth{focal_depth}_cp"] is not None]
        summary["|".join(key)]={
            "observations":len(rows),
            "unique_positions":len({row["fen_sha256"] for row in rows}),
            "mate_sensitive_positions":sum(row["mate_affected"] for row in rows),
            "nonmate_stockfish_comparisons":len(cp),
            "mean_played_gap_from_best_cp":round(sum(cp)/len(cp),2) if cp else None,
            "played_gaps_over_100cp":sum(x>100 for x in cp),
            "mean_policy_minus_original_rodent_cp":(
                round(sum(compared)/len(compared),2) if compared else None),
            f"mean_original_rodent_depth8_minus_depth{focal_depth}_cp":(
                round(sum(stronger)/len(stronger),2) if stronger else None),
            "original_depth8_improves_over_100cp":sum(x>100 for x in stronger),
            "original_depth8_worsens_over_100cp":sum(x < -100 for x in stronger),
            "observed_position_based_endgames":sum(row["phase"]=="endgame" for row in rows),
        }
    report={
        "schema":SCHEMA,
        "status":"INDEPENDENT_ROOT_PHASE_DIAGNOSIS_NOT_CAUSAL_OR_HUMAN_ELO",
        "complete_game_results_sha256":file_hash(results),
        "complete_game_pgn_sha256":file_hash(pgn),
        "stockfish_sha256":file_hash(sfpath),
        "original_rodent_sha256":file_hash(exe),
        "independent_stockfish_depth":depth,
        "original_rodent_played_depth":focal_depth,
        "original_rodent_unbooked_counterfactual_depth":8,
        "deterministic_sample_limit_per_game_phase_source":max_per_phase_source,
        "total_sampled_real_focal_decisions":len(observations),
        "distinct_original_fens":len({o["fen_sha256"] for o in observations}),
        "sources":["rodent","repertoire","neural"],
        "phase_definition":"opening_before_ply40_else_material_endgame_<=16_nonpawn_points_else_middlegame",
        "repertoire_counterfactual":f"ACTUAL_ORIGINAL_RODENT_UCI_ROOT_DEPTH{focal_depth}",
        "outer_test_used":False,"model_tuned_to_diagnostics":False,
        "limitations":[
            "The sample is a fixed subset of actual games, not every move.",
            "Stockfish depth-limited forced-root values can change at deeper searches.",
            "Rodent depth8 is a same-position counterfactual, not a completed depth8 game.",
            "Model and book choices occur on different game trajectories; this cannot assign causal match-point losses.",
            "A missing phase/source cell is missing evidence, never a zero-error claim.",
            "Opponent depths calibrate relative engine search, not verified human Elo.",
        ],
        "groups":summary,"observations":observations,
    }
    with out.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,ensure_ascii=False,sort_keys=True,allow_nan=False)
        handle.write("\n")
    return report


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Attribute genuine graded UCI decisions on actual FENs")
    parser.add_argument("--results",type=Path,required=True)
    parser.add_argument("--pgn",type=Path,required=True)
    parser.add_argument("--rodent-root",type=Path,required=True)
    parser.add_argument("--stockfish",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--depth",type=int,default=14)
    parser.add_argument("--focal-depth",type=int,choices=FOCAL_DEPTHS,default=4)
    args=parser.parse_args(argv)
    result=evaluate(args.results,args.pgn,args.stockfish,args.rodent_root,
                    args.out,depth=args.depth,focal_depth=args.focal_depth)
    print(json.dumps({
        "status":result["status"],
        "sampled":result["total_sampled_real_focal_decisions"],
        "unique_fens":result["distinct_original_fens"],
        "groups":result["groups"],
    },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
