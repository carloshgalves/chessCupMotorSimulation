"""Immutable Fischer product-release attestation; distinct from human Elo."""
import json
from importlib.resources import files
from pathlib import Path
import tomllib

from chesssim.catalog import find_player
from chesssim.fischer_final_gate import FOCAL_DEPTH, RODENT_HASHES


def test_explicitly_qualified_motor_bases_are_frozen():
    manifest=json.loads(files("chesssim.data").joinpath("qualified_game_assets.json").read_text())
    assert manifest["schema"]=="chesssim-qualified-game-assets-v1"
    assert set(manifest["players"])=={"fischer", "tal", "karpov", "kasparov",
                                      "alekhine", "botvinnik", "smyslov", "petrosian",
                                      "spassky", "lasker", "rubinstein", "anand",
                                      "steinitz", "anderssen", "tarrasch", "marshall",
                                      "nimzowitsch", "reti", "larsen", "topalov"}
    f=manifest["players"]["fischer"]
    assert f["status"]=="FROZEN_GAME_ASSET"
    assert f["depth"]==FOCAL_DEPTH==16
    assert f["profile"]=="Rodent IV original Fischer"
    assert find_player("fischer").rodent_alias=="Fischer"
    assert f["hashes"]=={
        "rodent":RODENT_HASHES["rodent"],
        "fischer_profile":RODENT_HASHES["fischer"],
        "simple_control":RODENT_HASHES["simple"],
    }
    assert f["qualification_run_id"]==45097738965
    assert f["source_baseline_run_id"]==44970751726
    assert all(f["gates"].values())
    assert len(f["qualification_report_sha256"])==64
    assert f["internal_opening_book"] is False
    assert f["phase_adaptive_depth"] is False
    assert f["neural_override"] is False
    assert not any("Elo human" in x for x in f["restrictions"])


def test_frozen_manifest_is_explicitly_packaged_in_distribution():
    project=tomllib.loads((Path(__file__).resolve().parents[1]/"pyproject.toml").read_text())
    package_data=project["tool"]["setuptools"]["package-data"]["chesssim.data"]
    assert "qualified_game_assets.json" in package_data


def test_wave_one_release_provenance_and_gates():
    from chesssim.qualified_runtime import assets
    for slug in ("alekhine", "botvinnik", "smyslov", "petrosian"):
        a = assets()[slug]
        assert a["qualification_run_id"] == 45597119305
        assert a["qualification_artifact_id"] == 15023757504
        assert len(a["release_artifact_ids"]) == 2
        assert all(a["gates"].values())
        assert sum(a["evidence"]["screen_wdl"]) == 24
        assert sum(a["evidence"]["full_book_duel_wdl"]) == 4
        assert a["evidence"]["screen_score"] >= .40
        assert a["evidence"]["repeat_matches"] == 10


def test_wave_two_release_provenance_and_gates():
    from chesssim.qualified_runtime import assets
    from chesssim.product_wave_release import WAVE2_PRIOR
    for slug, binding in WAVE2_PRIOR.items():
        a = assets()[slug]
        assert a["qualification_run_id"] == 45633448078
        assert a["qualification_artifact_id"] == 15035146859
        assert a["calibration_run_id"] == 45597119316
        assert a["calibration_artifact_id"] == binding[0]
        assert a["hashes"]["profile"] == binding[2]
        assert a["historical_book_digest"] == binding[3]
        assert a["depth"] == 20
        assert len(set(a["release_artifact_ids"])) == 2
        assert all(a["gates"].values())
        assert sum(a["evidence"]["screen_wdl"]) == 24
        assert sum(a["evidence"]["full_book_duel_wdl"]) == 4
        assert a["evidence"]["screen_score"] >= .40
        assert a["evidence"]["repeat_matches"] == 10


def test_wave_three_release_provenance_and_gates():
    from chesssim.qualified_runtime import assets
    evidence = json.loads((Path(__file__).resolve().parents[1] /
                           "docs/evidence/wave-03-release-results.json").read_text())
    expected = {"steinitz", "anderssen", "tarrasch", "marshall",
                "nimzowitsch", "reti", "larsen", "topalov"}
    reports = evidence["reports"]
    assert len(reports) == 16
    assert {r["slug"] for r in reports} == expected
    for slug in expected:
        a = assets()[slug]
        rs = sorted((r for r in reports if r["slug"] == slug), key=lambda r: r["seed"])
        assert [r["seed"] for r in rs] == [2029, 2030]
        assert a["release_artifact_ids"] == [r["artifact_id"] for r in rs]
        assert a["qualification_run_id"] == rs[0]["run_id"] == rs[1]["run_id"]
        assert a["qualification_artifact_id"] == evidence["aggregate_artifacts"][str(rs[0]["run_id"])]
        assert all(r["calibration_report_sha256"] == a["calibration_report_sha256"] for r in rs)
        assert all(r["alias"] == a["alias"] for r in rs)
        assert all(a["gates"].values())
        assert a["evidence"]["screen_score"] >= .40
        assert sum(a["evidence"]["screen_wdl"]) == 24
        assert sum(a["evidence"]["full_book_duel_wdl"]) == 4
        assert a["evidence"]["repeat_matches"] == 10
        assert all(r["status"] == "FULL_PRODUCT_REVIEW_READY" for r in rs)
        assert all(len(r["games"]) == 2 for r in rs)
    assert assets()["topalov"]["wave_variant"] == "balanced"
    assert assets()["topalov"]["depth"] == 20
