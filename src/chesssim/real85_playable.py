"""Playable historical Rodent IV pilot backed by high-coverage REAL PGNs.

85% is the target fraction of distinct, accepted games used to fit empirical
opening-choice counts and historical descriptive style statistics; it is NOT
a claim of training the neural Maia/DSC weights on 85% of the corpus.
The remaining games are evaluated but never included in those counts.

Historical identities can share style: no four-class identity requirement.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.pgn

from .catalog import Player, load_roster
from .game_diversity import mainline_identity
from .matches import _play_one, file_hash
from .opening_books import SCHEMA as BOOK_SCHEMA, load_opening_book, position_token
from .real_training_cohorts import (
    _iter_verified_games, _player_configuration, _scan, _source_bytes,
)
from .registry import registered_aliases
from .rodent import OFFICIAL_COMMIT

SCHEMA = "chesssim-real-85pct-playable-historic-profile-v1"
REPORT_SCHEMA = "chesssim-real-four-complete-uci-calibration-v1"
DEFAULT_PLAYERS = ("fischer", "tal", "karpov", "kasparov")
SALT = "chesssim-real-85pct-v1"
TRAIN_PARTS = 8500
DENOMINATOR = 10000
MAX_OPENING_PLIES = 24


def game_arm(trajectory: str) -> str:
    """Global game identity, never a player-name-based allocation."""
    if (not isinstance(trajectory, str) or len(trajectory) != 64
            or any(c not in "0123456789abcdef" for c in trajectory)):
        raise ValueError("Invalid historical game trajectory digest")
    bucket = int(sha256(f"{SALT}:{trajectory}".encode()).hexdigest()[:16], 16) % DENOMINATOR
    return "train" if bucket < TRAIN_PARTS else "test"


def _style_features(game: chess.pgn.Game, color: bool) -> dict:
    """Count actual decisions by stage, not invented attributes or Elo."""
    board = game.board()
    stats = Counter()
    for move in game.mainline_moves():
        if board.turn == color:
            stage = "opening" if board.ply() < 24 else (
                "middle" if board.ply() < 70 else "late")
            stats[f"{stage}_moves"] += 1
            stats["moves"] += 1
            stats[f"{stage}_captures"] += bool(board.is_capture(move))
            stats[f"{stage}_checks"] += bool(board.gives_check(move))
            stats[f"{stage}_castles"] += bool(board.is_castling(move))
            stats[f"{stage}_pawn_moves"] += (
                board.piece_type_at(move.from_square) == chess.PAWN)
            if board.is_capture(move):
                stats["captures"] += 1
            if board.gives_check(move):
                stats["checks"] += 1
            if board.is_castling(move):
                stats["castles"] += 1
        board.push(move)
    return dict(stats)


def _style_summary(totals: Counter) -> dict:
    out = {"moves": totals["moves"]}
    for phase in ("opening", "middle", "late"):
        count = totals[f"{phase}_moves"]
        out[phase] = {"decisions": count}
        for action in ("captures", "checks", "castles", "pawn_moves"):
            out[phase][action+"_rate"] = round(
                totals[f"{phase}_{action}"]/count, 6) if count else None
    return out


def _write_new(path: Path, content: dict) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Refusing overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")) + "\n", encoding="utf-8")


def train_real_profiles(out: Path, *, players: list[str] | None = None,
                        archive_dir: Path | None = None,
                        allow_missing: bool = False,
                        max_opening_plies: int = MAX_OPENING_PLIES) -> dict:
    """Fit true opening move probabilities from ~85% of all verified real games.

    Every player move of every fitting game also contributes to phase-style
    frequency statistics; held-out games are never used to fit these.
    TRAIN coverage is counted over *accepted distinct trajectories*, with no
    arbitrary 128-game cap and no artificial differences between players.
    """
    if not 2 <= max_opening_plies <= 40:
        raise ValueError("max_opening_plies must be from 2 through 40")
    selected = list(players or DEFAULT_PLAYERS)
    conf = _player_configuration(selected, None, None)
    out = Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Output must be a new directory with an existing parent")
    stage = out.with_name(out.name + ".tmp-real85")
    if stage.exists() or stage.is_symlink():
        raise FileExistsError("Temporary training stage exists")
    stage.mkdir()
    result = {}
    try:
        for slug in selected:
            player = conf[slug]
            try:
                _raw, pgn, source = _source_bytes(player["source"], archive_dir)
            except Exception as exc:
                from urllib.error import HTTPError
                if not allow_missing or not isinstance(exc, (HTTPError, FileNotFoundError)):
                    raise
                if isinstance(exc, HTTPError) and exc.code != 404:
                    raise
                result[slug] = {
                    "status": "NO_DOCUMENTED_SOURCE",
                    "reason": "HTTP_404" if isinstance(exc, HTTPError) else "LOCAL_FILE_MISSING",
                }
                continue
            rows, counters = _scan(pgn, slug, player["surname"])
            if not rows:
                result[slug] = {"status": "NO_VALID_GAMES", "source": source,
                                "scan": counters}
                continue
            chosen = {r["mainline_sha256"]: game_arm(r["mainline_sha256"])
                      for r in rows}
            # The same Karpov/Kasparov trajectory always falls in the same arm.
            train_count = sum(arm == "train" for arm in chosen.values())
            test_count = len(chosen) - train_count
            coverage = train_count / len(chosen)
            if not test_count or not train_count:
                result[slug] = {"status": "INSUFFICIENT_SPLIT",
                                "accepted_games": len(rows)}
                continue
            if len(rows) >= 100 and coverage < 0.8:
                raise ValueError(f"REAL_SOURCE_COVERAGE_UNDER_80_PERCENT: {slug}")
            move_votes = {"white": defaultdict(Counter), "black": defaultdict(Counter)}
            historical = {"train": Counter(), "test": Counter()}
            held_eval = Counter()
            games_seen = Counter()
            for game, meta in _iter_verified_games(pgn, rows, player["surname"]):
                digest, _plies = mainline_identity(game)
                arm = chosen[digest]
                games_seen[arm] += 1
                color = meta["color"] == "white"
                historical[arm].update(_style_features(game, color))
                board = game.board()
                for move in game.mainline_moves():
                    if board.turn == color and board.ply() < max_opening_plies:
                        side = meta["color"]
                        pos = position_token(board)
                        if arm == "train":
                            move_votes[side][pos][move.uci()] += 1
                        else:
                            held_eval["opening_decisions"] += 1
                            observed = move_votes[side].get(pos)
                            # Deferred evaluation below: votes are incomplete
                            # until ALL TRAIN games have been processed.
                    board.push(move)
            if games_seen["train"] != train_count or games_seen["test"] != test_count:
                raise AssertionError("Second PGN pass changed historic game split")
            # Evaluate held games only AFTER freezing all learned opening counts.
            held_eval = Counter()
            for game, meta in _iter_verified_games(pgn, rows, player["surname"]):
                digest, _ = mainline_identity(game)
                if chosen[digest] != "test":
                    continue
                board = game.board()
                for move in game.mainline_moves():
                    if board.turn == (meta["color"] == "white") and board.ply() < max_opening_plies:
                        votes = move_votes[meta["color"]].get(position_token(board))
                        held_eval["decisions"] += 1
                        if votes:
                            held_eval["covered"] += 1
                            best = max(votes.values())
                            held_eval["top1"] += move.uci() in {
                                m for m, n in votes.items() if n == best
                            }
                    board.push(move)
            book = {
                "schema": BOOK_SCHEMA, "player": slug,
                "match_name": player["name"],
                "policy": {"max_plies": max_opening_plies,
                           "never_engine_verified": True},
                "source": {
                    "pgn_sha256": sha256(pgn).hexdigest(),
                    "snapshot_commit": None if archive_dir else
                        "aaadf90e11862bc6f207fed407a70e7017df26bd",
                    "accepted_games": train_count,
                    "source_games_total": len(rows),
                    "train_ratio": round(coverage, 6),
                    "provenance": "REAL_PGN_85PCT_GAME_DISJOINT_TRAIN",
                },
                "positions": {
                    side: {fen: dict(sorted(moves.items()))
                           for fen, moves in sorted(move_votes[side].items())}
                    for side in ("white", "black")
                },
            }
            _write_new(stage / f"{slug}.json", book)
            result[slug] = {
                "status": "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE",
                "source": source, "source_pgn_sha256": sha256(pgn).hexdigest(),
                "scan": counters, "accepted_distinct_games": len(rows),
                "train_games": train_count, "test_games": test_count,
                "train_fraction": round(coverage, 6),
                "train_style": _style_summary(historical["train"]),
                "test_style": _style_summary(historical["test"]),
                "heldout_opening": {
                    **dict(held_eval),
                    "top1_over_covered": round(
                        held_eval["top1"]/held_eval["covered"], 6
                    ) if held_eval["covered"] else None,
                    "coverage": round(
                        held_eval["covered"]/held_eval["decisions"], 6
                    ) if held_eval["decisions"] else None,
                },
                "learned_opening_positions": sum(
                    len(book["positions"][side]) for side in ("white", "black")),
                "learned_opening_decisions": sum(
                    sum(sum(votes.values()) for votes in book["positions"][side].values())
                    for side in ("white", "black")),
                "source_sha256": book["source"]["pgn_sha256"],
                "book_sha256": file_hash(stage / f"{slug}.json"),
            }
        report = {
            "schema": SCHEMA, "status": "EXPERIMENTAL_HISTORIC_PLAYER_REPERTOIRES",
            "train_target": 0.85, "split_method": "global SHA256 mainline trajectory",
            "test_fraction_target": 0.15,
            "train_vs_neural_note": (
                "85pct of accepted games train empirical opening choices and "
                "descriptive per-phase frequencies; Maia/DSC neural weights are NOT refitted"),
            "players": result,
            "do_not_mix_with_old_year_holdout": True,
            "original_rodent_modified": False, "official_style_validated": False,
        }
        _write_new(stage / "report.json", report)
        stage.rename(out)
        return report
    except BaseException:
        import shutil
        import os
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _style_of_engine_game(game: chess.pgn.Game, player_white: bool) -> dict:
    return _style_summary(Counter(_style_features(game, player_white)))


def play_real_four(root: Path, profile_dir: Path, out: Path, *,
                   players: list[str] | None = None, depth: int = 2,
                   max_plies: int = 450, opening_seed: int = 2033,
                   use_repertoire: bool = True) -> dict:
    """Play actual Rodent-vs-Simple games, reversed colors, to terminal state.

    A ply cap produces INCOMPLETE, never a fabricated draw or strength score.
    Real Rodent handles search; only early opening preference is overridden.
    """
    if not 1 <= depth <= 12 or not 100 <= max_plies <= 800:
        raise ValueError("Invalid real UCI depth or full-game ply budget")
    root = Path(root).resolve(strict=True)
    out = Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Output must be a new directory")
    book_meta = json.loads((Path(profile_dir) / "report.json").read_text())
    if book_meta.get("schema") != SCHEMA:
        raise ValueError("Not a supported real 85% trained profile set")
    requested = list(players or DEFAULT_PLAYERS)
    roster = {p.slug: p for p in load_roster()}
    exe = root / "rodent-iv-plain.exe"
    if not exe.is_file():
        raise FileNotFoundError("Real Rodent IV executable not found")
    aliases = registered_aliases(root / "personalities" / "basic.ini")
    if "Simple" not in aliases:
        raise ValueError("Real Rodent IV generic Simple personality unavailable")
    control_file = root / "personalities" / aliases["Simple"]
    if not control_file.is_file():
        raise FileNotFoundError(control_file)
    control = Player(number=0, name="Rodent IV Simple", slug="control-simple",
                     group="baseline", rodent_alias="Simple",
                     rodent_file=aliases["Simple"])
    trained = {}
    for slug in requested:
        if slug not in roster or roster[slug].rodent_alias is None:
            raise ValueError(f"Historical player {slug} has no runnable Rodent profile")
        actual = roster[slug]
        if aliases.get(actual.rodent_alias) != actual.profile_filename:
            raise ValueError(f"Rodent alias for {slug} is not registered")
        profile_file = root / "personalities" / actual.profile_filename
        if not profile_file.is_file():
            raise FileNotFoundError(profile_file)
        row = book_meta["players"].get(slug, {})
        if row.get("status") != "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE":
            raise ValueError(f"No genuinely trained historical profile: {slug}")
        book_file = Path(profile_dir) / (slug+".json")
        if file_hash(book_file) != row["book_sha256"]:
            raise ValueError("Trained historical book artifact changed")
        trained[slug] = load_opening_book(book_file, slug)
    games, pgns = [], []
    exe_sha = file_hash(exe)
    for slug in requested:
        player = roster[slug]
        hashes = {"exe": exe_sha,
                  slug: file_hash(root / "personalities" / player.profile_filename),
                  control.slug: file_hash(control_file)}
        for color_idx, (white, black) in enumerate(((player, control), (control, player))):
            books = {slug: trained[slug]} if use_repertoire else None
            pgn, meta = _play_one(
                root, exe, white, black, depth=depth, movetime=None,
                max_plies=max_plies, round_number=color_idx+1,
                hashes=hashes, use_books=False,
                historical_openings=books, opening_seed=opening_seed)
            # Original source, engine and per-game modification remain traceable.
            pgn.headers["HistoricalTrainingFraction"] = str(
                book_meta["players"][slug]["train_fraction"])
            pgn.headers["HistoricalBookSHA256"] = book_meta["players"][slug]["book_sha256"]
            pgn.headers["HistoricalPlayer"] = slug
            pgn.headers["ExperimentalProfile"] = "true"
            pgn.headers["TrueResultOnly"] = "true"
            pgns.append(str(pgn))
            player_white = white.slug == slug
            result = meta["result"]
            score = None if meta["status"] != "COMPLETED" else (
                0.5 if result == "1/2-1/2" else
                float((result == "1-0") == player_white))
            games.append({
                "player": slug, "color": "white" if player_white else "black",
                "status": meta["status"], "result": result,
                "termination": meta["termination"], "plies": meta["plies"],
                "score_vs_simple": score,
                "opening_moves_from_real_pg_ns": sum(meta["opening_moves"].values()),
                "engine_style": _style_of_engine_game(pgn, player_white),
            })
    totals = {}
    for slug in requested:
        subset = [r for r in games if r["player"] == slug]
        completed = [r for r in subset if r["score_vs_simple"] is not None]
        totals[slug] = {
            "scheduled": len(subset), "completed": len(completed),
            "points_vs_simple": sum(r["score_vs_simple"] for r in completed),
            "score_rate_vs_simple": round(sum(r["score_vs_simple"] for r in completed)
                                         /len(completed), 6) if completed else None,
            "book_plies_played": sum(r["opening_moves_from_real_pg_ns"] for r in subset),
            "historical_test_style": book_meta["players"][slug]["test_style"],
            "historical_train_games": book_meta["players"][slug]["train_games"],
            "historical_test_games": book_meta["players"][slug]["test_games"],
            "games_style": [r["engine_style"] for r in subset],
        }
    report = {
        "schema": REPORT_SCHEMA, "status": (
            "ACTUAL_COMPLETE_GAMES_PLAYED" if all(x["status"] == "COMPLETED" for x in games)
            else "REAL_UCI_GAMES_SOME_INCOMPLETE"),
        "rodent_source_commit_reference": OFFICIAL_COMMIT,
        "rodent_binary_sha256": exe_sha,
        "search_depth": depth, "max_plies": max_plies,
        "opponent": "Rodent IV Simple (same binary)",
        "opening_training_method": "real_85pct_game_disjoint_empirical_moves",
        "use_historical_repertoire": bool(use_repertoire),
        "no_elo_inferred_from_small_sample": True,
        "no_neural_finetuning_claim": True,
        "players": totals, "games": games,
    }
    out.mkdir()
    try:
        _write_new(out / "results.json", report)
        (out / "played.pgn").write_text("\n\n".join(pgns)+"\n", encoding="utf-8")
    except BaseException:
        import shutil
        shutil.rmtree(out)
        raise
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Historical 85% real-game playable Rodent pilot")
    sub = p.add_subparsers(dest="command", required=True)
    fit = sub.add_parser("fit")
    src = fit.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch", action="store_true")
    src.add_argument("--archive-dir", type=Path)
    fit.add_argument("--players", nargs="+", default=list(DEFAULT_PLAYERS))
    fit.add_argument("--allow-missing", action="store_true")
    fit.add_argument("--max-opening-plies", type=int, default=MAX_OPENING_PLIES)
    fit.add_argument("--out", type=Path, required=True)
    play = sub.add_parser("play")
    play.add_argument("--rodent-root", type=Path, required=True)
    play.add_argument("--profiles", type=Path, required=True)
    play.add_argument("--out", type=Path, required=True)
    play.add_argument("--players", nargs="+", default=list(DEFAULT_PLAYERS))
    play.add_argument("--depth", type=int, default=2)
    play.add_argument("--max-plies", type=int, default=450)
    play.add_argument("--without-repertoire", action="store_true")
    args = p.parse_args(argv)
    if args.command == "fit":
        doc = train_real_profiles(args.out, players=args.players,
                                  archive_dir=args.archive_dir,
                                  allow_missing=args.allow_missing,
                                  max_opening_plies=args.max_opening_plies)
        print(json.dumps({"status": doc["status"],
                          "players": {slug:{
                              key: row.get(key) for key in
                              ("status","accepted_distinct_games","train_games","test_games",
                               "train_fraction","learned_opening_positions")}
                              for slug,row in doc["players"].items()}},
                         sort_keys=True), flush=True)
    else:
        doc = play_real_four(args.rodent_root, args.profiles, args.out,
                             players=args.players, depth=args.depth,
                             max_plies=args.max_plies,
                             use_repertoire=not args.without_repertoire)
        print(json.dumps({"status": doc["status"], "players": doc["players"]},
                         sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
