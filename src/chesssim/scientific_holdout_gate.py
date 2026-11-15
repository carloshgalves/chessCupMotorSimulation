"""Final holdout requires pre-registration reviewed in the repository, not a CLI flag.

This safety gate NEVER creates authorization. It refuses use until a frozen
protocol digest is explicitly allowlisted by a separately reviewed code commit.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re

SCHEMA = "chesssim-final-holdout-preregistration-v1"
# DO NOT auto-populate from operator input, PR descriptions, or CI outcomes.
# No historical model has met independent blind/safety acceptance yet.
TRUSTED_FROZEN_PREREGISTRATIONS: frozenset[str] = frozenset()
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
FIELDS = frozenset({
    "schema", "candidate_model_sha256", "train_manifest_sha256",
    "source_snapshot_commit", "source_pgn_commitment_sha256",
    "blind_identity_protocol_sha256", "tactical_safety_protocol_sha256",
    "hypotheses_and_multiplicity_sha256", "seed", "per_player",
    "per_game", "rodent_depth", "rodent_seconds", "use_rodent",
    "frozen_protocol_sha256",
})


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _no_duplicate_keys(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate key in pre-registration")
        result[key] = value
    return result


def authorize_final_holdout(path: Path, *, model_sha256: str,
                            train_manifest_sha256: str,
                            source_pgn_commitment_sha256: str,
                            seed: int, per_player: int, per_game: int,
                            rodent_depth: int, rodent_seconds: float,
                            use_rodent: bool) -> str:
    path = Path(path)
    if any(x.is_symlink() for x in (path, *path.parents)):
        raise ValueError("Pre-registration must not be a symlink or symlink ancestor")
    if not path.is_file() or path.stat().st_size > 16_384:
        raise ValueError("Missing or oversized frozen pre-registration")
    payload = json.loads(path.read_text(encoding="utf-8"),
                         object_pairs_hook=_no_duplicate_keys)
    if type(payload) is not dict or set(payload) != FIELDS or payload["schema"] != SCHEMA:
        raise ValueError("Missing/malformed final protocol commitments")
    for key in ("candidate_model_sha256", "train_manifest_sha256",
                "source_pgn_commitment_sha256", "blind_identity_protocol_sha256",
                "tactical_safety_protocol_sha256", "hypotheses_and_multiplicity_sha256"):
        if type(payload[key]) is not str or not HEX64.fullmatch(payload[key]):
            raise ValueError("Malformed SHA-256 scientific commitment: " + key)
    if (type(payload["source_snapshot_commit"]) is not str
            or not HEX40.fullmatch(payload["source_snapshot_commit"])):
        raise ValueError("Final source snapshot commit must be a full Git SHA")
    if (type(payload["seed"]) is not int or type(payload["per_player"]) is not int
            or type(payload["per_game"]) is not int or
            type(payload["rodent_depth"]) is not int or
            type(payload["rodent_seconds"]) not in (float, int) or
            type(payload["use_rodent"]) is not bool):
        raise ValueError("Malformed frozen evaluation parameters")
    plain = {k:v for k,v in payload.items() if k != "frozen_protocol_sha256"}
    digest = _digest(plain)
    if payload["frozen_protocol_sha256"] != digest:
        raise ValueError("Frozen final protocol was modified")
    expected = {
        "candidate_model_sha256": model_sha256,
        "train_manifest_sha256": train_manifest_sha256,
        "source_pgn_commitment_sha256": source_pgn_commitment_sha256,
        "seed": seed, "per_player": per_player, "per_game": per_game,
        "rodent_depth": rodent_depth, "rodent_seconds": rodent_seconds,
        "use_rodent": use_rodent,
    }
    if any(payload[key] != val for key,val in expected.items()):
        raise PermissionError("Final pre-registration does not match exact evaluated model/split/budget")
    if digest not in TRUSTED_FROZEN_PREREGISTRATIONS:
        raise PermissionError("Final holdout requires a separately reviewed frozen pre-registration")
    return digest
