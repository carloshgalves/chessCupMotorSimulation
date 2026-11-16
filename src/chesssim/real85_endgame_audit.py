"""Audit true historical endgame decisions, NEVER fit on the 15% game-disjoint TEST.

A source-locked real85 empirical opening repertoire supplies the *partition*
and provenance, not the move-prediction signal in these endgame positions.
Rodent IV plays freely under its original personality (books disabled), while
independent Stockfish measures the gap to a historical move on the same FEN.

This is a diagnostic of historical transfer and tactical/endgame weaknesses,
NOT a declaration of human-equivalent Elo or an optimization target for TEST.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median

import chess
import chess.engine

from .catalog import load_roster
from .game_diversity import mainline_identity
from .matches import _open_engine, file_hash
from .real85_playable import (
    DEFAULT_PLAYERS, SCHEMA as REAL85_SCHEMA, _material_endgame, game_arm, _write_new,
)
from .real_training_cohorts import _iter_verified_games, _player_configuration, _scan, _source_bytes
from .registry import registered_aliases

EXTRACT_SCHEMA = "chesssim-real85-heldout-material-endgame-test-only-v1"
AUDIT_SCHEMA = "chesssim-real85-rodent-stockfish-historical-endgames-v1"


def _order(*parts: object) -> str:
    return sha256(":".join(map(str, parts)).encode()).hexdigest()


def extract(profiles: Path, out: Path, *, players: list[str] | None = None,
            archive_dir: Path | None = None, per_player: int = 12) -> dict:
    """Select <=1 TEST endgame move per historical game, without exposing FIT.

    Each accepted unique trajectory is assigned using real85's global
    trajectory hash; observed historical decisions are NOT model fit material.
    Selection is hash-based and deterministic, with no reliance on PGN order.
    """
    if not 1 <= per_player <= 128:
        raise ValueError("per_player must be between 1 and 128")
    players = list(players or DEFAULT_PLAYERS)
    conf = _player_configuration(players, None, None)
    profiles = Path(profiles)
    manifest = json.loads((profiles / "report.json").read_text())
    if manifest.get("schema") != REAL85_SCHEMA:
        raise ValueError("A source-locked real85 training manifest is required")
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise FileExistsError("Endgame output must be a new file in existing directory")
    results = {}
    for slug in players:
        history = manifest.get("players", {}).get(slug)
        if not isinstance(history, dict) or history.get("status") != (
                "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE"):
            raise ValueError(f"Missing source-locked real85 profile: {slug}")
        _raw, pgn, source = _source_bytes(conf[slug]["source"], archive_dir)
        if sha256(pgn).hexdigest() != history.get("source_pgn_sha256"):
            raise ValueError(f"Historical source changed since real85 FIT: {slug}")
        rows, scan = _scan(pgn, slug, conf[slug]["surname"])
        if len(rows) != history.get("accepted_distinct_games"):
            raise ValueError(f"Unique-game population changed since FIT: {slug}")
        expected_test = sum(game_arm(r["mainline_sha256"]) == "test" for r in rows)
        if expected_test != history.get("test_games"):
            raise ValueError(f"Global game partition changed since FIT: {slug}")
        candidates = []
        total_endgame_decisions = 0
        tests_seen = 0
        for game, meta in _iter_verified_games(pgn, rows, conf[slug]["surname"]):
            identity, _ = mainline_identity(game)
            if game_arm(identity) != "test":
                continue
            tests_seen += 1
            focal = meta["color"] == "white"
            board = game.board()
            eligible = []
            for move in game.mainline_moves():
                if board.turn == focal and _material_endgame(board):
                    total_endgame_decisions += 1
                    fen = board.fen()
                    eligible.append({
                        "game_sha256": identity, "fen": fen,
                        "fen_sha256": sha256(fen.encode()).hexdigest(),
                        "historical_move_uci": move.uci(),
                        "ply": board.ply() + 1,
                        "year": meta["date"],
                        "color": meta["color"],
                    })
                board.push(move)
            if eligible:
                candidates.append(min(
                    eligible,
                    key=lambda item: _order("within-real85-test-endgame", identity,
                                            item["ply"], item["historical_move_uci"])))
        if tests_seen != expected_test:
            raise RuntimeError(f"TEST identity population mismatch: {slug}")
        # One decision per real TEST game; sampled games equally independent.
        sampled = sorted(candidates,
                         key=lambda r: _order("between-real85-test-games", r["game_sha256"]))[:per_player]
        if len({r["game_sha256"] for r in sampled}) != len(sampled):
            raise RuntimeError("A game was selected more than once")
        for row in sampled:
            board = chess.Board(row["fen"])
            move = chess.Move.from_uci(row["historical_move_uci"])
            if move not in board.legal_moves or not _material_endgame(board):
                raise RuntimeError("A source endgame decision became illegal")
        results[slug] = {
            "status": "READY_FOR_INDEPENDENT_ENDGAME_AUDIT" if sampled else "NO_REAL_TEST_ENDGAMES",
            "source": source, "source_pgn_sha256": sha256(pgn).hexdigest(),
            "test_games": expected_test, "test_endgame_games_available": len(candidates),
            "test_endgame_decisions_available": total_endgame_decisions,
            "sampled_distinct_games": len(sampled),
            "sampled_decisions": sampled,
            "scan": scan,
        }
    report = {
        "schema": EXTRACT_SCHEMA, "status": "TEST_ONLY_ENDGAME_POSITIONS_EXTRACTED",
        "source_manifest_sha256": file_hash(profiles / "report.json"),
        "split": "real85-global-trajectory-TEST-only",
        "old_year_holdout_used": False, "trained_on_test": False,
        "selection": "min-hash-one-material-endgame-decision-per-real-TEST-game",
        "per_player_limit": per_player,
        "criterion": "both_sides_combined_nonpawn_material_at_most_16",
        "players": results,
        "warning": "This is a diagnostic TEST arm; do not tune parameters on these positions.",
    }
    _write_new(out, report)
    return report


def _evaluate_stockfish(engine, board: chess.Board, color: bool, depth: int) -> tuple[int, bool]:
    info = engine.analyse(board, chess.engine.Limit(depth=depth))
    score = info["score"].pov(color)
    value = score.score(mate_score=100000)
    if value is None:
        raise RuntimeError("Independent Stockfish returned no numeric evaluation")
    return int(value), score.mate() is not None


def _moved_score(engine, board: chess.Board, move: chess.Move, color: bool,
                 depth: int) -> tuple[int, bool]:
    if move not in board.legal_moves:
        raise ValueError("Invalid human/engine candidate on historical FEN")
    candidate = board.copy(stack=False)
    candidate.push(move)
    return _evaluate_stockfish(engine, candidate, color, depth)


def audit(positions: Path, rodent_root: Path, stockfish: Path, out: Path, *,
          rodent_depth: int = 4, stockfish_depth: int = 8) -> dict:
    """Observe ORIGINAL Rodent personalities and real Stockfish on held-out FENs.

    The same frozen positions and Stockfish evaluator are used for observed
    historical and Rodent-generated moves. No weights or parameters are fit.
    """
    if not 1 <= rodent_depth <= 12 or not 4 <= stockfish_depth <= 20:
        raise ValueError("Unsafe UCI evaluation depth budget")
    doc = json.loads(Path(positions).read_text())
    if doc.get("schema") != EXTRACT_SCHEMA or doc.get("trained_on_test") is not False:
        raise ValueError("Endgame audit accepts only verified TEST-only extraction")
    root = Path(rodent_root).resolve(strict=True)
    exe = root / "rodent-iv-plain.exe"
    stockfish = Path(stockfish).resolve(strict=True)
    if not exe.is_file() or not stockfish.is_file():
        raise FileNotFoundError("Real Rodent and independent Stockfish binaries required")
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise FileExistsError("Endgame audit output must be new")
    roster = {p.slug: p for p in load_roster()}
    aliases = registered_aliases(root / "personalities" / "basic.ini")
    players = doc.get("players")
    if not isinstance(players, dict) or not players:
        raise ValueError("No historical TEST profiles to evaluate")
    evaluated = {}
    sf = chess.engine.SimpleEngine.popen_uci(str(stockfish), timeout=120.0)
    try:
        sf_id = sf.id.get("name", "unknown")
        for slug, sample in players.items():
            player = roster.get(slug)
            if (player is None or player.rodent_alias is None or
                    aliases.get(player.rodent_alias) != player.profile_filename):
                raise ValueError(f"No authentic Rodent IV personality: {slug}")
            profile_file = root / "personalities" / player.profile_filename
            if not profile_file.is_file():
                raise FileNotFoundError(profile_file)
            rows = sample.get("sampled_decisions")
            if not isinstance(rows, list) or len(rows) != sample.get("sampled_distinct_games"):
                raise ValueError(f"Corrupted TEST endgame sample: {slug}")
            if len({r["game_sha256"] for r in rows}) != len(rows):
                raise ValueError("TEST decision identity duplicated")
            observations = []
            engine = _open_engine(root, exe, player, book=False)
            try:
                for row in rows:
                    board = chess.Board(row["fen"])
                    if (not _material_endgame(board) or
                            board.turn != (row["color"] == "white") or
                            sha256(board.fen().encode()).hexdigest() != row["fen_sha256"]):
                        raise ValueError("Corrupted historical TEST position")
                    historical = chess.Move.from_uci(row["historical_move_uci"])
                    if historical not in board.legal_moves:
                        raise ValueError("Illegal historical move")
                    original = engine.play(board, chess.engine.Limit(depth=rodent_depth),
                                           info=chess.engine.INFO_NONE).move
                    if original not in board.legal_moves:
                        raise RuntimeError("Rodent IV returned an illegal endgame move")
                    best, best_mate = _evaluate_stockfish(sf, board, board.turn, stockfish_depth)
                    historic_score, hist_mate = _moved_score(
                        sf, board, historical, board.turn, stockfish_depth)
                    engine_score, engine_mate = _moved_score(
                        sf, board, original, board.turn, stockfish_depth)
                    observations.append({
                        "game_sha256": row["game_sha256"],
                        "fen_sha256": row["fen_sha256"],
                        "ply": row["ply"], "year": row["year"],
                        "historical_uci": historical.uci(),
                        "rodent_uci": original.uci(),
                        "identical_move": original == historical,
                        "historical_cp_loss": max(0, best - historic_score),
                        "rodent_cp_loss": max(0, best - engine_score),
                        "mate_affected": best_mate or hist_mate or engine_mate,
                    })
            finally:
                try:
                    engine.quit()
                finally:
                    engine.close()
            nonmate = [r for r in observations if not r["mate_affected"]]
            evaluated[slug] = {
                "test_endgame_games_available": sample["test_endgame_games_available"],
                "test_endgame_decisions_available": sample["test_endgame_decisions_available"],
                "sampled_distinct_games": len(observations),
                "same_historical_move": sum(r["identical_move"] for r in observations),
                "mate_affected_comparisons": len(observations) - len(nonmate),
                "nonmate_mean_rodent_cp_loss": (
                    round(mean(r["rodent_cp_loss"] for r in nonmate), 3) if nonmate else None),
                "nonmate_median_rodent_cp_loss": (
                    median(r["rodent_cp_loss"] for r in nonmate) if nonmate else None),
                "nonmate_mean_historical_cp_loss": (
                    round(mean(r["historical_cp_loss"] for r in nonmate), 3)
                    if nonmate else None),
                "rodent_blunders_over_250cp_nonmate": sum(
                    r["rodent_cp_loss"] >= 250 for r in nonmate),
                "observations": observations,
            }
    finally:
        try:
            sf.quit()
        finally:
            sf.close()
    report = {
        "schema": AUDIT_SCHEMA, "status": "DESCRIPTIVE_ENDGAME_TRANSFER_NOT_ELO",
        "positions_sha256": file_hash(Path(positions)),
        "rodent_binary_sha256": file_hash(exe),
        "stockfish_binary_sha256": file_hash(stockfish),
        "stockfish_name": sf_id,
        "rodent_depth": rodent_depth, "stockfish_depth": stockfish_depth,
        "training_or_parameter_selection_performed": False,
        "players": evaluated,
        "warning": "Scores are depth-sensitive, sampled once per real TEST game; not historic Elo or a promotion gate.",
    }
    _write_new(out, report)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="TEST-only real historical endgame diagnostic")
    sub = p.add_subparsers(dest="command", required=True)
    extraction = sub.add_parser("extract")
    source = extraction.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    extraction.add_argument("--profiles", type=Path, required=True)
    extraction.add_argument("--out", type=Path, required=True)
    extraction.add_argument("--players", nargs="+", default=list(DEFAULT_PLAYERS))
    extraction.add_argument("--per-player", type=int, default=12)
    evaluation = sub.add_parser("audit")
    evaluation.add_argument("--positions", type=Path, required=True)
    evaluation.add_argument("--rodent-root", type=Path, required=True)
    evaluation.add_argument("--stockfish", type=Path, required=True)
    evaluation.add_argument("--out", type=Path, required=True)
    evaluation.add_argument("--rodent-depth", type=int, default=4)
    evaluation.add_argument("--stockfish-depth", type=int, default=8)
    args = p.parse_args(argv)
    if args.command == "extract":
        result = extract(args.profiles, args.out, players=args.players,
                         archive_dir=args.archive_dir, per_player=args.per_player)
        print(json.dumps({k:{
            "games":v["sampled_distinct_games"],
            "available":v["test_endgame_games_available"]}
            for k,v in result["players"].items()}, sort_keys=True))
    else:
        result = audit(args.positions, args.rodent_root, args.stockfish,
                       args.out, rodent_depth=args.rodent_depth,
                       stockfish_depth=args.stockfish_depth)
        print(json.dumps({k:{
            "games":v["sampled_distinct_games"],
            "same_move":v["same_historical_move"],
            "mean_rodent_cp_loss":v["nonmate_mean_rodent_cp_loss"]}
            for k,v in result["players"].items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
