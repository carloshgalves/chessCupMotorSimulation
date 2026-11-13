"""Fail-closed, training-only PGN verification against a declared split index.

Only the explicitly supplied *training* PGN is read. Validation/holdout are
never opened. Source rights, operator identity, and scientific embargo cannot
be verified by matching local training bytes to a private index.
"""
from __future__ import annotations

from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import re
import stat
import tempfile

import chess.pgn

from .game_diversity import mainline_identity
from .historical_split_index import (
    _games, _read_bounded_json, _sources, _split_by_year,
    build_historical_split_manifest,
)
from .licensed_pgn_cache import _ResultConsistentGameBuilder, _strict_movetext

SCHEMA = "chesssim-historical-train-pgn-verification-v1"
MAX_TRAIN_PGN_BYTES = 8 * 1024 * 1024
MAX_TRAIN_GAMES = 10_000
PGN_DATE = re.compile(r"\d{4}\.\d{2}\.\d{2}\Z")
FINAL_RESULT = re.compile(r"(?:^|\s)(?:1-0|0-1|1/2-1/2|\*)\s*\Z")
ALLOWED_TRAIN_HEADERS = frozenset({
    "Event", "Site", "Date", "Round", "White", "Black", "Result",
    "TimeControl", "SetUp", "FEN", "Variant",
})
MAX_TRAIN_HEADER_VALUE = 512


def _require_sanitized_headers(game: chess.pgn.Game) -> None:
    """Reject uncommitted header channels absent from mainline_identity()."""
    if any(tag not in ALLOWED_TRAIN_HEADERS for tag in game.headers):
        raise ValueError("Training PGN contains unsupported header tags")
    if any(not isinstance(value, str) or len(value) > MAX_TRAIN_HEADER_VALUE
           or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
           for value in game.headers.values()):
        raise ValueError("Training PGN contains invalid or oversized headers")
    setup = game.headers.get("SetUp")
    if setup is not None and setup not in {"0", "1"}:
        raise ValueError("Training PGN has invalid SetUp header")
    if ("FEN" in game.headers) != (setup == "1"):
        raise ValueError("Training PGN SetUp/FEN declaration inconsistent")


def _metadata_quality(game: chess.pgn.Game) -> dict[str, bool]:
    def present(header: str) -> bool:
        raw = game.headers.get(header, "")
        return bool(raw.strip()) and raw.strip() not in {"?", "??"}

    return {
        "white_known": present("White"),
        "black_known": present("Black"),
        "event_known": present("Event"),
        "time_control_known": present("TimeControl"),
    }


def _result_terminates_game(segment: str) -> bool:
    """Check the result token in *each* PGN game, never just in its header.

    python-chess accepts a game with a Result header but no movetext
    terminator. Header lines and comments may contain result-like strings:
    strip them before checking the final movetext token.
    """
    lines = [line for line in segment.splitlines()
             if not line.lstrip().startswith("[")]
    text = "\n".join(lines)
    text = re.sub(r"(?m)^\s*%[^\n]*", " ", text)
    text = re.sub(r";[^\n]*", " ", text)
    text = re.sub(r"\{[^{}]*\}", " ", text, flags=re.S)
    return FINAL_RESULT.search(text) is not None


def _require_mainline_only(pgn_text: str) -> None:
    """No comments, alternate variations, NAGs or PGN escape lines.

    These can contain data not reflected by mainline_identity(), potentially
    including moves from reserved cohorts. This train-only verifier must not
    silently accept them simply because the hashed mainline is unchanged.
    Header values are excluded from this lexical scan.
    """
    movetext = "\n".join(
        line for line in pgn_text.splitlines()
        if not line.lstrip().startswith("["))
    if any(marker in movetext for marker in "{}();$%!?"):
        raise ValueError("Training PGN must contain mainline moves only")


def _training_index(index: dict) -> tuple[dict, dict[str, dict]]:
    # Validate full *index metadata*, not raw reserved moves or FENs.
    manifest = build_historical_split_manifest(index)
    if manifest["status"] != "INDEXED_NOT_SOURCE_VERIFIED":
        raise ValueError("Historical index lacks all three nonempty partitions")
    sources = _sources(index["sources"])
    entries = _games(index["games"], {row["id"] for row in sources})
    dated = [row for row in entries if row["date"] is not None]
    split = _split_by_year(dated)
    training = {row["mainline_sha256"]: row for row in split["train"]}
    if not training:
        raise ValueError("Historical training partition is empty")
    return manifest, training


def _verify_operator_pins(manifest: dict, *,
                          expected_index_evidence_sha256: str | None,
                          expected_train_partition_sha256: str | None) -> dict | None:
    """Check preapproved commitments without authenticating their origin."""
    values = (expected_index_evidence_sha256, expected_train_partition_sha256)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("Both pinned index and training commitments are required")
    for value in values:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Invalid pinned SHA-256 commitment")
    expected = {
        "expected_index_evidence_sha256": manifest["input_evidence_sha256"],
        "expected_train_partition_sha256": (
            manifest["partitions"]["train"]["game_commitment_sha256"]),
    }
    if (values[0] != expected["expected_index_evidence_sha256"]
            or values[1] != expected["expected_train_partition_sha256"]):
        raise ValueError("Historical index differs from pinned approved commitments")
    return {
        "status": "MATCHED_OPERATOR_SUPPLIED_PINS_NOT_AUTHENTICATED",
        "index_evidence_sha256": values[0],
        "train_partition_sha256": values[1],
        "verified_before_training_pgn_read": True,
    }


def verify_historical_training_pgn(
        index: dict, pgn_bytes: bytes, *,
        expected_index_evidence_sha256: str | None = None,
        expected_train_partition_sha256: str | None = None) -> dict:
    """Verify exact membership, dates, and quality flags for training only.

    Failures are intentionally nonspecific: no raw PGN/FEN, reserved digest,
    individual match identifier or historical move is included in output.
    """
    manifest, expected = _training_index(index)
    pins = _verify_operator_pins(
        manifest,
        expected_index_evidence_sha256=expected_index_evidence_sha256,
        expected_train_partition_sha256=expected_train_partition_sha256)
    if (not isinstance(pgn_bytes, bytes) or not pgn_bytes
            or len(pgn_bytes) > MAX_TRAIN_PGN_BYTES or b"\0" in pgn_bytes):
        raise ValueError("Training PGN must be nonempty bounded text")
    try:
        decoded = pgn_bytes.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Training PGN must use UTF-8") from exc
    if FINAL_RESULT.search(decoded) is None:
        raise ValueError("Training PGN must end with an explicit result")
    # The python-chess parser otherwise discards non-SAN garbage. Reject it.
    # Training input must also carry no ignored side-variation/comment data.
    _require_mainline_only(decoded)
    _strict_movetext(decoded)

    stream = StringIO(decoded)
    observed: set[str] = set()
    while True:
        starting_offset = stream.tell()
        try:
            game = chess.pgn.read_game(
                stream, Visitor=_ResultConsistentGameBuilder)
        except (ValueError, AssertionError) as exc:
            raise ValueError("Invalid or unsupported training PGN") from exc
        if game is None:
            break
        if not _result_terminates_game(
                decoded[starting_offset:stream.tell()]):
            raise ValueError("Training PGN game has no explicit result token")
        if game.errors or game.headers.get("Variant", "Standard") not in {
                "Standard", "Chess"}:
            raise ValueError("Invalid training PGN game or variant")
        _require_sanitized_headers(game)
        identity, plies = mainline_identity(game)
        if plies < 1 or len(observed) >= MAX_TRAIN_GAMES:
            raise ValueError("Empty or over-budget training PGN game")
        if identity in observed:
            raise ValueError("Duplicate training trajectory")
        record = expected.get(identity)
        if record is None:
            # Could be a holdout, an extra game, or tampered data: never reveal.
            raise ValueError("Training PGN does not match its declared partition")
        date_text = game.headers.get("Date", "")
        if not isinstance(date_text, str) or PGN_DATE.fullmatch(date_text) is None:
            raise ValueError("Training game must have a full calendar date")
        if date_text.replace(".", "-") != record["date"]:
            raise ValueError("Training game date disagrees with index")
        if _metadata_quality(game) != record["metadata"]:
            raise ValueError("Training game metadata flags disagree with index")
        observed.add(identity)

    if len(observed) != len(expected):
        raise ValueError("Training PGN is missing indexed games")
    report = {
        "schema": SCHEMA,
        "status": "TRAIN_PARTITION_MATCHED_SOURCE_NOT_VERIFIED",
        "index_evidence_sha256": manifest["input_evidence_sha256"],
        "train_partition_commitment_sha256": (
            manifest["partitions"]["train"]["game_commitment_sha256"]),
        "train_pgn_sha256": sha256(pgn_bytes).hexdigest(),
        "training_games_verified": len(observed),
        "validated_partitions": ["train"],
        "reserved_partitions_opened": False,
        "source_rights_status": "OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED",
        "embargo_status": "RESERVED_GAMES_NOT_OPENED_EMBARGO_NOT_PROVED",
        "limitations": [
            "Only training PGN bytes and declared game membership were checked.",
            "The index and game source may have been forged or modified together.",
            "Original source hashes, licensing and player identity remain unverified.",
            "No validation/holdout PGNs or positions were opened or evaluated.",
            "No independence, transposition embargo or sealed filesystem gate proven.",
            "No historical personality, Elo, UCI strength or style certification.",
        ],
    }
    if pins is not None:
        report["pin_verification"] = pins
    return report


def _read_training_pgn(path: Path) -> bytes:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Training PGN cannot use symbolic paths")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise ValueError("Cannot securely open training PGN") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_TRAIN_PGN_BYTES:
            raise ValueError("Training PGN must be a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = None
            payload = stream.read(MAX_TRAIN_PGN_BYTES + 1)
    finally:
        if fd is not None:
            os.close(fd)
    if len(payload) > MAX_TRAIN_PGN_BYTES:
        raise ValueError("Training PGN exceeds file size limit")
    return payload


def run_historical_train_verification(index_path: Path, train_pgn_path: Path,
                                      output_path: Path, *,
                                      ack_train_only: bool = False,
                                      expected_index_evidence_sha256: str | None = None,
                                      expected_train_partition_sha256: str | None = None) -> dict:
    """Read index metadata + train-only file and publish a new atomic report."""
    if ack_train_only is not True:
        raise ValueError("Explicit train-only input acknowledgement required")
    index_path, train_pgn_path = Path(index_path), Path(train_pgn_path)
    output = Path(output_path)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite historical training report")
    if any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Output cannot traverse symbolic directories")
    if not output.parent.is_dir():
        raise ValueError("Output parent directory must already exist")
    index, index_raw = _read_bounded_json(index_path)
    # Validate the frozen index against external operator-supplied pins
    # *before* opening any PGN, to prevent even an unsuccessful exploratory
    # read under a drifted partition. A pin is not a digital signature.
    if (expected_index_evidence_sha256 is not None
            or expected_train_partition_sha256 is not None):
        _verify_operator_pins(
            build_historical_split_manifest(index),
            expected_index_evidence_sha256=expected_index_evidence_sha256,
            expected_train_partition_sha256=expected_train_partition_sha256)
    training_raw = _read_training_pgn(train_pgn_path)
    report = verify_historical_training_pgn(
        index, training_raw,
        expected_index_evidence_sha256=expected_index_evidence_sha256,
        expected_train_partition_sha256=expected_train_partition_sha256)
    report["index_file_sha256"] = sha256(index_raw).hexdigest()
    rendered = json.dumps(
        report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=".chesssim-historical-train-", suffix=".tmp", dir=output.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic exclusive publication (no overwrite, even across processes).
        os.link(temp, output)
    finally:
        if fd is not None:
            os.close(fd)
        temp.unlink(missing_ok=True)
    return report
