"""Unit contracts only. Scientific workflow re-downloads real PGNs and builds Rodent."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import chess
import chess.pgn
import pytest

from chesssim.real_policy_experiment import (
    POLICY_SCHEMA, _bottom_k, _features, _metric,
    _read_json, evaluate_policy, fit_policy, predict,
)
from chesssim.real_training_cohorts import build_real_training_cohorts


def _game(player: str, year: int, marker: int):
    g = chess.pgn.Game()
    g.headers["Event"] = "TEST_FIXTURE_ONLY_NOT_REAL_SCIENTIFIC_EVIDENCE"
    g.headers["White"] = "Robert Fischer" if player == "fischer" else "Mikhail Tal"
    g.headers["Black"] = "Opponent"
    g.headers["Date"] = f"{year}.02.15"
    g.headers["Result"] = "1-0"
    board = g.board()
    node = g
    for ply in range(28):
        legal = sorted(board.legal_moves, key=lambda m: m.uci())
        move = legal[(ply * 7 + marker * 17 + year) % len(legal)]
        node = node.add_variation(move)
        board.push(move)
    return g


def _data(tmp_path: Path):
    sources = tmp_path / "sources"
    sources.mkdir()
    exporter = chess.pgn.StringExporter(
        headers=True, variations=False, comments=False)
    for slug, source in [("fischer", "Fischer"), ("tal", "Tal")]:
        games = [_game(slug, year, marker)
                 for year in range(1990, 2005) for marker in range(2)]
        (sources / (source + ".pgn")).write_text(
            "\n\n".join(g.accept(exporter) for g in games) + "\n", encoding="utf-8")
    cohorts = tmp_path / "cohorts"
    build_real_training_cohorts(
        cohorts, players=["fischer", "tal"], archive_dir=sources,
        split_policy="career-stratified", min_ply=17)
    return sources, cohorts


def test_real_choice_fit_and_reserved_year_evaluation_are_deterministic(tmp_path):
    sources, cohorts = _data(tmp_path)
    a = fit_policy(cohorts, tmp_path / "a.json", max_per_player=30, max_per_game=2)
    b = fit_policy(cohorts, tmp_path / "b.json", max_per_player=30, max_per_game=2)
    assert a == b
    assert a["schema"] == POLICY_SCHEMA
    assert set(a["profiles"]) == {"fischer", "tal"}
    assert a["training_observations_used"] > 0
    model = _read_json(tmp_path / "a.json")
    assert model["model_sha256"] == a["model_sha256"]
    board = chess.Board()
    predicted = predict(model, board, "fischer", "early_observed")
    generic = predict(model, board, "fischer", "early_observed", generic=True)
    assert chess.Move.from_uci(predicted) in board.legal_moves
    assert chess.Move.from_uci(generic) in board.legal_moves

    output = evaluate_policy(
        cohorts, tmp_path / "a.json", tmp_path / "evaluation.json",
        archive_dir=sources, per_player=10, per_game=2)
    assert output["arm"] == "validation"
    assert output["rodent"]["status"] == "NOT_RUN_NO_REAL_ENGINE_SPECIFIED"
    assert set(output["evaluation"]) == {"fischer", "tal"}
    assert all(
        metric["positions"] > 0 and 0 <= metric["top1"] <= 1
        for groups in output["evaluation"].values() for metric in groups.values())
    assert all("generic_trained" in group and "trained_fischer" in group and
               "trained_tal" in group for group in output["evaluation"].values())
    assert _read_json(tmp_path / "evaluation.json") == output


def test_final_holdout_requires_explicit_approval_and_no_repeat(tmp_path):
    sources, cohorts = _data(tmp_path)
    fit_policy(cohorts, tmp_path / "model.json", max_per_player=10)
    with pytest.raises(ValueError, match="acknowledgement"):
        evaluate_policy(cohorts, tmp_path / "model.json", tmp_path / "holdout.json",
                        archive_dir=sources, arm="holdout", per_player=4)
    assert not (tmp_path / "holdout.json").exists()
    evaluation = evaluate_policy(
        cohorts, tmp_path / "model.json", tmp_path / "holdout.json",
        archive_dir=sources, arm="holdout", acknowledge_holdout=True, per_player=4)
    assert evaluation["arm"] == "holdout"
    with pytest.raises(FileExistsError):
        evaluate_policy(cohorts, tmp_path / "model.json", tmp_path / "holdout.json",
                        archive_dir=sources, arm="validation", per_player=4)


def test_refuses_tampered_model_manifest_or_source_bytes(tmp_path):
    sources, cohorts = _data(tmp_path)
    fit_policy(cohorts, tmp_path / "model.json", max_per_player=12)
    model = json.loads((tmp_path / "model.json").read_text())
    model["training_observations_used"] += 1
    (tmp_path / "model.json").write_text(json.dumps(model))
    with pytest.raises(ValueError, match="Corrupt"):
        evaluate_policy(cohorts, tmp_path / "model.json", tmp_path / "bad.json",
                        archive_dir=sources)
    assert not (tmp_path / "bad.json").exists()


def test_chess_move_features_validate_illegal_moves():
    board = chess.Board()
    assert "piece:P" in _features(board, chess.Move.from_uci("e2e4"))
    assert "pawn_push_two:True" in _features(board, chess.Move.from_uci("e2e4"))
    with pytest.raises(ValueError, match="Illegal"):
        _features(board, chess.Move.from_uci("e2e5"))


def test_selection_is_deterministic_and_game_bounded():
    rows = [{"player": "fischer", "game_sha256": f"{i:064x}", "ply": ply}
            for i in range(25) for ply in range(17, 31)]
    a = _bottom_k(rows, limit=24, per_game=2, seed=42)
    b = _bottom_k(list(reversed(rows)), limit=24, per_game=2, seed=42)
    assert a == b
    assert len(a) == 24
    counts = {}
    for row in a:
        counts[row["game_sha256"]] = counts.get(row["game_sha256"], 0) + 1
    assert max(counts.values()) <= 2


def test_paired_metrics_do_not_count_moves_as_independent_games():
    rows = [{"fen": chess.STARTING_FEN, "move": "e2e4",
             "game_sha256": "a" * 64}] * 3
    result = _metric(rows, ["e2e4", "d2d4", "e2e4"])
    assert result["positions"] == 3
    assert result["historical_games"] == 1
    assert result["exact_match"] == 2
    with pytest.raises(ValueError, match="unaligned"):
        _metric(rows, ["e2e4"])
