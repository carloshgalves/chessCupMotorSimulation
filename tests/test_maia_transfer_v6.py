"""Maia transfer V6 engineering fixtures. No mock-based scientific claims."""
from __future__ import annotations

import chess
import chess.pgn
import pytest

from chesssim.maia_transfer_v6 import (
    ELOS, MAX_CP_LOSS, _calibrate, _choose, _position_key,
    _restore_real_histories, _split_by_game, main, run_experiment,
)


def _record(i, human="e2e4", chosen="e2e4"):
    board = chess.Board()
    return {
        "fen": board.fen(),
        "move": human,
        "player": "fischer",
        "game_sha256": f"{i:064x}",
        "ply": 21,
        "rodent": "d2d4",
        "maia": {"1800": chosen, "2400": "g1f3"},
        "rodent_variations": [
            {"move": "d2d4", "cp": 45},
            {"move": "e2e4", "cp": 35},
            {"move": "g1f3", "cp": -25},
            {"move": "c2c4", "cp": -100},
        ],
    }


def test_tactical_budget_blocks_unknown_or_high_loss_moves():
    r = _record(1)
    variants = r["rodent_variations"]
    assert _choose("d2d4", "e2e4", variants, None) == "d2d4"
    assert _choose("d2d4", "e2e4", variants, 0) == "d2d4"
    assert _choose("d2d4", "e2e4", variants, 35) == "e2e4"
    assert _choose("d2d4", "g1f3", variants, 35) == "d2d4"
    assert _choose("d2d4", "b1c3", variants, 150) == "d2d4"
    assert _choose("d2d4", "d2d4", variants, 150) == "d2d4"
    assert ELOS == (1800, 2400)
    assert MAX_CP_LOSS == (0, 35, 80, 150)
    with pytest.raises(ValueError, match="Unregistered"):
        _choose("d2d4", "e2e4", variants, 1000)
    with pytest.raises(ValueError, match="bestmove"):
        _choose("g1f3", "e2e4", variants, 35)


def test_calibration_audits_different_games_and_can_accept_genuine_gain():
    rows = [_record(i) for i in range(40)]
    a, b = _split_by_game(rows, 2036, "fischer")
    assert len(a) >= 12 and len(b) >= 8
    assert {rows[i]["game_sha256"] for i in a}.isdisjoint(
        {rows[i]["game_sha256"] for i in b})
    decision = _calibrate(rows, 2036, "fischer")
    assert decision["decision"] == "PERMIT_LIMITED_MAIA_CHOICE"
    assert decision["configured_maia_elo"] == 1800
    assert decision["max_tactical_loss_cp"] in (35, 80, 150)
    assert decision["audit_paired_statistic"]["bootstrap_95_ci"][0] > 0
    assert _calibrate(rows, 2036, "fischer") == decision


def test_calibration_fails_closed_when_maia_hurts_real_move_agreement():
    rows = [_record(i, human="d2d4") for i in range(40)]
    decision = _calibrate(rows, 2036, "fischer")
    assert decision["configured_maia_elo"] is None
    assert decision["max_tactical_loss_cp"] is None
    assert decision["decision"].startswith("DENY")
    assert decision["audit_changed_lances"] == 0


def test_rebuilds_historical_history_without_pushing_target(monkeypatch, tmp_path):
    game = chess.pgn.Game()
    board = chess.Board()
    node = game
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6",
                "f1c4", "f8c5", "d2d3", "g8f6",
                "e1g1", "e8g8", "c2c3", "d7d6",
                "f1e1", "a7a6", "c4b3", "c5a7",
                "h2h3", "h7h6", "b1d2", "c6a5",
                "b3c2", "d6d5"):
        node = node.add_variation(chess.Move.from_uci(uci))
    import hashlib
    raw = b"genuine fixture bytes not used for science"
    digest = hashlib.sha256(raw).hexdigest()
    samples = {"fischer": [{
        "game_sha256": "ab" * 32, "ply": 21, "move": "b3c2",
        "fen": None,
    }]}
    for i, move in enumerate(game.mainline_moves(), start=1):
        if i == 21:
            samples["fischer"][0]["fen"] = board.fen()
            assert board.ply() == 20
            break
        board.push(move)
    from chesssim import maia_transfer_v6 as m
    monkeypatch.setattr(m, "_source_bytes", lambda *_: (b"x", raw, "fixture"))
    monkeypatch.setattr(m, "_scan", lambda *_: ([{"mainline_sha256": "ab" * 32}], {}))
    monkeypatch.setattr(m, "_iter_verified_games", lambda *_: iter([
        (game, {"mainline_sha256": "ab" * 32})
    ]))
    monkeypatch.setattr(m, "_player_configuration", lambda *_: {
        "fischer": {"source": "Fischer", "surname": "Fischer"}
    })
    manifest = {"sources": {"fischer": {"source": "Fischer.pgn"}}}
    monkeypatch.setattr(m, "_read_json", lambda *_: manifest)
    base = {"profiles": {"fischer": {}},
            "source_pgn_sha256": {"fischer": digest}}
    restored, hashes = _restore_real_histories(tmp_path, base, samples, None)
    b = restored["fischer"][("ab" * 32, 21)]
    assert b.fen() == samples["fischer"][0]["fen"]
    assert len(b.move_stack) == 20
    assert b.peek().uci() == "c6a5"
    assert b.legal_moves.count() > 1
    assert hashes["fischer"] == digest


def test_canonical_board_state_ignores_fen_move_counters():
    a = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    b = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 17 35"
    assert _position_key(a) == _position_key(b)


def test_requires_exact_maia_commit_and_no_holdout_option(tmp_path):
    with pytest.raises(ValueError, match="pinned"):
        run_experiment(tmp_path, tmp_path / "v3", tmp_path, tmp_path / "out",
                       maia_revision="main")
    with pytest.raises(SystemExit):
        main(["--cohorts", "c", "--v3-model", "v3", "--rodent-root", "r",
              "--out", "o", "--maia-revision", "0" * 40,
              "--arm", "holdout", "--fetch"])
