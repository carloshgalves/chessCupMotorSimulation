"""Opt-in normalization, provenance and real UCI integration."""
import json
import os
from pathlib import Path

import chess.pgn
import pytest

from chesssim.arena import ArenaManager, normalize_config
from chesssim.cup_games import play_cup_game
from chesssim.cup_rules import normalize, stage_game
from chesssim.leagues import default_engine_runner, normalize_config as league_config
from chesssim.matches import play_duel
from chesssim.qualified_runtime import (
    BOOKS, MODE, NODE_CAP, PROFILE_KEYS, SLUGS, VERSION, assets, headers,
    resolve, verify_headers, verify_openings,
)


def test_legacy_shape_preserved_and_qualified_is_explicit():
    raw = {"white": "fischer", "black": "tal", "mode": "depth", "depth": 6}
    assert "personality_mode" not in normalize_config(raw)
    raw = {"white": "fischer", "black": "kasparov", "personality_mode": MODE}
    assert normalize_config(raw)["mode"] == MODE
    with pytest.raises(ValueError):
        normalize_config({**raw, "depth": 16})
    with pytest.raises(ValueError):
        normalize_config({**raw, "black": "carlsen"})


@pytest.mark.parametrize("factory", [normalize, league_config])
def test_only_real_qualified_participants(factory):
    raw = {"name": "Teste homologado", "participants": sorted(SLUGS),
           "personality_mode": MODE, "execution_mode": "engine_full"}
    assert factory(raw)["personality_mode"] == MODE
    with pytest.raises(ValueError):
        factory({**raw, "execution_mode": "fast_statistical"})
    with pytest.raises(ValueError):
        factory({**raw, "participants": ["fischer", "tal", "karpov", "carlsen"]})


def test_missing_bundle_no_fallback(tmp_path):
    with pytest.raises(ValueError, match="Bundle homologado"):
        resolve(tmp_path, SLUGS)


def test_headers_reject_modified_book_and_depth():
    frozen = assets()
    resources = {"engine_sha256": frozen["fischer"]["hashes"]["rodent"],
                 "profile_sha256": {s: frozen[s]["hashes"][PROFILE_KEYS[s]] for s in SLUGS},
                 "book_sha256": {s: BOOKS[s][0] for s in SLUGS},
                 "depths": {s: frozen[s]["depth"] for s in SLUGS},
                 "personality_mode": MODE, "version": VERSION, "node_cap": NODE_CAP}
    game = chess.pgn.Game()
    headers(game, "fischer", "kasparov", resources, 2029, 1)
    verify_headers(game, "fischer", "kasparov", resources, 2029, 1)
    game.headers["ProductDepthBlack"] = "16"
    with pytest.raises(ValueError):
        verify_headers(game, "fischer", "kasparov", resources, 2029, 1)
    headers(game, "fischer", "kasparov", resources, 2029, 1)
    resources["book_sha256"]["fischer"] = "0" * 64
    with pytest.raises(ValueError):
        verify_headers(game, "fischer", "kasparov", resources, 2029, 1)


@pytest.mark.skipif(not os.getenv("CHESSSIM_QUALIFIED_RODENT"), reason="Requires pinned real Rodent bundle")
def test_real_qualified_three_execution_paths(tmp_path, monkeypatch):
    root = Path(os.environ["CHESSSIM_QUALIFIED_RODENT"])
    folder, players, books, depths, resources = resolve(root, sorted(SLUGS))
    assert depths == {"fischer": 16, "tal": 16, "karpov": 16, "kasparov": 20,
                      "alekhine": 16, "botvinnik": 20, "smyslov": 20, "petrosian": 16,
                      "spassky": 20, "lasker": 20, "rubinstein": 20, "anand": 20,
                      "steinitz": 20, "anderssen": 16, "tarrasch": 20, "marshall": 16,
                      "nimzowitsch": 20, "reti": 20, "larsen": 16, "topalov": 20}
    pair = ("topalov", "steinitz")
    _, p, b, d, res = resolve(root, pair)
    output = play_duel(folder, tmp_path, p[pair[0]], p[pair[1]], depth=20,
                       reverse=False, max_plies=24, use_books=False,
                       historical_openings=b, node_cap=NODE_CAP,
                       product_depths=d, qualified_resources=res, opening_seed=2029)
    report = json.loads((output / "summary.json").read_text())
    assert report["qualified_resources"] == res
    with (output / "jogo_1.pgn").open() as stream:
        game = chess.pgn.read_game(stream)
    assert game is not None and not game.errors
    verify_headers(game, *pair, res, 2029, 1)
    verify_openings(game, *pair, books, 2029, 1)
    assert len(list(game.mainline_moves())) > 16

    # Exercise the actual Arena session/preflight path with a bounded real runner.
    def arena_runner(root, output, white, black, **kwargs):
        assert kwargs["product_depths"] == d
        kwargs["max_plies"] = 24
        return play_duel(root, output, white, black, **kwargs)

    manager = ArenaManager(root, tmp_path / "arena", runner=arena_runner)
    try:
        manager.launch({"white": pair[0], "black": pair[1], "personality_mode": MODE,
                        "reverse": False})
        manager._thread.join(timeout=180)
        assert not manager._thread.is_alive()
        session = manager.status()["session"]
        assert session["status"] in ("COMPLETED", "INCOMPLETE")
        assert session["config"]["personality_mode"] == MODE
    finally:
        manager.shutdown()

    cfg = normalize({"name": "Copa homologada", "preset": "custom",
                     "participants": sorted(SLUGS), "personality_mode": MODE})
    spec = stage_game({"id": "r01-m01", "round": 1, "players": ["anderssen", "larsen"]}, cfg, 0, 0)
    game, meta = play_cup_game(root, spec, cfg, resources, lambda: False, max_plies=24)
    verify_headers(game, spec["white"], spec["black"], resources, cfg["seed"], spec["round"])
    verify_openings(game, spec["white"], spec["black"], books, cfg["seed"], spec["round"])
    assert meta["plies"] > 16
    assert game.headers["TimeControl"] == "40/5400:1800+30"

    import chesssim.leagues as leagues
    original = leagues._play_one

    def bounded(*args, **kwargs):
        args = list(args)
        args[6] = 24
        return original(*args, **kwargs)

    monkeypatch.setattr(leagues, "_play_one", bounded)
    cfg = league_config({"name": "Liga homologada", "participants": sorted(SLUGS),
                         "personality_mode": MODE})
    match = {"white": "marshall", "black": "nimzowitsch", "round": 1}
    game, meta = default_engine_runner(root, match, cfg, lambda: False)
    verify_headers(game, "marshall", "nimzowitsch", resources, cfg["seed"], 1)
    verify_openings(game, "marshall", "nimzowitsch", books, cfg["seed"], 1)
    assert meta["plies"] > 16
    assert game.headers["TimeControl"] == "+".join(map(str, cfg["clock"]))
