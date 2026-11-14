"""Regression fixtures, NOT scientific evidence. Real workflow uses pinned PGNs."""
from __future__ import annotations

import json

import chess
import chess.pgn
import pytest

from chesssim.contextual_policy_v3 import (
    SCHEMA, STATUS, contextual_features, _board_context,
    fit_contextual, predict_contextual, paired_game_inference,
    evaluate_contextual, main,
)
from chesssim.experimental_policy_adapter import ExperimentalPolicyAdapter
from chesssim.real_policy_experiment import fit_policy
from chesssim.real_training_cohorts import build_real_training_cohorts


@pytest.fixture
def artifacts(tmp_path):
    source = tmp_path / "pgn"
    source.mkdir()
    exporter = chess.pgn.StringExporter(headers=True, variations=False,
                                        comments=False)
    for slug, source_name, fullname in (
        ("fischer", "Fischer", "Robert Fischer"),
        ("tal", "Tal", "Mikhail Tal"),
    ):
        games = []
        for year in range(1980, 1996):
            for index in range(3):
                g = chess.pgn.Game()
                g.headers["White"] = fullname
                g.headers["Black"] = "Synthetic opponent"
                g.headers["Date"] = f"{year}.05.17"
                g.headers["Event"] = "REGRESSION_FIXTURE_ONLY"
                node = g
                board = g.board()
                for ply in range(34):
                    moves = sorted(board.legal_moves, key=lambda move: move.uci())
                    move = moves[(year * 3 + index * 17 + ply * 9) % len(moves)]
                    node = node.add_variation(move)
                    board.push(move)
                games.append(g.accept(exporter))
        (source / (source_name + ".pgn")).write_text(
            "\n\n".join(games) + "\n", encoding="utf-8")
    cohorts = tmp_path / "cohorts"
    build_real_training_cohorts(
        cohorts, archive_dir=source, players=["fischer", "tal"],
        min_ply=17, split_policy="career-stratified")
    old = tmp_path / "legacy.json"
    fresh = tmp_path / "context.json"
    fit_policy(cohorts, old, max_per_player=32, max_per_game=2, seed=2026)
    fit_contextual(cohorts, fresh, max_per_player=32,
                   max_per_game=2, seed=2026)
    return source, cohorts, old, fresh


def test_features_are_predecision_legal_and_do_not_mutate_board():
    board = chess.Board()
    fen = board.fen()
    context = _board_context(board)
    a = contextual_features(board, chess.Move.from_uci("e2e4"),
                            context=context)
    b = contextual_features(board, chess.Move.from_uci("g1f3"),
                            context=context)
    assert "v3:phase:high_material" in a
    assert "v3:phase_capture:high_material:False" in a
    assert a != b
    assert board.fen() == fen
    with pytest.raises(ValueError, match="Illegal"):
        contextual_features(board, chess.Move.from_uci("e2e5"))


def test_contextual_real_contract_and_legacy_comparison(artifacts, tmp_path):
    source, cohorts, old, fresh = artifacts
    model = json.loads(fresh.read_text())
    assert model["schema"] == SCHEMA and model["status"] == STATUS
    assert model["training_observations_used"] > 0
    assert model["train_manifest_sha256"]
    assert set(model["profiles"]) == {"fischer", "tal"}
    assert len(model["generic_training_loss"]) == 3
    assert all(len(p["training_loss"]) == 3 for p in model["profiles"].values())
    other = fit_contextual(cohorts, tmp_path / "repeat.json",
                           max_per_player=32, max_per_game=2, seed=2026)
    assert other == model
    board = chess.Board()
    for generic in [True, False]:
        move = predict_contextual(model, board, "fischer", "early_observed",
                                  generic=generic)
        assert chess.Move.from_uci(move) in board.legal_moves
    report = evaluate_contextual(
        cohorts, fresh, old, tmp_path / "eval.json",
        archive_dir=source, per_player=8, per_game=2, repetitions=200)
    assert report["validation_arm"] == "validation"
    assert report["final_holdout_opened"] is False
    assert report["promotion"].startswith("DENIED")
    assert report["rodent_engine"]["status"] == "NOT_REQUESTED"
    assert report["evaluation_decisions"] > 0
    for target in ("fischer", "tal"):
        row = report["by_historical_player"][target]
        assert row["sample"]["distinct_games"] >= 2
        assert "contextual_named" in row["models"]
        assert "legacy_named" in row["models"]
        assert "contextual_generic" in row["models"]
        assert "contextual_named_vs_legacy_named" in row["paired_game_statistics"]
        assert row["historical_style_validated"] is False


def test_adapter_uses_v3_with_existing_v2_play_protocol(artifacts):
    _, _, _, fresh = artifacts
    adapted = ExperimentalPolicyAdapter.load(
        fresh, bindings={"fischer": "overall", "tal": "overall"},
        override_percent=100, min_ply=1)
    assert adapted.manifest()["model_schema"] == SCHEMA
    board = chess.Board()
    result = adapted.select(
        board, slug="fischer",
        engine_move=chess.Move.from_uci("e2e4"), round_number=1, ply=1)
    assert result.move in board.legal_moves
    assert result.audit["policy_move"] is not None
    assert result.audit["eligible"] is True


def test_paired_game_inference_clusters_per_game_and_is_deterministic():
    fen = chess.STARTING_FEN
    rows = [
        {"fen": fen, "game_sha256": key, "move": "e2e4"}
        for key in ["a", "a", "a", "b", "b", "c"]
    ]
    candidate = ["e2e4"] * len(rows)
    baseline = ["d2d4"] * len(rows)
    result = paired_game_inference(rows, candidate, baseline,
                                   seed=99, repetitions=200)
    assert result["games"] == 3
    assert result["positions"] == 6
    assert result["macro_gain_candidate_minus_control"] == 1
    assert result["bootstrap_95_ci"] == [1, 1]
    assert result == paired_game_inference(
        rows, candidate, baseline, seed=99, repetitions=200)
    with pytest.raises(ValueError, match="identical"):
        paired_game_inference(rows, candidate[:-1], baseline)
    with pytest.raises(ValueError, match="Invalid candidate"):
        paired_game_inference(rows, ["e2e5"] * 6, baseline)


def test_rejects_tampered_corpus_and_unmatched_v2_model(artifacts, tmp_path):
    source, cohorts, old, fresh = artifacts
    model = json.loads(fresh.read_text())
    model["profiles"]["fischer"]["weights"]["v3:material:2"] = 7
    fresh.write_text(json.dumps(model))
    with pytest.raises(ValueError, match="tampered"):
        evaluate_contextual(cohorts, fresh, old, tmp_path / "bad.json",
                            archive_dir=source, repetitions=200)
    assert not (tmp_path / "bad.json").exists()


def test_final_holdout_not_exposed_by_benchmark_cli():
    with pytest.raises(SystemExit):
        main(["evaluate", "--cohorts", "cohorts", "--model", "model",
              "--legacy-model", "old", "--fetch", "--out", "report",
              "--arm", "holdout"])
