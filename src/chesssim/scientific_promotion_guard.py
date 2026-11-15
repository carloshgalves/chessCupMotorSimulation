"""Fail-closed scientific promotion authority boundary.

A SHA-256 integrity digest is NOT an approval signature. A user-provided
manifest can never authorize its own promotion: only a reviewed code change
can explicitly list a report hash in this module.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

from .real_policy_experiment import _digest

SCHEMA = "chesssim-reviewed-scientific-personality-promotion-v1"
# Empty BY DESIGN: currently no Maia3/DSC historical identity has passed
# an independent final blind-style and tactical-safety review.
TRUSTED_FINAL_REPORT_SHA256: frozenset[str] = frozenset()
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def verify_promotion(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("Promotion manifest must be JSON object")
    fields = {
        "schema", "state", "player", "candidate_model_sha256",
        "training_manifest_sha256", "final_protocol_sha256",
        "independent_final_report_sha256", "blind_identity_passed",
        "tactical_safety_passed", "baseline_comparison_passed",
        "final_holdout_opened_once", "manifest_sha256",
    }
    if set(manifest) != fields or manifest.get("schema") != SCHEMA:
        raise ValueError("Unknown/incomplete promotion protocol")
    if not isinstance(manifest["player"], str) or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*", manifest["player"]
    ):
        raise ValueError("Invalid historical identity")
    for field in (
        "candidate_model_sha256", "training_manifest_sha256",
        "final_protocol_sha256", "independent_final_report_sha256",
    ):
        if not isinstance(manifest[field], str) or not HEX64.fullmatch(manifest[field]):
            raise ValueError("Missing immutable scientific commitment: " + field)
    doc = {k:v for k,v in manifest.items() if k != "manifest_sha256"}
    if manifest.get("manifest_sha256") != _digest(doc):
        raise ValueError("Tampered/uncommitted scientific manifest")
    if (manifest["state"] != "APPROVED_FOR_EXPERIMENTAL_PLAY"
            or any(manifest[k] is not True for k in (
                "blind_identity_passed", "tactical_safety_passed",
                "baseline_comparison_passed", "final_holdout_opened_once"))):
        raise PermissionError("Scientific promotion requirements not met")
    if manifest["independent_final_report_sha256"] not in TRUSTED_FINAL_REPORT_SHA256:
        raise PermissionError("Final independent report is not explicitly reviewed and trusted")
    return {"state": "APPROVED_FOR_EXPERIMENTAL_PLAY",
            "player": manifest["player"],
            "model_sha256": manifest["candidate_model_sha256"],
            "approval_manifest_sha256": manifest["manifest_sha256"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify independently authorized research promotion")
    p.add_argument("--manifest", type=Path, required=True)
    args = p.parse_args(argv)
    path = args.manifest
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32_768:
        raise ValueError("Unsafe or missing scientific promotion manifest")
    verdict = verify_promotion(json.loads(path.read_text(encoding="utf-8")))
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
