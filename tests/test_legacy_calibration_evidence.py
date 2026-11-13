"""Regression gates for legacy Rodent calibration (no real UCI engine needed)."""
from __future__ import annotations

import json

import pytest

from chesssim import calibration as cal


def _metric(positions, predicted):
    correct = sum(move == row["move"] for row, move in zip(positions, predicted))
    return {"decisions": len(positions), "correct": correct,
            "top1": correct / len(positions),
            "white": sum(row["color"] == "white" for row in positions),
            "black": sum(row["color"] == "black" for row in positions),
            "_predicted_moves": predicted}


def _positions(games, move="e2e4"):
    return [{"game": game, "move": move,
             "color": "white" if i % 2 == 0 else "black"}
            for i, game in enumerate(games)]


def _engine_root(tmp_path):
    root = tmp_path / "rodent"
    (root / "personalities").mkdir(parents=True)
    (root / "personalities" / "karpov.txt").write_text(
        "setoption name OwnAttack value 110\n", encoding="utf-8")
    (root / "rodent-iv-plain.exe").write_bytes(b"synthetic-engine")
    return root


def test_paired_results_preserve_position_alignment_not_move_histograms():
    rows = _positions(["g1", "g2"])
    rows[1]["move"] = "d2d4"
    baseline = _metric(rows, ["e2e4", "d2d4"])
    # Identical marginal move frequencies, but both predictions are on the wrong FEN.
    candidate = _metric(rows, ["d2d4", "e2e4"])

    result = cal.paired_holdout_decisions(rows, baseline, candidate)

    assert result["status"] == "EXPLORATORY_DECISION_AGREEMENT_NOT_STYLE_VALIDATION"
    assert result["sampled_games"] == 2
    assert result["baseline_top1"] == 1
    assert result["candidate_top1"] == 0
    assert result["top1_gain"] == -1
    assert result["paired_counts"] == {
        "both_correct": 0, "baseline_only_correct": 2,
        "candidate_only_correct": 0, "both_wrong": 0,
        "different_predictions": 2,
    }
    assert result["macro_top1_by_game"]["gain"] == -1
    assert "_predicted_moves" not in str(result)


def test_paired_results_count_discordance_and_game_macro():
    rows = _positions(["g1", "g1", "g2", "g2"])
    baseline = _metric(rows, ["e2e4", "e2e4", "e2e4", "a2a3"])
    candidate = _metric(rows, ["a2a3", "e2e4", "e2e4", "e2e4"])
    result = cal.paired_holdout_decisions(rows, baseline, candidate)
    assert result["paired_counts"] == {
        "both_correct": 2, "baseline_only_correct": 1,
        "candidate_only_correct": 1, "both_wrong": 0,
        "different_predictions": 2,
    }
    assert result["macro_top1_by_game"] == {
        "baseline": 0.75, "candidate": 0.75, "gain": 0.0,
    }


@pytest.mark.parametrize("baseline,candidate", [
    ({}, {}),
    ({"correct": 1, "_predicted_moves": ["e2e4"]},
     {"correct": 1, "_predicted_moves": []}),
    ({"correct": 0, "_predicted_moves": ["e2e4"]},
     {"correct": 1, "_predicted_moves": ["e2e4"]}),
])
def test_paired_results_fail_closed_on_missing_or_inconsistent_evidence(baseline, candidate):
    with pytest.raises(ValueError):
        cal.paired_holdout_decisions(_positions(["g1"]), baseline, candidate)


def test_legacy_calibration_rejects_misleading_partition_count(tmp_path, monkeypatch):
    root = _engine_root(tmp_path)
    train = _positions(["train"])
    validation = _positions(["heldout"])
    monkeypatch.setattr(cal, "extract_decisions",
                        lambda *a, **kw: (train + validation, {"games_accepted": 6}))
    monkeypatch.setattr(cal, "split_positions",
                        lambda *a, **kw: (train, validation, {"val_games": 5}))
    monkeypatch.setattr(cal, "evaluate_candidate",
                        lambda *a, **kw: pytest.fail("Engine must not be called"))
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="efetivamente amostradas"):
        cal.calibrate_personality(root, tmp_path / "fixture.pgn", output,
                                  target="Mecking", target_slug="mequinho",
                                  match_name="Mecking", min_holdout_games=5)
    assert not output.exists()


def test_legacy_calibration_uses_existing_paired_evaluations_only(tmp_path, monkeypatch):
    root = _engine_root(tmp_path)
    train = _positions(["train"])
    validation = _positions([f"heldout-{i}" for i in range(5)])
    baseline_text = (root / "personalities" / "karpov.txt").read_text()
    monkeypatch.setattr(cal, "extract_decisions",
                        lambda *a, **kw: (train + validation, {"games_accepted": 6}))
    monkeypatch.setattr(cal, "split_positions",
                        lambda *a, **kw: (train, validation, {"val_games": 5}))
    calls = []

    def evaluator(engine_root, profile_rel, positions, depth):
        profile_path = engine_root / "personalities" / profile_rel
        assert profile_path.is_file(), "Must never evaluate a deleted profile"
        personalized = profile_path.read_text() != baseline_text
        calls.append(("train" if positions is train else "holdout", personalized))
        correct = 1 if positions is train and personalized else (
            0 if positions is train else (4 if personalized else 2))
        predicted = [row["move"] if i < correct else "a2a3"
                     for i, row in enumerate(positions)]
        return _metric(positions, predicted)

    monkeypatch.setattr(cal, "evaluate_candidate", evaluator)
    output = tmp_path / "out"
    report = cal.calibrate_personality(
        root, tmp_path / "fixture.pgn", output, target="Mecking",
        target_slug="mequinho", match_name="Mecking",
        trials=1, min_holdout_games=5)

    assert calls == [
        ("train", False), ("train", True),
        ("holdout", False), ("holdout", True),
    ]
    assert report["paired_holdout_decisions"]["baseline_top1"] == 0.4
    assert report["paired_holdout_decisions"]["candidate_top1"] == 0.8
    assert report["paired_holdout_decisions"]["sampled_games"] == 5
    assert "style_fidelity" not in report
    assert "_predicted_moves" not in str(report)
    assert json.loads((output / "calibration.json").read_text()) == report
    assert list((root / "personalities" / "custom").glob("*.txt")) == []


def test_legacy_calibration_cleans_temporary_profile_on_engine_failure(tmp_path, monkeypatch):
    root = _engine_root(tmp_path)
    train = _positions(["train"])
    validation = _positions([f"heldout-{i}" for i in range(5)])
    monkeypatch.setattr(cal, "extract_decisions",
                        lambda *a, **kw: (train + validation, {"games_accepted": 6}))
    monkeypatch.setattr(cal, "split_positions",
                        lambda *a, **kw: (train, validation, {"val_games": 5}))

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic UCI failure")

    monkeypatch.setattr(cal, "evaluate_candidate", broken)
    with pytest.raises(RuntimeError, match="synthetic UCI failure"):
        cal.calibrate_personality(root, tmp_path / "fixture.pgn", tmp_path / "out",
                                  target="Mecking", target_slug="mequinho",
                                  match_name="Mecking", trials=1, min_holdout_games=5)
    assert list((root / "personalities" / "custom").glob("*.txt")) == []
