"""V6: reproducible, CPU-budgeted Maia-3 vs Rodent historical transfer.

This is NOT a claim that Maia's published online-Lichess result replicates on
20th-century over-the-board grandmaster games. The model is genuinely pretrained
by CSSLab, run via the actual pinned Maia-3 UCI implementation. The hybrid
threshold is learned using TRAIN-year games and audited on distinct TRAIN
games. Every final comparison uses separate held-year validation games.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import re
import sys

import chess
import chess.engine

from .catalog import find_player
from .contextual_policy_v3 import (
    SCHEMA as V3_SCHEMA, _assert_training_manifest, _read_model,
    paired_game_inference, predict_contextual,
)
from .engine_pv_policy_v5 import _analyse as rodent_multipv
from .real_gate_v4 import _position_key
from .real_policy_experiment import (
    _bottom_k, _digest, _metric, _read_eval_positions, _read_json,
    _validated_training_rows, _write_once,
)
from .real_training_cohorts import (
    _iter_verified_games, _player_configuration, _scan, _source_bytes,
)

SCHEMA = "chesssim-real-maia3-5m-historical-transfer-v6"
MAIA_CODE_COMMIT = "1e13597c42d4858b7cfd7cfdae01e297263364b2"
MAIA_REPO = "UofTCSSLab/Maia3-5M"
MODEL_FILE = "maia3-5m.pt"
# Prespecified: alternatives are Lichess *scenarios*, NOT inferred OTB FIDE Elos.
ELOS = (1800, 2400)
MAX_CP_LOSS = (0, 35, 80, 150)
PENALTY = 0.01


def _samp_train(cohorts: Path, base: dict, count: int, seed: int) -> dict:
    _assert_training_manifest(cohorts, base)
    manifest = _read_json(cohorts / "manifest.json")
    grouped = defaultdict(list)
    for item in _validated_training_rows(cohorts, manifest):
        if item["ply"] >= 21:
            grouped[item["player"]].append(item)
    selected = {}
    for slug in sorted(base["profiles"]):
        rows = _bottom_k(grouped[slug], limit=count, per_game=2, seed=seed)
        if len(rows) != count or len({x["game_sha256"] for x in rows}) < 20:
            raise ValueError("Too few separate genuine TRAIN games for Maia calibration: " + slug)
        selected[slug] = [
            {"player": slug, "game_sha256": r["game_sha256"], "ply": r["ply"],
             "fen": r["policy_input"]["fen"], "move": r["policy_target"]["move_uci"],
             "year": r["policy_input"]["year"]}
            for r in rows
        ]
    return selected


def _samp_validation(cohorts: Path, base: dict, count: int, seed: int,
                     archive_dir: Path | None) -> tuple[dict, dict, dict]:
    candidates, dropped, hashes = _read_eval_positions(
        cohorts, base, arm="validation", archive_dir=archive_dir,
        per_player=count * 8, per_game=2, seed=seed)
    manifest = _read_json(cohorts / "manifest.json")
    training_states = {_position_key(r["policy_input"]["fen"])
                       for r in _validated_training_rows(cohorts, manifest)}
    samples, overlap = {}, {}
    for slug in sorted(base["profiles"]):
        available = [r for r in candidates[slug] if
                     r["ply"] >= 21 and _position_key(r["fen"]) not in training_states]
        overlap[slug] = len(candidates[slug]) - len(available)
        chosen = _bottom_k(available, limit=count, per_game=2, seed=seed)
        if len(chosen) != count or len({x["game_sha256"] for x in chosen}) < 10:
            raise ValueError("Too few independent held-year Maia transfer positions: " + slug)
        samples[slug] = chosen
    return samples, {"exclude_existing": dict(dropped),
                     "canonical_or_early_excluded": overlap,
                     "all_train_canonical_embargoed": len(training_states)}, hashes


def _restore_real_histories(cohorts: Path, base: dict, samples: dict,
                            archive_dir: Path | None) -> tuple[dict, dict]:
    """Recover EXACT board move stacks; no FEN-only 'fake history'.

    Historical PGNs are parsed again, and every selected sample is checked
    against its recorded predecision FEN and next human move. This does not
    pass the future target move to any UCI model.
    """
    manifest = _read_json(cohorts / "manifest.json")
    source_files = {s: item["source"].removesuffix(".pgn")
                    for s, item in manifest["sources"].items()}
    config = _player_configuration(sorted(base["profiles"]), None, source_files)
    boards, source_hashes = {}, {}
    for slug in sorted(samples):
        _, raw, _ = _source_bytes(config[slug]["source"], archive_dir)
        digest = sha256(raw).hexdigest()
        if digest != base["source_pgn_sha256"][slug]:
            raise ValueError("Historical source drift before Maia inference: " + slug)
        source_hashes[slug] = digest
        all_index, _ = _scan(raw, slug, config[slug]["surname"])
        by_game = defaultdict(dict)
        for row in samples[slug]:
            identity = (row["game_sha256"], row["ply"])
            if row["ply"] < 21 or row["ply"] in by_game[row["game_sha256"]]:
                raise ValueError("Invalid/duplicate sample or Maia opening eligibility")
            by_game[row["game_sha256"]][row["ply"]] = row
        found = {}
        for game, meta in _iter_verified_games(raw, all_index, config[slug]["surname"]):
            game_id = meta["mainline_sha256"]
            if game_id not in by_game:
                continue
            board = game.board()
            for ply, next_move in enumerate(game.mainline_moves(), start=1):
                row = by_game[game_id].get(ply)
                if row is not None:
                    if board.fen() != row["fen"] or next_move.uci() != row["move"]:
                        raise ValueError("Historical move/FEN mismatch while rebuilding history")
                    # Preserve every earlier move but NEVER push the human label.
                    found[(game_id, ply)] = board.copy(stack=True)
                board.push(next_move)
        if len(found) != len(samples[slug]):
            raise ValueError("Some genuine PGN histories were not recovered: " + slug)
        boards[slug] = found
    return boards, source_hashes


def _choose(rodent: str, maia: str, variations: list[dict],
            threshold: int | None) -> str:
    if threshold is None or maia == rodent:
        return rodent
    if threshold not in MAX_CP_LOSS:
        raise ValueError("Unregistered tactical budget")
    if not variations or variations[0]["move"] != rodent:
        raise ValueError("Mismatched engine bestmove and MultiPV")
    by_move = {p["move"]: p for p in variations}
    if len(by_move) != len(variations):
        raise ValueError("Duplicate Rodent candidate")
    candidate = by_move.get(maia)
    if candidate is None:
        return rodent
    loss = variations[0]["cp"] - candidate["cp"]
    if loss < 0:
        raise ValueError("Rodent MultiPV score order changed unexpectedly")
    return maia if loss <= threshold else rodent


def _split_by_game(rows: list[dict], seed: int, slug: str) -> tuple[list[int], list[int]]:
    games = sorted({r["game_sha256"] for r in rows},
                   key=lambda g: sha256(f"{seed}|{slug}|{g}".encode()).digest())
    if len(games) < 20:
        raise ValueError("Too few distinct real games for train-only audit")
    cut = max(12, min(len(games) - 8, int(len(games) * 0.65)))
    train = set(games[:cut])
    return ([i for i, row in enumerate(rows) if row["game_sha256"] in train],
            [i for i, row in enumerate(rows) if row["game_sha256"] not in train])


def _macro_penalized_gain(rows: list[dict], proposed: list[str],
                          base: list[str]) -> float:
    per_game = defaultdict(list)
    for row, a, b in zip(rows, proposed, base):
        per_game[row["game_sha256"]].append(int(a == row["move"]) - int(b == row["move"]))
    overrides = sum(a != b for a, b in zip(proposed, base))
    return (sum(sum(v)/len(v) for v in per_game.values()) / len(per_game)
            - PENALTY * overrides / len(rows))


def _calibrate(rows: list[dict], seed: int, slug: str) -> dict:
    tune, audit = _split_by_game(rows, seed, slug)
    choices = []
    for elo in ELOS:
        for threshold in MAX_CP_LOSS:
            proposed = [_choose(r["rodent"], r["maia"][str(elo)],
                                r["rodent_variations"], threshold)
                        for r in rows]
            train_rows = [rows[i] for i in tune]
            gain = _macro_penalized_gain(
                train_rows, [proposed[i] for i in tune],
                [rows[i]["rodent"] for i in tune])
            changes = sum(proposed[i] != rows[i]["rodent"] for i in tune)
            choices.append((gain, changes, elo, threshold, proposed))
    # Non-intervention (gain=0) is the default, equal-score wins never activate.
    candidates = [v for v in choices if v[0] > 0 and v[1] > 0]
    selected = (max(candidates, key=lambda p: (p[0], -p[1], -p[3], -p[2]))
                if candidates else None)
    audit_rows = [rows[i] for i in audit]
    baseline = [rows[i]["rodent"] for i in audit]
    audit_moves = ([selected[4][i] for i in audit]
                   if selected else baseline)
    proof = paired_game_inference(
        audit_rows, audit_moves, baseline,
        seed=seed + 1, repetitions=1000)
    changed = sum(x != y for x, y in zip(audit_moves, baseline))
    # The audit is a *different set of games*, not an external holdout.
    approved = bool(selected and changed >= 3 and
                    proof["bootstrap_95_ci"][0] > 0)
    return {
        "configured_maia_elo": selected[2] if approved else None,
        "max_tactical_loss_cp": selected[3] if approved else None,
        "selected_in_tuning": (
            {"maia_elo": selected[2], "max_tactical_loss_cp": selected[3]}
            if selected else None),
        "tuning_games": len({rows[i]["game_sha256"] for i in tune}),
        "audit_games": len({rows[i]["game_sha256"] for i in audit}),
        "audit_changed_lances": changed,
        "audit_paired_statistic": proof,
        "decision": ("PERMIT_LIMITED_MAIA_CHOICE" if approved
                     else "DENY_NO_GAME_DISJOINT_POSITIVE_REPLICATION"),
    }


def _real_predictions(samples: dict, boards: dict, *, rodent_root: Path,
                      checkpoint: Path, scenario_elos: tuple[int, ...],
                      depth: int) -> tuple[dict, dict]:
    """Use Maia-3 5M and Rodent IV real executables. No emulated engine."""
    from .matches import _open_engine, file_hash, validate
    # huggingface_hub snapshot paths are normally symlinks into verified
    # content-addressed blob storage; resolve them instead of rejecting
    # official cache layout.
    checkpoint = checkpoint.resolve(strict=True)
    if not checkpoint.is_file():
        raise ValueError("Missing Maia-3 checkpoint")
    cmd = [
        sys.executable, "-m", "maia3.uci",
        "--model", "maia3-5m", "--checkpoint-path", str(checkpoint),
        "--device", "cpu", "--no-use-amp", "--use-uci-history",
        "--temperature", "0", "--top-p", "1", "--multipv", "4",
    ]
    env = {**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}
    maia_engine = chess.engine.SimpleEngine.popen_uci(cmd, timeout=180, env=env)
    all_records = {}
    binaries = {}
    profiles = {}
    try:
        if not {"SelfElo", "OppoElo", "MultiPV"} <= set(maia_engine.options):
            raise ValueError("Wrong Maia-3 UCI engine or options")
        for slug in sorted(samples):
            player = find_player(slug)
            executable, paths = validate(rodent_root, player, player)
            digest = file_hash(executable)
            if binaries and digest != next(iter(binaries.values())):
                raise ValueError("Actual Rodent binary changed between players")
            binaries[slug] = digest
            profiles[slug] = file_hash(paths[slug])
            rodent = _open_engine(rodent_root, executable, player, book=False)
            try:
                settings = {k: v for k, v in
                            (("Threads", 1), ("Hash", 16), ("UseBook", False),
                             ("UCI_LimitStrength", False))
                            if k in rodent.options}
                if settings:
                    rodent.configure(settings)
                items = []
                for row in samples[slug]:
                    actual_board = boards[slug][(row["game_sha256"], row["ply"])]
                    if actual_board.fen() != row["fen"]:
                        raise ValueError("Maia and Rodent not evaluating identical board")
                    # Engine output is computed BEFORE inspecting the historical
                    # target, which is used only by the supervised gate.
                    maes = {}
                    for elo in scenario_elos:
                        maia_engine.configure({"SelfElo": elo, "OppoElo": elo})
                        predicted = maia_engine.play(
                            actual_board, chess.engine.Limit(nodes=1),
                            info=chess.engine.INFO_NONE).move
                        if predicted not in actual_board.legal_moves:
                            raise ValueError("Maia-3 proposed illegal move")
                        maes[str(elo)] = predicted.uci()
                    variants = rodent_multipv(
                        rodent, actual_board, multipv=4, depth=depth)
                    best = variants[0]["move"]
                    if chess.Move.from_uci(best) not in actual_board.legal_moves:
                        raise ValueError("Rodent returned illegal move")
                    items.append({**row, "maia": maes, "rodent": best,
                                  "rodent_variations": variants})
                all_records[slug] = items
            finally:
                try:
                    rodent.quit()
                except Exception:
                    rodent.close()
    finally:
        try:
            maia_engine.quit()
        except Exception:
            maia_engine.close()
    return all_records, {"rodent_binary": next(iter(binaries.values())),
                         "profiles": profiles,
                         "maia_checkpoint_sha256": sha256(checkpoint.read_bytes()).hexdigest(),
                         "maia_code_commit": MAIA_CODE_COMMIT,
                         "maia_model_repo": MAIA_REPO,
                         "history": "RECONSTRUCTED_FROM_REAL_PGN_MOVE_STACK",
                         "maia_inference": "CPU_5M_ZERO_TEMPERATURE_NO_SEARCH",
                         "rodent_multipv": 4, "rodent_depth": depth}


def run_experiment(cohorts: Path, v3_model: Path, rodent_root: Path,
                   output: Path, *, maia_revision: str,
                   archive_dir: Path | None = None,
                   train_per_player: int = 48, val_per_player: int = 24,
                   seed: int = 2036, rodent_depth: int = 3) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", maia_revision):
        raise ValueError("Maia checkpoint must be pinned to a 40-character HF commit SHA")
    if (not 24 <= train_per_player <= 300 or
            not 16 <= val_per_player <= 100 or
            not 2 <= rodent_depth <= 5):
        raise ValueError("Unsafe/underpowered CPU experimental configuration")
    v3 = _read_model(v3_model, V3_SCHEMA)
    _assert_training_manifest(cohorts, v3)
    training = _samp_train(cohorts, v3, train_per_player, seed)
    held, rejected, original_hashes = _samp_validation(
        cohorts, v3, val_per_player, seed + 1, archive_dir)
    train_boards, train_hashes = _restore_real_histories(
        cohorts, v3, training, archive_dir)
    val_boards, val_hashes = _restore_real_histories(
        cohorts, v3, held, archive_dir)
    if train_hashes != val_hashes or val_hashes != original_hashes:
        raise ValueError("Historical source changed during benchmarking")
    # Explicitly pinned third-party pretrained checkpoint: use HF hash in
    # resolution, then compute actual downloaded bytes SHA for audit.
    from huggingface_hub import hf_hub_download
    checkpoint = Path(hf_hub_download(
        MAIA_REPO, filename=MODEL_FILE, revision=maia_revision))
    train_results, engine_meta = _real_predictions(
        training, train_boards, rodent_root=rodent_root, checkpoint=checkpoint,
        scenario_elos=ELOS, depth=rodent_depth)
    gate = {slug: _calibrate(rows, seed + 9, slug)
            for slug, rows in train_results.items()}
    validation_results, validation_meta = _real_predictions(
        held, val_boards, rodent_root=rodent_root, checkpoint=checkpoint,
        scenario_elos=ELOS, depth=rodent_depth)
    if engine_meta != validation_meta:
        raise ValueError("Engine binary/checkpoint/personality changed between arms")
    results = {}
    total = defaultdict(lambda: Counter())
    for index, slug in enumerate(sorted(validation_results)):
        rows = validation_results[slug]
        base = [r["rodent"] for r in rows]
        maias = {e: [r["maia"][str(e)] for r in rows] for e in ELOS}
        g = gate[slug]
        chosen = ([_choose(r["rodent"], r["maia"][str(g["configured_maia_elo"])],
                           r["rodent_variations"], g["max_tactical_loss_cp"])
                   for r in rows] if g["configured_maia_elo"] else base)
        v3pred = [predict_contextual(
            v3, chess.Board(r["fen"]), slug, "overall") for r in rows]
        preds = {"rodent_multipv_top1": base, "maia3_1800": maias[1800],
                 "maia3_2400": maias[2400], "calibrated_maia_safety_gate": chosen,
                 "v3_named": v3pred}
        metrics = {name: _metric(rows, moves) for name, moves in preds.items()}
        paired = {
            name + "_vs_rodent": paired_game_inference(
                rows, moves, base, seed=seed + index * 10 + j,
                repetitions=1000)
            for j, (name, moves) in enumerate(preds.items()) if name != "rodent_multipv_top1"
        }
        for name, item in metrics.items():
            total[name]["correct"] += item["exact_match"]
            total[name]["positions"] += len(rows)
        results[slug] = {
            "validation_games": len({r["game_sha256"] for r in rows}),
            "validation_positions": len(rows),
            "gate": g,
            "metrics": metrics,
            "paired_game_statistics": paired,
            "gate_overrides": sum(a != b for a, b in zip(chosen, base)),
            "performance_ceiling_claimed": False,
        }
    response = {
        "schema": SCHEMA, "status": "REAL_MAIA3_5M_PRETRAINED_CPU_TRANSFER_EXPLORATORY",
        "protocol": "MAIA_LIKE_MOVE_MATCHING_MIN_PLY21_FULL_HISTORY_NO_CLOCK_AVAILABLE",
        "maia_hf_revision": maia_revision,
        "maia_genuine_checkpoint_sha256": engine_meta["maia_checkpoint_sha256"],
        "source_pgn_sha256": original_hashes,
        "train_manifest_sha256": v3["train_manifest_sha256"],
        "v3_model_sha256": v3["model_sha256"],
        "engine": engine_meta, "train_arm_only_gate": True,
        "validation_arm": "validation",
        "calibration_rows_per_player": train_per_player,
        "validation_rows_per_player": val_per_player,
        "elo_scenarios_not_actual_elo": list(ELOS),
        "tactical_threshold_grid_cp": list(MAX_CP_LOSS),
        "validation_exclusions": rejected, "players": results,
        "overall": {name: {"correct": v["correct"],
                            "positions": v["positions"],
                            "top1": v["correct"] / v["positions"]}
                    for name, v in sorted(total.items())},
        "total_calibration_positions": sum(len(x) for x in training.values()),
        "total_validation_positions": sum(len(x) for x in held.values()),
        "maia_2020_published_accuracy_is_NOT_comparable": True,
        "historical_FIDE_ratings_NOT_converted_to_Lichess": True,
        "no_clock_annotations_in_historical_pgn": True,
        "no_opening_moves_1_through_20": True,
        "third_party_pretraining_not_game_disjoint_audited": True,
        "holdout_opened": False,
        "official_personality_promotion": "DENIED",
        "limitations": [
            "Maia-3 was pretrained on modern online games; this evaluation uses historical OTB games.",
            "Fixed Lichess Elo scenarios are not estimates of an OTB player's true Elo.",
            "All historical positions have true reconstructed game history (not FEN padding).",
            "Historic PGNs lack reliable per-move clocks, so Maia's clock filter cannot be replicated.",
            "Maia-3 is third-party pretrained; overlap with its released training was not independently proven absent.",
            "Rodent MultiPV depth-3 centipawn gaps are not tactical safety certificates.",
            "This validation has been inspected in previous PRs: all inference is exploratory.",
            "Gate audit is game-disjoint but within TRAIN, not a final test set.",
            "Lack of significance is not evidence of equivalence or an intrinsic performance ceiling.",
        ],
    }
    response["report_sha256"] = _digest(response)
    _write_once(output, response)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Low-budget genuine Maia-3 5M vs Rodent IV human move transfer")
    parser.add_argument("--cohorts", type=Path, required=True)
    parser.add_argument("--v3-model", type=Path, required=True)
    parser.add_argument("--rodent-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--maia-revision", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--archive-dir", type=Path)
    parser.add_argument("--train-per-player", type=int, default=48)
    parser.add_argument("--val-per-player", type=int, default=24)
    parser.add_argument("--seed", type=int, default=2036)
    parser.add_argument("--rodent-depth", type=int, default=3)
    args = parser.parse_args(argv)
    report = run_experiment(
        args.cohorts, args.v3_model, args.rodent_root, args.out,
        maia_revision=args.maia_revision, archive_dir=args.archive_dir,
        train_per_player=args.train_per_player,
        val_per_player=args.val_per_player,
        seed=args.seed, rodent_depth=args.rodent_depth)
    print(json.dumps({
        "validation_positions": report["total_validation_positions"],
        "maia_model_sha256": report["maia_genuine_checkpoint_sha256"],
        "overall": report["overall"],
        "gate_by_player": {s: r["gate"]["decision"] for s, r in report["players"].items()},
        "promotion": report["official_personality_promotion"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
