"""Fit real-data move-choice policies and compare on untouched historical years.

Input: PR #136 train-only policy_input/policy_target JSONL. Training is a
regularised empirical choice-over-opportunity model, *not* neural training.
Evaluation re-reads pinned REAL PGNs for validation years; all candidate
policies (and optional actual Rodent IV personalities) face identical boards.
No source downloads are silently replaced by fixtures.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import heapq
import json
import math
import os
from pathlib import Path
import re

import chess
import chess.engine

from .catalog import find_player
from .real_training_cohorts import (
    _assign, _focal_decisions, _iter_verified_games,
    _player_configuration, _scan, _source_bytes,
)

POLICY_SCHEMA = "chesssim-real-imitation-conditional-logit-v2"
REPORT_SCHEMA = "chesssim-real-policy-comparison-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_ARMS = ("validation", "holdout")


def _read_json(path: Path, maximum: int = 5_000_000) -> dict:
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Symbolic input path is forbidden")
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Missing or oversized JSON input")
    def unique(pairs):
        item = {}
        for key, value in pairs:
            if key in item:
                raise ValueError("Duplicate JSON key")
            item[key] = value
        return item
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def _write_once(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("Refusing to replace research evidence")
    if not path.parent.is_dir() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Output parent is missing or symbolic")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                         allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)


def _digest(obj) -> str:
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _features(board: chess.Board, move: chess.Move, *,
              assumed_legal: bool = False) -> tuple[str, ...]:
    """All features are computed from the position *before* the played move."""
    piece = board.piece_at(move.from_square)
    if piece is None or (not assumed_legal and move not in board.legal_moves):
        raise ValueError("Illegal candidate move")
    symbol = piece.symbol().upper()
    dest = chess.square_name(move.to_square)
    src = chess.square_name(move.from_square)
    delta_file = chess.square_file(move.to_square) - chess.square_file(move.from_square)
    delta_rank = chess.square_rank(move.to_square) - chess.square_rank(move.from_square)
    captured = board.piece_at(move.to_square)
    values = [
        "piece:" + symbol,
        "origin_file:" + src[0],
        "target_file:" + dest[0],
        "target_rank:" + dest[1],
        "target_square:" + dest,
        "piece_target:" + symbol + ":" + dest,
        "vector:" + str(max(-2, min(2, delta_file))) + ":" +
        str(max(-2, min(2, delta_rank))),
        "capture:" + ("yes" if board.is_capture(move) else "no"),
        "check:" + ("yes" if board.gives_check(move) else "no"),
        "castle:" + ("yes" if board.is_castling(move) else "no"),
        "promotion:" + (chess.piece_symbol(move.promotion) if move.promotion else "none"),
        "central_target:" + ("yes" if dest in ("d4", "e4", "d5", "e5") else "no"),
        "piece_target_rank:" + symbol + ":" + dest[1],
    ]
    if captured is not None:
        values.append("captured_piece:" + captured.symbol().upper())
    if piece.piece_type == chess.PAWN:
        values.append("pawn_push_two:" + str(abs(delta_rank) == 2))
    return tuple(values)


def _hash_rank(seed: int, row: dict) -> int:
    return int(sha256(f"{seed}|{row['game_sha256']}|{row['ply']}|{row['player']}".encode()).hexdigest(), 16)


def _bottom_k(rows, *, limit: int, per_game: int, seed: int) -> list[dict]:
    """Bounded deterministic minhash sample, after limiting game clustering.

    The same game can contribute at most per_game decisions. The factor-8
    oversample avoids placing only one historical game in a full sample.
    """
    if type(limit) is not int or not 1 <= limit <= 200_000:
        raise ValueError("Invalid sample limit")
    if type(per_game) is not int or not 1 <= per_game <= 10:
        raise ValueError("Invalid maximum per game")
    heap = []
    for row in rows:
        rank = _hash_rank(seed, row)
        item = (-rank, str(row["game_sha256"]) + ":" + str(row["ply"]), row)
        capacity = limit * 8
        if len(heap) < capacity:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    chosen = []
    counts = Counter()
    for _, _, row in sorted(heap, key=lambda item: (-item[0], item[1])):
        if counts[row["game_sha256"]] >= per_game:
            continue
        counts[row["game_sha256"]] += 1
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def _validated_training_rows(folder: Path, manifest: dict):
    players = manifest.get("players")
    if (manifest.get("schema") != "chesssim-real-player-year-train-v1" or
            manifest.get("partition_policy") not in ("chronological", "career-stratified")
            or not isinstance(players, dict) or not players):
        raise ValueError("Expected real historical training manifest")
    for name in manifest.get("training_files", []):
        if not isinstance(name, str) or not re.fullmatch(
                r"train/[a-z0-9-]+/[0-9]{4}\.jsonl", name):
            raise ValueError("Unsafe training file path")
        file = folder / name
        slug, year = Path(name).parts[1], int(Path(name).stem)
        if slug not in players:
            raise ValueError("Unexpected training player")
        arm = players[slug]["partition_by_whole_year"]["train"]
        if year not in arm["years"]:
            raise ValueError("Training row year outside declared train years")
        if file.is_symlink() or not file.is_file():
            raise ValueError("Missing train-only JSONL")
        with file.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("player") != slug or row.get("year") != year:
                    raise ValueError("Inconsistent training row metadata")
                policy, target = row.get("policy_input"), row.get("policy_target")
                if (not isinstance(policy, dict) or
                        set(policy) != {"fen", "player", "year", "career_band", "age_at_game",
                                        "age_bounds", "color", "ply"} or
                        not isinstance(target, dict) or set(target) != {"move_uci"} or
                        policy["player"] != slug or policy["year"] != year or
                        policy["fen"] != row.get("fen") or
                        target["move_uci"] != row.get("move_uci") or
                        not isinstance(row.get("game_sha256"), str) or
                        HEX.fullmatch(row["game_sha256"]) is None):
                    raise ValueError("Invalid policy input/target contract")
                yield {"player": slug, "game_sha256": row["game_sha256"],
                       "ply": row["ply"], "policy_input": policy,
                       "policy_target": target}


def _encode_choice(row: dict) -> tuple[list[tuple[str, ...]], int]:
    """Legal alternatives are the denominator of supervised likelihood."""
    board = chess.Board(row["policy_input"]["fen"])
    if not board.is_valid():
        raise ValueError("Invalid historical training board")
    target = chess.Move.from_uci(row["policy_target"]["move_uci"])
    legal = sorted(board.legal_moves, key=lambda move: move.uci())
    if target not in legal:
        raise ValueError("Historic move is not legal in the supplied position")
    return ([_features(board, move, assumed_legal=True) for move in legal],
            legal.index(target))


def _train_logit(examples: list[tuple[list[tuple[str, ...]], int]], *,
                 prior: dict | None = None,
                 epochs: int = 3, rate: float = 0.25,
                 shrinkage: float = 0.015) -> tuple[dict, list[float]]:
    """Conditional multinomial logit over all legal alternatives, pure Python.

    AdaGrad and shrinkage to a parent policy prevent sparse player/era
    profiles from throwing away general move-selection knowledge.
    """
    if not examples:
        raise ValueError("No real training examples")
    if not 1 <= epochs <= 10 or not 0 < rate <= 1 or not 0 <= shrinkage <= 1:
        raise ValueError("Unsupported optimizer parameters")
    parent = prior or {}
    weights = dict(parent)
    momentum = defaultdict(float)
    losses = []
    for epoch in range(epochs):
        loss = 0.0
        # Deterministic forward/reverse passes avoid uncontrolled RNG
        # while altering temporal correlation across observations.
        iterator = examples if epoch % 2 == 0 else reversed(examples)
        for choices, observed_index in iterator:
            scores = [sum(weights.get(token, 0.0) for token in features)
                      for features in choices]
            top = max(scores)
            exps = [math.exp(value - top) for value in scores]
            normalizer = sum(exps)
            probabilities = [value / normalizer for value in exps]
            loss -= math.log(max(1e-300, probabilities[observed_index]))
            gradient = defaultdict(float)
            for index, features in enumerate(choices):
                delta = (1.0 if index == observed_index else 0.0) - probabilities[index]
                for token in features:
                    gradient[token] += delta
            for token in sorted(gradient):
                current = weights.get(token, 0.0)
                updated_gradient = gradient[token] - shrinkage * (
                    current - parent.get(token, 0.0))
                momentum[token] += updated_gradient * updated_gradient
                weights[token] = max(-8.0, min(8.0, current + rate *
                    updated_gradient / math.sqrt(1.0 + momentum[token])))
        losses.append(round(loss / len(examples), 8))
    return ({key: round(val, 8) for key, val in sorted(weights.items())
             if abs(val) >= 1e-7}, losses)


def fit_policy(cohorts: Path, out: Path, *, max_per_player: int = 1200,
               max_per_game: int = 3, seed: int = 2026) -> dict:
    if type(seed) is not int or type(max_per_player) is not int:
        raise ValueError("Invalid training parameters")
    manifest_bytes = (cohorts / "manifest.json").read_bytes()
    manifest = _read_json(cohorts / "manifest.json")
    training_files_sha256 = {
        rel: sha256((cohorts / rel).read_bytes()).hexdigest()
        for rel in manifest["training_files"]
    }
    grouped = defaultdict(list)
    for row in _validated_training_rows(cohorts, manifest):
        grouped[row["player"]].append(row)
    if not grouped or set(grouped) != set(manifest["players"]):
        raise ValueError("Training data must cover each declared player")

    # Every fitted observation is a real FEN + real historic target move.
    # Enforce game grouping and deterministic minhash before any optimisation.
    encoded = {}
    for slug in sorted(grouped):
        samples = _bottom_k(grouped[slug], limit=max_per_player,
                            per_game=max_per_game, seed=seed)
        if not samples:
            raise ValueError("No genuine training decisions for " + slug)
        encoded[slug] = [
            {"band": row["policy_input"]["career_band"],
             "choice": _encode_choice(row),
             "game": row["game_sha256"]}
            for row in samples
        ]

    pooled = [item["choice"] for slug in sorted(encoded) for item in encoded[slug]]
    generic, global_losses = _train_logit(pooled, epochs=3, rate=0.23)
    profiles = {}
    for slug in sorted(encoded):
        own = [item["choice"] for item in encoded[slug]]
        personal, personal_losses = _train_logit(
            own, prior=generic, epochs=3, rate=0.19, shrinkage=0.03)
        career = {}
        stage_loss = {}
        for band in sorted({item["band"] for item in encoded[slug]}):
            stage_examples = [item["choice"] for item in encoded[slug]
                              if item["band"] == band]
            if len(stage_examples) >= 40:
                stage_weights, history = _train_logit(
                    stage_examples, prior=personal, epochs=2,
                    rate=0.14, shrinkage=0.08)
                career[band] = stage_weights
                stage_loss[band] = history
        profiles[slug] = {
            "observations": len(own),
            "distinct_training_games": len({x["game"] for x in encoded[slug]}),
            "weights": personal,
            "career_weights": career,
            "training_loss": personal_losses,
            "career_training_loss": stage_loss,
        }
    model = {
        "schema": POLICY_SCHEMA,
        "status": "REAL_PGN_TRAINED_EXPERIMENTAL_NOT_STYLE_VALIDATED",
        "train_manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "source_pgn_sha256": {k: v["pgn_sha256"] for k, v in manifest["sources"].items()},
        "training_files_sha256": training_files_sha256,
        "split_policy": manifest["partition_policy"],
        "training_examples_in_manifest": manifest["training_examples_exported"],
        "training_observations_used": len(pooled),
        "max_per_player": max_per_player, "max_per_game": max_per_game, "seed": seed,
        "method": "L2_ADAGRAD_CONDITIONAL_LOGIT_OVER_LEGAL_MOVES",
        "generic_weights": generic,
        "generic_training_loss": global_losses,
        "profiles": profiles,
        "interpretation": "Real human conditional move choice, not chess strength or validated identity",
    }
    model["model_sha256"] = _digest(model)
    _write_once(out, model)
    return model


def _choice_weights(model: dict, slug: str, band: str, *,
                    generic: bool) -> dict:
    if model.get("schema") != POLICY_SCHEMA:
        raise ValueError("Unexpected model schema")
    profile = model["profiles"].get(slug)
    if profile is None:
        raise ValueError("Profile not fitted")
    if generic:
        return model["generic_weights"]
    return profile["career_weights"].get(band, profile["weights"])


def predict(model: dict, board: chess.Board, slug: str, band: str,
            *, generic: bool = False) -> str:
    weights = _choice_weights(model, slug, band, generic=generic)
    candidates = sorted(board.legal_moves, key=lambda move: move.uci())
    if not candidates:
        raise ValueError("No legal candidate")
    def key(move: chess.Move):
        score = sum(weights.get(feature, 0.0) for feature in
                    _features(board, move, assumed_legal=True))
        return score, -int.from_bytes(move.uci().encode(), "big")
    return max(candidates, key=key).uci()


def _read_eval_positions(cohorts: Path, model: dict, *,
                         arm: str, archive_dir: Path | None, seed: int,
                         per_player: int, per_game: int):
    if arm not in ALLOWED_ARMS:
        raise ValueError("Final evaluation must name validation or holdout")
    manifest = _read_json(cohorts / "manifest.json")
    if (sha256((cohorts / "manifest.json").read_bytes()).hexdigest() !=
            model.get("train_manifest_sha256")):
        raise ValueError("Model and historical split manifest do not match")
    expected_files = model.get("training_files_sha256")
    if (not isinstance(expected_files, dict) or
            set(expected_files) != set(manifest["training_files"])):
        raise ValueError("Missing training JSONL commitments")
    for relative, expected in expected_files.items():
        if (not isinstance(relative, str) or not re.fullmatch(
                r"train/[a-z0-9-]+/[0-9]{4}\.jsonl", relative)):
            raise ValueError("Invalid training file path commitment")
        if (not isinstance(expected, str) or HEX.fullmatch(expected) is None or
                sha256((cohorts / relative).read_bytes()).hexdigest() != expected):
            raise ValueError("Training JSONL changed after model fit")
    config = _player_configuration(
        sorted(model["profiles"]), None,
        {slug: data["source"].removesuffix(".pgn")
         for slug, data in manifest["sources"].items()})
    training_fens = set()
    training_games = set()
    for row in _validated_training_rows(cohorts, manifest):
        training_fens.add(row["policy_input"]["fen"])
        training_games.add(row["game_sha256"])
    candidates = defaultdict(list)
    skipped = Counter()
    source_hashes = {}
    # The retrospective validation experiment uses pinned public bytes and
    # refuses sources inconsistent with the train manifest.
    for slug in sorted(config):
        _, raw, _ = _source_bytes(config[slug]["source"], archive_dir)
        digest = sha256(raw).hexdigest()
        source_hashes[slug] = digest
        if digest != model["source_pgn_sha256"].get(slug):
            raise ValueError("Historical source bytes changed: " + slug)
        rows, _ = _scan(raw, slug, config[slug]["surname"])
        parts, _ = _assign(rows, manifest["partition_policy"])
        if parts != manifest["players"][slug]["partition_by_whole_year"]:
            raise ValueError("Historical year split changed: " + slug)
        for game, row in _iter_verified_games(raw, rows, config[slug]["surname"]):
            if row["arm"] != arm:
                continue
            if row["mainline_sha256"] in training_games:
                skipped["training_trajectory_overlap"] += 1
                continue
            year = int(row["date"])
            for ply, fen, move in _focal_decisions(game, row["color"], manifest["min_ply"]):
                if fen in training_fens:
                    skipped["training_fen_overlap"] += 1
                    continue
                candidates[slug].append({
                    "player": slug, "game_sha256": row["mainline_sha256"],
                    "ply": ply, "fen": fen, "move": move,
                    "year": year,
                    "career_band": _band_for_year(year, rows),
                })
    sampled = {
        slug: _bottom_k(candidates[slug], limit=per_player,
                        per_game=per_game, seed=seed)
        for slug in sorted(config)
    }
    if any(not x for x in sampled.values()):
        raise ValueError("No real held-out observations for all named players")
    return sampled, skipped, source_hashes


def _band_for_year(year: int, rows: list[dict]) -> str:
    from .real_training_cohorts import _career_band
    return _career_band(year, sorted({int(r["date"]) for r in rows if r["date"]}))


def _metric(rows: list[dict], predictions: list[str]) -> dict:
    if len(rows) != len(predictions) or not rows:
        raise ValueError("Missing/unaligned predictions")
    matched = 0
    per_game = defaultdict(list)
    for row, prediction in zip(rows, predictions):
        board = chess.Board(row["fen"])
        if chess.Move.from_uci(prediction) not in board.legal_moves:
            raise ValueError("Illegal candidate from evaluated model")
        success = prediction == row["move"]
        matched += success
        per_game[row["game_sha256"]].append(success)
    macro = sum(sum(x)/len(x) for x in per_game.values()) / len(per_game)
    return {"positions": len(rows), "historical_games": len(per_game),
            "exact_match": int(matched), "top1": matched / len(rows),
            "macro_by_game": macro,
            "prediction_trace_sha256": sha256(" ".join(predictions).encode()).hexdigest()}


def _rodent_predictions(root: Path, rows: list[dict], slugs: list[str], *,
                        depth: int, time_per_position: float):
    """Actually launch the installed Rodent binary; no fallback or mock."""
    from .matches import validate, _open_engine, file_hash
    root = root.expanduser().resolve(strict=True)
    engines = {}
    hashes = {}
    try:
        for slug in slugs:
            p = find_player(slug)
            executable, path_map = validate(root, p, p)
            hashes[slug] = file_hash(path_map[slug])
            engines[slug] = _open_engine(root, executable, p, book=False)
        executable = root / "rodent-iv-plain.exe"
        hashes["exe"] = file_hash(executable)
        # The generic control must use the same pinned Rodent home as
        # the named profiles. Never permit an ambient RODENT4HOME to redirect
        # to a different installed set of personalities.
        generic = chess.engine.SimpleEngine.popen_uci(
            str(executable), cwd=str(root), timeout=15,
            env={**os.environ, "RODENT4HOME": str(root)})
        engines["generic_rodent"] = generic
        # A reproducible generic UCI control: select the official nonhistorical
        # "Simple" personality explicitly, never assume the engine default.
        option = generic.options.get("Personality")
        if option is None or (option.var and "Simple" not in option.var):
            raise RuntimeError("Real Rodent does not offer declared Simple control")
        generic.configure({"Personality": "Simple"})
        from .registry import registered_aliases
        aliases = registered_aliases(root / "personalities" / "basic.ini")
        filename = aliases.get("Simple")
        if not filename:
            raise RuntimeError("Simple UCI control is not registered")
        hashes["simple_profile"] = file_hash(root / "personalities" / filename)
        settings = {key: val for key, val in
                    (("UseBook", False), ("Threads", 1), ("Hash", 16),
                     ("UCI_LimitStrength", False))
                    if key in generic.options}
        if settings:
            generic.configure(settings)
        predictions = {slug: [] for slug in engines}
        for row in rows:
            board = chess.Board(row["fen"])
            for slug, engine in engines.items():
                res = engine.play(
                    board, chess.engine.Limit(depth=depth, time=time_per_position),
                    info=chess.engine.INFO_NONE)
                if res.move not in board.legal_moves:
                    raise RuntimeError("Rodent emitted illegal move")
                predictions[slug].append(res.move.uci())
        return predictions, hashes
    finally:
        for engine in engines.values():
            try:
                engine.quit()
            except Exception:
                engine.close()


def evaluate_policy(cohorts: Path, model_path: Path, out: Path, *,
                    arm: str = "validation", archive_dir: Path | None = None,
                    seed: int = 2027, per_player: int = 40, per_game: int = 2,
                    rodent_root: Path | None = None, rodent_depth: int = 2,
                    rodent_seconds: float = 0.15,
                    acknowledge_holdout: bool = False) -> dict:
    if arm == "holdout" and acknowledge_holdout is not True:
        raise ValueError("Final holdout needs explicit acknowledgement")
    if type(rodent_depth) is not int or not 1 <= rodent_depth <= 20:
        raise ValueError("Invalid Rodent depth")
    if not 0.01 <= rodent_seconds <= 10:
        raise ValueError("Rodent per-position time budget must be 0.01..10")
    model = _read_json(model_path, 8_000_000)
    claimed = model.pop("model_sha256", None)
    if not isinstance(claimed, str) or claimed != _digest(model):
        raise ValueError("Corrupt or modified trained model")
    model["model_sha256"] = claimed
    sample, excluded, source_hashes = _read_eval_positions(
        cohorts, model, arm=arm, archive_dir=archive_dir,
        seed=seed, per_player=per_player, per_game=per_game)
    slugs = sorted(sample)
    metrics = {}
    for target_slug, rows in sorted(sample.items()):
        pred = {"generic_trained": [predict(model, chess.Board(r["fen"]), target_slug,
                                           r["career_band"], generic=True) for r in rows]}
        for profile in slugs:
            pred["trained_" + profile] = [
                predict(model, chess.Board(r["fen"]), profile,
                        r["career_band"]) for r in rows]
        metrics[target_slug] = {name: _metric(rows, moves) for name, moves in pred.items()}
    rodent_evidence = {"status": "NOT_RUN_NO_REAL_ENGINE_SPECIFIED"}
    if rodent_root is not None:
        all_rows = [row for slug in slugs for row in sample[slug]]
        predictions, hashes = _rodent_predictions(
            rodent_root, all_rows, slugs,
            depth=rodent_depth, time_per_position=rodent_seconds)
        for slug in slugs:
            start = sum(len(sample[p]) for p in slugs[:slugs.index(slug)])
            stop = start + len(sample[slug])
            for profile, moves in predictions.items():
                metrics[slug]["rodent_" + profile] = _metric(sample[slug], moves[start:stop])
        rodent_evidence = {
            "status": "ACTUAL_UCI_MOVES_MEASURED",
            "binary_sha256": hashes["exe"],
            "personality_sha256": {s: hashes[s] for s in slugs},
            "generic_personality": "Simple",
            "generic_profile_sha256": hashes["simple_profile"],
            "depth": rodent_depth, "seconds_per_position": rodent_seconds,
            "books": "OFF", "predictors": ["rodent_" + s for s in predictions],
        }
    # A fitting objective can improve while out-of-period style identity
    # deteriorates. Never promote from training loss or raw win rates.
    diagnostics = {}
    for slug in slugs:
        rows = metrics[slug]
        own = rows["trained_" + slug]["top1"]
        general = rows["generic_trained"]["top1"]
        swapped = {other: rows["trained_" + other]["top1"] for other in slugs
                   if other != slug}
        diagnostic = {
            "personalized_minus_generic_top1": round(own - general, 8),
            "correct_label_minus_best_swapped_top1":
                round(own - max(swapped.values()), 8),
            "best_swapped_profile": min(
                (other for other, value in swapped.items()
                 if value == max(swapped.values()))),
            "profile_identity_accepted": False,
            "status": "EXPLORATORY_NO_STYLE_ACCEPTANCE",
        }
        if rodent_root is not None:
            diagnostic["rodent_named_minus_simple_top1"] = round(
                rows["rodent_" + slug]["top1"] -
                rows["rodent_generic_rodent"]["top1"], 8)
        diagnostics[slug] = diagnostic
    summary = {
        "schema": REPORT_SCHEMA,
        "status": "REAL_HELDOUT_HUMAN_MOVE_AGREEMENT_NOT_STYLE_VALIDATION",
        "model_sha256": model["model_sha256"],
        "split_policy": model["split_policy"],
        "arm": arm,
        "source_sha256": source_hashes,
        "seed": seed, "sample_limit_per_player": per_player,
        "max_decisions_per_game": per_game,
        "excluded": dict(excluded),
        "evaluation": metrics,
        "style_diagnostics": diagnostics,
        "model_promotion": "DENIED_NO_INDEPENDENT_STYLE_ACCEPTANCE",
        "rodent": rodent_evidence,
        "limitations": [
            "Exact human move agreement, not chess strength, Elo or proof of player identity.",
            "The declared year split is reconstructed from real PGN bytes; validation is retrospective and not physically sealed.",
            "Samples are deterministically selected, not iid; report both top1 and per-game macro.",
            "A similar strategic board with a different full FEN may remain between train and validation.",
            "Generic baseline is trained on the same real observations without player labels.",
            "All candidate models are scored on the exact same historical boards per target player.",
        ],
    }
    _write_once(out, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real historical move-policy fitting and comparison")
    sub = parser.add_subparsers(dest="action", required=True)
    fit = sub.add_parser("train", help="Fit real move-choice policy from train-only JSONL")
    fit.add_argument("--cohorts", required=True, type=Path)
    fit.add_argument("--out", required=True, type=Path)
    fit.add_argument("--max-per-player", type=int, default=1200)
    fit.add_argument("--max-per-game", type=int, default=3)
    fit.add_argument("--seed", type=int, default=2026)
    ev = sub.add_parser("evaluate", help="Test on actual reserved historical games")
    ev.add_argument("--cohorts", required=True, type=Path)
    ev.add_argument("--model", required=True, type=Path)
    ev.add_argument("--out", required=True, type=Path)
    sources = ev.add_mutually_exclusive_group(required=True)
    sources.add_argument("--fetch", action="store_true")
    sources.add_argument("--archive-dir", type=Path)
    ev.add_argument("--arm", choices=ALLOWED_ARMS, default="validation")
    ev.add_argument("--acknowledge-holdout", action="store_true")
    ev.add_argument("--seed", type=int, default=2027)
    ev.add_argument("--per-player", type=int, default=40)
    ev.add_argument("--per-game", type=int, default=2)
    ev.add_argument("--rodent-root", type=Path)
    ev.add_argument("--rodent-depth", type=int, default=2)
    ev.add_argument("--rodent-seconds", type=float, default=0.15)
    args = parser.parse_args(argv)
    if args.action == "train":
        output = fit_policy(args.cohorts, args.out, max_per_player=args.max_per_player,
                            max_per_game=args.max_per_game, seed=args.seed)
        result = {"status": output["status"], "training_observations_used":
                  output["training_observations_used"], "model_sha256": output["model_sha256"]}
    else:
        output = evaluate_policy(
            args.cohorts, args.model, args.out, arm=args.arm,
            archive_dir=args.archive_dir, seed=args.seed, per_player=args.per_player,
            per_game=args.per_game, rodent_root=args.rodent_root,
            rodent_depth=args.rodent_depth, rodent_seconds=args.rodent_seconds,
            acknowledge_holdout=args.acknowledge_holdout)
        result = {"status": output["status"], "rodent_status": output["rodent"]["status"],
                  "evaluation": {k: {name: {"top1": row["top1"],
                                          "macro_by_game": row["macro_by_game"],
                                          "n": row["positions"]}
                                     for name, row in v.items()}
                                 for k, v in output["evaluation"].items()}}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
