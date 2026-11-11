"""Season-separated probability diagnostics on verified UCI evidence (#61)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import subprocess
import sys

import pytest

from chesssim import fast_statistical_audit as audit

ROOT = Path(__file__).resolve().parents[1]


def record(season: int, game: int, *, result="1-0", modality="rapid") -> dict:
    event = f"league-{season:016x}"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=season)
    return {
        "id": f"league:{event}:r{game:03d}-g01",
        "event_id": event,
        "result": result,
        "modality": modality,
        "white": "fischer",
        "black": "tal",
        "status": "COMPLETED",
        "source_kind": "engine_full",
        "origin": "league",
        "rating_eligible": True,
        "receipt_sha256": f"{season:032x}{game:032x}",
        "evidence_pgn_sha256": f"{game:032x}{season:032x}",
        "recorded_utc": now.isoformat(),
    }


def sample(seasons=8, games_per_season=12) -> list[dict]:
    return [
        record(s, g, result=("1-0", "1/2-1/2", "0-1")[(s + g) % 3],
               modality="rapid" if s % 2 else "blitz")
        for s in range(1, seasons + 1)
        for g in range(1, games_per_season + 1)
    ]


def test_nominal_probabilities_are_identical_to_actual_fast_generator_thresholds():
    from chesssim import leagues
    assert audit.NOMINAL_FAST_WDL == (0.38, 0.24, 0.38)
    # The generator still returns an outcome, not an invented probability model.
    assert "seed-only" in leagues.projected_result.__doc__
    assert sum(audit.NOMINAL_FAST_WDL) == pytest.approx(1.0)


def test_no_real_verified_seasons_never_claims_calibration():
    report = audit.evaluate_verified_games([])
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["evaluation"] is None
    assert report["split"] is None
    assert report["model_status"] == "DEMONSTRATION_NOT_CALIBRATED"
    few = audit.evaluate_verified_games(sample(seasons=3))
    assert few["evaluation"] is None


def test_one_year_holdout_is_split_by_competition_without_leakage():
    data = sample()
    result = audit.evaluate_verified_games(data)
    assert result["status"] == "DESCRIPTIVE_HOLDOUT_NOT_CALIBRATED"
    assert result["verified_uci_games"] == 96
    split = result["split"]
    assert split["train_games"] == 72
    assert split["holdout_games"] == 24
    assert split["train_seasons"] == [f"league-{n:016x}" for n in range(1, 7)]
    assert split["holdout_seasons"] == [f"league-{n:016x}" for n in (7, 8)]
    assert split["no_overlap"]
    metrics = result["evaluation"]
    assert metrics["fast_nominal"]["games"] == 24
    assert metrics["fast_nominal"]["observed_wdl_counts"] == {
        "white_win": 8, "draw": 8, "black_win": 8}
    assert metrics["empirical_baseline"]["training_outcome_counts"] == [24, 24, 24]
    assert metrics["cluster_ci95"] is None
    assert set(metrics["by_modality_fast_nominal"]) == {"rapid", "blitz"}


def test_holdout_results_never_influence_training_prior():
    data = sample()
    a = audit.evaluate_verified_games(data)
    edited = [{**x, "result": "0-1"} if x["event_id"] in
              {f"league-{n:016x}" for n in (7, 8)} else x for x in data]
    b = audit.evaluate_verified_games(edited)
    assert (a["evaluation"]["empirical_baseline"]["probabilities"] ==
            b["evaluation"]["empirical_baseline"]["probabilities"])
    assert a["evaluation"]["fast_nominal"] != b["evaluation"]["fast_nominal"]
    assert a["input_verified_records_sha256"] != b["input_verified_records_sha256"]


def test_bootstrap_is_deterministic_and_clusters_by_season():
    data = sample(seasons=20, games_per_season=8)
    a = audit.evaluate_verified_games(data)
    b = audit.evaluate_verified_games(list(reversed(data)))
    assert a == b
    ci = a["evaluation"]["cluster_ci95"]
    assert ci["method"] == "season_cluster_bootstrap"
    assert ci["iterations"] == 300
    for key in ("fast_minus_empirical_brier_ci95",
                "fast_minus_empirical_log_loss_ci95",
                "observed_draw_fraction_ci95"):
        assert len(ci[key]) == 2
        assert all(math.isfinite(value) for value in ci[key])
        assert ci[key][0] <= ci[key][1]


def test_the_multiclass_scoring_rule_has_correct_scale():
    games = [record(1, i, result="1/2-1/2") for i in range(1, 5)]
    m = audit._metrics(games, (0.38, 0.24, 0.38))
    assert m["multiclass_brier"] == pytest.approx(0.8664)
    assert m["log_loss"] == pytest.approx(-math.log(0.24))
    assert m["white_minus_black_win_fraction"] == 0


def test_rejected_season_prevents_selective_survivorship_analysis():
    report = audit.evaluate_verified_games(sample(), rejected_seasons=1,
                                           projections_seen=400)
    assert report["status"] == "BLOCKED_UNVERIFIED_SEASONS"
    assert report["evaluation"] is None
    assert report["excluded_synthetic_projections"] == 400


def test_insufficient_games_do_not_publish_overconfident_statistics():
    report = audit.evaluate_verified_games(sample(seasons=6, games_per_season=2))
    assert report["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["split"] is not None
    assert report["evaluation"] is None


@pytest.mark.parametrize(("field", "bad"), [
    ("source_kind", "fast_statistical"),
    ("origin", "projection"),
    ("rating_eligible", False),
    ("status", "INCOMPLETE"),
    ("result", "*"),
    ("event_id", "../../private"),
    ("recorded_utc", "2026-10-01"),
    ("receipt_sha256", "invalid"),
    ("evidence_pgn_sha256", None),
    ("white", None),
    ("modality", "fictional"),
])
def test_unverified_uci_record_is_never_included(field, bad):
    rows = sample()
    rows[0] = {**rows[0], field: bad}
    with pytest.raises(ValueError):
        audit.evaluate_verified_games(rows)


def test_duplicate_game_identity_fails_even_with_different_results():
    games = sample()
    games.append({**games[0], "result": "0-1"})
    with pytest.raises(ValueError):
        audit.evaluate_verified_games(games)


def test_timestamps_must_be_consistent_inside_each_season():
    games = sample()
    games[1] = {**games[1], "recorded_utc": "2026-01-01T00:00:00+00:00"}
    with pytest.raises(ValueError, match="temporal"):
        audit.evaluate_verified_games(games)


def test_readonly_adapter_excludes_projections_and_uses_uci_proof(monkeypatch, tmp_path):
    seen = []
    def verified(workspace):
        seen.append(workspace)
        return sample(), [{"source_kind": "fast_statistical"}], 0
    monkeypatch.setattr(audit, "verified_league_games", verified)
    path = tmp_path / "games"
    result = audit.audit_workspace(path)
    assert seen == [path]
    assert result["verified_uci_games"] == 96
    assert result["excluded_synthetic_projections"] == 1


def test_export_is_exclusive_and_does_not_touch_source_files(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "legal.json"
    source.write_text("untouched", encoding="utf-8")
    monkeypatch.setattr(audit, "verified_league_games",
                        lambda _workspace: (sample(), [], 0))
    out = tmp_path / "score.json"
    result = audit.export_audit(workspace, out)
    assert json.loads(out.read_text(encoding="utf-8")) == result
    assert source.read_text(encoding="utf-8") == "untouched"
    with pytest.raises(ValueError):
        audit.export_audit(workspace, out)
    assert not list(tmp_path.glob(".fast-audit-*.tmp"))


def test_cli_exposes_offline_audit_without_running_any_engine():
    r = subprocess.run([sys.executable, "-m", "chesssim", "fast-audit", "--help"],
                       cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert "--workspace" in r.stdout and "--out" in r.stdout
