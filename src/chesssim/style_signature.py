""" descriptive historical style signatures from PGN corpora.

These summaries are *descriptive only*. They do not validate a Rodent profile,
do not estimate Elo and do not prove historical identity. They are intended to
provide another lens besides exact move agreement.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from .historical import player_side
from .peak_windows import load_peak_window

PROTOCOL = {
    "version": "style-signature-v8",
    "scope": "descriptive-only historical style summaries from recorded games",
    "selection": "never opens sealed test sets and never promotes a personality",
    "warning": "move frequencies and opening tendencies are not a full identity model",
}


def _result_for_side(result: str, side_white: bool) -> str:
    if result == "1/2-1/2":
        return "draw"
    if result == "1-0":
        return "win" if side_white else "loss"
    if result == "0-1":
        return "loss" if side_white else "win"
    return "other"


def _top(counter: Counter, limit: int = 8) -> list[dict]:
    return [{"label": label, "count": count} for label, count in counter.most_common(limit)]


def _cohort_signature(pgn: Path, match_name: str, *, from_year: int | None = None,
                      to_year: int | None = None) -> dict:
    import chess
    import chess.pgn

    if not pgn.is_file():
        raise FileNotFoundError(pgn)
    counters = Counter()
    years = Counter()
    eco = Counter()
    openings = Counter()
    first_white = Counter()
    first_black = Counter()
    castle_side = Counter()
    game_lengths = []
    move_counts = []
    outcomes = Counter()
    rejected = Counter()

    with pgn.open(encoding="utf-8-sig", errors="replace") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors:
                rejected["illegal_or_malformed"] += 1
                continue
            side = player_side(game, match_name)
            if side is None:
                rejected["not_unique_target"] += 1
                continue
            date = game.headers.get("Date", "")
            m = re.match(r"^(\d{4})", date)
            if not m:
                rejected["no_year"] += 1
                continue
            year = int(m.group(1))
            if from_year is not None and year < from_year:
                rejected["before_window"] += 1
                continue
            if to_year is not None and year > to_year:
                rejected["after_window"] += 1
                continue
            if game.headers.get("Variant", "Standard") not in ("Standard", "Chess"):
                rejected["variant"] += 1
                continue

            years[str(year)] += 1
            eco_value = (game.headers.get("ECO") or "").strip()
            if eco_value:
                eco[eco_value] += 1
            opening_value = (game.headers.get("Opening") or "").strip()
            if opening_value:
                openings[opening_value] += 1
            outcomes[_result_for_side(game.headers.get("Result", "*"), side)] += 1

            counters["games"] += 1
            counters["white_games" if side else "black_games"] += 1

            board = game.board()
            player_move_index = 0
            castles = None
            player_moves = 0
            for move in game.mainline_moves():
                if board.turn == side:
                    player_move_index += 1
                    player_moves += 1
                    piece = board.piece_at(move.from_square)
                    piece_type = piece.piece_type if piece else None
                    counters["player_moves"] += 1
                    if board.is_capture(move):
                        counters["captures"] += 1
                    if board.gives_check(move):
                        counters["checks"] += 1
                    if board.is_castling(move):
                        counters["castles"] += 1
                        castles = "kingside" if chess.square_file(move.to_square) == 6 else "queenside"
                    if piece_type == chess.PAWN:
                        counters["pawn_moves"] += 1
                    if piece_type == chess.QUEEN:
                        counters["queen_moves"] += 1
                        if board.fullmove_number <= 10:
                            counters["early_queen_moves"] += 1
                    if piece_type == chess.KNIGHT:
                        counters["knight_moves"] += 1
                    if piece_type == chess.BISHOP:
                        counters["bishop_moves"] += 1
                    if piece_type == chess.ROOK:
                        counters["rook_moves"] += 1
                    if piece_type == chess.KING:
                        counters["king_moves"] += 1
                    if player_move_index == 1:
                        bucket = first_white if side else first_black
                        bucket[move.uci()] += 1
                board.push(move)
            move_counts.append(player_moves)
            game_lengths.append(board.ply())
            if castles is not None:
                castle_side[castles] += 1
            else:
                castle_side["none"] += 1

    games = counters["games"]
    moves = counters["player_moves"]
    def rate(key: str, denom: int) -> float | None:
        return counters[key] / denom if denom else None

    return {
        "games": games,
        "years": dict(sorted(years.items(), key=lambda kv: int(kv[0]))),
        "rejected": dict(rejected),
        "results": dict(outcomes),
        "white_share": counters["white_games"] / games if games else None,
        "black_share": counters["black_games"] / games if games else None,
        "avg_game_plies": sum(game_lengths) / len(game_lengths) if game_lengths else None,
        "avg_player_moves_per_game": sum(move_counts) / len(move_counts) if move_counts else None,
        "capture_rate": rate("captures", moves),
        "check_rate": rate("checks", moves),
        "castle_rate": rate("castles", games),
        "castle_side": dict(castle_side),
        "pawn_move_rate": rate("pawn_moves", moves),
        "queen_move_rate": rate("queen_moves", moves),
        "early_queen_move_rate": rate("early_queen_moves", moves),
        "knight_move_rate": rate("knight_moves", moves),
        "bishop_move_rate": rate("bishop_moves", moves),
        "rook_move_rate": rate("rook_moves", moves),
        "king_move_rate": rate("king_moves", moves),
        "top_eco": _top(eco),
        "top_openings": _top(openings),
        "top_first_moves_white": _top(first_white),
        "top_first_moves_black": _top(first_black),
    }


def style_signature_report(pgn: Path, peak_registry: Path, output: Path, *, slug: str,
                           match_name: str, allow_provisional: bool = False,
                           career_from_year: int = 1600, career_to_year: int = 2100) -> dict:
    if output.exists():
        raise FileExistsError(output)
    peak = load_peak_window(peak_registry, slug, allow_provisional=allow_provisional)
    report = {
        "protocol": PROTOCOL,
        "player": slug,
        "match_name": match_name,
        "inputs": {
            "pgn": str(pgn),
            "pgn_sha256": sha256(pgn.read_bytes()).hexdigest(),
            "peak_registry_sha256": sha256(peak_registry.read_bytes()).hexdigest(),
        },
        "peak_window": peak,
        "signatures": {
            "career": _cohort_signature(pgn, match_name, from_year=career_from_year, to_year=career_to_year),
            "peak": _cohort_signature(pgn, match_name, from_year=peak["from_year"], to_year=peak["to_year"]),
            "outside_peak": {
                "early_career": _cohort_signature(pgn, match_name, from_year=career_from_year, to_year=peak["from_year"] - 1) if peak["from_year"] > career_from_year else None,
                "late_career": _cohort_signature(pgn, match_name, from_year=peak["to_year"] + 1, to_year=career_to_year) if peak["to_year"] < career_to_year else None,
            },
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
