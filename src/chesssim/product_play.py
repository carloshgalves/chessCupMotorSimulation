"""Opt-in product duel; preserves original installation and frozen motor bytes."""
import argparse
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from importlib.resources import files
import json

from .catalog import find_player
from .matches import file_hash, play_duel
from .opening_books import load_opening_book
from .product_repertoires import audit, build, PERIODS
from .historical_quality_parity import source_rows
from .tal_strength_doe import make_isolated_profiles


def play(root, output, books, *, archive_dir=None, seed=2029, max_plies=400):
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
        return play_duel(overlay, output, fischer, tal, depth=16,
                         reverse=True, max_plies=max_plies, use_books=False,
                         historical_openings=selected, opening_seed=seed)


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
