"""Fail-closed upstream pretraining overlap probe, TRAIN-years only (#159).

External indexes are operator-supplied *candidate* subsets, never certified
model training inventories. A nonmatch cannot establish disjoint pretraining.
No network, checkpoint, validation, holdout, or raw PGN access occurs here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA = "chesssim-upstream-pretrain-overlap-train-v1"
INDEX_SCHEMA = "chesssim-upstream-fingerprint-subset-v1"
PLAYERS = ("fischer", "karpov", "kasparov", "tal")
MODELS = ("dsc150", "maia79")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_FINGERPRINTS_PER_KIND = 150_000
FINGERPRINT_CONTRACT = "chesssim-mainline-v1+fen4-sha256-v1"


def canonical_position_digest(fen: str) -> str:
    """Hash four-field position key used for ChessCup canonical embargo.

    Fullmove and halfmove counts do not distinguish positions; castling
    rights and en-passant state do. This is not a move-context match.
    """
    if not isinstance(fen, str) or len(fen) > 120 or len(fen.split()) != 6:
        raise ValueError("Expected a six-field predecision FEN")
    fields = fen.split()
    if fields[1] not in ("w", "b") or fields[2] == "" or fields[3] == "":
        raise ValueError("Invalid FEN metadata")
    key = " ".join(fields[:4])
    return sha256(("chesssim-fen4-v1\n" + key).encode("ascii")).hexdigest()


def _hex(value: object, field: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise ValueError("Invalid " + field + " SHA256")
    return value


def _strict_json(path: Path) -> dict:
    path = Path(path)
    if (path.is_symlink() or any(p.is_symlink() for p in path.parents)
            or not path.is_file() or path.stat().st_size > MAX_INDEX_BYTES):
        raise ValueError("Missing, symbolic, or oversized candidate index")

    def unique_keys(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("Duplicate JSON key")
            out[key] = value
        return out

    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(raw, dict):
        raise ValueError("Index must be a JSON object")
    return raw


def validate_candidate_index(raw: dict, model: str) -> dict:
    """Accept positive candidate matches, but never evidence of non-exposure."""
    required = {"schema", "model", "coverage", "fingerprint_contract",
                "source_url", "source_artifact_sha256", "game_sha256",
                "position_sha256"}
    if (model not in MODELS or not isinstance(raw, dict) or set(raw) != required
            or raw["schema"] != INDEX_SCHEMA or raw["model"] != model
            or raw["coverage"] != "CANDIDATE_SUBSET_NOT_TRAINING_ATTESTED"
            or raw["fingerprint_contract"] != FINGERPRINT_CONTRACT):
        raise ValueError("Index is not a bounded, unverified candidate subset")
    source = raw["source_url"]
    if not isinstance(source, str) or len(source) > 500:
        raise ValueError("Invalid upstream source URL")
    parsed = urlsplit(source)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or any(c.isspace() for c in source)):
        raise ValueError("Candidate source must be an HTTPS URL")
    _hex(raw["source_artifact_sha256"], "source artifact")
    result = {}
    for field in ("game_sha256", "position_sha256"):
        seq = raw[field]
        if (not isinstance(seq, list) or len(seq) > MAX_FINGERPRINTS_PER_KIND
                or any(not isinstance(x, str) or HEX64.fullmatch(x) is None for x in seq)
                or len(seq) != len(set(seq))):
            raise ValueError("Invalid or duplicate candidate fingerprints: " + field)
        result[field] = frozenset(seq)
    return {"source_url": source, "source_artifact_sha256": raw["source_artifact_sha256"],
            **result}


def audit_train_rows(rows, candidate_indexes: dict[str, dict] | None = None) -> dict:
    """Count candidate collisions without treating them as pretraining exposure.

    A trajectory shared by Karpov and Kasparov is one global game. Only
    TRAIN rows may enter: a non-TRAIN row is rejected, not silently omitted.
    """
    indexes = candidate_indexes or {}
    if not set(indexes).issubset(MODELS):
        raise ValueError("Unknown upstream model")
    checked = {model: validate_candidate_index(indexes[model], model)
               for model in indexes}
    observations = defaultdict(lambda: {"decisions": 0, "games": set(),
                                     "candidate_exact_games": set(),
                                     "candidate_position_games": set(),
                                     "candidate_position_decisions": 0})
    game_year = {}
    unique = set()
    all_games = set()
    row_count = 0
    for row in rows:
        slug = row.get("player") if isinstance(row, dict) else None
        game = _hex(row.get("game_sha256"), "game") if isinstance(row, dict) else None
        policy = row.get("policy_input") if isinstance(row, dict) else None
        if (slug not in PLAYERS or not isinstance(policy, dict)
                or not isinstance(game, str) or policy.get("player") != slug):
            raise ValueError("Only four authenticated historical TRAIN players")
        year = policy.get("year")
        if (type(year) is not int or not 1800 <= year <= 2026
                or row.get("arm", "train") != "train"):
            raise ValueError("Non-TRAIN or invalid historical year")
        ply = row.get("ply")
        if type(ply) is not int or not 1 <= ply <= 500:
            raise ValueError("Invalid historical ply")
        game_key = (slug, game)
        if game_key in game_year and game_year[game_key] != year:
            raise ValueError("Same historical game assigned different years")
        game_year[game_key] = year
        obs_id = (slug, game, ply)
        if obs_id in unique:
            raise ValueError("Duplicate player-game-ply observation")
        unique.add(obs_id)
        all_games.add(game)
        fen = canonical_position_digest(policy.get("fen"))
        row_count += 1
        for model in MODELS:
            key = (model, slug, year)
            cell = observations[key]
            cell["decisions"] += 1
            cell["games"].add(game)
            if model in checked:
                if game in checked[model]["game_sha256"]:
                    cell["candidate_exact_games"].add(game)
                if fen in checked[model]["position_sha256"]:
                    cell["candidate_position_games"].add(game)
                    cell["candidate_position_decisions"] += 1
    if row_count == 0:
        raise ValueError("Empty train-only cohort")
    model_reports = {}
    for model in MODELS:
        per_player = {}
        for slug in PLAYERS:
            years = {}
            for m, p, year in sorted(observations):
                if (m, p) != (model, slug):
                    continue
                cell = observations[m, p, year]
                years[str(year)] = {
                    "arm": "train", "decisions": cell["decisions"],
                    "games": len(cell["games"]),
                    "candidate_exact_game_matches": len(cell["candidate_exact_games"]),
                    "candidate_position_match_games": len(cell["candidate_position_games"]),
                    "candidate_position_match_decisions": cell["candidate_position_decisions"],
                    "unknown_training_provenance_games": len(cell["games"]),
                }
            per_player[slug] = years
        model_reports[model] = {
            "status": "UNVERIFIABLE_PRETRAIN_OVERLAP",
            "candidate_index_supplied": model in checked,
            "candidate_source_url": checked[model]["source_url"] if model in checked else None,
            "candidate_source_artifact_sha256": (
                checked[model]["source_artifact_sha256"] if model in checked else None),
            "per_player_year": per_player,
        }
    return {
        "schema": SCHEMA,
        "status": "UNVERIFIABLE_PRETRAIN_OVERLAP",
        "scope": "TRAIN_ONLY_FOUR_HISTORICAL_PLAYERS",
        "players": list(PLAYERS),
        "models": model_reports,
        "training_decisions": row_count,
        "globally_distinct_training_games": len(all_games),
        "validation_opened": False,
        "final_holdout_opened": False,
        "personality_promoted": False,
        "limitations": [
            "Candidate source fingerprint matches are not authenticated model-training exposure.",
            "No complete upstream training-game manifest was available or verified.",
            "Absence of a match in a candidate subset is NOT evidence of dataset disjointness.",
            "Shared canonical FEN is weaker than evidence of memorization or an identical game.",
            "Only exported ChessCup TRAIN decisions were scanned, never VALIDATION/HOLDOUT.",
            "No claims about Elo, historic personality identity, or checkpoint decontamination.",
        ],
    }


def run(cohorts: Path, out: Path, *, dsc_index: Path | None = None,
        maia_index: Path | None = None) -> dict:
    from .real_policy_experiment import (
        _digest, _read_json, _validated_training_rows, _write_once,
    )

    manifest = _read_json(Path(cohorts) / "manifest.json")
    if (manifest.get("schema") != "chesssim-real-player-year-train-v1"
            or set(manifest.get("players", {})) != set(PLAYERS)):
        raise ValueError("Expected four-player real TRAIN corpus, never sealed arms")
    indexes = {}
    if dsc_index is not None:
        indexes["dsc150"] = _strict_json(dsc_index)
    if maia_index is not None:
        indexes["maia79"] = _strict_json(maia_index)
    result = audit_train_rows(_validated_training_rows(Path(cohorts), manifest), indexes)
    result["training_manifest_sha256"] = _digest(manifest)
    result["snapshot_commit"] = manifest.get("snapshot_commit")
    result["report_sha256"] = _digest(result)
    _write_once(Path(out), result)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fail-closed TRAIN-only external pretrain overlap audit")
    p.add_argument("--cohorts", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--dsc-index", type=Path, help="unverified candidate fingerprints (optional)")
    p.add_argument("--maia-index", type=Path, help="unverified candidate fingerprints (optional)")
    args = p.parse_args(argv)
    report = run(args.cohorts, args.out, dsc_index=args.dsc_index,
                 maia_index=args.maia_index)
    print(json.dumps({
        "status": report["status"], "train_decisions": report["training_decisions"],
        "train_games": report["globally_distinct_training_games"],
        "validation_opened": False, "final_holdout_opened": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
