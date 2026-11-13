"""Exact-FEN negative controls for experimental historical chess representation.

Compares independently supplied *decision traces* against historical choices
observed at the same complete FEN. No engine, corpus downloader, model fitting,
final-test access, identity classifier, Elo, or historical-fidelity estimate.

This is not a proof of out-of-sample evaluation: the supplying research pipeline
must independently verify train/validation/test embargo and game provenance.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

SCHEMA = "chesssim-shared-fen-negative-control-v1"
MAX_ROWS = 50_000
MAX_SHARED_FENS = 4_000
MAX_JSON_BYTES = 8 * 1024 * 1024


def _required_str(value, field: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    if len(value) > 1024:
        raise ValueError(f"{field} too long")
    return value


def _legal_move(board, raw: str, field: str) -> str:
    import chess

    _required_str(raw, field)
    try:
        move = chess.Move.from_uci(raw)
    except ValueError as exc:
        raise ValueError(f"{field} is not a UCI move") from exc
    if move not in board.legal_moves:
        raise ValueError(f"{field} is illegal for the supplied FEN")
    return move.uci()


def _validate_positions(positions: list[dict], names: set[str], min_ply: int):
    import chess

    if not isinstance(positions, list) or len(positions) > MAX_ROWS:
        raise ValueError("positions must be a bounded list")
    unique = {}
    duplicates = 0
    early_excluded = 0
    for row in positions:
        if not isinstance(row, dict):
            raise ValueError("position row must be an object")
        player = _required_str(row.get("player"), "player")
        if player not in names:
            raise ValueError("unexpected player label")
        game = _required_str(row.get("game"), "game")
        fen = _required_str(row.get("fen"), "fen")
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError("invalid FEN") from exc
        if not board.is_valid() or board.fen() != fen:
            raise ValueError("invalid or noncanonical FEN")
        ply = row.get("ply")
        if type(ply) is not int or ply != board.ply() + 1:
            raise ValueError("ply must equal the full FEN move counter")
        move = _legal_move(board, row.get("move"), "historical move")
        key = (player, game, fen)
        normalized = {"player": player, "game": game, "fen": fen,
                      "move": move, "ply": ply}
        if key in unique:
            if unique[key] != normalized:
                raise ValueError("conflicting historical moves in one game/FEN")
            duplicates += 1
            continue
        unique[key] = normalized
        if ply < min_ply:
            early_excluded += 1

    canonical = sorted(unique.values(), key=lambda r: (
        r["player"], r["game"], r["fen"], r["ply"], r["move"]))
    eligible = [r for r in canonical if r["ply"] >= min_ply]
    by_fen = defaultdict(lambda: defaultdict(list))
    for row in eligible:
        by_fen[row["fen"]][row["player"]].append(row)
    return canonical, by_fen, duplicates, early_excluded


def compare_shared_fen_assignments(
    positions: list[dict],
    predictions: dict[str, dict[str, str]],
    *,
    player_a: str,
    player_b: str,
    profile_a: str,
    profile_b: str,
    min_ply: int = 17,
) -> dict:
    """Evaluate the requested and swapped profile/target assignments.

    All four profile/target scores use the *same* exact FEN set, with a uniform
    weight per FEN and a within-FEN fraction of distinct historical games.
    This prevents source-game counts and repeated identical records from
    giving a shared position greater weight. Games are NOT independent across
    FENs; consequently no confidence interval or significance is inferred.
    """
    for field, value in (("player_a", player_a), ("player_b", player_b),
                         ("profile_a", profile_a), ("profile_b", profile_b)):
        _required_str(value, field)
    if player_a == player_b or profile_a == profile_b:
        raise ValueError("comparison requires two distinct players and profiles")
    if type(min_ply) is not int or not 1 <= min_ply <= 500:
        raise ValueError("min_ply must be an integer between 1 and 500")
    if not isinstance(predictions, dict) or set(predictions) != {profile_a, profile_b}:
        raise ValueError("exactly two declared prediction profiles are required")

    canonical, grouped, duplicates, excluded = _validate_positions(
        positions, {player_a, player_b}, min_ply)
    shared = {fen: both for fen, both in grouped.items()
              if player_a in both and player_b in both}
    if len(shared) > MAX_SHARED_FENS:
        raise ValueError("shared position budget exceeded")
    expected = set(shared)
    normalized_predictions = {}
    import chess

    for profile in (profile_a, profile_b):
        choices = predictions[profile]
        if not isinstance(choices, dict) or set(choices) != expected:
            raise ValueError("predictions must match exactly the shared FEN set")
        clean = {}
        for fen in sorted(choices):
            board = chess.Board(fen)
            clean[fen] = _legal_move(board, choices[fen], "profile prediction")
        normalized_predictions[profile] = clean

    encoded = json.dumps({
        "rows": canonical,
        "predictions": normalized_predictions,
        "min_ply": min_ply,
        "roles": {"player_a": player_a, "player_b": player_b,
                  "profile_a": profile_a, "profile_b": profile_b},
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = sha256(encoded.encode("utf-8")).hexdigest()

    limits = {
        "opening_filter": (
            "Chronological ply threshold only; it does not establish that "
            "positions are genuinely outside an opening repertoire."),
        "split_provenance": "NOT_VERIFIED",
        "inference": "DESCRIPTIVE_NO_INDEPENDENCE_CLAIM",
        "interpretation": (
            "This assignment contrast is not independent historical validation, "
            "a player identity classifier, a strength measure, or fidelity proof."),
    }
    result = {
        "schema": SCHEMA,
        "status": "NO_EXACT_SHARED_FENS" if not shared
                  else "DESCRIPTIVE_ONLY_NOT_HISTORICALLY_VALIDATED",
        "input_evidence_sha256": digest,
        "matched_fens": len(shared),
        "early_rows_excluded": excluded,
        "unmatched_fens_excluded": len(grouped) - len(shared),
        "duplicate_records_ignored": duplicates,
        "distinct_game_ids_by_player": {
            player: len({r["game"] for both in shared.values()
                         for r in both.get(player, [])})
            for player in (player_a, player_b)
        },
        "observations_by_player": {
            player: sum(len(both.get(player, [])) for both in shared.values())
            for player in (player_a, player_b)
        },
        "accuracy_by_profile_and_player": {
            profile: {player: None for player in (player_a, player_b)}
            for profile in (profile_a, profile_b)
        },
        "declared_assignment_score": None,
        "swapped_assignment_score": None,
        "contrast": None,
        "limitations": limits,
    }
    if not shared:
        return result

    scores = result["accuracy_by_profile_and_player"]
    for profile in (profile_a, profile_b):
        for player in (player_a, player_b):
            accuracies = [
                sum(row["move"] == normalized_predictions[profile][fen]
                    for row in shared[fen][player]) / len(shared[fen][player])
                for fen in sorted(shared)
            ]
            scores[profile][player] = sum(accuracies) / len(accuracies)

    assigned = (scores[profile_a][player_a] + scores[profile_b][player_b]) / 2
    swapped = (scores[profile_a][player_b] + scores[profile_b][player_a]) / 2
    result["declared_assignment_score"] = assigned
    result["swapped_assignment_score"] = swapped
    result["contrast"] = assigned - swapped
    return result


def run_shared_fen_file(input_path: Path, output_path: Path, *,
                        ack_unsealed: bool = False) -> dict:
    """Load a bounded local JSON, compare decisions, and create a new JSON.

    Explicit acknowledgement is required because this function cannot know
    whether an external caller is providing development data or a final
    reserved research cohort. It cannot independently verify corpus rights,
    provenance, data splits, or that historical decisions were not used to
    tune the supplied profiles.
    """
    if ack_unsealed is not True:
        raise ValueError("Explicit nonsealed research input acknowledgement required")
    source = Path(input_path)
    output = Path(output_path)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Refusing to overwrite existing report")
    if source.is_symlink():
        raise ValueError("Symlinked input is not permitted")
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError("Cannot safely read input JSON") from exc
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_BYTES:
            raise ValueError("Input must be a regular JSON file within size limit")
        payload = stream.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds size limit")
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid JSON input") from exc
    required = {"positions", "predictions", "player_a", "player_b",
                "profile_a", "profile_b"}
    accepted = required | {"min_ply"}
    if (not isinstance(document, dict) or
            not required.issubset(document) or
            not set(document).issubset(accepted)):
        raise ValueError("Input JSON has missing or unexpected fields")
    report = compare_shared_fen_assignments(
        document["positions"], document["predictions"],
        player_a=document["player_a"], player_b=document["player_b"],
        profile_a=document["profile_a"], profile_b=document["profile_b"],
        min_ply=document.get("min_ply", 17))
    report["input_file_sha256"] = sha256(payload).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create protects any preexisting report, even across processes.
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return report
