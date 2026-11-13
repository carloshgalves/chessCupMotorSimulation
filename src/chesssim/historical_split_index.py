"""Offline historical *index-only* split preflight; no PGNs or engines.

This is a deterministic commitment over operator-declared game identities.
It is not a source-content verification, data-rights audit, sealed data access
boundary, style validation or proof of absence of training/test leakage.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

INDEX_SCHEMA = "chesssim-historical-game-index-v1"
REPORT_SCHEMA = "chesssim-historical-split-preflight-v1"
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_INDEX_GAMES = 10_000
MAX_INDEX_SOURCES = 32
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
TARGET = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
QUALITY_FIELDS = frozenset({
    "white_known", "black_known", "event_known", "time_control_known",
})
SOURCE_FIELDS = frozenset({
    "id", "sha256", "license_id", "license_evidence_uri",
})
GAME_FIELDS = frozenset({
    "mainline_sha256", "date", "source_ids", "metadata",
})
PARTITION_NAMES = ("train", "validation", "holdout")


def _digest(data: object) -> str:
    serialized = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def _field_string(value: object, field: str, limit: int = 512) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > limit or value != value.strip()):
        raise ValueError(f"{field} must be a bounded nonblank string")
    return value


def _identity(value: object, field: str, pattern: re.Pattern) -> str:
    value = _field_string(value, field, 64)
    if pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid {field}")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise ValueError(f"Invalid SHA-256 for {field}")
    return value


def _date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or ISO_DATE.fullmatch(value) is None:
        raise ValueError("Game date must be full ISO YYYY-MM-DD or null")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid calendar date") from exc
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical game date")
    return value


def _sources(raw: object) -> list[dict]:
    if (not isinstance(raw, list) or
            not 1 <= len(raw) <= MAX_INDEX_SOURCES):
        raise ValueError("sources must be a nonempty bounded list")
    normalized = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != SOURCE_FIELDS:
            raise ValueError("Invalid source declaration fields")
        sid = _identity(item["id"], "source id", IDENTIFIER)
        if sid in seen:
            raise ValueError("Duplicate source id")
        seen.add(sid)
        digest = _sha(item["sha256"], "source")
        license_id = _field_string(item["license_id"], "license id", 64)
        evidence = _field_string(
            item["license_evidence_uri"], "license evidence uri", 512)
        if not evidence.startswith("https://"):
            raise ValueError("License evidence must have an HTTPS URI")
        normalized.append({
            "id": sid, "sha256": digest, "license_id": license_id,
            "license_evidence_uri": evidence,
        })
    return sorted(normalized, key=lambda row: row["id"])


def _games(raw: object, valid_sources: set[str]) -> list[dict]:
    if (not isinstance(raw, list) or
            not 1 <= len(raw) <= MAX_INDEX_GAMES):
        raise ValueError("games must be a nonempty bounded list")
    normalized = []
    seen = set()
    for row in raw:
        if not isinstance(row, dict) or set(row) != GAME_FIELDS:
            raise ValueError("Invalid game index fields")
        digest = _sha(row["mainline_sha256"], "game mainline")
        if digest in seen:
            # Fail closed rather than counting duplicates as independent games.
            raise ValueError("Duplicate game mainline digest")
        seen.add(digest)
        played = _date(row["date"])
        refs = row["source_ids"]
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or ref not in valid_sources
                       for ref in refs)
                or refs != sorted(set(refs))):
            raise ValueError("Source references must be known, unique and sorted")
        quality = row["metadata"]
        if (not isinstance(quality, dict) or set(quality) != QUALITY_FIELDS
                or any(type(value) is not bool for value in quality.values())):
            raise ValueError("Metadata quality must contain four boolean fields")
        normalized.append({
            "mainline_sha256": digest, "date": played,
            "source_ids": refs[:],
            "metadata": {key: quality[key] for key in sorted(QUALITY_FIELDS)},
        })
    return sorted(
        normalized, key=lambda row: (row["date"] or "9999-99-99",
                                     row["mainline_sha256"]))


def _split_by_year(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["date"][:4]), []).append(row)
    count = len(rows)
    train_target = max(1, count * 60 // 100) if count else 0
    validation_target = max(1, count * 20 // 100) if count >= 3 else 0
    result: dict[str, list[dict]] = {arm: [] for arm in PARTITION_NAMES}
    for year in sorted(grouped):
        if len(result["train"]) < train_target:
            arm = "train"
        elif len(result["validation"]) < validation_target:
            arm = "validation"
        else:
            arm = "holdout"
        result[arm].extend(grouped[year])
    return result


def _partition_report(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda row: (row["date"], row["mainline_sha256"]))
    years = sorted({int(row["date"][:4]) for row in rows})
    # Only aggregate metadata and a cryptographic commitment, no game IDs.
    commitment = [{"date": row["date"],
                   "mainline_sha256": row["mainline_sha256"]} for row in rows]
    return {
        "games": len(rows),
        "years": years,
        "first_date": rows[0]["date"] if rows else None,
        "last_date": rows[-1]["date"] if rows else None,
        "game_commitment_sha256": _digest(commitment),
    }


def build_historical_split_manifest(document: dict) -> dict:
    """Partition a declared game index by whole calendar years.

    The game IDs are precomputed and *not* independently corroborated against
    raw PGNs. Input metadata/rights remain unverified operator declarations.
    Output hides the raw training/validation/holdout identifiers.
    """
    if (not isinstance(document, dict) or set(document) != {
            "schema", "target_slug", "sources", "games"}):
        raise ValueError("Game index must contain exactly the declared fields")
    if document["schema"] != INDEX_SCHEMA:
        raise ValueError("Unsupported historical index schema")
    slug = _identity(document["target_slug"], "target slug", TARGET)
    sources = _sources(document["sources"])
    all_games = _games(document["games"], {row["id"] for row in sources})
    quality = Counter()
    for game in all_games:
        if game["date"] is None:
            quality["unknown_date"] += 1
        for flag, label in (
                ("white_known", "missing_white_name"),
                ("black_known", "missing_black_name"),
                ("event_known", "missing_event"),
                ("time_control_known", "missing_time_control")):
            if not game["metadata"][flag]:
                quality[label] += 1
    quality_counts = {
        field: quality[field] for field in (
            "unknown_date", "missing_white_name", "missing_black_name",
            "missing_event", "missing_time_control")
    }
    eligible = [row for row in all_games if row["date"] is not None]
    splits = _split_by_year(eligible)
    partition_data = {
        arm: _partition_report(splits[arm]) for arm in PARTITION_NAMES
    }
    sufficient = all(splits[arm] for arm in PARTITION_NAMES)
    return {
        "schema": REPORT_SCHEMA,
        "status": ("INDEXED_NOT_SOURCE_VERIFIED" if sufficient
                   else "INSUFFICIENT_PERIOD_COVERAGE"),
        "target_slug": slug,
        "input_evidence_sha256": _digest({
            "schema": INDEX_SCHEMA, "target_slug": slug,
            "sources": sources, "games": all_games,
        }),
        "source_provenance": "OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED",
        "source_declarations": sources,
        "partition_policy": (
            "CHRONOLOGICAL_WHOLE_CALENDAR_YEAR_60_20_20_TARGETS"),
        "game_index_count": len(all_games),
        "eligible_dated_games": len(eligible),
        "quarantined_unknown_date": quality["unknown_date"],
        "metadata_quality": quality_counts,
        "partitions": partition_data,
        "embargo_status": "NOT_VERIFIED_BY_THIS_INDEX",
        "reserved_game_ids_exported": False,
        "limitations": [
            "Source rights and game identities are not independently verified.",
            "Game hashes are operator supplied; raw PGN bytes are never checked.",
            "Year-group partition commitments are not filesystem access controls.",
            "This does not verify training, validation, holdout or legacy embargo.",
            "No reserved identities, PGNs, engine decisions or style validation.",
            "No model selection, tuning or final-test evaluation is permitted.",
        ],
    }


def _read_bounded_json(path: Path) -> tuple[dict, bytes]:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Symbolic paths are not allowed")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise ValueError("Unable to open historical index") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INDEX_BYTES:
            raise ValueError("Historical index must be a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            payload = stream.read(MAX_INDEX_BYTES + 1)
    finally:
        if fd is not None:
            os.close(fd)
    if len(payload) > MAX_INDEX_BYTES:
        raise ValueError("Historical index exceeds byte budget")

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key in game index")
            result[key] = value
        return result

    try:
        document = json.loads(payload, object_pairs_hook=unique_fields)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Historical game index JSON is invalid") from exc
    return document, payload


def run_historical_split_preflight(input_path: Path, output_path: Path, *,
                                   ack_unsealed: bool = False) -> dict:
    """Create a new offline report; never overwrite a prior report or input."""
    if ack_unsealed is not True:
        raise ValueError("Explicit unsealed-index acknowledgement required")
    source = Path(input_path)
    destination = Path(output_path)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to overwrite a historical split report")
    if any(parent.is_symlink() for parent in destination.parents):
        raise ValueError("Output cannot pass through symbolic directories")
    if not destination.parent.is_dir():
        raise ValueError("Output parent directory must already exist")

    document, payload = _read_bounded_json(source)
    report = build_historical_split_manifest(document)
    report["input_file_sha256"] = sha256(payload).hexdigest()
    rendered = json.dumps(
        report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(rendered)
    return report
