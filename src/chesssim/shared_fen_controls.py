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

SCHEMA = "chesssim-shared-fen-negative-control-v1"
MAX_ROWS = 50_000
MAX_SHARED_FENS = 4_000


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
        "source_sha256": digest,
        "matched_fens": len(shared),
        "early_rows_excluded": excluded,
        "unmatched_fens_excluded": len(grouped) - len(shared),
        "duplicate_records_ignored": duplicates,
        "distinct_games_by_player": {
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
        "correct_assignment_score": None,
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
    result["correct_assignment_score"] = assigned
    result["swapped_assignment_score"] = swapped
    result["contrast"] = assigned - swapped
    return result
