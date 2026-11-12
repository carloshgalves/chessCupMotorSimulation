"""Experimental four-field FEN position embargo for opt-in fictional PGN training.

The embargo protects all originally reserved validation/holdout positions from
entering training. Holdout has priority over validation. Never publish the
reserved PGN, moves, raw positions or individual reserved game identities.
The original cache remains accessible outside this workflow.
"""
from __future__ import annotations

from collections import Counter

SCHEMA = "chesssim-custom-position-embargo-v1"
MAX_REPLAYED_STATES = 200_000


def _positions(row: dict, min_ply: int, budget: list[int]) -> frozenset[str]:
    game = row["_game"]
    board = game.board()  # Supports the PGN SetUp/FEN initial position.
    found = set()
    for ply, move in enumerate(game.mainline_moves(), start=1):
        if move not in board.legal_moves:
            raise ValueError("PGN com lance ilegal durante auditoria posicional")
        board.push(move)
        if ply < min_ply:
            continue
        budget[0] += 1
        if budget[0] > MAX_REPLAYED_STATES:
            raise ValueError("Orçamento de 200000 posições de auditoria excedido")
        # Match the existing research key: placement, turn, castling, en-passant.
        # Move clocks are intentionally excluded. The post-move board state is
        # included even for the last move, which may be a terminal position.
        found.add(" ".join(board.fen().split()[:4]))
    return frozenset(found)


def apply_embargo(pieces: dict[str, list[dict]], *, min_ply: int
                  ) -> tuple[dict[str, list[dict]], dict]:
    """Quarantine whole training/validation games whose positions leak.

    Holdout is unchanged. Training is checked against ALL originally reserved
    positions (including validation games that must themselves be quarantined).
    Validation conflicts with holdout are removed. Unassessed training is
    quarantined; unassessed reserved games prevent a positive audit status.
    """
    if type(min_ply) is not int or not 1 <= min_ply <= 80:
        raise ValueError("embargo-min-ply deve ser inteiro de 1 a 80")
    if set(pieces) != {"train", "validation", "holdout"}:
        raise ValueError("Partições esperadas ausentes")
    budget = [0]
    states: dict[str, list[frozenset[str]]] = {}
    groups: dict[str, set[str]] = {}
    for group in ("train", "validation", "holdout"):
        states[group] = [_positions(row, min_ply, budget) for row in pieces[group]]
        groups[group] = set().union(*states[group])
    original_reserved = groups["validation"] | groups["holdout"]
    pre_overlap = {
        "train_vs_validation_unique_positions": len(
            groups["train"] & groups["validation"]),
        "train_vs_holdout_unique_positions": len(
            groups["train"] & groups["holdout"]),
        "validation_vs_holdout_unique_positions": len(
            groups["validation"] & groups["holdout"]),
    }
    quarantined = Counter()
    final: dict[str, list[dict]] = {"train": [], "validation": [],
                                    "holdout": list(pieces["holdout"])}
    for row, viewed in zip(pieces["validation"], states["validation"]):
        if not viewed:
            # Remains reserved for safety, but does not count as evaluated.
            quarantined["validation_unassessed_short"] += 1
        elif viewed & groups["holdout"]:
            quarantined["validation_overlap_holdout"] += 1
        else:
            final["validation"].append(row)
    for row, viewed in zip(pieces["train"], states["train"]):
        if not viewed:
            quarantined["train_unassessed_short"] += 1
        elif viewed & original_reserved:
            quarantined["train_overlap_reserved"] += 1
        else:
            final["train"].append(row)
    # The holdout cannot be discarded to make the audit look successful.
    unassessed_holdout = sum(not viewed for viewed in states["holdout"])
    quarantined["holdout_unassessed_short"] = unassessed_holdout
    final_positions = {}
    for group in ("train", "validation", "holdout"):
        keep_ids = {id(row) for row in final[group]}
        final_positions[group] = set().union(*[
            viewed for row, viewed in zip(pieces[group], states[group])
            if id(row) in keep_ids
        ])
    if (final_positions["train"] & final_positions["validation"] or
            final_positions["train"] & final_positions["holdout"] or
            final_positions["validation"] & final_positions["holdout"]):
        raise AssertionError("Vazamento posicional não removido")
    # If any reserved game has no auditable post-move position, no affirmative
    # certification may be issued. A group emptied by quarantine also fails
    # the later train/validation/holdout sufficiency check.
    complete_reserved = (bool(pieces["validation"]) and bool(pieces["holdout"])
                         and quarantined["validation_unassessed_short"] == 0
                         and unassessed_holdout == 0)
    status = ("AUDITED_DISJOINT" if complete_reserved else
              "INCOMPLETE_RESERVED_POSITION_COVERAGE")
    report = {
        "schema": SCHEMA,
        "status": status,
        "min_ply": min_ply,
        "position_key": "POST_MOVE_FEN_FIELDS_1_TO_4_NO_CLOCKS",
        "max_replayed_states": MAX_REPLAYED_STATES,
        "replayed_states": budget[0],
        "scope_note": (
            "Only post-move positions from min_ply onward are audited; "
            "early shared positions are deliberately out of scope."
        ),
        "original_partition_games": {
            group: len(pieces[group]) for group in ("train", "validation", "holdout")
        },
        "output_partition_games": {
            group: len(final[group]) for group in ("train", "validation", "holdout")
        },
        "pre_quarantine_overlap": pre_overlap,
        "quarantined_games": dict(sorted(quarantined.items())),
        "post_quarantine_cross_partition_overlap_unique_positions": 0,
        "reserved_games_with_no_audited_position": (
            quarantined["validation_unassessed_short"] + unassessed_holdout
        ),
        "limitations": [
            "No assurance about positions before min_ply.",
            "No export or mutation of holdout games; source cache remains accessible.",
            "No transposition-based near-duplicate or engine-strength claim.",
            "The embargo is on FEN position equality, not statistical independence.",
            "No UCI engine or sealed filesystem holdout is created.",
        ],
    }
    return final, report
