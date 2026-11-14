"""Experimental adapter contracts. Fake engines here are ONLY unit tests.

The separate integration workflow must compile and run real Rodent IV.
Fixtures never constitute evidence of historical performance.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import chess
import pytest

from chesssim.catalog import find_player
from chesssim.experimental_policy_adapter import (
    ExperimentalPolicyAdapter, RESEARCH_STATUS, trace_summary,
)
from chesssim.matches import _play_one, play_duel
from chesssim.real_policy_experiment import POLICY_SCHEMA, _digest


def model_fixture(path: Path) -> Path:
    model = {
        "schema": POLICY_SCHEMA,
        "status": "REAL_PGN_TRAINED_EXPERIMENTAL_NOT_STYLE_VALIDATED",
        "method": "L2_ADAGRAD_CONDITIONAL_LOGIT_OVER_LEGAL_MOVES",
        "source_pgn_sha256": {"fischer": "a" * 64, "tal": "b" * 64},
        "train_manifest_sha256": "c" * 64,
        "generic_weights": {},
        "profiles": {
            "fischer": {
                "weights": {"piece_target:P:e4": 8.0},
                "career_weights": {"early_observed": {"piece_target:P:d4": 7.5}},
            },
            "tal": {
                "weights": {"piece_target:P:e5": 8.0},
                "career_weights": {"late_observed": {"piece_target:P:d5": 7.5}},
            },
        },
    }
    model["model_sha256"] = _digest(model)
    path.write_text(json.dumps(model, sort_keys=True), encoding="utf-8")
    return path


def adapter(tmp_path: Path, *, percent: int = 100, min_ply: int = 1,
            eras: dict | None = None) -> ExperimentalPolicyAdapter:
    path = model_fixture(tmp_path / "model.json")
    return ExperimentalPolicyAdapter.load(
        path,
        bindings=eras or {"fischer": "overall", "tal": "overall"},
        override_percent=percent, min_ply=min_ply, seed=19)


def test_verified_model_override_shadow_and_unchanged_board(tmp_path):
    model = adapter(tmp_path)
    board = chess.Board()
    initial_fen = board.fen()
    engine_move = chess.Move.from_uci("g1f3")
    choice = model.select(board, slug="fischer", engine_move=engine_move,
                          round_number=1, ply=1)
    assert choice.move.uci() == "e2e4"
    assert choice.audit["rodent_move"] == "g1f3"
    assert choice.audit["policy_move"] == "e2e4"
    assert choice.audit["played_move"] == "e2e4"
    assert choice.audit["outcome"] == "EXTERNAL_POLICY_OVERRIDE"
    assert choice.audit["sampled"] is True
    assert board.fen() == initial_fen
    assert choice.audit["fen_sha256"] == sha256(initial_fen.encode()).hexdigest()
    assert model.select(board, slug="fischer", engine_move=engine_move,
                        round_number=1, ply=1) == choice

    shadow = ExperimentalPolicyAdapter.load(
        tmp_path / "model.json", bindings=model.bindings,
        override_percent=0, seed=19, min_ply=1)
    result = shadow.select(board, slug="fischer", engine_move=engine_move,
                           round_number=1, ply=1)
    assert result.move == engine_move
    assert result.audit["policy_move"] == "e2e4"
    assert result.audit["outcome"] == "SHADOW_ONLY"
    assert result.audit["sampled"] is False

    warmup = ExperimentalPolicyAdapter.load(
        tmp_path / "model.json", bindings=model.bindings,
        override_percent=100, min_ply=17)
    result = warmup.select(board, slug="fischer", engine_move=engine_move,
                           round_number=1, ply=1)
    assert result.audit["outcome"] == "WARMUP_UNMODIFIED"
    assert result.audit["policy_move"] is None
    assert result.move == engine_move


def test_loaded_era_is_explicit_and_unknown_era_fails_closed(tmp_path):
    early = adapter(tmp_path, eras={"fischer": "early_observed",
                                   "tal": "late_observed"})
    board = chess.Board()
    engine = chess.Move.from_uci("g1f3")
    choice = early.select(board, slug="fischer", engine_move=engine,
                          round_number=1, ply=1)
    assert choice.move.uci() == "d2d4"
    assert early.manifest()["players_and_eras"]["tal"] == "late_observed"
    assert early.manifest()["engine_internal_parameters_changed"] is False
    assert early.manifest()["learning_during_game"] is False
    with pytest.raises(ValueError, match="not trained"):
        adapter(tmp_path, eras={"fischer": "late_observed", "tal": "overall"})


def test_rejects_tampering_bad_rate_and_illegal_uci(tmp_path):
    original = adapter(tmp_path)
    path = tmp_path / "model.json"
    contents = json.loads(path.read_text())
    contents["profiles"]["fischer"]["weights"]["piece_target:P:e4"] = -8
    path.write_text(json.dumps(contents), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        ExperimentalPolicyAdapter.load(
            path, bindings=original.bindings, override_percent=100)
    model_fixture(path)
    for rate in (-1, 101, 22.5, True):
        with pytest.raises(ValueError, match="Override percent"):
            ExperimentalPolicyAdapter.load(
                path, bindings=original.bindings, override_percent=rate)
    with pytest.raises(ValueError, match="illegal"):
        original.select(chess.Board(), slug="fischer",
                        engine_move=chess.Move.from_uci("e2e5"),
                        round_number=1, ply=1)
    with pytest.raises(ValueError, match="index"):
        original.select(chess.Board(), slug="fischer",
                        engine_move=chess.Move.from_uci("e2e4"),
                        round_number=1, ply=3)


def test_enforced_official_and_repertoire_guards_before_engine_start(tmp_path):
    from chesssim import matches
    first = find_player("fischer")
    second = find_player("tal")
    loaded = adapter(tmp_path)
    exe = tmp_path / "dummy-engine"
    exe.write_bytes(b"x")
    paths = {}
    for p in (first, second):
        pf = tmp_path / (p.slug + ".txt")
        pf.write_text("fake unit test profile")
        paths[p.slug] = pf
    def fake_validate(_root, _a, _b):
        return exe, paths
    # Positive path is tested below; official and book modes must refuse
    # execution before output directories are created.
    original = matches.validate
    matches.validate = fake_validate
    try:
        for props in (
            {"use_books": True}, {"use_books": None},
            {"clock": (600, 2), "use_books": False},
            {"historical_openings": {"fischer": {}}, "use_books": False},
        ):
            with pytest.raises(ValueError, match="Experimental policy"):
                play_duel(tmp_path, tmp_path / "out", first, second,
                          depth=1, reverse=False, max_plies=6,
                          experimental_policy=loaded, **props)
        assert not (tmp_path / "out").exists()
    finally:
        matches.validate = original


class _MockEngine:
    """Fake, deterministic legal play exclusively for unit tests."""
    def play(self, board, limit, **kwargs):
        return SimpleNamespace(
            move=min(board.legal_moves, key=lambda move: move.uci()))
    def quit(self):
        pass
    def close(self):
        pass


def test_real_match_orchestration_persists_overrides_without_online_learning(
        tmp_path, monkeypatch):
    from chesssim import matches
    loaded = adapter(tmp_path)
    a, b = find_player("fischer"), find_player("tal")
    monkeypatch.setattr(matches, "_open_engine", lambda *args, **kwargs: _MockEngine())
    hashes = {"exe": "engine-test", a.slug: "fischer-test", b.slug: "tal-test"}
    game, meta = _play_one(
        tmp_path, tmp_path / "dummy", a, b, 1, None, 8, 1, hashes,
        use_books=False, experimental_policy=loaded)
    assert meta["plies"] == 8
    assert len(meta["policy_trace"]) == 8
    assert sum(row["played_plies"] for row in meta["policy_counts"].values()) == 8
    assert game.headers["PolicyOfficial"] == "false"
    assert game.headers["PolicyMechanism"] == "EXTERNAL_MOVE_OVERRIDE_NOT_ENGINE_TRAINING"
    assert any(e["outcome"] == "EXTERNAL_POLICY_OVERRIDE" for e in meta["policy_trace"])
    assert any("experimental external policy override" in n.comment.lower()
               for n in game.mainline())
    assert all(chess.Move.from_uci(entry["played_move"]) is not None
               for entry in meta["policy_trace"])
    # reversing colors must preserve trained identity by slug, not color.
    swapped, reverse_meta = _play_one(
        tmp_path, tmp_path / "dummy", b, a, 1, None, 8, 2, hashes,
        use_books=False, experimental_policy=loaded)
    assert swapped.headers["PolicyBlackEra"] == "overall"
    assert reverse_meta["policy_trace"][0]["player"] == "tal"
    assert reverse_meta["policy_trace"][1]["player"] == "fischer"
    assert loaded.model["model_sha256"] == game.headers["PolicyModelSHA256"]


def test_duel_writes_auditable_metadata_and_reports_incomplete_plies(
        tmp_path, monkeypatch):
    from chesssim import matches
    loaded = adapter(tmp_path)
    a, b = find_player("fischer"), find_player("tal")
    engine = tmp_path / "real-name-but-unit-fixture.exe"
    engine.write_bytes(b"fake engine for unit test")
    files = {}
    for p in (a, b):
        pf = tmp_path / (p.slug + ".txt")
        pf.write_text("unit-test profile", encoding="utf-8")
        files[p.slug] = pf
    monkeypatch.setattr(matches, "validate", lambda *_: (engine, files))
    monkeypatch.setattr(matches, "_open_engine", lambda *args, **kwargs: _MockEngine())
    folder = play_duel(
        tmp_path, tmp_path / "results", a, b,
        depth=1, max_plies=6, use_books=False, reverse=True,
        experimental_policy=loaded)
    report = json.loads((folder / "summary.json").read_text())
    assert report["status"] == "INCOMPLETE"  # not a fabricated draw
    assert report["experimental_learned_policy"]["status"] == RESEARCH_STATUS
    assert report["experimental_learned_policy"]["override_percent"] == 100
    assert len(report["games"]) == 2
    assert all(len(game["policy_trace"]) == 6 for game in report["games"])
    assert report["games"][0]["policy_trace"][0]["player"] == "fischer"
    assert report["games"][1]["policy_trace"][0]["player"] == "tal"
    assert (folder / "jogo_1.pgn").exists()
    assert (folder / "jogo_2.pgn").exists()
    assert not (folder / "jogo_1.partial.pgn").is_symlink()


def test_cli_rejects_policy_flags_without_model_before_game(capsys):
    from chesssim.cli import main
    status = main(["play", "fischer", "tal", "--policy-override-percent", "100"])
    assert status == 2
    assert "--policy-model" in capsys.readouterr().err


def test_trace_summary_counts_committed_outcomes():
    events = [
        {"player": "fischer", "eligible": True, "sampled": True,
         "outcome": "EXTERNAL_POLICY_OVERRIDE"},
        {"player": "fischer", "eligible": True, "sampled": False,
         "outcome": "SHADOW_ONLY"},
        {"player": "tal", "eligible": False, "sampled": False,
         "outcome": "WARMUP_UNMODIFIED"},
    ]
    totals = trace_summary(events)
    assert totals["fischer"]["actual_overrides"] == 1
    assert totals["fischer"]["eligible"] == 2
    assert totals["tal"]["eligible"] == 0
