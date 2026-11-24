import json
from importlib.resources import files

from chesssim.product_karpov_repair import LABELS, points
from chesssim.product_peak_profiles import split_label


def test_screen_covers_all_deeper_candidates():
    assert len(LABELS) == 6
    assert set(LABELS) == {"original16", "original20", "restriction16",
                           "restriction20", "balanced16", "balanced20"}
    assert all(split_label(label)[1] in (16, 20) for label in LABELS)


def test_points_do_not_turn_incomplete_games_into_draws():
    assert points({"result": "*"}, "white") is None
    assert points({"result": "1/2-1/2"}, "black") == .5
    assert points({"result": "1-0"}, "white") == 1
    assert points({"result": "1-0"}, "black") == 0


def test_kasparov_exact_product_registry():
    assets = json.loads(files("chesssim.data").joinpath("qualified_game_assets.json").read_text())
    k = assets["players"]["kasparov"]
    assert k["status"] == "FROZEN_GAME_ASSET"
    assert k["depth"] == 20
    assert k["uci_node_cap"] == 5_000_000
    assert k["hashes"]["kasparov_profile"] == "ff687985e8e2faa000365aba515e2f5b1f3bb517c029da321fa62874546f1d58"
    assert k["overrides"] == {"KingTropism": 65, "OwnAttack": 265, "PiecePressure": 160}
    assert assets["players"]["karpov"]["hashes"]["karpov_profile"] == "0ad16301bf986f2eff29cd182db8544546a617d511d2646b57cd1d9a169335e1"
