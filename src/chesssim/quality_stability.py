"""Depth-stability report for *identical* independently audited chess decisions.

This is a descriptive UCI search-stability diagnostic, never an Elo estimate or
historical-style validation. Inputs remain immutable; no depth overwrites another.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median

PROTOCOL = "depth-quality-comparison-v9.2"
CLASSIFICATIONS = ("under_100cp", "mistake_100_249cp", "blunder_250plus_cp")


def decision_class(cp_loss: int | float) -> str:
    if isinstance(cp_loss, bool) or not isinstance(cp_loss, (int, float)):
        raise ValueError("Missing or invalid centipawn loss")
    if not isfinite(cp_loss) or cp_loss < 0:
        raise ValueError("Centipawn loss must be finite and non-negative")
    if cp_loss >= 250:
        return CLASSIFICATIONS[2]
    if cp_loss >= 100:
        return CLASSIFICATIONS[1]
    return CLASSIFICATIONS[0]


def compare_depth_evaluations(before: dict, after: dict) -> dict:
    """Compare two *unmodified* depth-specific evaluations of one move."""
    start, end = before["cp_loss"], after["cp_loss"]
    first, last = decision_class(start), decision_class(end)
    best_a, best_b = before.get("best_move"), after.get("best_move")
    return {
        "depth_from": before["depth"], "depth_to": after["depth"],
        "cp_loss_from": start, "cp_loss_to": end,
        "cp_loss_delta": end - start,
        "absolute_cp_loss_delta": abs(end - start),
        "classification_from": first, "classification_to": last,
        "classification_changed": first != last,
        "best_move_from": best_a, "best_move_to": best_b,
        # Missing PV is UNKNOWN, not proof that the best move changed.
        "best_move_changed": best_a != best_b if best_a is not None and best_b is not None else None,
        "mate_affected": bool(before.get("mate_affected") or after.get("mate_affected")),
        "search_disagreement_changed": (
            before["search_disagreement"] != after["search_disagreement"]
            if "search_disagreement" in before and "search_disagreement" in after else None
        ),
    }


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_report(path: Path) -> tuple[dict, str]:
    digest = _hash(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("inputs"), dict) or (
            not isinstance(doc.get("decisions"), list)):
        raise ValueError("Quality report must have inputs and decisions")
    inputs = doc["inputs"]
    depth = inputs.get("depth")
    if type(depth) is not int or not 1 <= depth <= 35:
        raise ValueError("Quality report has invalid analysis depth")
    if (not isinstance(inputs.get("pgn_sha256"), str) or
            not isinstance(inputs.get("stockfish_sha256"), str) or
            not inputs["pgn_sha256"] or not inputs["stockfish_sha256"]):
        raise ValueError("Quality report lacks source fingerprints")
    if "analysis_limit" in inputs and inputs["analysis_limit"] != {
            "kind": "depth", "value": depth}:
        raise ValueError("Quality report has inconsistent search limit")
    if _hash(path) != digest:
        raise ValueError("Quality report changed during loading")
    return doc, digest


def _alignment_key(row: dict) -> tuple:
    if not isinstance(row, dict):
        raise ValueError("Quality decision must be a JSON object")
    # Original game index must identify the *source* game, before deduplication.
    source = row.get("source_game_index", row.get("game_index"))
    if source is not None and (type(source) is not int or source < 0):
        raise ValueError("Invalid source game index")
    for key in ("player", "color", "fullmove", "played"):
        if key not in row:
            raise ValueError(f"Quality decision lacks {key}")
    player, color, fullmove, played = (
        row["player"], row["color"], row["fullmove"], row["played"]
    )
    if (not isinstance(player, str) or not player or
            color not in ("white", "black") or
            type(fullmove) is not int or fullmove < 1 or
            not isinstance(played, str) or not played):
        raise ValueError("Quality decision has invalid identity")
    ply = row.get("ply")
    if ply is not None and (type(ply) is not int or ply < 1):
        raise ValueError("Quality decision has invalid ply")
    fen = row.get("fen")
    if fen is not None and (not isinstance(fen, str) or not fen):
        raise ValueError("Quality decision has invalid FEN")
    # None is allowed only for legacy reports; do not silently match a
    # report lacking FEN/ply with one where they are provided.
    return (source, player, color, fullmove, ply, played, fen)


def _duplicates(doc: dict) -> set[int]:
    duplicate = set()
    for collection in (doc.get("games", []), doc.get("excluded_duplicate_games", [])):
        if not isinstance(collection, list):
            raise ValueError("Invalid game provenance collection")
        for game in collection:
            if not isinstance(game, dict):
                raise ValueError("Invalid game provenance")
            if game.get("duplicate_mainline") or game.get("diagnostic_only"):
                index = game.get("source_game_index", game.get("game_index"))
                if type(index) is not int or index < 0:
                    raise ValueError("Duplicate provenance lacks game index")
                duplicate.add(index)
    return duplicate


def _evaluation(row: dict, depth: int, *, source_index: int) -> dict:
    if row.get("depth", depth) != depth:
        raise ValueError("Row search depth contradicts report depth")
    classification = decision_class(row.get("cp_loss"))
    best_cp, played_cp = row.get("best_eval_cp"), row.get("played_eval_cp")
    if best_cp is not None and played_cp is not None:
        if any(type(v) not in (int, float) or not isfinite(v)
               for v in (best_cp, played_cp)):
            raise ValueError("Non-finite centipawn evaluations")
        raw_gap = best_cp - played_cp
        recorded_gap = row.get("raw_gap_cp")
        if recorded_gap is not None and (
                type(recorded_gap) not in (int, float) or
                not isfinite(recorded_gap) or
                abs(recorded_gap - raw_gap) > 1e-8):
            raise ValueError("Quality row has inconsistent raw centipawn gap")
        if abs(row["cp_loss"] - max(0, raw_gap)) > 1e-8:
            raise ValueError("Quality row has inconsistent centipawn loss")
    payload = {
        "depth": depth,
        "decision_index": source_index,
        "cp_loss": row["cp_loss"],
        "classification": classification,
        "raw_gap_cp": row.get("raw_gap_cp"),
        "best_move": row.get("best_move"),
        "best_eval_cp": row.get("best_eval_cp"),
        "played_eval_cp": row.get("played_eval_cp"),
        "best_mate_in": row.get("best_mate_in"),
        "played_mate_in": row.get("played_mate_in"),
        "mate_affected": row.get("mate_affected"),
        "search_disagreement": row.get("search_disagreement"),
        "best_line_uci": row.get("best_line_uci"),
        "played_line_uci": row.get("played_line_uci"),
        "best_line_san": row.get("best_line_san"),
        "played_line_san": row.get("played_line_san"),
    }
    return payload


def _source_meta(doc: dict) -> dict:
    inp = doc["inputs"]
    return {
        "engine_uci_id": inp.get("engine_uci_id"),
        "engine_configured_options": inp.get("engine_configured_options"),
        "mate_mapping_cp": inp.get("mate_mapping_cp"),
    }


def compare_quality_reports(paths: list[Path], out: Path) -> dict:
    """Join exact historical decisions and preserve every depth's evaluation."""
    if out.exists():
        raise FileExistsError(out)
    if len(paths) < 2:
        raise ValueError("At least two quality reports required")
    if len(set(path.resolve() for path in paths)) != len(paths):
        raise ValueError("Every depth report must be a distinct file")
    loaded = [(*_load_report(path), path) for path in paths]
    if len({doc["inputs"]["depth"] for doc, _, _ in loaded}) != len(loaded):
        raise ValueError("Reports must use distinct analysis depths")
    pgn_hashes = {doc["inputs"]["pgn_sha256"] for doc, _, _ in loaded}
    engine_hashes = {doc["inputs"]["stockfish_sha256"] for doc, _, _ in loaded}
    if len(pgn_hashes) != 1:
        raise ValueError("Cannot compare reports from different PGNs")
    if len(engine_hashes) != 1:
        raise ValueError("Cannot compare reports from different Stockfish executables")
    provenance = [_source_meta(doc) for doc, _, _ in loaded]
    for field in ("engine_uci_id", "engine_configured_options", "mate_mapping_cp"):
        values = [p[field] for p in provenance]
        if any(v is not None for v in values) and (
                any(v is None for v in values) or
                any(v != values[0] for v in values[1:])):
            raise ValueError(f"Cannot compare incompatible {field} provenance")
    loaded.sort(key=lambda t: t[0]["inputs"]["depth"])
    indices = []
    collisions = []
    duplicate_sets = []
    for doc, _, _ in loaded:
        index = defaultdict(list)
        for i, row in enumerate(doc["decisions"]):
            key = _alignment_key(row)
            decision_class(row.get("cp_loss"))
            index[key].append((i, row))
        indices.append(index)
        collisions.append(sum(len(vals) - 1 for vals in index.values() if len(vals) > 1))
        duplicate_sets.append(_duplicates(doc))
    common = set.intersection(*(set(idx) for idx in indices))
    histories = []
    diagnostic_histories = []
    for key in sorted(common, key=lambda k: (
            k[0] if k[0] is not None else -1,
            k[4] if k[4] is not None else ((k[3] - 1) * 2 + (1 if k[2] == "white" else 2)),
            str(k))):
        if any(len(index[key]) != 1 for index in indices):
            continue  # Never choose an arbitrary colliding observation.
        entries = [idx[key][0] for idx in indices]
        duplicate = any(
            (key[0] in duplicates if key[0] is not None else False)
            for duplicates in duplicate_sets
        )
        evaluations = [
            _evaluation(row, doc["inputs"]["depth"], source_index=entry_index)
            for (entry_index, row), (doc, _, _) in zip(entries, loaded)
        ]
        diffs = [
            compare_depth_evaluations(a, b)
            for a, b in zip(evaluations, evaluations[1:])
        ]
        categories = [ev["classification"] for ev in evaluations]
        decisions = {
            "key": {
                "source_game_index": key[0], "player": key[1], "color": key[2],
                "fullmove": key[3], "ply": key[4],
                "played": key[5], "fen": key[6],
            },
            "diagnostic_duplicate": duplicate,
            "evaluations": evaluations,
            "consecutive_changes": diffs,
            "loss_by_depth": {str(ev["depth"]): ev["cp_loss"] for ev in evaluations},
            "difference_last_minus_first": evaluations[-1]["cp_loss"] - evaluations[0]["cp_loss"],
            "range_cp": max(ev["cp_loss"] for ev in evaluations) -
                        min(ev["cp_loss"] for ev in evaluations),
            "max_abs_step_delta": max(abs(change["cp_loss_delta"]) for change in diffs),
            "classification_changed": categories[0] != categories[-1],
            "classification_changed_at_any_step": len(set(categories)) > 1,
            "best_move_changed_at_any_step": any(
                d["best_move_changed"] is True for d in diffs
            ),
            "best_move_change_status": (
                "CHANGED" if any(d["best_move_changed"] is True for d in diffs)
                else "UNKNOWN" if any(d["best_move_changed"] is None for d in diffs)
                else "UNCHANGED"
            ),
            "mate_affected": any(ev["mate_affected"] for ev in evaluations),
        }
        (diagnostic_histories if duplicate else histories).append(decisions)
    ranked = sorted(
        histories,
        key=lambda row: (-row["range_cp"], -row["max_abs_step_delta"],
                         str(row["key"]))
    )
    count = len(histories)
    changed = sum(row["classification_changed"] for row in histories)
    changed_any = sum(row["classification_changed_at_any_step"] for row in histories)
    unstable_move = sum(row["best_move_changed_at_any_step"] for row in histories)
    best_move_statuses = Counter(row["best_move_change_status"] for row in histories)
    best_move_known = best_move_statuses["CHANGED"] + best_move_statuses["UNCHANGED"]
    partial = any(key[0] is None or key[4] is None or key[6] is None
                  for key in common)
    input_data = [
        {"path": str(path), "sha256": digest,
         "depth": doc["inputs"]["depth"],
         "sampled_positions": len(doc["decisions"]),
         "engine_uci_id": doc["inputs"].get("engine_uci_id"),
         "engine_configured_options": doc["inputs"].get("engine_configured_options")}
        for doc, digest, path in loaded
    ]
    # Detect a source report rewritten after loading/analysis, before output.
    if any(_hash(path) != digest for _, digest, path in loaded):
        raise ValueError("A quality report changed while comparing depths")
    result = {
        "protocol": PROTOCOL,
        "status": "EXPLORATORY_DEPTH_STABILITY_NOT_ELO" if count else "NO_ALIGNED_INDEPENDENT_DECISIONS",
        "pgn_sha256": next(iter(pgn_hashes)),
        "stockfish_sha256": next(iter(engine_hashes)),
        "inputs": input_data,
        "depths": [d["depth"] for d in input_data],
        "aligned_decisions": count,
        "all_aligned_decisions": count + len(diagnostic_histories),
        "diagnostic_duplicate_aligned": len(diagnostic_histories),
        "unmatched_or_ambiguous_by_report": [
            len(doc["decisions"]) - count - len(diagnostic_histories)
            for doc, _, _ in loaded
        ],
        "legacy_collision_counts": collisions,
        "classification_changes": changed,
        "classification_changes_at_any_step": changed_any,
        "best_move_changes_at_any_step": unstable_move,
        "best_move_change_status_counts": {
            "changed": best_move_statuses["CHANGED"],
            "unchanged": best_move_statuses["UNCHANGED"],
            "unknown": best_move_statuses["UNKNOWN"],
        },
        "best_move_change_rate_known": unstable_move / best_move_known if best_move_known else None,
        "classification_change_rate": changed / count if count else None,
        "classification_change_rate_any_step": changed_any / count if count else None,
        "mean_cp_loss_range": mean(row["range_cp"] for row in histories) if count else None,
        "median_cp_loss_range": median(row["range_cp"] for row in histories) if count else None,
        "largest_changes": ranked[:20],
        "aligned_history": histories,
        "diagnostic_duplicate_history": diagnostic_histories,
        "warnings": [
            "Matched decisions compare the same PGN move only; different depths do not independently validate strength or historical style.",
            "100/250-cp categories and mate mappings are depth- and search-dependent, not chess.com accuracy or Elo.",
            "Legacy reports without complete game/ply/FEN identifiers have reduced alignment assurance." if partial else
            "Exact game/ply/FEN identifiers were required for alignment.",
            "Root-move forced searches may vary even at the same requested depth.",
            "Unmatched decisions are not assumed stable, nor included in change-rate denominators.",
            "When any principal variation is missing, best-move stability is UNKNOWN rather than unchanged.",
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result
