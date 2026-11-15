"""CPU-only, paired real historical comparison of Maia3 and DSC original UCI.

Uses held-year VALIDATION only; results are exploratory, NOT a final holdout.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from collections import defaultdict

import chess
import chess.engine

from .contextual_policy_v3 import SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model, paired_game_inference
from .maia_transfer_v6 import MAIA_HF_REVISION, MAIA_CHECKPOINT_SHA256, _samp_validation, _restore_real_histories, _verify_maia_source_code
from .maia_personalization_v7 import (
    MAIA79_HF_REVISION, MAIA79_CHECKPOINT_SHA256, _load_adapter,
    _open_maia, _rerank, _verify_checkpoint,
)
from .real_policy_experiment import _digest, _metric, _write_once

SCHEMA = "chesssim-maia23-79-dsc150-otb-comparison-v1"
MAIA23_HF_REVISION = "51a0145a8178046f7de23119160b136672deeb2b"
DSC_SOURCE_COMMIT = "229ef4291c38c088a9fd3a410e4c0e926a20cea1"
DSC150_WEIGHTS_SHA256 = "66b21d7882bb3fb9737bc798e709671325838e5469ed6bfcf114b3abbcb48e24"
DSC150_CONFIG_SHA256 = "6916d0ed2b33a4f41c71e704bd23f3ebd6676759d70d5e3b009a6a39d3315e42"
MAIA_SCENARIO = 2400
DSC_FIDE_SCENARIO = 2400
MULTIPV = 20


def _dsc_preflight() -> dict:
    spec = importlib.util.find_spec("chessds_dsc")
    if spec is None or spec.origin is None:
        raise ValueError("Install the pinned DSC source, not an anonymous engine")
    root = Path(spec.origin).resolve(strict=True).parent.parent
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    if commit != DSC_SOURCE_COMMIT:
        raise ValueError("DSC source checkout differs from frozen v0.1.0")
    from chessds_dsc.registry import resolve
    model = resolve("dsc-150m")
    if (model.weights_sha256 != DSC150_WEIGHTS_SHA256
            or model.config_sha256 != DSC150_CONFIG_SHA256):
        raise ValueError("DSC original registered model hashes changed")
    return {"code_commit": commit, "weights_sha256": model.weights_sha256,
            "config_sha256": model.config_sha256, "registry_model_id": model.id}


def _candidates(engine: chess.engine.SimpleEngine, board: chess.Board) -> list[str]:
    if not board.is_valid() or board.is_game_over(claim_draw=False):
        raise ValueError("Historical position is invalid/terminal")
    count = min(MULTIPV, board.legal_moves.count())
    lines = engine.analyse(board, chess.engine.Limit(nodes=1), multipv=count,
                           info=chess.engine.INFO_PV)
    if not isinstance(lines, list) or len(lines) != count:
        raise ValueError("UCI engine failed to emit the expected independent MultiPV list")
    candidates = []
    for line in lines:
        pv = line.get("pv")
        if not pv or pv[0] not in board.legal_moves:
            raise ValueError("Candidate missing or illegal")
        candidates.append(pv[0].uci())
    if len(set(candidates)) != count:
        raise ValueError("Duplicate UCI MultiPV choices")
    return candidates


def _metrics(rows: list[dict], recommendations: list[list[str]]) -> dict:
    if len(rows) != len(recommendations):
        raise ValueError("Different position counts across models")
    first = [moves[0] for moves in recommendations]
    result = _metric(rows, first)
    result["top3"] = sum(row["move"] in choices[:3]
                         for row, choices in zip(rows, recommendations)) / len(rows)
    result["top3_correct"] = sum(row["move"] in choices[:3]
                                for row, choices in zip(rows, recommendations))
    return result


def run(cohorts: Path, v3_path: Path, adapter_path: Path, output: Path, *,
        archive_dir: Path | None, per_player: int, seed: int) -> dict:
    if not 16 <= per_player <= 96:
        raise ValueError("Bounded CPU comparison requires 16..96 decisions per player")
    v3 = _read_model(v3_path, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    adapter = _load_adapter(adapter_path, v3)
    _verify_maia_source_code()
    dsc_identity = _dsc_preflight()
    chosen, excluded, hashes = _samp_validation(
        cohorts, v3, per_player, seed + 1, archive_dir)
    history, history_hashes = _restore_real_histories(cohorts, v3, chosen, archive_dir)
    if history_hashes != hashes:
        raise ValueError("PGN source changed during the benchmark")
    from huggingface_hub import hf_hub_download
    downloads = {
        "maia3_5m": ("UofTCSSLab/Maia3-5M", "maia3-5m.pt", MAIA_HF_REVISION),
        "maia3_23m": ("UofTCSSLab/Maia3-23M", "maia3-23m.pt", MAIA23_HF_REVISION),
        "maia3_79m": ("UofTCSSLab/Maia3-79M", "maia3-79m.pt", MAIA79_HF_REVISION),
    }
    checkpoints = {}
    digests = {}
    for label, (name, filename, revision) in downloads.items():
        path = Path(hf_hub_download(name, filename=filename, revision=revision))
        expected = (MAIA_CHECKPOINT_SHA256 if label == "maia3_5m" else
                    MAIA79_CHECKPOINT_SHA256 if label == "maia3_79m" else None)
        digests[label] = _verify_checkpoint(path, known_digest=expected)
        checkpoints[label] = path
    all_rows = [r for slug in sorted(chosen) for r in chosen[slug]]
    all_boards = [history[slug][(r["game_sha256"], r["ply"])]
                  for slug in sorted(chosen) for r in chosen[slug]]
    if any(b.fen() != r["fen"] for b,r in zip(all_boards, all_rows)):
        raise ValueError("Genuine reconstructed PGN history mismatch")
    predictions = {}
    times = {}
    for label in ("maia3_5m", "maia3_23m", "maia3_79m", "dsc_150m"):
        t0 = perf_counter()
        if label.startswith("maia3_"):
            engine = _open_maia(label.replace("_", "-"), checkpoints[label])
        else:
            command = [
                sys.executable, "-m", "chessds_dsc.uci",
                "--model", "dsc-150m", "--device", "cpu",
                "--use-uci-history", "--temperature", "0",
                "--top-p", "1", "--multipv", str(MULTIPV),
                "--time-control", "classical", "--rating-system", "fide",
                "--elo", str(DSC_FIDE_SCENARIO), "--output-mode", "likelihood",
            ]
            engine = chess.engine.SimpleEngine.popen_uci(command, timeout=360)
            if not {"MultiPV", "Elo", "RatingSystem", "TimeControl"} <= set(engine.options):
                engine.quit()
                raise ValueError("Wrong DSC UCI engine or context options")
            engine.configure({"Elo": DSC_FIDE_SCENARIO,
                              "TimeControl": "classical", "RatingSystem": "fide"})
        try:
            predictions[label] = [_candidates(engine, b) for b in all_boards]
        finally:
            try:
                engine.quit()
            except Exception:
                engine.close()
        times[label] = round(perf_counter() - t0, 3)
    # The individual adapter is only a DIAGNOSTIC from V7: never deploy.
    for index, (r, candidates) in enumerate(zip(all_rows, predictions["maia3_79m"])):
        slug = r["player"]
        board = chess.Board(r["fen"])
        individual = _rerank(board, candidates,
                             adapter["profiles"][slug]["weights"], 1.0)
        predictions.setdefault("maia79_personal_diagnostic", []).append([individual])
    metrics = {label: _metrics(all_rows, p) for label,p in predictions.items()}
    per_player_results = {}
    for slug in sorted(chosen):
        indexes = [i for i,r in enumerate(all_rows) if r["player"] == slug]
        rows = [all_rows[i] for i in indexes]
        per_player_results[slug] = {
            label: _metrics(rows, [p[i] for i in indexes])
            for label,p in predictions.items()
        }
    baseline = [p[0] for p in predictions["maia3_79m"]]
    paired = {
        label: paired_game_inference(all_rows, [p[0] for p in predictions[label]],
                                     baseline, seed=seed+i+1000, repetitions=2000)
        for i,label in enumerate(sorted(predictions))
        if label != "maia3_79m"
    }
    result = {
        "schema": SCHEMA,
        "status": "EXPLORATORY_ORIGINAL_ENGINES_NO_PERSONALITY_PROMOTION",
        "source_pgn_sha256": hashes,
        "manifest_sha256": v3["train_manifest_sha256"],
        "adapter_sha256": adapter["model_sha256"],
        "maia_source_verified": True,
        "maia_revisions": {label:v[2] for label,v in downloads.items()},
        "maia_checkpoint_sha256": digests,
        "dsc_identity": dsc_identity,
        "model_context": {
            "maia_lichess_rating_scenario": MAIA_SCENARIO,
            "dsc_fide_rating_scenario": DSC_FIDE_SCENARIO,
            "dsc_time_control": "classical",
            "ratings_not_equivalent": True,
        },
        "players": per_player_results,
        "models": metrics,
        "game_paired_vs_79m": paired,
        "wall_seconds_including_engine_startup_and_inference": times,
        "validation_decisions": len(all_rows),
        "validation_distinct_games": len({r["game_sha256"] for r in all_rows}),
        "validation_exclusions": excluded,
        "holdout_opened": False,
        "official_personality_promotion": "DENIED",
        "limitations": [
            "Maia rating and DSC FIDE scale scenarios are not numerically equivalent.",
            "Validation historical years were previously consulted: inferential claims exploratory.",
            "DSC-150M and Maia checkpoints pretrained on different corpora; no external decontamination certification.",
            "Adapted Maia model strength=1 diagnostic is NOT an approved personality.",
            "Measured wall-time is runtime-specific; not a controlled cross-hardware benchmark.",
        ],
    }
    result["report_sha256"] = _digest(result)
    _write_once(output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Maia3-5/23/79 and DSC-150M paired OTB CPU benchmark")
    p.add_argument("--cohorts", required=True, type=Path)
    p.add_argument("--v3-model", required=True, type=Path)
    p.add_argument("--adapter", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    p.add_argument("--per-player", type=int, default=64)
    p.add_argument("--seed", type=int, default=2038)
    args = p.parse_args(argv)
    result = run(args.cohorts, args.v3_model, args.adapter, args.out,
                 archive_dir=args.archive_dir, per_player=args.per_player,
                 seed=args.seed)
    print(json.dumps({"models": result["models"],
                      "validation_decisions": result["validation_decisions"],
                      "holdout_opened": result["holdout_opened"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
