"""Tal Balanced product validation: source containment, clocks and style."""
from __future__ import annotations

from hashlib import sha256
import json
import random

import chess
import chess.pgn
import pytest

from chesssim import fischer_final_gate as clock
from chesssim import tal_balanced_validation as gate


def fixture_source(monkeypatch):
    rng = random.Random(1102026)
    rows, games, arms = [], {}, {}
    for i in range(80):
        game = chess.pgn.Game()
        node = game
        for _ in range(50):
            board = node.board()
            if board.is_game_over(claim_draw=True):
                break
            node = node.add_variation(rng.choice(list(board.legal_moves)))
        gid = f"{i + 1:064x}"
        arm = "tune" if i < 66 else "excluded_test" if i < 76 else "fit"
        arms[gid] = arm
        rows.append({
            "mainline_sha256": gid, "color": "white" if i % 2 else "black",
            "plies": node.board().ply(),
        })
        games[gid] = game
    monkeypatch.setattr(gate, "EXPECTED_GAMES", 80)
    monkeypatch.setattr(gate, "EXPECTED_TEST", 10)
    monkeypatch.setattr(gate, "MIN_STYLES", 50)
    monkeypatch.setattr(gate, "MIN_STYLE_GAMES", 40)
    monkeypatch.setattr(gate, "PRIOR_SOURCE_SHA", sha256(b"fixture").hexdigest())
    monkeypatch.setattr(gate, "load_source", lambda archive_dir=None:
                        (b"fixture", rows, {}, "LOCAL_FIXTURE", "Tal"))
    monkeypatch.setattr(gate, "neural_arm", lambda gid: arms[gid])
    monkeypatch.setattr(gate, "_iter_verified_games", lambda pgn, selected, surname:
                        ((games[row["mainline_sha256"]], row) for row in selected))
    # Test roots are intentionally constructed independently of the style
    # trajectories. In production both are loaded from same pinned PGN.
    roots = []
    for i in range(56):
        root = chess.Board()
        randomizer = random.Random(i + 2027)
        prefix = []
        for _ in range(12):
            move = randomizer.choice(list(root.legal_moves))
            prefix.append(move.uci())
            root.push(move)
        roots.append({
            "id": f"root-{i}", "prefix": prefix,
            "arm": "tune",
        })
    monkeypatch.setattr(gate, "graded_panel", lambda archive_dir=None:
                        (roots, {"panel_sha256": "a"*64}))
    monkeypatch.setattr(gate, "frozen_fischer", lambda: {
        "run": 45097738965,
        "hashes": {"rodent": "a"*64, "fischer_profile": "b"*64,
                   "simple_control": "c"*64},
    })
    return rows, games, arms


def test_plan_uses_only_tune_retrospective_and_pairs_every_clock_root(monkeypatch):
    rows, games, arms = fixture_source(monkeypatch)
    source = gate.source_cases()
    report = gate.public_plan(source)
    assert report["clock_games"] == 48
    assert report["clock_roots"] == 12
    assert report["shards"] == 12
    assert report["style_positions"] >= 50
    assert report["style_source_games"] >= 40
    assert report["test_games_unopened"] == 10
    assert report["blind_holdout"] is False
    assert report["official_profiles_modified"] is False
    assert sum(report["style_shards"]) == report["style_positions"]
    assert report["clock_shards"] == [4]*12
    assert {x["variant"] for x in source["clock_cases"]} == {
        "original16", "balanced16",
    }
    assert {x["color"] for x in source["clock_cases"]} == {"white", "black"}
    assert {x["game"] for x in source["style_cases"].values()}.issubset({
        r["mainline_sha256"] for r in rows if arms[r["mainline_sha256"]] == "tune"
    })
    assert gate.public_plan(gate.source_cases()) == report


def test_frozen_source_protects_split_and_sha(monkeypatch):
    rows, games, arms = fixture_source(monkeypatch)
    arms[rows[0]["mainline_sha256"]] = "excluded_test"
    with pytest.raises(ValueError, match="split"):
        gate.source_cases()
    arms[rows[0]["mainline_sha256"]] = "tune"
    monkeypatch.setattr(gate, "PRIOR_SOURCE_SHA", "bad")
    with pytest.raises(ValueError, match="source"):
        gate.source_cases()


def test_style_features_bound_to_original_position_and_legal_moves():
    board = chess.Board()
    gid, ident = "f"*64, "1"*64
    meta = {
        "id": ident, "game": gid, "phase": "opening", "ply": 0,
        "human": "e2e4", "fen_sha256": sha256(board.fen().encode()).hexdigest(),
    }
    while int(ident[:12], 16) % gate.REPEAT_MOD == 0:
        ident = f"{int(ident, 16) + 16**52:064x}"
    meta["id"] = ident
    roles = dict.fromkeys(gate.STYLE_ROLES, "e2e4")
    roles["simple16"] = "d2d4"
    entry = {
        "schema": gate.SCHEMA, **meta,
        **{key: val for key, val in roles.items() if key != "human"},
        "features": {
            k: gate.features(board, chess.Move.from_uci(v))
            for k, v in roles.items()
        },
        "repeat_balanced": None,
        "depth_met": {"original16": True, "balanced16": True, "simple16": True},
        "manifest_sha256": "c"*64,
    }
    assert gate._validate_style(entry, meta, board, "c"*64) == entry
    with pytest.raises(ValueError, match="historic"):
        gate._validate_style({**entry, "game": "e"*64}, meta, board, "c"*64)
    mutant = json.loads(json.dumps(entry))
    mutant["features"]["balanced16"]["forcing"] = True
    with pytest.raises(ValueError, match="feature"):
        gate._validate_style(mutant, meta, board, "c"*64)
    with pytest.raises(ValueError, match="choice"):
        gate._validate_style({**entry, "balanced16": "e2e5"}, meta, board, "c"*64)


def test_actual_10_plus_2_terminal_replay_and_fake_result_rejected(monkeypatch):
    monkeypatch.setattr(clock, "initial_board", lambda root: chess.Board())
    script = [chess.Move.from_uci(m) for m in
              ("e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7")]
    monkeypatch.setattr(
        clock, "_clock_choose",
        lambda engine, board, clocks: (script[board.ply()], 1.5, {"depth": 16}),
    )
    case = {
        "id": "a"*64, "root": "b"*64, "variant": "balanced16",
        "color": "white", "clock": [600, 2], "control": "simple16",
        "depth": 16,
    }
    doc = gate._make_clock(case, {}, object(), object())
    assert doc["result"] == "1-0"
    assert doc["termination"] == "CHECKMATE"
    assert doc["status"] == "COMPLETE"
    envelope = {
        "schema": gate.SCHEMA, "case": case,
        "slot_mapping": {"fischer": "balanced16", "simple": "simple16"},
        "manifest_sha256": "d"*64, "game": doc,
    }
    assert gate._validate_game(envelope, case, {}, "d"*64) == envelope
    altered = json.loads(json.dumps(envelope))
    altered["game"]["result"] = "1/2-1/2"
    with pytest.raises(ValueError, match="terminal"):
        gate._validate_game(altered, case, {}, "d"*64)
    with pytest.raises(ValueError, match="bound"):
        gate._validate_game(
            {**envelope, "slot_mapping": {"fischer": "original16"}},
            case, {}, "d"*64,
        )


def test_paired_bootstrap_resamples_openings_not_ply():
    assert gate.paired_ci({"one": [1.0, 0.0]}) is None
    half = {f"{i:064x}": [0.5, 0.5] for i in range(12)}
    assert gate.paired_ci(half) == [0.5, 0.5]
    assert gate.paired_ci({**half, "bad": [0.2]}) is None


def test_no_style_test_root_access_and_unique_shard_allocation(monkeypatch):
    fixture_source(monkeypatch)
    source = gate.source_cases()
    all_clocks = []
    all_styles = []
    for i in range(gate.SHARDS):
        clocks, styles = gate.shard_plan(source, i)
        all_clocks.extend(x["id"] for x in clocks)
        all_styles.extend(x["id"] for x in styles)
    assert len(all_clocks) == len(set(all_clocks)) == 48
    assert len(all_styles) == len(set(all_styles)) == len(source["style_cases"])
    with pytest.raises(ValueError, match="shard index"):
        gate.shard_plan(source, 12)
