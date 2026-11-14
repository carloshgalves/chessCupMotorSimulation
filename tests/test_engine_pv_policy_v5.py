"""V5 engineering tests; synthetic rows below NEVER count as scientific evidence."""
from __future__ import annotations

import copy
import json

import chess
import chess.engine
import pytest

from chesssim.engine_pv_policy_v5 import (
    FEATURES, SCHEMA, _analyse, _fit, _predict, _record_candidate,
    _target_index, _read_trained, _predict_style_residual,
)
from chesssim.real_policy_experiment import _digest


class StubUciMultiPV:
    """Only for testing the UCI information contract, not historical accuracy."""
    def __init__(self, *, malformed=False):
        self.malformed = malformed
        self.calls = []

    def analyse(self, board, limit, *, multipv, info):
        self.calls.append({
            "depth": limit.depth, "multipv": multipv, "info": info,
        })
        legal = ["e2e4", "d2d4", "g1f3", "c2c4"]
        variants = []
        for i, uci in enumerate(legal[:multipv]):
            move = chess.Move.from_uci(uci)
            after = board.copy()
            after.push(move)
            reply = (chess.Move.from_uci("e7e5")
                     if uci == "e2e4" else sorted(
                         after.legal_moves, key=lambda m: m.uci())[0])
            variants.append({
                "pv": [move, reply],
                "score": chess.engine.PovScore(
                    chess.engine.Cp(60 - i * 40), chess.WHITE),
                "depth": limit.depth,
            })
        if self.malformed:
            variants[-1]["pv"] = [variants[0]["pv"][0]]
        return variants


def test_real_uci_information_contract_and_target_blind_ranked_variations():
    board = chess.Board()
    engine = StubUciMultiPV()
    candidates = _analyse(engine, board, multipv=4, depth=3)
    assert len(candidates) == 4
    assert [x["move"] for x in candidates] == ["e2e4", "d2d4", "g1f3", "c2c4"]
    assert all(len(x["features"]) == len(FEATURES) for x in candidates)
    assert engine.calls[0]["multipv"] == 4
    assert engine.calls[0]["depth"] == 3
    assert candidates[0]["pv"] == ["e2e4", "e7e5"]
    assert candidates[0]["features"][0] > candidates[1]["features"][0]
    assert board.fen() == chess.STARTING_FEN
    # A human decision outside top-k MUST NOT be injected into choices.
    row = {"candidates": candidates, "real_human_move": "b1c3"}
    assert _target_index(row) is None
    assert _predict([0.0] * len(FEATURES), row) == "e2e4"


def test_rejects_duplicate_or_unsafe_uci_variations():
    board = chess.Board()
    with pytest.raises(ValueError, match="Repeated root"):
        _analyse(StubUciMultiPV(malformed=True), board, multipv=4, depth=3)
    with pytest.raises(ValueError, match="configuration"):
        _analyse(StubUciMultiPV(), board, multipv=1, depth=3)
    with pytest.raises(ValueError, match="configuration"):
        _analyse(StubUciMultiPV(), board, multipv=4, depth=1)
    score = chess.engine.PovScore(chess.engine.Cp(10), chess.WHITE)
    with pytest.raises(ValueError, match="incomplete"):
        _record_candidate(board, {
            "pv": [chess.Move.from_uci("e2e4")],
            "score": score, "depth": 2,
        }, rank=0, best_cp=10, total=4, expected_depth=3)
    with pytest.raises(ValueError, match="illegal root"):
        _record_candidate(board, {
            "pv": [chess.Move.from_uci("e2e5")],
            "score": score, "depth": 3,
        }, rank=0, best_cp=10, total=4, expected_depth=3)


def _training_example(human, bias):
    moves = ["e2e4", "d2d4", "g1f3", "c2c4"]
    candidates = []
    for i, move in enumerate(moves):
        vector = [0.0] * len(FEATURES)
        vector[0] = (3 - i) / 3.0
        vector[1] = -(i / 3.0)
        vector[2] = -(i / 3.0)
        vector[4] = float(i == bias)
        candidates.append({"move": move, "features": vector})
    return {
        "candidates": candidates,
        "real_human_move": human,
        "move": human,
        "fen": chess.STARTING_FEN,
        "game_sha256": f"fixture-{human}-{bias}",
    }


def test_hierarchical_logit_changes_weights_using_real_choice_targets_shape():
    examples = [_training_example("c2c4", 3)] * 24
    initial = [0.0] * len(FEATURES)
    initial[0] = 1.0
    fitted, loss = _fit(
        examples, initial,
        epochs=14, learning_rate=0.17, regularization=0.02, seed=5)
    assert len(fitted) == len(FEATURES)
    assert len(loss) == 14
    assert loss[-1] < loss[0]
    assert _predict(fitted, examples[0]) == "c2c4"
    # An ablation that removes the pooled engine prior must be able to
    # expose genuine player-relative ranking differences.
    assert _predict_style_residual(fitted, initial, examples[0]) == "c2c4"
    assert _predict_style_residual(initial, initial, examples[0]) == "e2e4"
    assert fitted == _fit(examples, initial, epochs=14,
                          learning_rate=0.17, regularization=0.02, seed=5)[0]
    missing = _training_example("b1c3", 1)
    with pytest.raises(ValueError, match="observed target"):
        _fit([missing], initial, epochs=1,
             learning_rate=0.05, regularization=0.10, seed=5)


def test_signed_model_rejects_forgery_and_wrong_v3_sha(tmp_path):
    base = {
        "schema": "chesssim-real-contextual-conditional-choice-v3",
        "model_sha256": "11" * 32,
        "train_manifest_sha256": "22" * 32,
        "training_files_sha256": {"train/fischer/1961.jsonl": "33" * 32},
        "source_pgn_sha256": {"fischer": "44" * 32},
        "profiles": {"fischer": {}},
    }
    unsigned = {
        "schema": SCHEMA,
        "status": "REAL_PGN_MULTIPV_TRAINED_NOT_IDENTITY_OR_STRENGTH_VALIDATED",
        "v3_model_sha256": base["model_sha256"],
        "train_manifest_sha256": base["train_manifest_sha256"],
        "training_files_sha256": base["training_files_sha256"],
        "source_pgn_sha256": base["source_pgn_sha256"],
        "candidate_policy": "TARGET_BLIND_TOP_K_FROM_NAMED_RODENT_MULTIPV",
        "feature_names": list(FEATURES),
        "generic_weights": [0.1] * len(FEATURES),
        "players": {"fischer": {"weights": [0.2] * len(FEATURES)}},
    }
    data = {**unsigned, "model_sha256": _digest(unsigned)}
    file = tmp_path / "model.json"
    file.write_text(json.dumps(data))
    assert _read_trained(file, base)["model_sha256"] == data["model_sha256"]
    corrupted = copy.deepcopy(data)
    corrupted["players"]["fischer"]["weights"][0] = 100
    file.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="Tampered"):
        _read_trained(file, base)
    file.write_text(json.dumps(data))
    forged_base = {**base, "model_sha256": "55" * 32}
    with pytest.raises(ValueError, match="Tampered"):
        _read_trained(file, forged_base)


def test_engine_score_inverts_correctly_for_black():
    board = chess.Board()
    board.push_uci("e2e4")
    move = chess.Move.from_uci("e7e5")
    info = {
        "pv": [move, chess.Move.from_uci("g1f3")],
        "score": chess.engine.PovScore(chess.engine.Cp(90), chess.WHITE),
        "depth": 3,
    }
    row = _record_candidate(board, info, rank=0, best_cp=-90,
                            total=2, expected_depth=3)
    assert row["cp"] == -90
    assert row["features"][0] < 0
