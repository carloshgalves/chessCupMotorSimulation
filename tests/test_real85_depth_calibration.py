"""Regression checks for the d4/d6 matched experimental protocol."""
import json

import pytest

from chesssim import real85_depth_calibration as calibration
from chesssim import real85_graded_openings as graded


@pytest.mark.parametrize("value", [True, 6.0, "6", 2, 8, None])
def test_depth_is_explicit_and_limited(value):
    with pytest.raises(ValueError, match="4 or 6"):
        graded._focal_depth(value)


def test_depth_comparison_requires_matching_inputs_and_colors(tmp_path, monkeypatch):
    dirs = {d: tmp_path / str(d) for d in (4, 6)}
    for depth, folder in dirs.items():
        folder.mkdir()
        rows = []
        for slug in graded.PILOTS:
            for idx in range(graded.START_COUNT):
                for opponent in graded.DEPTHS:
                    for color in ("white", "black"):
                        for arm in ("shadow", "guided"):
                            result = ("1-0" if color == "white" else "0-1") if depth == 6 else "1/2-1/2"
                            rows.append({"player": slug, "opening_index": idx,
                                         "opponent_depth": opponent, "color": color,
                                         "arm": arm, "result": result})
        (folder / "results.json").write_text(json.dumps({"games": rows}), encoding="utf-8")
    pairs = []
    def replay(folder, *, focal_depth):
        assert dirs[focal_depth] == folder
        pairs.append(focal_depth)
        return {**{k: "a"*64 for k in ("opening_panel_sha256", "source_manifest_sha256",
                                        "rodent_original_sha256", "stockfish_sha256")},
                "profile_sha256": {"simple": "b"*64},
                "model_sha256": {slug: "c"*64 for slug in graded.PILOTS},
                "book_sha256": {slug: "d"*64 for slug in graded.PILOTS}}
    monkeypatch.setattr(calibration, "validate", replay)
    report = calibration.compare(dirs[4], dirs[6])
    assert pairs == [4, 6]
    assert report["matched_pairs"] == 96 and report["total_completed_games"] == 192
    assert report["players"]["tal"]["6"]["guided"] == {
        "matched_games": 6, "depth4_points": 3.0, "depth6_points": 6.0,
        "score_difference": 3.0, "improved": 6, "unchanged": 0, "worsened": 0}
    assert not report["historically_validated"]
    def detached(folder, *, focal_depth):
        data = replay(folder, focal_depth=focal_depth)
        if focal_depth == 6:
            data["model_sha256"] = {"tal": "e"*64}
        return data
    monkeypatch.setattr(calibration, "validate", detached)
    with pytest.raises(ValueError, match="model_sha256"):
        calibration.compare(dirs[4], dirs[6])
