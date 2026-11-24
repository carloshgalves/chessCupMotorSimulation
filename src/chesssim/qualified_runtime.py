"""Explicit opt-in, local, hash-verified product personality bundle."""
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import shutil

from .catalog import find_player
from .matches import file_hash, validate
from .opening_books import load_opening_book, select_book_move
from .registry import add_to_first_set, registered_aliases

MODE = "qualified"
VERSION = "qualified-product-v1"
NODE_CAP = 5_000_000
SLUGS = frozenset(("fischer", "tal", "karpov", "kasparov"))
BOOKS = {
    "fischer": ("f6ce8e2f451e7701e3f9ea37a66a6a5688e56fa089172087218e05173fb835af", "bytes"),
    "tal": ("175706169f66e2648dcb8cc79dff50516544eaf057b9b0cc705b3cf0c798f44d", "bytes"),
    "karpov": ("89903c23be426fa7e151117d026588273b9601e682f217b4955dc57e3584d191", "digest"),
    "kasparov": ("cb415b7b928ade70572a5084d968ed17d9ccca451f60614a38d0fd5379a098ba", "digest"),
}
PROFILE_KEYS = {"fischer": "fischer_profile", "tal": "tal_balanced_profile",
                "karpov": "karpov_profile", "kasparov": "kasparov_profile"}


def assets():
    doc = json.loads(files("chesssim.data").joinpath("qualified_game_assets.json").read_text())
    result = doc["players"]
    if set(result) != SLUGS or any(a["status"] != "FROZEN_GAME_ASSET" for a in result.values()):
        raise ValueError("Qualified roster is not frozen")
    return result


def validate_mode(raw, slugs, *, real=True):
    mode = raw.get("personality_mode", "legacy")
    if mode not in ("legacy", MODE):
        raise ValueError("Modo de personalidade inválido")
    if mode == MODE and (not real or not slugs or not set(slugs) <= SLUGS):
        raise ValueError("Modo homologado exige apenas motores homologados e execução real")
    return {"personality_mode": MODE} if mode == MODE else (
        {"personality_mode": "legacy"} if "personality_mode" in raw else {})


def book_digest(book, slug):
    expected, method = BOOKS[slug]
    if method == "bytes":
        data = (json.dumps(book, sort_keys=True, separators=(",", ":")) + "\n").encode()
        actual = sha256(data).hexdigest()
    else:
        from .fischer_full_games import digest
        actual = digest(book)
    if actual != expected:
        raise ValueError("Repertório homologado divergente: " + slug)
    return actual


def player_for(slug, asset):
    p = find_player(slug)
    alias = asset.get("alias", p.rodent_alias)
    return replace(p, rodent_alias=alias, rodent_file=alias.lower() + ".txt")


def prepare(root, *, archive_dir=None):
    """Build once from pinned corpora and exact Rodent build; never edit legacy files."""
    from .historical_quality_parity import source_rows
    from .product_peak_profiles import PEAKS, source, render
    from .product_repertoires import PERIODS, build, audit
    from .tal_strength_doe import render_tal_variant
    root = Path(root).resolve(strict=True)
    target = root / VERSION
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    registry = assets()
    if file_hash(root / "rodent-iv-plain.exe") != registry["fischer"]["hashes"]["rodent"]:
        raise ValueError("Rodent executável não homologado")
    sources = source_rows(archive_dir)
    for slug in PEAKS:
        if archive_dir is not None:
            raise ValueError("Local archive for peak sources not supported; use pinned remote")
        sources[slug] = source(slug)
    with TemporaryDirectory(prefix=".qualified-", dir=root) as tmp:
        folder = Path(tmp) / VERSION
        folder.mkdir()
        shutil.copy2(root / "rodent-iv-plain.exe", folder / "rodent-iv-plain.exe")
        shutil.copytree(root / "personalities", folder / "personalities")
        if (root / "books").is_dir():
            shutil.copytree(root / "books", folder / "books")
        directory = folder / "personalities"
        books = folder / "qualified-books"
        books.mkdir()
        additions = {}
        active = registered_aliases(directory / "basic.ini")
        for slug, asset in registry.items():
            original = find_player(slug)
            p = player_for(slug, asset)
            if asset.get("overrides"):
                raw = (directory / original.profile_filename).read_text(encoding="utf-8-sig")
                renderer = render_tal_variant if slug == "tal" else render
                (directory / p.profile_filename).write_text(
                    renderer(raw, asset["overrides"]), encoding="utf-8", newline="\n")
            if p.rodent_alias in active:
                if active[p.rodent_alias] != p.profile_filename:
                    raise ValueError("Alias conflitante")
            else:
                additions[p.rodent_alias] = p.profile_filename
            book = build(slug, sources[slug], period=(PERIODS | PEAKS)[slug])
            audit(book)
            book_digest(book, slug)
            (books / (slug + ".json")).write_text(json.dumps(book, sort_keys=True), encoding="utf-8")
        ini = directory / "basic.ini"
        ini.write_text(add_to_first_set(ini.read_text(), additions), encoding="utf-8")
        resolve_bundle(folder, sorted(SLUGS))
        folder.rename(target)
    return target


def resolve_bundle(folder, slugs):
    slugs = list(slugs)
    validate_mode({"personality_mode": MODE}, slugs)
    registry = assets()
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Bundle homologado indisponível; execute qualified_runtime prepare")
    players, books, depths = {}, {}, {}
    profiles, book_hashes = {}, {}
    engine_hash = file_hash(folder / "rodent-iv-plain.exe")
    for slug in slugs:
        asset = registry[slug]
        p = player_for(slug, asset)
        exe, paths = validate(folder, p, p)
        profile = file_hash(paths[slug])
        if (file_hash(exe) != engine_hash or engine_hash != asset["hashes"]["rodent"]
                or profile != asset["hashes"][PROFILE_KEYS[slug]]
                or asset.get("uci_node_cap", NODE_CAP) != NODE_CAP):
            raise ValueError("Motor ou perfil homologado divergente: " + slug)
        book = load_opening_book(folder / "qualified-books" / (slug + ".json"), slug)
        book_hashes[slug] = book_digest(book, slug)
        profiles[slug] = profile
        players[slug], books[slug], depths[slug] = p, book, asset["depth"]
    resources = {"engine_sha256": engine_hash, "profile_sha256": profiles,
                 "book_sha256": book_hashes, "depths": depths,
                 "personality_mode": MODE, "version": VERSION, "node_cap": NODE_CAP}
    return players, books, depths, resources


def resolve(root, slugs):
    folder = Path(root) / VERSION
    return (folder, *resolve_bundle(folder, slugs))


def headers(game, white, black, resources, seed, number):
    game.headers.update({
        "PersonalityMode": VERSION, "WhiteBookSHA256": resources["book_sha256"][white],
        "BlackBookSHA256": resources["book_sha256"][black],
        "ProductDepthWhite": str(resources["depths"][white]),
        "ProductDepthBlack": str(resources["depths"][black]),
        "NodeCap": str(NODE_CAP), "OpeningSeed": str(seed), "OpeningGameNumber": str(number),
    })


def verify_headers(game, white, black, resources, seed, number):
    import chess.pgn
    registry = assets()
    if (resources.get("personality_mode") != MODE or resources.get("version") != VERSION
            or resources.get("node_cap") != NODE_CAP):
        raise ValueError("Snapshot homologado inválido")
    for slug in (white, black):
        asset = registry[slug]
        if (resources["engine_sha256"] != asset["hashes"]["rodent"]
                or resources["profile_sha256"][slug] != asset["hashes"][PROFILE_KEYS[slug]]
                or resources["book_sha256"][slug] != BOOKS[slug][0]
                or resources["depths"][slug] != asset["depth"]):
            raise ValueError("Snapshot homologado divergente")
    expected = chess.pgn.Game()
    headers(expected, white, black, resources, seed, number)
    for key in ("PersonalityMode", "WhiteBookSHA256", "BlackBookSHA256",
                "ProductDepthWhite", "ProductDepthBlack", "NodeCap", "OpeningSeed",
                "OpeningGameNumber"):
        if game.headers.get(key) != expected.headers[key]:
            raise ValueError("Proveniência homologada divergente: " + key)


def verify_openings(game, white, black, books, seed, number):
    board = game.board()
    for move in game.mainline_moves():
        slug = white if board.turn else black
        expected = select_book_move(books[slug], board, seed=seed, game_number=number)
        if expected is not None and expected != move:
            raise ValueError("Lance divergente do repertório homologado")
        if move not in board.legal_moves:
            raise ValueError("Lance ilegal")
        board.push(move)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare", "check"))
    p.add_argument("--rodent", type=Path, required=True)
    a = p.parse_args()
    if a.action == "prepare":
        print(prepare(a.rodent))
    else:
        print(json.dumps(resolve(a.rodent, sorted(SLUGS))[-1], sort_keys=True))


if __name__ == "__main__":
    main()
