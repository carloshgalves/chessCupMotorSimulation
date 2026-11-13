"""Large *real* historical PGN cohort audit for issue #11.

Downloads explicitly selected public PGN Mentor player ZIP archives or reads
the same archives from a local directory. Raw PGNs are never committed or
written to the report. The resulting analysis is exploratory, NOT evidence
that any chess engine reproduces a real person's style.

Original index: https://www.pgnmentor.com/files.html. The CI downloads
historical PGN snapshots from GitHub mirror rozim/ChessData.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

import chess.pgn

from .game_diversity import mainline_identity
from .historical_split_index import _partition_report, _split_by_year

SCHEMA = "chesssim-real-historical-cohort-audit-v1"
PLAYER_FILES = {
    "Fischer": {"surname": "fischer", "published_games": 827, "min_usable": 500},
    "Tal": {"surname": "tal", "published_games": 2431, "min_usable": 1400},
    "Karpov": {"surname": "karpov", "published_games": 3529, "min_usable": 2300},
    "Kasparov": {"surname": "kasparov", "published_games": 2128, "min_usable": 1300},
}
SOURCE_PAGE = "https://github.com/rozim/ChessData/tree/master/PgnMentor"
ORIGINAL_CATALOG = "https://www.pgnmentor.com/files.html"
SOURCE_BASE = "https://raw.githubusercontent.com/rozim/ChessData/master/PgnMentor/"
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_PGN_BYTES = 80 * 1024 * 1024
MAX_GAMES_PER_COHORT = 12_000
MIN_TOTAL_GAMES = 6_500
MAX_PLIES = 500
MAX_FENS_PER_COHORT = 250_000
DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\Z")


def archive_url(name: str) -> str:
    if name not in PLAYER_FILES:
        raise ValueError("Unrecognized historical player archive")
    return SOURCE_BASE + name + ".pgn"


def _read_archive(name: str, *, archive_dir: Path | None,
                  fetch: bool, timeout: int = 40) -> bytes:
    if name not in PLAYER_FILES:
        raise ValueError("Unsupported cohort")
    if archive_dir is not None:
        directory = Path(archive_dir)
        pgndata = directory / (name + ".pgn")
        zipdata = directory / (name + ".zip")
        path = pgndata if pgndata.is_file() or pgndata.is_symlink() else zipdata
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Source archive path contains a symbolic link")
        if not path.is_file() or path.stat().st_size > MAX_PGN_BYTES:
            raise ValueError("Missing or oversized source PGN/ZIP")
        blob = path.read_bytes()
    elif fetch:
        # Only four literal, allowlisted HTTPS URLs; no user-controlled hosts,
        # redirects or unbounded network reads.
        request = Request(archive_url(name), headers={
            "User-Agent": "ChessCupResearchAudit/1.0 (+offline-reproducible)"})
        with urlopen(request, timeout=timeout) as response:
            final = urlsplit(response.url)
            if (final.scheme != "https"
                    or final.hostname != "raw.githubusercontent.com"
                    or final.path != "/rozim/ChessData/master/PgnMentor/" + name + ".pgn"
                    or final.username or final.password or final.query):
                raise ValueError("Unexpected source archive redirect")
            blob = response.read(MAX_ARCHIVE_BYTES + 1)
    else:
        raise ValueError("Use --fetch or --archive-dir with existing ZIPs")
    limit = MAX_ARCHIVE_BYTES if blob[:4] == b"PK\x03\x04" else MAX_PGN_BYTES
    if not blob or len(blob) > limit:
        raise ValueError("Source PGN/ZIP is empty or above size limit")
    return blob


def _extract_pgn(name: str, archive: bytes) -> bytes:
    # The public GitHub mirror provides actual PGNs directly; original
    # PGN Mentor local downloads may be ZIP archives. Both are real data.
    if archive[:4] != b"PK\x03\x04":
        if (not archive or len(archive) > MAX_PGN_BYTES
                or b"\x00" in archive or b"[" not in archive[:4096]):
            raise ValueError("Invalid raw player PGN")
        return archive
    try:
        with ZipFile(BytesIO(archive)) as zf:
            members = [info for info in zf.infolist() if not info.is_dir()]
            if len(members) != 1 or members[0].filename != name + ".pgn":
                raise ValueError("ZIP must contain exactly the expected player PGN")
            item = members[0]
            if (item.file_size <= 0 or item.file_size > MAX_PGN_BYTES
                    or item.flag_bits & 1):
                raise ValueError("Invalid, encrypted or oversized PGN ZIP member")
            with zf.open(item) as stream:
                content = stream.read(MAX_PGN_BYTES + 1)
    except (BadZipFile, RuntimeError, OSError) as exc:
        raise ValueError("Invalid or corrupt historical PGN ZIP") from exc
    if not content or len(content) > MAX_PGN_BYTES or b"\x00" in content:
        raise ValueError("Invalid or oversized extracted PGN")
    return content


def _decode_pgn(blob: bytes) -> tuple[str, str]:
    try:
        return blob.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        # Preserve legacy Latin-1 metadata without silently dropping games.
        return blob.decode("latin-1"), "latin-1-fallback"


def _target_color(game: chess.pgn.Game, surname: str) -> bool | None:
    pattern = re.compile(rf"(?<![a-z]){re.escape(surname)}(?![a-z])")
    white = bool(pattern.search(game.headers.get("White", "").casefold()))
    black = bool(pattern.search(game.headers.get("Black", "").casefold()))
    return white if white != black else None


def _played_date(raw: str) -> str | None:
    match = DATE.fullmatch(raw)
    if not match:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def analyze_cohort(name: str, archive: bytes, *, min_ply: int = 17) -> dict:
    """Index real games, but do not derive or evaluate model predictions."""
    if name not in PLAYER_FILES or type(min_ply) is not int or not 1 <= min_ply <= 500:
        raise ValueError("Unsupported player or invalid ply threshold")
    raw = _extract_pgn(name, archive)
    decoded, encoding = _decode_pgn(raw)
    stream = StringIO(decoded)
    counts = Counter()
    dedup = set()
    entries = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        counts["pgn_records"] += 1
        if counts["pgn_records"] > MAX_GAMES_PER_COHORT:
            raise ValueError("Historical cohort exceeds game budget")
        if game.errors:
            counts["parse_errors"] += 1
            continue
        target = _target_color(game, PLAYER_FILES[name]["surname"])
        if target is None:
            counts["target_not_identifiable"] += 1
            continue
        digest, plies = mainline_identity(game)
        if plies == 0 or plies > MAX_PLIES:
            counts["invalid_ply_count"] += 1
            continue
        if digest in dedup:
            counts["duplicate_mainline"] += 1
            continue
        dedup.add(digest)
        played = _played_date(game.headers.get("Date", ""))
        if played is None:
            counts["missing_or_invalid_full_date"] += 1
        positions = []
        board = game.board()
        for ply, move in enumerate(game.mainline_moves(), start=1):
            if ply >= min_ply and board.turn == target:
                positions.append((board.fen(), move.uci()))
            board.push(move)
        entries.append({
            "mainline_sha256": digest, "date": played,
            "positions": positions, "plies": plies,
        })
    if len(entries) < PLAYER_FILES[name]["min_usable"]:
        raise ValueError(
            f"Real {name} cohort under minimum usable game threshold "
            f"({len(entries)} < {PLAYER_FILES[name]['min_usable']})")
    eligible = [entry for entry in entries if entry["date"] is not None]
    if not eligible:
        raise ValueError("No dated historical games")
    split = _split_by_year(eligible)
    partitions = {
        label: _partition_report(rows)
        for label, rows in split.items()
    }
    if not all(partitions[label]["games"] for label in ("train", "validation", "holdout")):
        raise ValueError("Insufficient years for three historical cohorts")
    return {
        "player": name,
        "download_sha256": sha256(archive).hexdigest(),
        "pgn_sha256": sha256(raw).hexdigest(),
        "download_bytes": len(archive),
        "pgn_bytes": len(raw),
        "source_url": archive_url(name),
        "source_catalog": SOURCE_PAGE,
        "original_catalog": ORIGINAL_CATALOG,
        "encoding": encoding,
        "counters": {
            key: counts[key] for key in (
                "pgn_records", "parse_errors", "target_not_identifiable",
                "invalid_ply_count", "duplicate_mainline",
                "missing_or_invalid_full_date")
        },
        "unique_games": len(entries),
        "dated_unique_games": len(eligible),
        "partitions": partitions,
        "_train_entries": split["train"],
        "_all_digests": {x["mainline_sha256"] for x in entries},
    }


def _training_fen_votes(cohort: dict) -> tuple[dict[str, Counter], int]:
    votes = defaultdict(Counter)
    counted = 0
    for entry in cohort["_train_entries"]:
        per_game = defaultdict(set)
        for fen, move in entry["positions"]:
            per_game[fen].add(move)
        for fen, moves in per_game.items():
            for move in moves:
                votes[fen][move] += 1
                counted += 1
        if len(votes) > MAX_FENS_PER_COHORT:
            raise ValueError("Too many distinct training positions")
    return votes, counted


def _compare_two(a: dict[str, Counter], b: dict[str, Counter]) -> dict:
    shared = sorted(a.keys() & b.keys())
    changed = 0
    both_obs = [0, 0]
    identical_choice = 0
    for fen in shared:
        av, bv = a[fen], b[fen]
        both_obs[0] += sum(av.values())
        both_obs[1] += sum(bv.values())
        best_a = min(av, key=lambda m: (-av[m], m))
        best_b = min(bv, key=lambda m: (-bv[m], m))
        changed += best_a != best_b
        identical_choice += bool(av.keys() & bv.keys())
    return {
        "exact_full_fens_shared": len(shared),
        "training_observations_on_shared_fens": both_obs,
        "fens_with_different_modal_moves": changed,
        "fens_with_any_common_observed_move": identical_choice,
        "caution": "DESCRIPTIVE_TRAINING_ONLY_NO_ENGINE_OR_HISTORICAL_IDENTITY_TEST",
    }


def audit_historical_archives(archives: dict[str, bytes], *,
                              min_ply: int = 17) -> dict:
    """Audit four real PGN collections without exporting raw games."""
    if set(archives) != set(PLAYER_FILES):
        raise ValueError("Exactly the four predeclared historical cohorts required")
    cohorts = {
        name: analyze_cohort(name, archives[name], min_ply=min_ply)
        for name in sorted(PLAYER_FILES)
    }
    total = sum(c["unique_games"] for c in cohorts.values())
    if total < MIN_TOTAL_GAMES:
        raise ValueError("Real historical game evidence below required unique cohort games")
    votes = {}
    observations = {}
    for name in sorted(cohorts):
        votes[name], observations[name] = _training_fen_votes(cohorts[name])
    pairs = {}
    names = sorted(cohorts)
    for i, left in enumerate(names):
        for right in names[i+1:]:
            pairs[left + "__" + right] = _compare_two(votes[left], votes[right])
    overlapping = {}
    for i, left in enumerate(names):
        for right in names[i+1:]:
            overlapping[left + "__" + right] = len(
                cohorts[left]["_all_digests"] & cohorts[right]["_all_digests"])
    public = {}
    for name in names:
        c = cohorts[name]
        public[name] = {k: v for k, v in c.items() if not k.startswith("_")}
        public[name]["training_fen_count"] = len(votes[name])
        public[name]["training_decision_observations"] = observations[name]
    return {
        "schema": SCHEMA,
        "status": "REAL_HISTORICAL_PUBLIC_PGN_EXPLORATORY_NOT_STYLE_VALIDATED",
        "min_ply": min_ply,
        "total_unique_cohort_games": total,
        "total_training_games": sum(
            x["partitions"]["train"]["games"] for x in public.values()),
        "sources": public,
        "shared_fen_pairs": pairs,
        "cross_cohort_duplicate_trajectories": overlapping,
        "limitations": [
            "Real published PGNs were parsed, but attribution and completeness are not independently certified.",
            "The ZIP sources were fully read, including games later assigned to reserved dates; NO sealed-test guarantee.",
            "Only training-year positions contribute to the shared-FEN descriptive counts.",
            "Distinct trajectories and names do not guarantee independent games or exclude confounding.",
            "Exact full FEN includes halfmove clock and fullmove number; overlap may be sparse.",
            "No Rodent/Stockfish candidate profile predictions, blinding or style validation were run.",
            "Source download availability and redistribution rights are separate from local analysis.",
        ],
    }


def run_real_historical_audit(output: Path, *,
                              archive_dir: Path | None = None,
                              fetch: bool = False,
                              min_ply: int = 17) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite real-corpus audit")
    if not output.parent.is_dir():
        raise ValueError("Output parent directory must exist")
    archives = {
        name: _read_archive(name, archive_dir=archive_dir, fetch=fetch)
        for name in sorted(PLAYER_FILES)
    }
    report = audit_historical_archives(archives, min_ply=min_ply)
    # Fail closed before publication. One report, no copied PGN data.
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2, ensure_ascii=False)
        stream.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Real PGN Mentor historical cohort analysis, 4 players and >=6500 games")
    paths = parser.add_mutually_exclusive_group(required=True)
    paths.add_argument("--fetch", action="store_true",
                       help="download predeclared public PGN Mentor archives")
    paths.add_argument("--archive-dir", type=Path,
                       help="use already-downloaded Fischer/Tal/Karpov/Kasparov.zip")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-ply", type=int, default=17)
    args = parser.parse_args(argv)
    report = run_real_historical_audit(
        args.out, archive_dir=args.archive_dir, fetch=args.fetch,
        min_ply=args.min_ply)
    print(json.dumps({
        "status": report["status"],
        "total_unique_cohort_games": report["total_unique_cohort_games"],
        "total_training_games": report["total_training_games"],
        "per_player_games": {
            name: data["unique_games"] for name, data in report["sources"].items()},
        "shared_fen_pairs": report["shared_fen_pairs"],
        "report": str(args.out),
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
