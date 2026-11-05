"""Independent Stockfish audit trace: provenance, PV legality and reconciliation.

Tests never require installation of Stockfish. A controlled engine implements
the same python-chess analyse() contract and allows exact conservation checks.
"""
from __future__ import annotations

import json

import chess
import chess.engine
import chess.pgn
import pytest

from chesssim import quality_audit as quality


def _games(path, *, duplicate=False):
    variations = [
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5"],
        ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c1g5", "f8e7"],
    ]
    with path.open("w", encoding="utf-8") as target:
        for index, moves in enumerate(variations + ([variations[0]] if duplicate else []), 1):
            game = chess.pgn.Game()
            game.headers["Event"] = f"synthetic {index}"
            game.headers["White"] = "Profile Alpha"
            game.headers["Black"] = "Profile Beta"
            board = game.board()
            node = game
            for uci in moves:
                move = chess.Move.from_uci(uci)
                assert move in board.legal_moves
                node = node.add_variation(move)
                board.push(move)
            print(game, file=target, end="\n\n")


class StableJudge:
    calls = []
    def __init__(self, *, invalid=None):
        self.id = {"name": "Controlled Fake UCI (test)", "author": "ChessSim test suite"}
        self.options = {"Threads": object(), "Hash": object()}
        self.configured = {}
        self.invalid = invalid
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def configure(self, options):
        self.configured = options.copy()

    def analyse(self, board, limit, *, root_moves=None, info=None):
        assert info == chess.engine.INFO_ALL
        assert limit.depth > 0
        candidate = sorted(board.legal_moves, key=lambda x: x.uci())[0]
        if root_moves is not None:
            assert len(root_moves) == 1
            candidate = root_moves[0]
        continuation = board.copy(stack=False)
        continuation.push(candidate)
        pv = [candidate]
        if not continuation.is_game_over():
            pv.append(sorted(continuation.legal_moves, key=lambda x: x.uci())[0])
        if self.invalid == "foreign" and root_moves is not None:
            pv[0] = sorted(
                (m for m in board.legal_moves if m != candidate), key=lambda x: x.uci()
            )[0]
        if self.invalid == "illegal":
            pv = [chess.Move.from_uci("a1a1")]
        if self.invalid == "missing":
            pv = []
        # Forced move is 40 cp worse except where already the best root move.
        score = 80 if root_moves is None or candidate == sorted(
            board.legal_moves, key=lambda x: x.uci()
        )[0] else 40
        self.calls.append({"fen": board.fen(), "depth": limit.depth,
                           "forced": root_moves is not None})
        return {"score": chess.engine.PovScore(chess.engine.Cp(score), board.turn),
                "pv": pv}


@pytest.fixture
def audit_input(tmp_path):
    pgn = tmp_path / "two.pgn"
    exe = tmp_path / "stockfish"
    exe.write_bytes(b"controlled-engine-executable")
    _games(pgn)
    return pgn, exe, tmp_path / "audit.json"


def _run(monkeypatch, pgn, exe, out, *, fake=None, **kwargs):
    judge = fake or StableJudge()
    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", lambda *a, **kw: judge)
    budget = kwargs.pop("max_positions", 12)
    return quality.analyze_games_v91(
        pgn, exe, out, depth=5, max_positions=budget, min_fullmove=1,
        pv_plies=2, **kwargs
    ), judge


def test_audit_records_engine_identity_exact_uci_pvs_and_consistent_totals(
    monkeypatch, audit_input
):
    pgn, exe, out = audit_input
    report, judge = _run(monkeypatch, pgn, exe, out)
    assert report["status"] == "EXPLORATORY_STRENGTH_DIAGNOSTIC_NOT_ELO"
    assert report["inputs"]["engine_uci_id"]["name"] == "Controlled Fake UCI (test)"
    assert report["inputs"]["engine_configured_options"] == {"Threads":1, "Hash":64}
    assert report["inputs"]["analysis_limit"] == {"kind": "depth", "value": 5}
    assert report["inputs"]["mate_mapping_cp"] == 1000
    assert judge.configured == {"Threads":1, "Hash":64}
    assert len(judge.calls) == 2 * len(report["decisions"])
    assert {row["depth"] for row in report["decisions"]} == {5}
    assert len(report["games"]) == 2
    assert report["sampling"]["method"] == "balanced"
    assert len(report["sampling"]["cells"]) == 4
    assert report["reconciliation"]["status"] == "CONSISTENT"
    assert report["reconciliation"]["game_level_decisions"] == len(report["decisions"])
    assert report["reconciliation"]["independent_decisions"] == len(report["decisions"])
    assert sum(row["decisions_analyzed"] for row in report["summary"].values()) == len(report["decisions"])
    assert sum(row["decisions_analyzed"] for row in report["summary_by_player_color"].values()) == len(report["decisions"])
    assert report["summary"]["Profile Alpha"]["decisions_analyzed"] == 6
    assert report["summary"]["Profile Beta"]["decisions_analyzed"] == 6
    assert report["summary_by_player_color"]["Profile Alpha|white"]["decisions_analyzed"] == 6
    assert all(r["best_move"] == r["best_line_uci"][0] for r in report["decisions"])
    for r in report["decisions"]:
        board = chess.Board(r["fen"])
        assert r["best_move_san"] == board.san(chess.Move.from_uci(r["best_move"]))
        assert r["played_move_san"] == board.san(chess.Move.from_uci(r["played"]))
        assert r["played_line_uci"][0] == r["played"]
        assert r["pv_availability"] == {"best": "PRESENT", "forced":"PRESENT"}
        assert len(r["best_line_san"]) == len(r["best_line_uci"])
        assert r["cp_loss"] >= 0
    assert json.loads(out.read_text(encoding="utf-8")) == report


def test_duplicate_games_can_be_inspected_without_polluting_aggregate(monkeypatch, audit_input):
    pgn, exe, out = audit_input
    _games(pgn, duplicate=True)
    report, _ = _run(monkeypatch, pgn, exe, out, dedupe_games=False, max_positions=20)
    independent = [d for d in report["decisions"] if d["game_index"] != 2]
    assert report["diversity"]["games_duplicate"] == 1
    assert report["sampling"]["diagnostic_duplicate_positions_selected"] > 0
    assert report["reconciliation"]["independent_decisions"] == len(independent)
    assert report["reconciliation"]["all_decisions"] == len(report["decisions"])
    assert report["reconciliation"]["diagnostic_duplicate_decisions"] == (
        len(report["decisions"]) - len(independent))
    assert sum(s["decisions_analyzed"] for s in report["summary"].values()) == len(independent)
    assert report["games"][2]["diagnostic_only"] is True


@pytest.mark.parametrize("invalid,diagnostic",[
    ("illegal","illegal or foreign"),
    ("foreign","wrong root move"),
])
def test_invalid_uci_variations_are_not_published(monkeypatch, audit_input, invalid, diagnostic):
    pgn, exe, out = audit_input
    with pytest.raises(ValueError, match=diagnostic):
        _run(monkeypatch, pgn, exe, out, fake=StableJudge(invalid=invalid))
    assert not out.exists()


def test_missing_engine_pv_does_not_create_a_best_move(monkeypatch, audit_input):
    pgn, exe, out = audit_input
    report, _ = _run(monkeypatch, pgn, exe, out, fake=StableJudge(invalid="missing"))
    assert all(r["best_move"] is None and r["best_move_san"] is None for r in report["decisions"])
    assert all(r["best_line_uci"] == [] and r["played_line_uci"] == [] for r in report["decisions"])
    assert all(r["pv_availability"] == {"best":"UNAVAILABLE","forced":"UNAVAILABLE"}
               for r in report["decisions"])


def test_mate_score_keeps_distance_and_cp_mapping_separate():
    white = chess.WHITE
    info = {"score": chess.engine.PovScore(chess.engine.Mate(3), white)}
    assert quality._eval_pov(info, white) == {"cp_equivalent": 997, "mate_in": 3}
    assert quality._eval_pov(info, chess.BLACK) == {
        "cp_equivalent": -997, "mate_in": -3
    }
