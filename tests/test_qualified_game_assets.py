"""Immutable Fischer product-release attestation; distinct from human Elo."""
import json
from importlib.resources import files

from chesssim.catalog import find_player
from chesssim.fischer_final_gate import FOCAL_DEPTH, RODENT_HASHES


def test_only_explicitly_qualified_original_fischer_is_frozen():
    manifest=json.loads(files("chesssim.data").joinpath("qualified_game_assets.json").read_text())
    assert manifest["schema"]=="chesssim-qualified-game-assets-v1"
    assert set(manifest["players"])=={"fischer"}
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
