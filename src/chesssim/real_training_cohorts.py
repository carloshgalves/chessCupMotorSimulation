"""Reproducible player/year training records from ACTUAL historical PGNs.

Only the training arm is exported. Validation/holdout decisions never enter
exported JSONL. This builds datasets; it does not train an engine or certify
historical style. The public snapshot is explicitly pinned.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import chess
import chess.pgn

from .catalog import load_roster
from .game_diversity import mainline_identity
from .historical_real_corpus import (
    MAX_GAMES_PER_COHORT, MAX_PGN_BYTES, MAX_PLIES, MIRROR_COMMIT,
    SOURCE_BASE, _decode_pgn, _extract_pgn, _played_year,
)
from .historical_split_index import _split_by_year

SCHEMA = "chesssim-real-player-year-train-v1"
FILE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,50}\Z")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
ECO = re.compile(r"[A-E][0-9]{2}\Z")
SOURCE_EXCEPTIONS = {
    "ding-liren": "Ding",
    "vachier-lagrave": "VachierLagrave",
    "wesley-so": "So",
    "judit-polgar": "Polgar",
    "hou-yifan": "Hou",
    "mequinho": "Mecking",
}
DEFAULT_PLAYERS = ("fischer", "tal", "karpov", "kasparov")
ARMS = ("train", "validation", "holdout")


def source_file_for_slug(slug: str) -> str:
    """Candidate PGN Mentor name, not proof that the source exists."""
    return SOURCE_EXCEPTIONS.get(slug, slug.replace("-", "").capitalize())


def _norm(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _target_color(game: chess.pgn.Game, surname: str) -> bool | None:
    pattern = re.compile(r"(?<![a-z])" + re.escape(_norm(surname)) + r"(?![a-z])")
    white = bool(pattern.search(_norm(game.headers.get("White", ""))))
    black = bool(pattern.search(_norm(game.headers.get("Black", ""))))
    return white if white != black else None


def _player_configuration(slugs: list[str], birthdates: dict | None,
                          source_files: dict | None) -> dict[str, dict]:
    roster = {p.slug: p for p in load_roster()}
    if not slugs or len(slugs) != len(set(slugs)) or any(s not in roster for s in slugs):
        raise ValueError("Select distinct players belonging to the historical roster")
    if birthdates is not None and (
            not isinstance(birthdates, dict) or not set(birthdates) <= set(slugs)):
        raise ValueError("Birthdates must map only selected roster slugs")
    if source_files is not None and (
            not isinstance(source_files, dict) or not set(source_files) <= set(slugs)):
        raise ValueError("Source files must map only selected roster slugs")
    result = {}
    for slug in slugs:
        player = roster[slug]
        source = (source_files or {}).get(slug, source_file_for_slug(slug))
        if not isinstance(source, str) or FILE_NAME.fullmatch(source) is None:
            raise ValueError("Invalid PGN Mentor source filename")
        # Focal surname is deliberately taken from the historical roster.
        # The surname-only association is reported as a limitation.
        surname = player.name.split()[-1]
        if slug == "vachier-lagrave":
            surname = "Lagrave"
        if slug == "ding-liren":
            surname = "Ding"
        if slug == "hou-yifan":
            surname = "Hou"
        birth = (birthdates or {}).get(slug)
        if birth is not None:
            if not isinstance(birth, str) or ISO_DATE.fullmatch(birth) is None:
                raise ValueError("Birthdate must be an exact ISO date")
            try:
                date.fromisoformat(birth)
            except ValueError as exc:
                raise ValueError("Invalid player birthdate") from exc
        result[slug] = {
            "name": player.name, "group": player.group, "surname": surname,
            "source": source, "birth_date": birth,
        }
    return result


def _source_bytes(source: str, archive_dir: Path | None) -> tuple[bytes, bytes, str]:
    if archive_dir is None:
        url = SOURCE_BASE + source + ".pgn"
        req = Request(url, headers={"User-Agent": "ChessSimRealTraining/1.0"})
        with urlopen(req, timeout=45) as response:
            final = urlsplit(response.url)
            wanted = "/rozim/ChessData/" + MIRROR_COMMIT + "/PgnMentor/" + source + ".pgn"
            if (final.scheme != "https" or final.hostname != "raw.githubusercontent.com"
                    or final.path != wanted or final.username or final.password
                    or final.query or final.fragment):
                raise ValueError("Unexpected PGN source redirect")
            blob = response.read(MAX_PGN_BYTES + 1)
        origin = url
    else:
        directory = Path(archive_dir)
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ValueError("Symbolic PGN directory not supported")
        raw = directory / (source + ".pgn")
        zipped = directory / (source + ".zip")
        found = raw if raw.is_file() or raw.is_symlink() else zipped
        if found.is_symlink() or not found.is_file() or found.stat().st_size > MAX_PGN_BYTES:
            raise ValueError("Required local PGN or ZIP missing or oversized")
        blob = found.read_bytes()
        origin = "LOCAL_OPERATOR_SUPPLIED:" + found.name
    if not blob or len(blob) > MAX_PGN_BYTES:
        raise ValueError("Invalid PGN source byte size")
    pgn = _extract_pgn(source, blob)
    return blob, pgn, origin


def _scan(pgn: bytes, slug: str, surname: str) -> tuple[list[dict], dict]:
    decoded, encoding = _decode_pgn(pgn)
    rows = []
    seen = set()
    counters = Counter()
    reader = StringIO(decoded)
    while True:
        game = chess.pgn.read_game(reader)
        if game is None:
            break
        counters["pgn_records"] += 1
        if counters["pgn_records"] > MAX_GAMES_PER_COHORT:
            raise ValueError("Too many games in one source")
        if game.errors:
            counters["invalid_pgn"] += 1
            continue
        color = _target_color(game, surname)
        if color is None:
            counters["identity_unresolved"] += 1
            continue
        digest, plies = mainline_identity(game)
        if not 1 <= plies <= MAX_PLIES:
            counters["invalid_length"] += 1
            continue
        if digest in seen:
            counters["duplicate_trajectory"] += 1
            continue
        seen.add(digest)
        year, precision = _played_year(game.headers.get("Date", ""))
        if year is None:
            counters["unknown_year"] += 1
        if precision == "year_only":
            counters["year_only"] += 1
        rows.append({
            "mainline_sha256": digest,
            "date": year, "raw_date": game.headers.get("Date", ""),
            "color": "white" if color == chess.WHITE else "black",
            "plies": plies,
            "result": game.headers.get("Result", "*"),
            "eco": game.headers.get("ECO", ""),
            "opponent": game.headers.get("Black" if color else "White", "?"),
            "slug": slug,
        })
    return rows, {"encoding": encoding, **dict(counters)}


def _assign(rows: list[dict]) -> dict:
    dated = [r for r in rows if r["date"] is not None]
    parts = _split_by_year(dated)
    for arm, subset in parts.items():
        for row in subset:
            row["arm"] = arm
    for row in rows:
        if row["date"] is None:
            row["arm"] = "unknown"
    return {
        arm: {"games": len(parts[arm]),
              "years": sorted({int(r["date"]) for r in parts[arm]})}
        for arm in ARMS
    }


def _iter_verified_games(pgn: bytes, rows: list[dict], surname: str):
    """Read a second time, matching exact identity and filtering consistently."""
    index = {r["mainline_sha256"]: r for r in rows}
    emitted = set()
    text, _ = _decode_pgn(pgn)
    stream = StringIO(text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        if game.errors or _target_color(game, surname) is None:
            continue
        digest, plies = mainline_identity(game)
        if not 1 <= plies <= MAX_PLIES or digest in emitted or digest not in index:
            continue
        emitted.add(digest)
        yield game, index[digest]
    if emitted != set(index):
        raise ValueError("PGN content changed between index and training passes")


def _focal_decisions(game: chess.pgn.Game, color: str, min_ply: int):
    board = game.board()
    target = chess.WHITE if color == "white" else chess.BLACK
    for ply, move in enumerate(game.mainline_moves(), start=1):
        if ply >= min_ply and board.turn == target:
            yield ply, board.fen(), move.uci()
        board.push(move)


def _phase(ply: int) -> str:
    # Explicit move-number proxy, not an engine-certified phase classifier.
    return "opening_proxy" if ply <= 16 else "middle_proxy" if ply <= 60 else "late_proxy"


def _career_band(year: int, years: list[int]) -> str:
    if len(years) < 3:
        return "insufficient_year_coverage"
    first, last = years[0], years[-1]
    bucket = min(2, 3 * (year - first) // (last - first + 1))
    return ("early_observed", "middle_observed", "late_observed")[bucket]


def _exact_age(birth: str | None, raw_date: str) -> int | None:
    if not birth or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", raw_date):
        return None
    try:
        played = date(*(int(piece) for piece in raw_date.split(".")))
        born = date.fromisoformat(birth)
    except ValueError:
        return None
    if played < born:
        return None
    return played.year - born.year - ((played.month, played.day) <
                                     (born.month, born.day))


def _focus_result(result: str, color: str) -> str:
    if result == "1/2-1/2":
        return "draw"
    if result == "1-0":
        return "win" if color == "white" else "loss"
    if result == "0-1":
        return "win" if color == "black" else "loss"
    return "unknown"


def _fen_digest(fen: str) -> str:
    return sha256(fen.encode("utf-8")).hexdigest()


def build_real_training_cohorts(out: Path, *, players: list[str] | None = None,
                                archive_dir: Path | None = None,
                                allow_missing: bool = False,
                                min_ply: int = 17,
                                birthdates: dict | None = None,
                                source_files: dict | None = None) -> dict:
    """Export only real train examples, grouped by focal player and exact year.

    Source missing can be skipped only on explicit opt-in. Unresolved players,
    dates, cross-arm trajectories and reserved FENs never enter training.
    The embargo protects exact full FENs, not strategic similarity.
    """
    if type(min_ply) is not int or not 1 <= min_ply <= MAX_PLIES:
        raise ValueError("min_ply must be 1..500")
    config = _player_configuration(list(players or DEFAULT_PLAYERS),
                                   birthdates, source_files)
    out = Path(out).expanduser()
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Output must be a NEW directory under an existing parent")
    if any(p.is_symlink() for p in (out.parent, *out.parent.parents)):
        raise ValueError("Output parent must not be symbolic")
    stage = Path(tempfile.mkdtemp(prefix=".chesssim-real-train-", dir=out.parent))
    try:
        cache_dir = stage / ".sources"
        cache_dir.mkdir(mode=0o700)
        rows_by_slug = {}
        partitions = {}
        source_summary = {}
        skipped = {}
        # Phase 1: all source bytes are pinned/cached before partitioning.
        for slug, cfg in sorted(config.items()):
            try:
                blob, pgn, origin = _source_bytes(cfg["source"], archive_dir)
            except HTTPError as exc:
                if allow_missing and exc.code == 404:
                    skipped[slug] = "NOT_PRESENT_IN_PINNED_SNAPSHOT"
                    continue
                raise
            except ValueError as exc:
                if allow_missing and "Required local PGN or ZIP missing" in str(exc):
                    skipped[slug] = "LOCAL_SOURCE_MISSING"
                    continue
                raise
            (cache_dir / (slug + ".pgn")).write_bytes(pgn)
            rows, counters = _scan(pgn, slug, cfg["surname"])
            if not rows:
                raise ValueError("No verified focal games for " + slug)
            rows_by_slug[slug] = rows
            partitions[slug] = _assign(rows)
            source_summary[slug] = {
                "source": cfg["source"] + ".pgn", "origin": origin,
                "download_sha256": sha256(blob).hexdigest(),
                "pgn_sha256": sha256(pgn).hexdigest(), "accepted_games": len(rows),
                "counters": counters,
            }
        if not rows_by_slug:
            raise ValueError("No real player PGNs were processed")

        # The same trajectory may occur in two player archives. If it belongs
        # to ANY different arms, embargo it globally rather than training it.
        arms_by_digest = defaultdict(set)
        for rows in rows_by_slug.values():
            for row in rows:
                arms_by_digest[row["mainline_sha256"]].add(row["arm"])
        conflicting = {key for key, arms in arms_by_digest.items() if len(arms) > 1}
        global_distinct_trajectories = len(arms_by_digest)
        for rows in rows_by_slug.values():
            for row in rows:
                if row["mainline_sha256"] in conflicting:
                    row["blocked_cross_arm"] = True

        db = sqlite3.connect(stage / ".fen-embargo.sqlite3")
        try:
            db.execute("CREATE TABLE reserved (digest TEXT PRIMARY KEY)")
            # Phase 2: build exact-FEN embargo from every non-train decision.
            for slug, rows in rows_by_slug.items():
                pgn = (cache_dir / (slug + ".pgn")).read_bytes()
                for game, row in _iter_verified_games(pgn, rows, config[slug]["surname"]):
                    if row["arm"] == "train" and not row.get("blocked_cross_arm"):
                        continue
                    db.executemany(
                        "INSERT OR IGNORE INTO reserved(digest) VALUES (?)",
                        ((_fen_digest(fen),) for _, fen, _ in
                         _focal_decisions(game, row["color"], min_ply)))
                db.commit()
            reserved_fens = db.execute("SELECT COUNT(*) FROM reserved").fetchone()[0]

            # Phase 3: train-only examples. Never write reserved PGNs or moves.
            train_root = stage / "train"
            train_root.mkdir()
            player_reports = {}
            total_examples = 0
            for slug, rows in sorted(rows_by_slug.items()):
                cfg = config[slug]
                dated_years = sorted({int(r["date"]) for r in rows if r["date"]})
                metrics = Counter()
                by_year = defaultdict(Counter)
                for game, row in _iter_verified_games(
                        (cache_dir / (slug + ".pgn")).read_bytes(),
                        rows, cfg["surname"]):
                    if row["arm"] != "train":
                        continue
                    if row.get("blocked_cross_arm"):
                        metrics["blocked_cross_arm_games"] += 1
                        continue
                    year = int(row["date"])
                    accepted = []
                    for ply, fen, move in _focal_decisions(game, row["color"], min_ply):
                        if db.execute("SELECT 1 FROM reserved WHERE digest=?",
                                      (_fen_digest(fen),)).fetchone() is not None:
                            metrics["blocked_reserved_fen_decisions"] += 1
                            by_year[year]["blocked_reserved_fen_decisions"] += 1
                            continue
                        eco = row["eco"] if ECO.fullmatch(row["eco"]) else None
                        example = {
                            "schema": SCHEMA, "player": slug, "name": cfg["name"],
                            "source_sha256": source_summary[slug]["pgn_sha256"],
                            "game_sha256": row["mainline_sha256"],
                            "year": year, "career_band": _career_band(year, dated_years),
                            "age_at_game": _exact_age(cfg["birth_date"], row["raw_date"]),
                            "color": row["color"], "opponent": row["opponent"],
                            "result_for_player": _focus_result(row["result"], row["color"]),
                            "eco": eco, "ply": ply, "phase_proxy": _phase(ply),
                            "fen": fen, "move_uci": move,
                        }
                        accepted.append(json.dumps(example, ensure_ascii=False,
                                                   sort_keys=True, separators=(",", ":")))
                        metrics["training_decisions"] += 1
                        by_year[year]["training_decisions"] += 1
                        metrics["color_" + row["color"]] += 1
                        metrics["result_" + example["result_for_player"]] += 1
                        metrics["phase_" + example["phase_proxy"]] += 1
                    metrics["training_games_selected"] += 1
                    by_year[year]["training_games_selected"] += 1
                    if accepted:
                        player_dir = train_root / slug
                        player_dir.mkdir(exist_ok=True)
                        with (player_dir / (str(year) + ".jsonl")).open(
                                "a", encoding="utf-8") as stream:
                            stream.write("\n".join(accepted) + "\n")
                total_examples += metrics["training_decisions"]
                player_reports[slug] = {
                    "name": cfg["name"], "group": cfg["group"],
                    "birth_date_declared": cfg["birth_date"],
                    "partition_by_whole_year": partitions[slug],
                    "undated_quarantined": sum(r["date"] is None for r in rows),
                    "metrics": dict(sorted(metrics.items())),
                    "by_year": {str(year): dict(sorted(data.items()))
                                for year, data in sorted(by_year.items())},
                }
        finally:
            db.close()

        # Remove original PGNs and reserved-position hashes from published data.
        shutil.rmtree(cache_dir)
        (stage / ".fen-embargo.sqlite3").unlink()
        report = {
            "schema": SCHEMA, "status": "REAL_PGN_TRAIN_ONLY_DATASET_NOT_MODEL_VALIDATED",
            "snapshot_commit": MIRROR_COMMIT if archive_dir is None else None,
            "min_ply": min_ply,
            "partition_policy": "PLAYER_WHOLE_YEAR_60_20_20_TARGET_WITH_GLOBAL_TRAJECTORY_AND_EXACT_FEN_EMBARGO",
            "sources": source_summary, "players": player_reports,
            "missing_sources": skipped,
            "accepted_player_game_entries": sum(len(rows) for rows in rows_by_slug.values()),
            "globally_distinct_move_trajectories": global_distinct_trajectories,
            "cross_arm_conflicting_trajectories": len(conflicting),
            "reserved_full_fens": reserved_fens,
            "training_examples_exported": total_examples,
            "training_files": sorted(str(p.relative_to(stage))
                                     for p in (stage / "train").rglob("*.jsonl")),
            "holdout_moves_exported": False, "validation_moves_exported": False,
            "notes": [
                "Focal identity is derived from a declared surname token in PGN headers; ambiguous matches are excluded.",
                "Career stage is the observed dataset year-span tercile, not a biographical claim.",
                "Phase labels are ply-number proxies, not Stockfish positional classifications.",
                "Only exact full FEN equality is embargoed; tactical/semantic analogues can remain.",
                "Same-trajectory digests may be different historical games with identical moves.",
                "DOB is optional declared metadata; age remains null without a complete game date.",
                "The output fits training inputs; fitting, engine predictions and scientific validation are separate.",
            ],
        }
        with (stage / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if out.exists():
            raise FileExistsError("Training output already exists")
        os.rename(stage, out)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Real PGN train-only cohorts grouped by player/year")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true",
                        help="download pinned public PGN snapshot")
    source.add_argument("--archive-dir", type=Path, help="use existing PGNs/ZIPs")
    selected = parser.add_mutually_exclusive_group()
    selected.add_argument("--players", nargs="+", help="roster slugs")
    selected.add_argument("--all-roster", action="store_true", help="attempt all 64")
    parser.add_argument("--allow-missing", action="store_true",
                        help="skip sources absent from the pinned snapshot (404)")
    parser.add_argument("--birthdates-json", type=Path, help="optional declared ISO DOB mapping")
    parser.add_argument("--source-files-json", type=Path,
                        help="optional source filename mapping by roster slug")
    parser.add_argument("--min-ply", type=int, default=17)
    parser.add_argument("--out", type=Path, required=True,
                        help="new output directory, never an existing directory")
    args = parser.parse_args(argv)
    slugs = ([p.slug for p in load_roster()] if args.all_roster
             else args.players or list(DEFAULT_PLAYERS))
    birthdates = (json.loads(args.birthdates_json.read_text(encoding="utf-8"))
                  if args.birthdates_json else None)
    files = (json.loads(args.source_files_json.read_text(encoding="utf-8"))
             if args.source_files_json else None)
    report = build_real_training_cohorts(
        args.out, players=slugs, archive_dir=args.archive_dir,
        allow_missing=args.allow_missing, birthdates=birthdates,
        source_files=files, min_ply=args.min_ply)
    print(json.dumps({
        "status": report["status"],
        "accepted_player_game_entries": report["accepted_player_game_entries"],
        "globally_distinct_move_trajectories": report["globally_distinct_move_trajectories"],
        "cross_arm_conflicting_trajectories": report["cross_arm_conflicting_trajectories"],
        "reserved_full_fens": report["reserved_full_fens"],
        "training_examples_exported": report["training_examples_exported"],
        "players": sorted(report["players"]),
        "missing_sources": report["missing_sources"],
        "manifest": str(args.out / "manifest.json"),
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
