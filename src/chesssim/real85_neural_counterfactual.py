"""Independent deeper same-FEN root-forced counterfactual audit of played UCI moves.

Given a completed book+neural UCI experiment, replay the ORIGINAL PGN,
authenticate every decision trace, and compare the real GUIDED neural move
with the Rodent bestmove that would have been played ON THAT VERY BOARD.
Both candidates get separate original-root Stockfish searches at the SAME
deeper depth; no new game or training data are generated.

A nonzero-depth, descriptive stress test is not an Elo estimate, tactical
proof or independent TEST benchmark. It must never retune the learned model.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median

import chess
import chess.engine
import chess.pgn

from .matches import file_hash
from .real85_neural_guarded_uci import SCHEMA as GAMES_SCHEMA

SCHEMA = "chesssim-real85-deeper-same-root-neural-counterfactual-v1"
MAX_INPUT_BYTES = 48 * 1024 * 1024


def _read_json(path: Path) -> dict:
    if (path.is_symlink() or not path.is_file() or
            path.stat().st_size > MAX_INPUT_BYTES):
        raise ValueError("Unsafe or oversized actual-game evidence")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicated evidence JSON key")
            result[key] = value
        return result
    payload = json.loads(path.read_text(encoding="utf-8"),
                         object_pairs_hook=no_duplicates,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             ValueError("Non-finite evidence input")))
    if not isinstance(payload, dict):
        raise ValueError("Actual-game evidence is not an object")
    return payload


def _requirements(doc: dict) -> tuple[dict, list[dict]]:
    if (doc.get("schema") != GAMES_SCHEMA or
            doc.get("status") != "ALL_REAL_UCI_GAMES_COMPLETE" or
            doc.get("historical_focal_book_enabled") is not True or
            doc.get("official_personality_files_changed") is not False or
            doc.get("rated_official_games") is not False or
            doc.get("historical_elo_estimated") is not False or
            doc.get("maia_dsc_weights_changed") is not False or
            doc.get("historical_book_training") !=
            "real85_game_disjoint_85pct_train" or
            type(doc.get("min_ply")) is not int or doc["min_ply"] <= 40 or
            type(doc.get("cp_budget")) is not int or not 0 <= doc["cp_budget"] <= 100):
        raise ValueError("Expected complete nonofficial authentic real85 book+neural games")
    players = doc.get("players")
    records = doc.get("games")
    if (not isinstance(players, list) or
            len(players) != len(set(players)) or
            set(players) != {"fischer", "tal", "karpov", "kasparov"} or
            not isinstance(records, list) or len(records) != 16):
        raise ValueError("Expected all four historical experiments and 16 completed games")
    counts = Counter()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Missing complete UCI-game record")
        slug, color, arm = (record.get("player"), record.get("color"),
                            record.get("arm"))
        if slug not in players or color not in ("white", "black") or arm not in (
                "shadow", "guided"):
            raise ValueError("Unexpected UCI experimental player/arm/color")
        counts[(slug, arm, color)] += 1
    if set(counts.values()) != {1} or len(counts) != 16:
        raise ValueError("Experimental color/arm coverage is not complete")
    for key in ("rodent_original_sha256", "stockfish_sha256"):
        value = doc.get(key)
        if (not isinstance(value, str) or len(value) != 64 or
                any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("Missing original UCI engine provenance")
    return {p: [] for p in players}, records


def _load_replayed(doc: dict, raw_pgn: Path) -> dict[str, list[dict]]:
    """Never trust a detached policy_trace without a matching legal played PGN."""
    by_player, records = _requirements(doc)
    if raw_pgn.is_symlink() or not raw_pgn.is_file() or raw_pgn.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Unsafe or oversized experimental PGN")
    with raw_pgn.open(encoding="utf-8") as handle:
        for game_index, record in enumerate(records):
            game = chess.pgn.read_game(handle)
            if game is None or game.errors:
                raise ValueError("Missing, truncated or malformed UCI PGN")
            if (game.headers.get("Result") != record.get("result") or
                    game.headers.get("Termination") != record.get("termination") or
                    game.board().fen() != chess.STARTING_FEN or
                    game.headers.get("NeuralMode") != record["arm"] or
                    game.headers.get("EngineSHA256") != doc["rodent_original_sha256"] or
                    game.headers.get("IndependentStockfishSHA256") != doc["stockfish_sha256"]):
                raise ValueError("Detached actual-game report or PGN provenance")
            moves = list(game.mainline_moves())
            events = record.get("policy_trace")
            openings = record.get("opening_trace")
            if (record.get("status") != "COMPLETED" or
                    record.get("result") not in ("1-0", "0-1", "1/2-1/2") or
                    type(record.get("plies")) is not int or
                    len(moves) != record["plies"] or
                    not isinstance(events, list) or len(events) != len(moves) or
                    not isinstance(openings, list)):
                raise ValueError("Trace or PGN has missing or incomplete real chess moves")
            focal_is_white = record["color"] == "white"
            board = game.board()
            actual_overrides = 0
            opening_seen = {}
            for entry in openings:
                ply = entry.get("ply")
                if (type(ply) is not int or ply < 1 or ply > 40 or
                        entry.get("player") != record["player"] or ply in opening_seen):
                    raise ValueError("Invalid or duplicated historical book evidence")
                opening_seen[ply] = entry["move"]
            if (len(opening_seen) != record.get("focal_historical_book_plies") or
                    record.get("simple_book_plies") != 0):
                raise ValueError("Focal opening counts or free Simple control manipulated")
            for ply, (move, event) in enumerate(zip(moves, events), 1):
                acting = record["player"] if board.turn == focal_is_white else "control-simple"
                fen = board.fen()
                original = event.get("rodent_move")
                played = event.get("played_move")
                if (event.get("ply") != ply or event.get("player") != acting or
                        event.get("fen_sha256") != sha256(fen.encode()).hexdigest() or
                        played != move.uci() or original is None or
                        chess.Move.from_uci(original) not in board.legal_moves or
                        move not in board.legal_moves):
                    raise ValueError("PGN and full UCI decision trace diverge")
                if board.is_game_over(claim_draw=True):
                    raise ValueError("Actual UCI game continued after a terminal chess position")
                if ply in opening_seen and (played != opening_seen[ply] or
                                             acting != record["player"]):
                    raise ValueError("Historical repertoire entry was not the played move")
                if event.get("outcome") == "EXTERNAL_POLICY_OVERRIDE":
                    actual_overrides += 1
                    if (acting == "control-simple" or record["arm"] != "guided" or
                            ply < doc["min_ply"] or ply in opening_seen or
                            event.get("policy_move") != played or
                            original == played or event.get("sampled") is not True or
                            event.get("rodent_in_analyzed_set") is not True or
                            type(event.get("safe_candidate_count")) is not int or
                            event["safe_candidate_count"] < 2 or
                            type(event.get("proposed_cp_gap_from_best")) is not int or
                            not 0 <= event["proposed_cp_gap_from_best"] <= doc["cp_budget"]):
                        raise ValueError("Played unsafe, unproven or non-focal neural override")
                    by_player[record["player"]].append({
                        "game_index": game_index,
                        "arm": record["arm"],
                        "color": record["color"],
                        "ply": ply, "fen": fen,
                        "fen_sha256": event["fen_sha256"],
                        "rodent_move": original,
                        "neural_move": played,
                        "shallow_cp_gap_from_best": event["proposed_cp_gap_from_best"],
                    })
                elif (played != original or event.get("sampled") is True or
                      (acting == "control-simple" and
                       event.get("outcome") != "CONTROL_RODENT_UNMODIFIED")):
                    raise ValueError("Unreported policy mutation in original UCI game")
                board.push(move)
            outcome = board.outcome(claim_draw=True)
            if outcome is None or outcome.result() != record.get("result"):
                raise ValueError("Claimed complete UCI outcome contradicts actual legal chess")
            if actual_overrides != record.get("actual_overrides"):
                raise ValueError("Actual neural intervention count does not reconcile")
            if record["arm"] == "shadow" and actual_overrides:
                raise ValueError("Shadow control unexpectedly changed real moves")
        if chess.pgn.read_game(handle) is not None:
            raise ValueError("Unmatched extra PGN games")
    return by_player


def _root_score(engine, board: chess.Board, move: chess.Move, depth: int):
    """Root restricted search, never child-FEN evaluation with extra ply."""
    if move not in board.legal_moves:
        raise ValueError("Counterfactual move is not legal in authentic PGN position")
    info = engine.analyse(board, chess.engine.Limit(depth=depth),
                          root_moves=[move],
                          info=chess.engine.INFO_SCORE | chess.engine.INFO_PV,
                          game=object())
    pv = info.get("pv")
    score = info.get("score")
    if not pv or pv[0] != move or score is None:
        raise RuntimeError("Independent Stockfish could not prove root-move evaluation")
    perspective = score.pov(board.turn)
    mate = perspective.mate()
    cp = perspective.score()
    if mate is None and (type(info.get("depth")) is not int or
                         info["depth"] < depth):
        raise RuntimeError("Independent Stockfish did not reach required root depth")
    if mate is None and (type(cp) is not int or not math.isfinite(cp)):
        raise RuntimeError("Independent Stockfish returned no centipawn evaluation")
    return {"cp": cp if mate is None else None, "mate_in": mate}


def _summary(observations: list[dict]) -> dict:
    nonmate = [r for r in observations if not r["mate_affected"]]
    by_game = defaultdict(list)
    for row in nonmate:
        by_game[row["game_index"]].append(row["deep_neural_minus_rodent_cp"])
    game_means = {str(game): round(mean(values), 3)
                  for game, values in sorted(by_game.items())}
    return {
        "actual_guided_overrides": len(observations),
        "mate_affected_excluded_from_cp": len(observations) - len(nonmate),
        "cp_comparisons": len(nonmate),
        "nonmate_mean_neural_minus_original_cp": (
            round(mean(row["deep_neural_minus_rodent_cp"] for row in nonmate), 3)
            if nonmate else None),
        "nonmate_median_neural_minus_original_cp": (
            round(median(row["deep_neural_minus_rodent_cp"] for row in nonmate), 3)
            if nonmate else None),
        "neural_deeper_worse_more_than_25cp": sum(
            r["deep_neural_minus_rodent_cp"] < -25 for r in nonmate),
        "neural_deeper_worse_more_than_100cp": sum(
            r["deep_neural_minus_rodent_cp"] < -100 for r in nonmate),
        "neural_deeper_worse_more_than_250cp": sum(
            r["deep_neural_minus_rodent_cp"] < -250 for r in nonmate),
        "neural_deeper_better_more_than_25cp": sum(
            r["deep_neural_minus_rodent_cp"] > 25 for r in nonmate),
        "neural_deeper_equal_within_25cp": sum(
            abs(r["deep_neural_minus_rodent_cp"]) <= 25 for r in nonmate),
        "guided_game_means_cp": game_means,
        "confidence_interval": None,
        "confidence_reason": (
            "Fewer than 20 independent guided games; no strength CI."
            if len({row["game_index"] for row in observations}) < 20 else
            "No preregistered strength interval from these exploratory games."),

    }


def audit(results: Path, pgn: Path, stockfish: Path, out: Path, *,
          depth: int = 12) -> dict:
    """Never update model weights or use historical TRAIN/TUNE/TEST as moves."""
    if type(depth) is not int or not 10 <= depth <= 20:
        raise ValueError("Independent deeper Stockfish depth must be integer 10..20")
    results = Path(results)
    pgn = Path(pgn)
    if results.is_symlink() or pgn.is_symlink() or any(
            parent.is_symlink() for parent in (*results.parents, *pgn.parents)):
        raise ValueError("Symbolic source evidence paths forbidden")
    doc = _read_json(results)
    rows_by_player = _load_replayed(doc, pgn)
    if sum(len(rows) for rows in rows_by_player.values()) == 0:
        raise ValueError("No actual guided neural override exists to counterfactually audit")
    stockfish = Path(stockfish).resolve(strict=True)
    if not stockfish.is_file():
        raise FileNotFoundError("Independent Stockfish UCI executable missing")
    out = Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise FileExistsError("Counterfactual output must be a new file in an existing directory")
    evaluated = {}
    engine = chess.engine.SimpleEngine.popen_uci(str(stockfish), timeout=120.0)
    try:
        for slug, rows in rows_by_player.items():
            observations = []
            for original in rows:
                board = chess.Board(original["fen"])
                rodent = chess.Move.from_uci(original["rodent_move"])
                neural = chess.Move.from_uci(original["neural_move"])
                # Alternate candidate order by recorded FEN to expose/order-robust
                # any systematic transposition-table or first-search bias.
                first_rodent = int(original["fen_sha256"][-1], 16) % 2 == 0
                pair = ([("rodent", rodent), ("neural", neural)] if first_rodent
                        else [("neural", neural), ("rodent", rodent)])
                scores = {name: _root_score(engine, board, move, depth)
                          for name, move in pair}
                mate = any(score["mate_in"] is not None for score in scores.values())
                delta = (None if mate else
                         scores["neural"]["cp"] - scores["rodent"]["cp"])
                observations.append({
                    "game_index": original["game_index"],
                    "color": original["color"], "ply": original["ply"],
                    "fen_sha256": original["fen_sha256"],
                    "rodent_move": original["rodent_move"],
                    "neural_move": original["neural_move"],
                    "shallow_cp_gap_from_best": original["shallow_cp_gap_from_best"],
                    "rodent_deep_root_cp": scores["rodent"]["cp"],
                    "neural_deep_root_cp": scores["neural"]["cp"],
                    "rodent_deep_mate_in": scores["rodent"]["mate_in"],
                    "neural_deep_mate_in": scores["neural"]["mate_in"],
                    "mate_affected": mate,
                    "deep_neural_minus_rodent_cp": delta,
                })
            evaluated[slug] = {**_summary(observations), "observations": observations}
    finally:
        try:
            engine.quit()
        finally:
            engine.close()
    report = {
        "schema": SCHEMA,
        "status": "INDEPENDENT_DEPTH_STRESS_OBSERVATION_NOT_HUMAN_ELO",
        "complete_game_results_sha256": file_hash(results),
        "complete_game_pgn_sha256": file_hash(pgn),
        "original_rodent_sha256": doc["rodent_original_sha256"],
        "move_time_stockfish_sha256": doc["stockfish_sha256"],
        "reanalysis_stockfish_sha256": file_hash(stockfish),
        "depth_of_original_move_guard": doc.get("stockfish_depth"),
        "depth_of_independent_counterfactual": depth,
        "all_observed_guided_overrides_audited": True,
        "no_game_or_model_training_performed": True,
        "outer_test_used": False,
        "model_tune_used": False,
        "played_source": "real completed game PGNs joined to per-ply counterfactual traces",
        "comparison": "independent same-root forced original Rodent vs actual neural move, same depth",
        "confidence": "DESCRIPTIVE_ONLY_NOT_A_PAIRED_MATCH_STRENGTH_ESTIMATE",
        "players": evaluated,
        "totals": _summary([row for data in evaluated.values()
                            for row in data["observations"]]),
    }
    with out.open("x", encoding="utf-8", newline="") as stream:
        json.dump(report, stream, sort_keys=True, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Deeper original-root Stockfish audit of real guided UCI move overrides")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--pgn", type=Path, required=True)
    parser.add_argument("--stockfish", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=12)
    args = parser.parse_args(argv)
    report = audit(args.results, args.pgn, args.stockfish, args.out, depth=args.depth)
    print(json.dumps({
        "status": report["status"],
        "depth": report["depth_of_independent_counterfactual"],
        "players": {slug: {k: value for k, value in row.items() if k != "observations"}
                    for slug, row in report["players"].items()},
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
