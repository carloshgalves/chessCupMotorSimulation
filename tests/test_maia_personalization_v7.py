"""Engineering-only fixtures for full historical game Maia3 79M V7.
No synthetic test observation is presented as empirical chess science.
"""
from __future__ import annotations

from collections import Counter
import copy
import json

import chess
import pytest

from chesssim.maia_personalization_v7 import (
    SCHEMA, STRENGTHS, _arm, _build_profile, _calibrate, _features,
    _load_adapter, _rerank, _verify_checkpoint,
    MAIA_ELO, MAX_ABS_WEIGHT,
)
from chesssim.real_policy_experiment import _digest


def test_all_histories_share_game_partition_even_across_players():
    game_ids = [f"{k:064x}" for k in range(500)]
    assert {_arm("fischer", g) for g in game_ids} == set(range(10))
    assert all(_arm("fischer", x) == _arm("tal", x) for x in game_ids)
    with pytest.raises(ValueError, match="mainline"):
        _arm("tal", "fake")
    assert MAIA_ELO == 2400


def test_semantic_features_reflect_legal_choice_and_side_to_move():
    board = chess.Board()
    pawn = chess.Move.from_uci("e2e4")
    features = _features(board, pawn)
    assert "piece=P" in features
    assert "pawn_double=1" in features
    assert "central=1" in features
    assert "file=4" in features
    with pytest.raises(ValueError, match="Illegal"):
        _features(board, chess.Move.from_uci("e2e5"))
    board.push(pawn)
    black_pawn = chess.Move.from_uci("e7e5")
    black_features = _features(board, black_pawn)
    assert "rank_relative=3" in black_features


def test_shrunken_full_training_profile_can_reweight_maia_candidates():
    selected = Counter({"piece=N": 1400, "piece=P": 80,
                        "capture=0": 1480, "file=5": 1400})
    expected = Counter({"piece=N": 900.0, "piece=P": 580.0,
                        "capture=0": 1480.0, "file=5": 200.0})
    profile = _build_profile(selected, expected, positions=1480, games=350)
    assert profile["decisions"] == 1480
    assert profile["distinct_games"] == 350
    assert profile["weights"]["piece=N"] > 0
    assert profile["weights"]["piece=P"] < 0
    assert all(abs(x) <= MAX_ABS_WEIGHT for x in profile["weights"].values())
    board = chess.Board()
    choices = ["e2e4", "g1f3", "d2d4", "b1c3"]
    assert _rerank(board, choices, profile["weights"], 0) == "e2e4"
    aggressive = {**profile["weights"], "piece=N": 2}
    assert _rerank(board, choices, aggressive, 4) == "g1f3"
    with pytest.raises(ValueError, match="unregistered"):
        _rerank(board, choices, aggressive, 99)
    with pytest.raises(ValueError, match="Duplicate|Invalid"):
        _rerank(board, ["e2e4", "e2e4"], aggressive, 2)
    assert STRENGTHS == (0.0, 0.5, 1.0, 2.0, 4.0)


def test_explicit_zero_data_abstention():
    profile = _build_profile(Counter(), Counter(), 0, 0)
    assert profile["status"] == "UNDERPOWERED_NO_TRAIN_DECISIONS"
    assert profile["weights"] == {}


def _row(i, target):
    board = chess.Board()
    # All real benchmarks reconstruct PGN histories; these are toy
    # positions only to prove the train-tune-audit gate's software behavior.
    board.push_uci(("a2a3","b2b3","c2c3","d2d3")[i % 4])
    board.push_uci(("a7a6","b7b6","c7c6","d7d6")[(i // 4) % 4])
    return {"player": "fischer", "game_sha256": f"{i:064x}",
            "ply": 21, "fen": board.fen(), "move": target,
            "year": 1972, "maia79_candidates": ["e2e4", "g1f3", "b1c3"]}


def test_gate_selection_requires_game_disjoint_replication():
    # Positive selection can be found but tiny number of ACTUAL unique
    # board configurations means this is only a test of the gate mechanics.
    samples = [_row(i, "g1f3") for i in range(30)]
    result = _calibrate(samples[:17], samples[17:],
                        {"piece=N": 2.0}, seed=5)
    assert result["tuning_games"] == 17
    assert result["audit_games"] == 13
    assert result["activated_strength"] in STRENGTHS
    assert result["decision"].startswith(("ACCEPT", "DENY"))
    denied = _calibrate(samples[:17], [], {"piece=N": 2.0}, seed=5)
    assert denied["activated_strength"] == 0
    assert denied["decision"].startswith("DENY")


def test_signature_and_pinned_checkpoints_reject_tampering(tmp_path):
    base = {"model_sha256": "11" * 32, "train_manifest_sha256": "22"*32,
            "training_files_sha256": {"train/a/1972.jsonl": "33"*32},
            "source_pgn_sha256": {"fischer": "44"*32},
            "profiles": {"fischer": {}}}
    plain = {
        "schema": SCHEMA, "v3_model_sha256": base["model_sha256"],
        "train_manifest_sha256": base["train_manifest_sha256"],
        "training_files_sha256": base["training_files_sha256"],
        "source_pgn_sha256": base["source_pgn_sha256"],
        "profiles": {"fischer": {"weights": {"piece=N": 0.3}}},
    }
    doc = {**plain, "model_sha256": _digest(plain)}
    path = tmp_path / "real-model.json"
    path.write_text(json.dumps(doc))
    assert _load_adapter(path, base)["model_sha256"] == doc["model_sha256"]
    altered = copy.deepcopy(doc)
    altered["profiles"]["fischer"]["weights"]["piece=N"] = 3.0
    altered.pop("model_sha256")
    altered["model_sha256"] = _digest(altered)
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="Out-of-range"):
        _load_adapter(path, base)
    ckpt = tmp_path / "maia3.bin"
    ckpt.write_bytes(b"fixture ONLY")
    from hashlib import sha256
    true_sha = sha256(b"fixture ONLY").hexdigest()
    assert _verify_checkpoint(ckpt, known_digest=true_sha) == true_sha
    with pytest.raises(ValueError, match="changed"):
        _verify_checkpoint(ckpt, known_digest="0"*64)
