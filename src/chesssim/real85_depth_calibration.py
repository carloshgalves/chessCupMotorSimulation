"""Compare the preregistered d4/d6 focal Rodent budgets on the same FIT panel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .matches import file_hash
from .real85_graded_openings import PILOTS, DEPTHS, START_COUNT, _points, _sha, validate


def compare(d4: Path, d6: Path) -> dict:
    """Replay both panels before describing matched outcome differences.

    No fitted model selection or historical Elo; no outer TEST is opened.
    """
    a, b = Path(d4), Path(d6)
    if a.resolve() == b.resolve():
        raise ValueError("Two different experiment directories required")
    baseline = validate(a, focal_depth=4)
    candidate = validate(b, focal_depth=6)
    for key in ("opening_panel_sha256", "source_manifest_sha256",
                "rodent_original_sha256", "stockfish_sha256",
                "profile_sha256", "model_sha256", "book_sha256"):
        if baseline[key] is None or baseline[key] != candidate[key]:
            raise ValueError(f"Unmatched calibration provenance: {key}")
    books = baseline["book_sha256"]
    if set(books) != set(PILOTS) or not all(_sha(v) for v in books.values()):
        raise ValueError("Missing book identities")
    def keyed(root):
        doc = json.loads((root / "results.json").read_text())
        return {(g["player"], g["opening_index"], g["opponent_depth"], g["color"], g["arm"]):
                _points(g["result"], g["color"] == "white")
                for g in doc["games"]}
    old, new = keyed(a), keyed(b)
    if old.keys() != new.keys() or len(old) != 96:
        raise ValueError("Unbalanced calibration matrix")
    players = {}
    for slug in PILOTS:
        players[slug] = {}
        for depth in DEPTHS:
            players[slug][str(depth)] = {}
            for arm in ("shadow", "guided"):
                keys = [(slug, idx, depth, color, arm)
                        for idx in range(START_COUNT) for color in ("white", "black")]
                diffs = [new[k] - old[k] for k in keys]
                players[slug][str(depth)][arm] = {
                    "matched_games": len(keys),
                    "depth4_points": sum(old[k] for k in keys),
                    "depth6_points": sum(new[k] for k in keys),
                    "score_difference": sum(diffs),
                    "improved": sum(d > 0 for d in diffs),
                    "unchanged": sum(d == 0 for d in diffs),
                    "worsened": sum(d < 0 for d in diffs),
                }
    return {
        "schema": "chesssim-real85-focal-depth-comparison-v1",
        "status": "DESCRIPTIVE_FIT_DEPTH_COMPARISON_NOT_HISTORICAL_CALIBRATION",
        "focal_depths": [4, 6],
        "matched_pairs": len(old),
        "total_completed_games": len(old) + len(new),
        "opening_panel_sha256": baseline["opening_panel_sha256"],
        "results_sha256": {"4": file_hash(a/"results.json"), "6": file_hash(b/"results.json")},
        "players": players,
        "historically_validated": False,
        "official_profiles_changed": False,
        "outer_test_used": False,
        "limitations": [
            "The same FIT openings and model weights are used for both budgets.",
            "Repeated and related opening positions are not independent samples.",
            "Score differences are descriptive and not human Elo or a promotion decision.",
            "Do not tune on this diagnostic and then describe it as independent validation.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth4", required=True, type=Path)
    parser.add_argument("--depth6", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    report = compare(args.depth4, args.depth6)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
