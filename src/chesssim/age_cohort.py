"""Offline exact-age research cohort from a locally rights-attested PGN cache.

This is a research preflight, NOT a historical personality or an engine profile.
Only a training PGN is materialized. Validation and holdout are registered
by digest/date, never exported as replayable PGNs in this artifact.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from hashlib import sha256
from io import StringIO
import json
import math
import os
from pathlib import Path
import re
import shutil

import chess.pgn

from .game_diversity import mainline_identity
from .licensed_pgn_cache import CACHE_SCHEMA, _regular
from .matches import _atomic_text

SCHEMA = "chesssim-exact-age-cohort-v1"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_GAMES = 1000
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
FULL_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\Z")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def _distinct_pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("Chave JSON duplicada no manifesto")
        output[key] = value
    return output


def _full_date(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    parts = FULL_DATE.fullmatch(raw)
    if parts is None:
        return None
    try:
        return date(*(int(item) for item in parts.groups()))
    except ValueError:
        return None


def _birth_date(raw: str) -> date:
    if not isinstance(raw, str) or ISO_DATE.fullmatch(raw) is None:
        raise ValueError("Nascimento precisa de data ISO completa AAAA-MM-DD")
    try:
        birth = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Data de nascimento inválida") from exc
    if birth > date.today():
        raise ValueError("Nascimento não pode ser futuro")
    return birth


def age_at_game(birth: date, played: date) -> int:
    """Completed civil years: Feb 29 birthdays turn older on Mar 1 off leap year."""
    if played < birth:
        raise ValueError("Partida anterior ao nascimento")
    return played.year - birth.year - (
        (played.month, played.day) < (birth.month, birth.day)
    )


def _normalize_name(name: object) -> str:
    if not isinstance(name, str) or len(name) > 150:
        raise ValueError("Nome do PGN inválido")
    normalized = " ".join(name.split()).casefold()
    if len(normalized) < 2 or normalized == "?":
        raise ValueError("Nome completo do PGN obrigatório")
    return normalized


def _header_name(value: object) -> str:
    # Unlike the explicitly selected identity, unrelated historical PGNs may
    # have unknown/missing player headers. Exclude, rather than crash or
    # accidentally match an alias/name fragment.
    if not isinstance(value, str) or len(value) > 150:
        return ""
    normalized = " ".join(value.split()).casefold()
    return normalized if normalized != "?" else ""


def _cache_snapshot(folder: Path) -> tuple[dict, list[tuple[chess.pgn.Game, str]]]:
    if folder.is_symlink() or any(p.is_symlink() for p in folder.parents):
        raise ValueError("Caminho simbólico do cache não permitido")
    # _regular also protects the nested files against symlink traversal.
    raw_manifest = _regular(folder / "manifest.json", MAX_MANIFEST_BYTES)
    try:
        report = json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_distinct_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Manifesto do cache inválido") from exc
    if (not isinstance(report, dict) or report.get("schema") != CACHE_SCHEMA
            or report.get("operation") != "APPLIED"
            or report.get("status") != "READY_FOR_LOCAL_CACHE"
            or report.get("rights_status") !=
            "OPERATOR_ATTESTED_NOT_INDEPENDENTLY_VERIFIED"):
        raise ValueError("Cache não é um pgn-cache aplicado com atestação de direitos")
    sources = report.get("sources")
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(s, dict) or
                   not isinstance(s.get("allowed_uses"), list) or
                   "derived_openings" not in s["allowed_uses"] or
                   not all(isinstance(use, str) for use in s["allowed_uses"])
                   for s in sources)):
        raise ValueError("Todos os PGNs precisam autorizar explicitamente derived_openings")
    source_ids = [s.get("id") for s in sources]
    if (any(not isinstance(source_id, str) or not source_id
            for source_id in source_ids) or
            len(source_ids) != len(set(source_ids))):
        raise ValueError("IDs de fontes do cache inválidos")
    source_ids = set(source_ids)
    games = report.get("games")
    if (not isinstance(games, list) or not 1 <= len(games) <= MAX_GAMES or
            type(report.get("games_distinct")) is not int
            or report["games_distinct"] != len(games)):
        raise ValueError("Quantidade de jogos do cache inválida")
    stored_hash = report.get("cache_pgn_sha256")
    if not isinstance(stored_hash, str) or not SHA256.fullmatch(stored_hash):
        raise ValueError("Hash do PGN de cache inválido")
    raw_games = _regular(folder / "games.pgn", MAX_CACHE_BYTES)
    if sha256(raw_games).hexdigest() != stored_hash:
        raise ValueError("Checksum de games.pgn diverge do manifesto")
    try:
        stream = StringIO(raw_games.decode("utf-8-sig"))
    except UnicodeError as exc:
        raise ValueError("PGN do cache não é UTF-8") from exc
    parsed = []
    seen = set()
    for row in games:
        if not isinstance(row, dict):
            raise ValueError("Registro de cache inválido")
        expected = row.get("mainline_sha256")
        source_refs = row.get("sources")
        if (not isinstance(expected, str) or not SHA256.fullmatch(expected)
                or expected in seen or type(row.get("plies")) is not int
                or row["plies"] <= 0 or row.get("result") not in
                {"1-0", "0-1", "1/2-1/2", "*"}
                or not isinstance(source_refs, list) or not source_refs
                or any(not isinstance(src, str) or src not in source_ids
                       for src in source_refs)
                or len(source_refs) != len(set(source_refs))):
            raise ValueError("Registro do manifesto não é verificável")
        game = chess.pgn.read_game(stream)
        if game is None or game.errors:
            raise ValueError("PGN ausente, inválido ou ilegal")
        if game.headers.get("Variant", "Standard") not in {"Standard", "Chess"}:
            raise ValueError("Somente xadrez padrão é elegível")
        digest, plies = mainline_identity(game)
        if (digest != expected or plies != row["plies"]
                or game.headers.get("Result") != row["result"]):
            raise ValueError("PGN e registro de cache divergem")
        seen.add(digest)
        parsed.append((game, digest))
    if chess.pgn.read_game(stream) is not None:
        raise ValueError("Há partidas extras sem registro no cache")
    return report, parsed


def _partition(rows: list[dict]) -> dict[str, list[dict]]:
    """Date-grouped chronological separation. No date crosses a partition."""
    groups = {}
    for item in rows:
        groups.setdefault(item["date"], []).append(item)
    count = len(rows)
    train_target = max(1, math.floor(count * 0.6)) if count else 0
    validation_target = max(1, math.floor(count * 0.2)) if count >= 3 else 0
    splits: dict[str, list[dict]] = {"train": [], "validation": [], "holdout": []}
    for played in sorted(groups):
        group = sorted(groups[played], key=lambda row: row["mainline_sha256"])
        if len(splits["train"]) < train_target:
            arm = "train"
        elif len(splits["validation"]) < validation_target:
            arm = "validation"
        else:
            arm = "holdout"
        splits[arm].extend(group)
    return splits


def build_age_cohort(cache: Path, out: Path, *, pgn_name: str, birth_date: str,
                     age: int, min_train: int = 100) -> dict:
    """Materialize a rights-attested *training-only* age slice in a new folder.

    Never changes the source cache or publishes a playable/validated profile.
    A name or birth date is operator-provided evidence, not verified identity.
    """
    if type(age) is not int or not 0 <= age <= 120:
        raise ValueError("Idade deve estar no intervalo 0..120 anos")
    if type(min_train) is not int or not 1 <= min_train <= MAX_GAMES:
        raise ValueError("Cobertura mínima deve estar entre 1 e 1000 jogos")
    key = _normalize_name(pgn_name)
    birth = _birth_date(birth_date)
    out = out.expanduser()
    if out.is_symlink() or out.exists() or any(p.is_symlink() for p in out.parents):
        raise FileExistsError("Destino já existe ou possui caminho simbólico")
    # No derived material is allowed inside the source cache.
    if out.resolve().is_relative_to(cache.expanduser().resolve()):
        raise ValueError("Destino do recorte não pode ficar dentro do cache")
    manifest, parsed = _cache_snapshot(cache.expanduser())
    rejected = Counter()
    cohort = []
    identities_seen = Counter()
    today = date.today()
    for game, digest in parsed:
        white = _header_name(game.headers.get("White"))
        black = _header_name(game.headers.get("Black"))
        same_white = white == key
        same_black = black == key
        if not same_white and not same_black:
            rejected["unresolved_player_headers" if not white or not black
                     else "other_player"] += 1
            continue
        if same_white and same_black:
            rejected["ambiguous_both_sides"] += 1
            continue
        identities_seen["white" if same_white else "black"] += 1
        played = _full_date(game.headers.get("Date"))
        if played is None:
            rejected["date_unknown_or_invalid"] += 1
            continue
        if played > today:
            rejected["date_in_future"] += 1
            continue
        if played < birth:
            rejected["before_birth"] += 1
            continue
        if age_at_game(birth, played) != age:
            rejected["outside_exact_age"] += 1
            continue
        cohort.append({
            "mainline_sha256": digest,
            "date": played.isoformat(),
            "side": "white" if same_white else "black",
            "plies": sum(1 for _ in game.mainline_moves()),
            "result_declared": game.headers["Result"],
            "_game": game,
        })
    pieces = _partition(cohort)
    train_text = "".join(
        item["_game"].accept(chess.pgn.StringExporter(
            headers=True, variations=False, comments=False)).rstrip() + "\n\n"
        for item in pieces["train"]
    )
    train_data = train_text.encode("utf-8")
    # No holdout PGN moves or validation PGN moves are materialized. The
    # source cache is still accessible to the operator; no isolation claim.
    partition_info = {}
    for group, entries in pieces.items():
        partition_info[group] = {
            "games": len(entries),
            "first_date": entries[0]["date"] if entries else None,
            "last_date": entries[-1]["date"] if entries else None,
            "white_games": sum(row["side"] == "white" for row in entries),
            "black_games": sum(row["side"] == "black" for row in entries),
            "mainline_sha256": [row["mainline_sha256"] for row in entries],
        }
    ready = (len(pieces["train"]) >= min_train and
             bool(pieces["validation"]) and bool(pieces["holdout"]))
    result = {
        "schema": SCHEMA,
        "status": ("COHORT_PREPARED_NOT_VALIDATED" if ready else
                   "INSUFFICIENT_EXACT_AGE_EVIDENCE"),
        "profile_available": False,
        "player_pgn_name_operator_attested": pgn_name.strip(),
        "birth_date_operator_attested": birth.isoformat(),
        "age_completed_years": age,
        "birthday_rule": "Feb 29 anniversaries on Mar 1 in non-leap years",
        "source": {
            "cache_schema": CACHE_SCHEMA,
            "cache_pgn_sha256": manifest["cache_pgn_sha256"],
            "source_catalog_sha256": manifest.get("source_catalog_sha256"),
            "rights_status": manifest["rights_status"],
            "source_ids": sorted(s["id"] for s in manifest["sources"]),
            "note": "Operator attestation is not independent legal or identity verification",
        },
        "eligible_exact_age_unique_games": len(cohort),
        "total_cached_games": len(parsed),
        "identity_side_matches": dict(sorted(identities_seen.items())),
        "excluded": dict(sorted(rejected.items())),
        "minimum_train_games_requested": min_train,
        "partition_policy": "chronological 60/20/20 targets; whole civil dates never split",
        "partitions": partition_info,
        "train_pgn_sha256": sha256(train_data).hexdigest(),
        "train_pgn_bytes": len(train_data),
        "limitations": [
            "Age slice is not a historically validated or playable personality.",
            "Birth date and PGN name are operator-attested, not independently verified.",
            "Source metadata and results remain historical assertions, not proof of identity.",
            "No validation or holdout PGN is materialized; source cache still contains both.",
            "No future-age or other-age matches enter the training export.",
            "No UCI engine, Stockfish, internet, rating or model tuning is invoked.",
        ],
    }
    # The new directory is an exclusive one-time artifact. The final
    # manifest is the commit marker; failed publication cleans only folders
    # that this function successfully created.
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(mode=0o700, exist_ok=False)
    try:
        _atomic_text(out / "train.pgn", train_text, durable=os.name == "posix")
        _atomic_text(out / "cohort.json", json.dumps(
            result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            durable=os.name == "posix")
    except BaseException:
        shutil.rmtree(out)
        raise
    return result
