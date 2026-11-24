"""Product release contract for Tal Balanced.

Explicit approval freezes only the identified motor-base.
"""
import json
from pathlib import Path

from chesssim.tal_balanced_clock_replication import BALANCED_SHA
from chesssim.tal_strength_doe import TAL_ORIGINAL_SHA, VARIANTS


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "docs/evidence/tal-balanced-d16-release-review.json"
FROZEN = ROOT / "src/chesssim/data/qualified_game_assets.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_tal_candidate_is_sha_bound_and_explicitly_approved():
    p = read_json(PROPOSAL)
    frozen = read_json(FROZEN)
    assert p["schema"] == "chesssim-tal-product-release-review-v1"
    assert p["identity"] == "tal-balanced-d16"
    assert p["status"] == "APPROVED_PRODUCT_MOTOR_BASE"
    assert p["release_decision"] == "EXPLICIT_MAINTAINER_PRODUCT_APPROVAL"
    assert p["release_approval"]["date"] == "2026-11-24"
    assert p["research_followup"]["confirmatory_noninferiority"] is False
    assert frozen["players"]["tal"]["hashes"]["tal_balanced_profile"] == BALANCED_SHA
    assert p["official_roster_changed"] is False
    assert p["official_tal_profile_modified"] is False
    assert p["fischer_frozen_asset_unchanged"] is True
    assert p["human_fide_elo_equivalence_claimed"] is False
    assert set(frozen["players"]) == {"fischer", "tal", "kasparov"}
    assert frozen["players"]["fischer"]["status"] == "FROZEN_GAME_ASSET"
    assert frozen["players"]["fischer"]["qualification_run_id"] == 45097738965
    assert p["original_tal_profile_sha256"] == TAL_ORIGINAL_SHA
    assert p["candidate"]["profile_sha256"] == BALANCED_SHA
    assert p["candidate"]["depth"] == 16
    assert p["candidate"]["fixed_depth"] == 16
    assert p["candidate"]["alias"] == VARIANTS["balanced16"]["alias"]
    assert p["candidate"]["overrides"] == VARIANTS["balanced16"]["overrides"]
    assert p["candidate"]["book_override"] is False
    assert p["candidate"]["uci_node_cap"] == 5_000_000


def test_three_independent_source_reports_are_identified_and_tune_failure_retained():
    p = read_json(PROPOSAL)
    ex = p["experiments"]
    assert ex["selection"]["run_id"] == 45197012079
    assert ex["selection"]["selected_candidate"] == "balanced16"
    assert ex["selection"]["confirmation_gain_ci95_pp"][0] > 0
    assert ex["style_retrospective"]["run_id"] == 45216462418
    assert ex["style_retrospective"]["nonblind"] is True
    assert ex["style_retrospective"]["style_gate"] is True
    assert ex["style_retrospective"]["repeatability_gate"] is True
    assert ex["style_retrospective"]["retrospective_clock_gate"] is False
    assert ex["style_retrospective"]["status"] == "DO_NOT_FREEZE_TAL_BALANCED"
    assert ex["style_retrospective"]["retrospective_clock_gain_ci95_pp"][0] < 0
    assert ex["fresh_fit_clock_replication"]["run_id"] == 45229239851
    assert ex["fresh_fit_clock_replication"]["status"] == (
        "ELIGIBLE_FOR_TAL_BALANCED_FURTHER_RELEASE_REVIEW")
    assert ex["fresh_fit_clock_replication"]["all_five_strength_gates_passed"]
    assert ex["fresh_fit_clock_replication"]["censored_games"] == 0
    assert ex["fresh_fit_clock_replication"]["new_roots"] == 48
    assert ex["fresh_fit_clock_replication"]["completed_games"] == 288
    assert ex["fresh_fit_clock_replication"]["focal_clock_forfeits"] == 0
    for source in ex.values():
        assert len(source["report_sha256"]) == 64
        assert all(ch in "0123456789abcdef" for ch in source["report_sha256"])


def test_288_clock_result_is_genuinely_paired_and_not_an_elo_claim():
    p = read_json(PROPOSAL)
    ex = p["experiments"]["fresh_fit_clock_replication"]
    wdl = {}
    for key in ("original_vs_simple", "balanced_vs_simple", "balanced_vs_original"):
        row = ex[key]
        assert row["games"] == 96
        assert row["wins"] + row["draws"] + row["losses"] == 96
        score = (row["wins"] + row["draws"]/2) / row["games"]
        assert abs(score - row["score"]) < 1e-10
        wdl[key] = score
    gain_pp = 100*(wdl["balanced_vs_simple"] - wdl["original_vs_simple"])
    assert abs(gain_pp - ex["paired_gain_pp"]) < 1e-10
    assert ex["paired_gain_ci95_pp"][0] > 0
    assert wdl["balanced_vs_simple"] >= .40
    assert wdl["balanced_vs_original"] >= .50
    assert p["human_fide_elo_equivalence_claimed"] is False
    assert ex["clock"] == "10+2"
    assert p["experiments"]["style_retrospective"]["historical_games"] == 66
    assert len(p["source_pgn_sha256"]) == 64
    assert any("TUNE" in text and "failed" in text.lower()
               for text in p["important_limitations"])
