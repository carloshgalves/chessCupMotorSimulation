"""Opt-in product duel; preserves original installation and frozen motor bytes."""
import argparse
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from importlib.resources import files
import json

from .catalog import find_player
from .matches import file_hash, play_duel
from .opening_books import load_opening_book, select_book_move
import chess.pgn
from .product_repertoires import audit, build, PERIODS
from .historical_quality_parity import source_rows
from .tal_strength_doe import make_isolated_profiles


def play(root, output, books, *, archive_dir=None, seed=2029, max_plies=600):
    manifest = json.loads(files("chesssim.data").joinpath("qualified_game_assets.json").read_text())
    assets = manifest["players"]
    for slug in PERIODS:
        if assets[slug]["status"] != "FROZEN_GAME_ASSET":
            raise ValueError("Unqualified product motor")
    sources = source_rows(archive_dir)
    selected = {}
    for slug in PERIODS:
        book = load_opening_book(Path(books) / f"{slug}.json", slug)
        if book != build(slug, sources[slug]):
            raise ValueError("Book differs from verified dated historical corpus")
        audit(book)
        selected[slug] = book
    with TemporaryDirectory(prefix="chesssim-product-") as work:
        overlay, original, variants = make_isolated_profiles(root, Path(work) / "rodent")
        if (original["rodent"] != assets["fischer"]["hashes"]["rodent"]
                or original["fischer"] != assets["fischer"]["hashes"]["fischer_profile"]
                or variants["TalBalanced"] != assets["tal"]["hashes"]["tal_balanced_profile"]):
            raise ValueError("Qualified asset hash mismatch")
        if file_hash(overlay / "personalities/talbalanced.txt") != variants["TalBalanced"]:
            raise ValueError("Qualified Tal bytes mismatch")
        fischer = find_player("fischer")
        tal = replace(find_player("tal"), rodent_alias="TalBalanced",
                      rodent_file="talbalanced.txt")
        result = play_duel(overlay, output, fischer, tal, depth=16,
                         reverse=True, max_plies=max_plies, use_books=False,
                         historical_openings=selected, opening_seed=seed,
                         max_wall_seconds=3600, node_cap=5_000_000)
        verify_duel(result, selected, seed)
        return result


def verify_duel(folder, books, seed):
    report = json.loads((folder / "summary.json").read_text())
    if report["status"] != "COMPLETED" or len(report["games"]) != 2:
        raise ValueError("Product duel did not complete both colors")
    if report["node_cap"] != 5_000_000:
        raise ValueError("Missing product search budget")
    for index, meta in enumerate(report["games"], 1):
        with (folder / f"jogo_{index}.pgn").open() as stream:
            game = chess.pgn.read_game(stream)
        if game is None or game.errors or game.headers["Result"] == "*":
            raise ValueError("Incomplete or illegal product PGN")
        board = game.board()
        players = ("fischer", "tal") if index == 1 else ("tal", "fischer")
        trace = {event["ply"]: event for event in meta["opening_trace"]}
        observed = set()
        searched = 0
        for move in game.mainline_moves():
            owner = players[0 if board.turn else 1]
            expected = select_book_move(books[owner], board, seed=seed, game_number=index)
            if expected is not None:
                event = trace.get(board.ply() + 1)
                if (move != expected or event is None or event["player"] != owner
                        or event["move"] != move.uci()):
                    raise ValueError("Played opening differs from historical policy")
                observed.add(board.ply() + 1)
            else:
                searched += 1
            if move not in board.legal_moves:
                raise ValueError("Illegal product move")
            board.push(move)
        if set(trace) != observed or not observed or not searched:
            raise ValueError("Missing book or UCI fallback evidence")
        if board.outcome(claim_draw=True) is None or board.outcome(claim_draw=True).result() != game.headers["Result"]:
            raise ValueError("Unverified product game outcome")
        print("PRODUCT_BOOK_GAME", index, len(observed), searched, game.headers["Result"], flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rodent", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--books", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--seed", type=int, default=2029)
    args = parser.parse_args(argv)
    print(play(args.rodent, args.out, args.books, archive_dir=args.archive_dir,
               seed=args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
