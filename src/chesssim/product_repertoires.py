"""Product opening books from dated, verified historical PGN trajectories."""
from collections import Counter, defaultdict
import argparse
from hashlib import sha256
import json
from pathlib import Path

import chess

from .historical_quality_parity import source_rows
from .opening_books import SCHEMA, position_token, select_book_move
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _iter_verified_games

PERIODS = {"fischer": (1955, 1972), "tal": (1957, 1961)}
MAX_PLIES = 16


def build(player, source, *, through_year=None, period=None):
    pgn, rows, _, origin, surname = source
    start, end = PERIODS[player] if period is None else period
    if type(start) is not int or type(end) is not int or start > end:
        raise ValueError("Invalid historical period")
    if through_year is not None:
        if not start <= through_year <= end:
            raise ValueError("Cutoff outside personality period")
        end = through_year
    eligible = [r for r in rows if str(r["date"]).isdigit()
                and start <= int(r["date"]) <= end
                and neural_arm(r["mainline_sha256"]) == "fit"]
    counts = {c: defaultdict(Counter) for c in ("white", "black")}
    accepted = Counter()
    excluded = Counter({"outside_period_or_not_fit": len(rows) - len(eligible)})
    for game, row in _iter_verified_games(pgn, eligible, surname):
        board = game.board()
        color = row["color"]
        if color not in counts or board.fen() != chess.STARTING_FEN:
            excluded["nonstandard_start_or_color"] += 1
            continue
        decisions = 0
        for move in game.mainline_moves():
            if board.ply() >= MAX_PLIES:
                break
            if move not in board.legal_moves:
                raise ValueError("Illegal historical opening move")
            if board.turn == (color == "white"):
                counts[color][position_token(board)][move.uci()] += 1
                decisions += 1
            board.push(move)
        if decisions:
            accepted[color] += 1
        else:
            excluded["no_focal_move"] += 1
    if not all(accepted[c] for c in counts):
        raise ValueError("Missing historical support for either color")
    return {"schema": SCHEMA, "player": player, "match_name": surname,
            "policy": {"max_plies": MAX_PLIES, "never_engine_verified": True},
            "source": {"pgn_sha256": sha256(pgn).hexdigest(), "origin": origin,
                       "from_year": start, "to_year": end,
                       "accepted_games": sum(accepted.values()),
                       "by_color": dict(accepted), "rejected": dict(excluded),
                       "provenance": {"status": "PINNED_PRODUCT_FIT_ONLY"}},
            "positions": {c: {fen: dict(sorted(moves.items()))
                              for fen, moves in sorted(counts[c].items())}
                          for c in counts}}


def audit(book):
    """Replay all stored states; ensure every weighted entry is legal."""
    totals = {}
    if set(book["positions"]) != {"white", "black"}:
        # Fail closed, but inspect illegal entries before completeness checks.
        incomplete = True
    else:
        incomplete = False
    for color, entries in book["positions"].items():
        moves = 0
        for fen, choices in entries.items():
            board = chess.Board(fen + " 0 1")
            if not board.is_valid() or board.turn != (color == "white"):
                raise ValueError("Invalid historical book state")
            for text, count in choices.items():
                if type(count) is not int or count <= 0:
                    raise ValueError("Invalid historical book weight")
                if chess.Move.from_uci(text) not in board.legal_moves:
                    raise ValueError("Illegal historical book move")
                moves += count
            if select_book_move(book, board) not in board.legal_moves:
                raise ValueError("Book selector failed")
        totals[color] = {"positions": len(entries), "observations": moves}
    if incomplete or not all(t["positions"] for t in totals.values()):
        raise ValueError("Incomplete historical book")
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path)
    args = parser.parse_args(argv)
    sources = source_rows(args.archive_dir)
    args.out.mkdir(parents=True, exist_ok=False)
    for player in PERIODS:
        book = build(player, sources[player])
        report = audit(book)
        payload = json.dumps(book, sort_keys=True, separators=(",", ":")) + "\n"
        (args.out / f"{player}.json").write_text(payload, encoding="utf-8")
        print(json.dumps({"player": player, "source": book["source"],
                          "audit": report, "book_sha256": sha256(payload.encode()).hexdigest()},
                         sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
