"""Tal graded full-game benchmarks; fixtures do not claim real Tal results."""
from __future__ import annotations

import json
import random

import chess
import chess.pgn
import pytest

from chesssim import tal_full_games as tal


def _games():
    rows, games, arms = [], {}, {}
    for i in range(100):
        rng = random.Random(1000 + i)
        node = chess.pgn.Game()
        for _ in range(28):
            if node.board().is_game_over(claim_draw=True):
                break
            node = node.add_variation(rng.choice(list(node.board().legal_moves)))
        gid = f"{i + 1:064x}"
        row = {"mainline_sha256": gid, "color": "white" if i % 2 else "black",
               "date": 1960 + i % 20, "plies": node.board().ply()}
        rows.append(row)
        games[gid] = node.game()
        arms[gid] = ("fit" if i < 70 else
                     "tune" if i < 90 else "excluded_test")
    return rows, games, arms


def _fixture(monkeypatch):
    rows, games, arms = _games()
    monkeypatch.setattr(tal, "EXPECTED_SOURCE_GAMES", len(rows))
    monkeypatch.setattr(tal, "DEVELOPMENT_ROOTS", 12)
    monkeypatch.setattr(tal, "MIN_TUNE_ROOTS", 12)
    monkeypatch.setattr(tal, "load_source",
                        lambda archive_dir=None: (
                            b"fixture", rows, {"pgn_records": 100},
                            "LOCAL_SYNTHETIC", "Tal"))
    monkeypatch.setattr(tal, "neural_arm", lambda gid: arms[gid])
    monkeypatch.setattr(
        tal, "_iter_verified_games",
        lambda pgn, selected, name: (
            (games[row["mainline_sha256"]], row) for row in selected))
    return rows, games, arms


def test_precommitted_distinct_unshared_roots_and_full_source_census(monkeypatch):
    rows, games, arms = _fixture(monkeypatch)
    panel, meta = tal.source_panel()
    assert len(panel) == 12 + meta["roots_tune"]
    assert meta["accepted_games"] == 100
    assert meta["reserved_test_games"] == 10
    assert meta["development_game_counts"] == {"fit": 70, "tune": 20}
    assert meta["roots_fit"] == 12
    assert meta["roots_tune"] >= 12
    assert meta["planned_games"] == len(panel) * 4
    assert meta["outer_test_moves_scored"] == 0
    assert meta["fide_or_human_elo"] is None
    assert not meta["tal_qualified"]
    assert len({tuple(x["prefix"]) for x in panel}) == len(panel)
    assert all(x["arm"] in {"fit", "tune"} for x in panel)
    before = [x["id"] for x in panel]
    rows.reverse()
    after, meta2 = tal.source_panel()
    assert before == [x["id"] for x in after]
    assert meta["panel_sha256"] == meta2["panel_sha256"]
    assert all(x["id"] not in {g["mainline_sha256"] for g in rows}
               for x in panel)


def test_complete_root_crossover_and_sharding(monkeypatch):
    _fixture(monkeypatch)
    panel, _ = tal.source_panel()
    full = list(tal.cases(panel))
    assert len(full) == len(panel) * 4
    assert len(set(x["id"] for x in full)) == len(full)
    left = tal.shard_cases(panel, 0, 2)
    right = tal.shard_cases(panel, 1, 2)
    assert sorted(x["id"] for x in left + right) == sorted(x["id"] for x in full)
    allocation = {}
    for idx, cases in enumerate((left, right)):
        for case in cases:
            allocation.setdefault(case["root"], set()).add(idx)
    assert all(len(indices) == 1 for indices in allocation.values())
    with pytest.raises(ValueError):
        tal.shard_cases(panel, 2, 2)


def test_complete_source_rejects_omitted_trajectory_and_overlap(monkeypatch):
    rows, games, arms = _fixture(monkeypatch)
    to_drop = rows[0]["mainline_sha256"]
    monkeypatch.setattr(tal, "_iter_verified_games", lambda pgn, selected, name: (
        (games[row["mainline_sha256"]], row) for row in selected
        if row["mainline_sha256"] != to_drop))
    with pytest.raises(ValueError, match="omitted"):
        tal.source_panel()
    monkeypatch.setattr(tal, "_iter_verified_games", lambda pgn, selected, name: (
        (games[row["mainline_sha256"]], row) for row in selected))
    arms.clear()
    arms.update({r["mainline_sha256"]: "fit" for r in rows})
    with pytest.raises(ValueError, match="Too few"):
        tal.source_panel()


def _seven():
    return ["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"]


def test_actual_legal_terminal_replay_and_no_score_on_incomplete(monkeypatch):
    monkeypatch.setattr(tal, "initial_board", lambda root: chess.Board())
    case = {"root": "f"*64, "arm": "fit", "opponent_depth": 12,
            "color": "white", "id": "1"*64}
    chosen = _seven()
    def choice(engine, board, depth):
        return (chess.Move.from_uci(chosen[board.ply()]),
                {"reached_requested": True, "reported_depth": depth,
                 "reported_nodes": 400})
    monkeypatch.setattr(tal, "choose", choice)
    rec = tal.play_case(case, {}, object(), object())
    assert rec["result"] == "1-0"
    assert rec["termination"] == "CHECKMATE"
    assert tal.validate_case(rec, case, {}) == rec
    with pytest.raises(ValueError, match="Invented"):
        tal.validate_case({**rec, "result": "1/2-1/2"}, case, {})
    with pytest.raises(ValueError, match="legal"):
        tal.validate_case({**rec, "moves": rec["moves"]+["e2e4"]}, case, {})
    monkeypatch.setattr(tal, "MAX_CONTINUATION_PLIES", 2)
    short = tal.play_case(case, {}, object(), object())
    assert short["status"] == "INCOMPLETE" and short["result"] == "*"
    assert tal.validate_case(short, case, {}) == short
    with pytest.raises(ValueError, match="Invented"):
        tal.validate_case({**short, "result": "1/2-1/2"}, case, {})


def test_aggregator_full_replay_clustered_wdl_and_refuse_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(tal, "initial_board", lambda root: chess.Board())
    monkeypatch.setattr(tal, "DEVELOPMENT_ROOTS", 2)
    roots = [{"id": f"{i+1:064x}", "prefix": [], "arm": arm, "source_games": []}
             for i, arm in enumerate(("fit", "fit", "tune"))]
    frozen = {"hashes": {"rodent": "a"*64,
                          "fischer_profile": "b"*64, "simple_control": "c"*64}}
    monkeypatch.setattr(tal, "frozen_fischer", lambda: frozen)
    base = {"schema": tal.SCHEMA, "roots_tune": 1, "planned_games": 12,
            "source_pgn_sha256": "f"*64}
    monkeypatch.setattr(tal, "source_panel", lambda archive_dir=None: (roots, base))
    hashes = {"rodent": "a"*64, "fischer": "b"*64, "simple": "c"*64,
              "tal": "d"*64, "registry": "e"*64}
    script = _seven()
    for i in range(2):
        folder = tmp_path / f"shard-{i}"
        folder.mkdir()
        planned = tal.shard_cases(roots, i, 2)
        manifest = {**base, "shard_index": i, "shard_count": 2,
                    "case_ids": [x["id"] for x in planned], "hashes": hashes}
        tal.atomic_json(folder / "manifest.json", manifest)
        for case in planned:
            move = {"case": case, "moves": script, "result": "1-0",
                    "termination": "CHECKMATE", "status": "COMPLETE",
                    "reached": {}, "depth_missing": {}, "nodes_exceeded": {},
                    "manifest_sha256": tal.digest(manifest)}
            tal.atomic_json(folder / f'{case["id"]}.json', move)
    result = tal.aggregate(tmp_path, 2)
    assert result["status"] == "ALL_GAMES_TERMINAL_UNCALIBRATED"
    assert result["observed_games"] == 12 and result["censored_games"] == 0
    assert result["by_arm_opponent"]["fit|12"]["games"] == 4
    assert result["by_arm_opponent"]["fit|12"]["wins"] == 2
    assert result["by_arm_opponent"]["fit|12"]["losses"] == 2
    assert result["by_arm_opponent"]["tune|16"]["score_fraction"] == .5
    assert result["by_arm_opponent"]["fit|12"]["root_cluster_ci95"] is None
    item = next((tmp_path / "shard-0").glob("[0-9a-f]*.json"))
    doc = json.loads(item.read_text())
    doc["result"] = "1/2-1/2"
    item.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="Invented"):
        tal.aggregate(tmp_path, 2)


def test_score_confidence_requires_independent_roots():
    assert tal.root_ci([("a", 1.)] * 200) is None
    interval = tal.root_ci([(f"k{i}", .5) for i in range(12)])
    assert interval == [.5, .5]
