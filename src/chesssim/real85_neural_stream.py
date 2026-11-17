"""Source-locked full-trajectory streaming input for future neural Maia/DSC FIT.

All eligible focal decisions from a deterministic >=80% of distinct, real
historical games are retained, including middle and endgame moves. Individual
JSONL rows contain full legal move trajectories, not a hardcoded decision cap.
No neural weights are modified here: this is a *reproducible input stage*.

The global game identity and 85% TRAIN/15% TEST split are exactly those
already used by real85_playable. A second global split reserves 3% of TRAIN
games for within-TRAIN TUNE. No TEST game is ever serialized.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil
from urllib.error import HTTPError

import chess

from .catalog import load_roster
from .game_diversity import mainline_identity
from .real85_playable import DEFAULT_PLAYERS, game_arm, _write_new
from .real_training_cohorts import (
    MIRROR_COMMIT, _iter_verified_games, _player_configuration, _scan, _source_bytes,
)

SCHEMA = "chesssim-real85-full-trajectory-stream-v1"
GAME_SCHEMA = "chesssim-real85-neural-game-trajectory-v1"
TUNE_SALT = "chesssim-real85-neural-within-train-v1"
FIT_THRESHOLD = 9700
DENOM = 10000


def neural_arm(game_sha256: str) -> str:
    """A global per-trajectory sub-arm; no player-name or metadata in the seed."""
    outer = game_arm(game_sha256)
    if outer == "test":
        return "excluded_test"
    val = int(sha256(f"{TUNE_SALT}:{game_sha256}".encode()).hexdigest()[:16], 16) % DENOM
    return "fit" if val < FIT_THRESHOLD else "tune"


def _digest_trajectory(fen: str, moves: list[str]) -> str:
    data = {"initial_fen": fen, "moves": moves}
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _record(game, meta: dict, slug: str, arm: str, source_sha: str) -> tuple[dict, int]:
    """One game record, with every move needed to reconstruct Maia history."""
    initial_fen = game.board().fen()
    moves = [move.uci() for move in game.mainline_moves()]
    digest, n = mainline_identity(game)
    if _digest_trajectory(initial_fen, moves) != digest or n != len(moves):
        raise RuntimeError("Historical mainline reconstruction inconsistency")
    board = game.board()
    focal_white = meta["color"] == "white"
    focal_decisions = 0
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise RuntimeError("Source mainline contains illegal move")
        focal_decisions += int(board.turn == focal_white)
        board.push(move)
    record = {
        "schema": GAME_SCHEMA, "player": slug, "arm": arm,
        "game_sha256": digest, "source_pgn_sha256": source_sha,
        "initial_fen": initial_fen, "focal_color": meta["color"],
        "uci_moves": moves, "focal_decisions": focal_decisions,
    }
    return record, focal_decisions


def build(out: Path, *, players: list[str] | None = None,
          archive_dir: Path | None = None,
          allow_missing: bool = False) -> dict:
    """Write source-verified FIT/TUNE trajectory shards for a later trainer.

    The entire source is scanned, so there is no first-N-games, first-N-moves,
    max-games=128 or arbitrary per-game decision sampling. We preserve every
    focal move from each emitted game, including all middle and endgames.
    """
    selected = list(players or DEFAULT_PLAYERS)
    conf = _player_configuration(selected, None, None)
    out = Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Output must be a new directory with an existing parent")
    stage = out.with_name(out.name + ".tmp-neural-stream")
    if stage.exists() or stage.is_symlink():
        raise FileExistsError("Temporary streaming output already exists")
    stage.mkdir()
    summaries = {}
    try:
        for slug in selected:
            source_key = conf[slug]["source"]
            if archive_dir is not None:
                directory = Path(archive_dir)
                if not directory.is_dir():
                    raise FileNotFoundError(f"Historical source directory does not exist: {directory}")
                if any(part.is_symlink() for part in (directory, *directory.parents)):
                    raise ValueError("Symbolic historical source directory is not supported")
                candidates = [directory / (source_key + ext) for ext in (".pgn", ".zip")]
                if any(candidate.is_symlink() for candidate in candidates):
                    raise ValueError("Symbolic historical PGN source is not supported")
                if not any(candidate.is_file() for candidate in candidates):
                    if not allow_missing:
                        raise FileNotFoundError(f"Local historical PGN source absent: {source_key}")
                    summaries[slug] = {"status":"SOURCE_MISSING","reason":"LOCAL_SOURCE_MISSING"}
                    continue
            try:
                _, pgn, source = _source_bytes(source_key, archive_dir)
            except (HTTPError, FileNotFoundError) as exc:
                if not allow_missing or (isinstance(exc, HTTPError) and exc.code != 404):
                    raise
                summaries[slug] = {
                    "status": "SOURCE_MISSING", "reason": "HTTP_404" if isinstance(exc, HTTPError)
                    else "LOCAL_SOURCE_MISSING",
                }
                continue
            rows, diagnostics = _scan(pgn, slug, conf[slug]["surname"])
            if not rows:
                summaries[slug] = {"status": "NO_REAL_ELIGIBLE_GAMES",
                                   "scan": diagnostics}
                continue
            arms = {r["mainline_sha256"]: neural_arm(r["mainline_sha256"]) for r in rows}
            totals = Counter(arms.values())
            source_sha = sha256(pgn).hexdigest()
            files = {arm: stage / f"{slug}-{arm}.jsonl" for arm in ("fit","tune")}
            hashers = {arm: sha256() for arm in files}
            serialized_games = Counter()
            serialized_decisions = Counter()
            with files["fit"].open("x", encoding="utf-8", newline="") as fit_handle, (
                    files["tune"].open("x", encoding="utf-8", newline="")) as tune_handle:
                handles = {"fit":fit_handle,"tune":tune_handle}
                for game, meta in _iter_verified_games(pgn, rows, conf[slug]["surname"]):
                    digest, _ = mainline_identity(game)
                    arm = arms[digest]
                    if arm == "excluded_test":
                        continue
                    record, focal = _record(game, meta, slug, arm, source_sha)
                    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False) + "\n"
                    handles[arm].write(encoded)
                    hashers[arm].update(encoded.encode("utf-8"))
                    serialized_games[arm] += 1
                    serialized_decisions[arm] += focal
            if (serialized_games["fit"] != totals["fit"] or
                    serialized_games["tune"] != totals["tune"]):
                raise RuntimeError("Historical full-stream source changed between two passes")
            n = len(rows)
            fitted = totals["fit"] / n
            status = ("READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING"
                      if fitted >= 0.8 and totals["tune"] and
                      serialized_decisions["fit"] and serialized_decisions["tune"]
                      else "INSUFFICIENT_INTRA_TRAINING_COVERAGE")
            summaries[slug] = {
                "status": status,
                "source": source, "source_snapshot": None if archive_dir else MIRROR_COMMIT,
                "source_pgn_sha256": source_sha,
                "accepted_distinct_games": n,
                "original_train_games": totals["fit"] + totals["tune"],
                "excluded_test_games": totals["excluded_test"],
                "fit_games": totals["fit"], "tune_games": totals["tune"],
                "fit_fraction_of_all_eligible_games": round(fitted, 6),
                "original_train_fraction": round(
                    (totals["fit"] + totals["tune"])/n, 6),
                "fit_focal_decisions_all_phases": serialized_decisions["fit"],
                "tune_focal_decisions_all_phases": serialized_decisions["tune"],
                "shards": {arm:{
                    "file": files[arm].name,
                    "sha256": hashers[arm].hexdigest(),
                    "games": serialized_games[arm],
                    "focal_decisions": serialized_decisions[arm],
                } for arm in files},
                "scan": diagnostics,
            }
        report = {
            "schema": SCHEMA, "status": "FULL_TRAJECTORIES_STAGED_NOT_NEURAL_TRAINED",
            "training_game_protocol": "real85-global-SHA-game-arm",
            "subsplit": "97-percent-of-original-TRAIN-for-FIT-3-percent-TUNE",
            "subsplit_salt": TUNE_SALT,
            "source_public_pgn_only": archive_dir is None,
            "source_mode": "PINNED_PUBLIC_FETCH" if archive_dir is None else "LOCAL_OPERATOR_SUPPLIED",
            "old_year_holdout_used": False,
            "excluded_test_games_serialized": 0,
            "training_gradients_applied": False,
            "neural_weights_changed": False,
            "all_game_moves_preserved": True,
            "warning": "Shards are local INPUTS, not a trained neural model; do not publish trajectories as CI artifacts.",
            "players": summaries,
        }
        _write_new(stage / "manifest.json", report)
        stage.rename(out)
        return report
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def verify(root: Path) -> dict:
    """Replay all local shards; verify legal game history, FIT/TUNE and hashes."""
    root = Path(root).resolve(strict=True)
    report = json.loads((root / "manifest.json").read_text())
    if report.get("schema") != SCHEMA or report.get("training_gradients_applied") is not False:
        raise ValueError("Not an original full-trajectory input manifest")
    count_games = Counter()
    count_decisions = Counter()
    known_game_arms = {}
    for slug, row in report["players"].items():
        if row.get("status") not in ("READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING",
                                      "INSUFFICIENT_INTRA_TRAINING_COVERAGE"):
            continue
        for arm in ("fit","tune"):
            meta = row["shards"][arm]
            path = root / meta["file"]
            if path.parent != root or path.is_symlink():
                raise ValueError("Nonlocal or symlink trajectory shard")
            hash_ = sha256()
            observed_games, observed_decisions = 0, 0
            seen = set()
            with path.open("rb") as stream:
                for raw in stream:
                    hash_.update(raw)
                    entry = json.loads(raw)
                    if (entry.get("schema") != GAME_SCHEMA or
                            entry.get("player") != slug or entry.get("arm") != arm or
                            entry.get("source_pgn_sha256") != row["source_pgn_sha256"]):
                        raise ValueError("Trajectory shard metadata mismatch")
                    digest = entry["game_sha256"]
                    if (neural_arm(digest) != arm or
                            _digest_trajectory(entry["initial_fen"], entry["uci_moves"]) != digest or
                            digest in seen):
                        raise ValueError("Trajectory FIT/TUNE identity or partition leak")
                    if digest in known_game_arms and known_game_arms[digest] != arm:
                        raise ValueError("Cross-player trajectory leaking across neural arms")
                    known_game_arms[digest] = arm
                    seen.add(digest)
                    board = chess.Board(entry["initial_fen"])
                    if not board.is_valid() or entry["focal_color"] not in ("white","black"):
                        raise ValueError("Invalid historical starting chess state")
                    white = entry["focal_color"] == "white"
                    decisions = 0
                    for encoded in entry["uci_moves"]:
                        move = chess.Move.from_uci(encoded)
                        if move not in board.legal_moves:
                            raise ValueError("Illegal historic training trajectory")
                        decisions += int(board.turn == white)
                        board.push(move)
                    if decisions != entry["focal_decisions"]:
                        raise ValueError("Historical focal decision count changed")
                    observed_games += 1
                    observed_decisions += decisions
            if (hash_.hexdigest() != meta["sha256"] or
                    observed_games != meta["games"] or
                    observed_decisions != meta["focal_decisions"]):
                raise ValueError("Historical shard file integrity or decision count mismatch")
            count_games[slug] += observed_games
            count_decisions[slug] += observed_decisions
        if count_games[slug] != row["fit_games"] + row["tune_games"]:
            raise ValueError("Neural full-game stream count does not match manifest")
    expected = set(report["players"])
    ready = {slug for slug,row in report["players"].items()
             if row.get("status") == "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING"}
    status = ("NO_REAL_INPUT_SHARDS" if sum(count_games.values()) == 0 else
              "PARTIAL_REAL_INPUT_VERIFICATION" if ready != expected else
              "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES")
    return {
        "status": status,
        "players": {slug:{"games":count_games[slug],"focal_decisions":count_decisions[slug]}
                    for slug in sorted(count_games)},
    }


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="All real85 FIT historical trajectories for neural training")
    sub=p.add_subparsers(dest="command",required=True)
    b=sub.add_parser("build")
    src=b.add_mutually_exclusive_group(required=True)
    src.add_argument("--fetch",action="store_true")
    src.add_argument("--archive-dir",type=Path)
    choice=b.add_mutually_exclusive_group()
    choice.add_argument("--players",nargs="+")
    choice.add_argument("--all-roster",action="store_true")
    b.add_argument("--allow-missing",action="store_true")
    b.add_argument("--out",type=Path,required=True)
    v=sub.add_parser("verify")
    v.add_argument("--dir",type=Path,required=True)
    args=p.parse_args(argv)
    if args.command=="build":
        selected = ([p.slug for p in load_roster()] if args.all_roster
                    else args.players or list(DEFAULT_PLAYERS))
        result=build(args.out,players=selected,archive_dir=args.archive_dir,
                     allow_missing=args.allow_missing)
        print(json.dumps({"status":result["status"],
            "players":{s:{k:row.get(k) for k in
                ("status","fit_games","tune_games","excluded_test_games",
                 "fit_fraction_of_all_eligible_games","fit_focal_decisions_all_phases")}
                for s,row in result["players"].items()}},sort_keys=True),flush=True)
    else:
        checked = verify(args.dir)
        print(json.dumps(checked,sort_keys=True),flush=True)
        if checked["status"] != "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES":
            return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())
