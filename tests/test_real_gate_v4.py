"""Engineering fixture tests ONLY; public real PGNs/actual Rodent run in CI."""
from __future__ import annotations

import copy
import json

import chess
import chess.pgn
import pytest

from chesssim.contextual_policy_v3 import fit_contextual
from chesssim.real_gate_v4 import (
    GATE_SCHEMA, GATE_STATUS, _calibration_samples, _choose, _position_key,
    _split_games, _style_lift_and_proposal, evaluate_gate, fit_gate,
    load_gate, THRESHOLDS,
)
from chesssim.real_policy_experiment import _bottom_k, _read_json, _validated_training_rows
from chesssim.real_training_cohorts import build_real_training_cohorts
from chesssim.experimental_policy_adapter import ExperimentalPolicyAdapter
from chesssim.catalog import find_player


@pytest.fixture
def synthetic_harness(tmp_path, monkeypatch):
    """Fake PGNs make ONLY assertions about program behavior, not science."""
    root = tmp_path / "historical-fixtures-not-evidence"
    root.mkdir()
    for slug, filename, player in [
        ("fischer", "Fischer", "Robert Fischer"),
        ("tal", "Tal", "Mikhail Tal"),
    ]:
        games = []
        for year in range(1980, 2001):
            for marker in range(5):
                game = chess.pgn.Game()
                game.headers.update({
                    "White": player, "Black": "Synthetic opponent",
                    "Date": f"{year}.03.12", "Result": "1-0",
                    "Event": "FIXTURE_DO_NOT_COUNT_AS_HISTORICAL_DATA",
                })
                board = game.board()
                node = game
                for ply in range(34):
                    moves = sorted(board.legal_moves, key=lambda m: m.uci())
                    if not moves:
                        break
                    next_move = moves[(year + marker * 31 + ply * 13) % len(moves)]
                    node = node.add_variation(next_move)
                    board.push(next_move)
                games.append(game.accept(chess.pgn.StringExporter(
                    headers=True, variations=False, comments=False)))
        (root / f"{filename}.pgn").write_text("\n\n".join(games) + "\n", encoding="utf-8")
    cohorts = tmp_path / "cohorts"
    build_real_training_cohorts(
        cohorts, archive_dir=root, players=["fischer", "tal"],
        min_ply=17, split_policy="career-stratified")
    model_path = tmp_path / "v3.json"
    model = fit_contextual(
        cohorts, model_path, max_per_player=36, max_per_game=2, seed=2026)
    def faux_uci(_root, rows, slugs, *, depth, time_per_position):
        # These moves are legal but never imply engine evidence.
        predictions = []
        for row in rows:
            board = chess.Board(row["fen"])
            predictions.append(min(board.legal_moves, key=lambda m: m.uci()).uci())
        return (
            {**{p: list(predictions) for p in slugs},
             "generic_rodent": list(predictions)},
            {"exe": "ab" * 32, "simple_profile": "bc" * 32,
             **{p: "cd" * 32 for p in slugs}},
        )
    monkeypatch.setattr("chesssim.real_gate_v4._rodent_predictions", faux_uci)
    return root, cohorts, model_path, model


def test_reconstructed_fitted_games_never_enter_calibration(synthetic_harness):
    _, cohorts, _, model = synthetic_harness
    samples, metadata = _calibration_samples(
        cohorts, model, per_player=40, per_game=2, seed=2028)
    manifest = _read_json(cohorts / "manifest.json")
    grouped = {}
    for row in _validated_training_rows(cohorts, manifest):
        grouped.setdefault(row["player"], []).append(row)
    seen_games, seen_fens, seen_states = set(), set(), set()
    for slug, rows in grouped.items():
        picked = _bottom_k(rows, limit=model["max_per_player"],
                           per_game=model["max_per_game"], seed=model["seed"])
        seen_games.update(row["game_sha256"] for row in picked)
        seen_fens.update(row["policy_input"]["fen"] for row in picked)
        seen_states.update(_position_key(row["policy_input"]["fen"]) for row in picked)
    assert metadata["fitted_games_globally_embargoed"] == len(seen_games)
    assert metadata["fitted_canonical_positions_globally_embargoed"] == len(seen_states)
    for slug, rows in samples.items():
        assert len(rows) == 40
        assert len({row["game_sha256"] for row in rows}) >= 20
        assert all(row["game_sha256"] not in seen_games for row in rows)
        assert all(row["fen"] not in seen_fens for row in rows)
        assert all(_position_key(row["fen"]) not in seen_states for row in rows)
        assert all(row["player"] == slug for row in rows)


def test_fit_gate_from_unused_train_games_audits_before_activating(
        synthetic_harness, tmp_path):
    _, cohorts, model_path, model = synthetic_harness
    path = tmp_path / "gate.json"
    gate = fit_gate(cohorts, model_path, tmp_path / "stub-unit-only", path,
                    per_player=40, per_game=2, seed=2028)
    assert gate["schema"] == GATE_SCHEMA
    assert gate["status"] == GATE_STATUS
    assert gate["base_model_sha256"] == model["model_sha256"]
    assert gate["training_arm"] == "train_only_out_of_fit_games"
    assert gate["final_holdout_opened"] is False
    assert gate["engine_weights_changed"] is False
    assert set(gate["players"]) == {"fischer", "tal"}
    for row in gate["players"].values():
        assert row["tuning_games"] >= 12
        assert row["audit_games"] >= 8
        assert row["audit_positions"] >= row["audit_games"]
        assert row["threshold"] is None or row["threshold"] in THRESHOLDS
        if row["threshold"] is not None:
            assert row["decision_rule"] == "ACCEPT_AUDIT_LOWER_BOUND_POSITIVE"
            assert row["audit_game_cluster_statistics"]["bootstrap_95_ci"][0] > 0
    assert load_gate(path, model)["gate_sha256"] == gate["gate_sha256"]
    with pytest.raises(FileExistsError):
        fit_gate(cohorts, model_path, tmp_path / "stub-unit-only", path,
                 per_player=40, per_game=2, seed=2028)


def test_gate_v3_play_adapter_fail_closed_and_shadow(synthetic_harness, tmp_path):
    _, cohorts, model_path, model = synthetic_harness
    gate_file = tmp_path / "gate.json"
    fit_gate(cohorts, model_path, tmp_path / "unit-only", gate_file,
             per_player=40, per_game=2)
    adapter = ExperimentalPolicyAdapter.load(
        model_path, bindings={"fischer": "overall", "tal": "overall"},
        override_percent=100, min_ply=1, gate_file=gate_file)
    assert adapter.manifest()["gate_sha256"]
    assert adapter.manifest()["mechanism"] == "GAME_DISJOINT_CALIBRATED_RODENT_V3_GATE"
    with pytest.raises(ValueError, match="overall"):
        ExperimentalPolicyAdapter.load(
            model_path, bindings={"fischer": "early_observed", "tal": "overall"},
            override_percent=100, min_ply=1, gate_file=gate_file)
    board = chess.Board()
    result = adapter.select(board, slug="fischer",
                            engine_move=chess.Move.from_uci("g1f3"),
                            round_number=1, ply=1)
    assert result.move in board.legal_moves
    assert result.audit["gate_sha256"]
    assert result.audit["gate_threshold"] == adapter.gate["players"]["fischer"]["threshold"]
    assert result.audit["outcome"] in {
        "GATE_RETAINED_RODENT", "EXTERNAL_POLICY_OVERRIDE",
        "POLICY_RODENT_AGREEMENT",
    }
    assert board.fen() == chess.STARTING_FEN
    shadow = ExperimentalPolicyAdapter.load(
        model_path, bindings={"fischer": "overall", "tal": "overall"},
        override_percent=0, min_ply=1, gate_file=gate_file)
    observed = shadow.select(board, slug="fischer",
                             engine_move=chess.Move.from_uci("g1f3"),
                             round_number=1, ply=1)
    assert observed.move.uci() == "g1f3"
    assert observed.audit["outcome"] == "SHADOW_ONLY"

    raw = _read_json(gate_file)
    raw["players"]["fischer"]["threshold"] = 100.0
    gate_file.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Tampered"):
        ExperimentalPolicyAdapter.load(
            model_path, bindings={"fischer": "overall", "tal": "overall"},
            override_percent=100, gate_file=gate_file)


def test_exact_rodent_binary_and_personality_guard(synthetic_harness, tmp_path,
                                                    monkeypatch):
    from chesssim import matches
    _, cohorts, model_path, _ = synthetic_harness
    gate_file = tmp_path / "gate.json"
    fit_gate(cohorts, model_path, tmp_path / "stub", gate_file,
             per_player=40, per_game=2)
    adapter = ExperimentalPolicyAdapter.load(
        model_path, bindings={"fischer": "overall", "tal": "overall"},
        override_percent=100, min_ply=1, gate_file=gate_file)
    a, b = find_player("fischer"), find_player("tal")
    exe = tmp_path / "rodent-iv-plain.exe"
    exe.write_bytes(b"fake binary unit only")
    profiles = {}
    for p in (a, b):
        path = tmp_path / (p.slug + ".txt")
        path.write_text("synthetic unit profile")
        profiles[p.slug] = path
    monkeypatch.setattr(matches, "validate", lambda *_: (exe, profiles))
    with pytest.raises(ValueError, match="binary differs"):
        matches.play_duel(tmp_path, tmp_path / "out", a, b, depth=1,
                          use_books=False, reverse=False, max_plies=4,
                          experimental_policy=adapter)
    assert not (tmp_path / "out").exists()


def test_real_sources_and_training_model_tamper_are_rejected(
        synthetic_harness, tmp_path):
    _, cohorts, model_path, original = synthetic_harness
    path = tmp_path / "corrupt.json"
    edited = copy.deepcopy(original)
    edited["profiles"]["fischer"]["weights"]["v3:phase:middle"] = 7.0
    path.write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="tampered"):
        fit_gate(cohorts, path, tmp_path / "fake", tmp_path / "gate.json",
                 per_player=40, per_game=2)
    assert not (tmp_path / "gate.json").exists()
    manifest = _read_json(cohorts / "manifest.json")
    relative = manifest["training_files"][0]
    file = cohorts / relative
    with file.open("a", encoding="utf-8") as stream:
        stream.write('{"not":"a real sample"}\n')
    with pytest.raises(ValueError, match="mutated"):
        _calibration_samples(cohorts, original, per_player=40, per_game=2, seed=2028)


def test_disjoint_split_same_game_and_non_degenerate_gate(synthetic_harness):
    _, cohorts, _, model = synthetic_harness
    sample, _ = _calibration_samples(cohorts, model, per_player=40, per_game=2,
                                     seed=2028)
    rows = sample["fischer"]
    a, b = _split_games(rows, seed=2028, slug="fischer")
    assert set(rows[i]["game_sha256"] for i in a).isdisjoint(
        rows[i]["game_sha256"] for i in b)
    assert len(set(rows[i]["game_sha256"] for i in b)) >= 8
    assert _choose("g1f3", "e2e4", 0.4, None) == "g1f3"
    assert _choose("g1f3", "e2e4", 0.4, 0.25) == "e2e4"
    assert _choose("g1f3", "e2e4", 0.25, 0.25) == "g1f3"
    assert _choose("g1f3", "g1f3", 9.0, 0.25) == "g1f3"
    board = chess.Board()
    proposal, lift = _style_lift_and_proposal(
        model, board, "fischer", "overall", "g1f3")
    assert chess.Move.from_uci(proposal) in board.legal_moves
    assert isinstance(lift, float) and lift == lift


def test_matched_validation_and_learning_curves_use_same_positions(
        synthetic_harness, tmp_path):
    sources, cohorts, model_path, _ = synthetic_harness
    gate_file = tmp_path / "gate.json"
    fit_gate(cohorts, model_path, tmp_path / "unit", gate_file,
             per_player=40, per_game=2)
    small = tmp_path / "v3-small.json"
    fit_contextual(cohorts, small, max_per_player=20,
                   max_per_game=2, seed=2026)
    report = evaluate_gate(
        cohorts, model_path, gate_file, tmp_path / "evaluation.json",
        archive_dir=sources, seed=2027, per_player=6, per_game=2,
        rodent_root=tmp_path / "unit", depth=2, seconds=0.12,
        repetitions=100, curve_models={20: small})
    assert report["final_holdout_opened"] is False
    assert report["actual_rodent_uci"] is True
    assert report["validation_arm"] == "validation"
    assert report["all_train_canonical_board_states_embargoed"] > 0
    assert set(report["validation_canonical_overlap_excluded"]) == {"fischer", "tal"}
    assert report["total_validation_decisions"] > 0
    assert set(report["learning_curve"]) == {"20", "36"}
    assert all(entry["total"] == report["total_validation_decisions"]
               for entry in report["learning_curve"].values())
    for slug, row in report["by_player"].items():
        assert row["validation_games"] >= 2
        assert row["learning_curve"]["20"]["positions"] == row["validation_decisions"]
        assert row["learning_curve"]["36"]["positions"] == row["validation_decisions"]
        assert row["paired_game_statistics"]["gate_vs_rodent"]["games"] == row["validation_games"]
        assert row["identity_validated"] is False
        assert row["gate_threshold"] is None or row["gate_threshold"] in THRESHOLDS


def test_holdout_option_does_not_exist():
    from chesssim.real_gate_v4 import main
    with pytest.raises(SystemExit):
        main(["evaluate", "--cohorts", "c", "--model", "m",
              "--gate", "g", "--rodent-root", "r", "--out", "o",
              "--arm", "holdout", "--fetch"])


def test_canonical_embargo_ignores_fen_move_counters():
    a = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    b = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 37 19"
    assert _position_key(a) == _position_key(b)
    with pytest.raises(ValueError, match="FEN"):
        _position_key("not a fen")
