"""Reference adjudication must not launder a failed historical parity gate."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json

import chess
import chess.engine
import pytest

from chesssim import reference_stability_adjudication as audit


def _records():
    rows = {}
    for player in ("fischer", "tal"):
        for number in range(5):
            for kind in ("flagged", "control"):
                case = {
                    "id": sha256(f"{player}:{number}:{kind}".encode()).hexdigest(),
                    "player": player, "era": "peak",
                    "phase": "middlegame", "game": f"game-{player}-{number}",
                    "human_move": "e2e4",
                }
                rows[case["id"]] = {
                    "case": case,
                    "measured": {"reference": {
                        "inconsistent": kind == "flagged",
                        "best": "e2e4", "second": "d2d4",
                    }, "moves": {
                        "human": "e2e4", "fischer": "d2d4",
                        "tal_balanced": "d2d4",
                        "tal_original": "d2d4", "simple": "d2d4",
                    }},
                }
    return rows


def test_disjoint_matched_source_controls_and_all_anomalies(monkeypatch):
    monkeypatch.setattr(audit, "ORIGINAL_FLAGS", 10)
    original = _records()
    selected = audit.sample(original)
    assert len(selected) == 20
    assert Counter(k for k, _ in selected.values()) == {
        "flagged": 10, "control": 10,
    }
    assert selected == audit.sample(original)
    assert all(row["case"]["player"] in ("fischer", "tal")
               for _, row in selected.values())
    across = [x["case"]["id"] for i in range(4)
              for _, x in audit.assignment(selected, i)]
    assert sorted(across) == sorted(selected)
    with pytest.raises(ValueError, match="shard index"):
        audit.assignment(selected, 4)


def test_original_reference_count_change_is_a_hard_failure(monkeypatch):
    monkeypatch.setattr(audit, "ORIGINAL_FLAGS", 11)
    with pytest.raises(ValueError, match="source count"):
        audit.sample(_records())


class FakeStockfish:
    def analyse(self, board, limit, multipv, game):
        assert limit.depth == 24
        assert multipv == 2
        assert board.fen() == chess.Board().fen()
        return [
            {"pv": [chess.Move.from_uci("e2e4")],
             "score": chess.engine.PovScore(chess.engine.Cp(30), chess.WHITE),
             "depth": 24},
            {"pv": [chess.Move.from_uci("d2d4")],
             "score": chess.engine.PovScore(chess.engine.Cp(20), chess.WHITE),
             "depth": 24},
        ]


def test_depth24_rechecks_all_source_human_engine_and_reference_choices(monkeypatch):
    recorded = _records()
    old = next(x for x in recorded.values()
               if x["measured"]["reference"]["inconsistent"])
    calls = []
    def forced(sf, board, move, depth):
        assert depth == 24
        calls.append(move.uci())
        return {"move": move.uci(), "cp": {
            "e2e4": 30, "d2d4": 88,
        }[move.uci()], "mate": None}
    monkeypatch.setattr(audit, "_score", forced)
    result = audit.inspect_case(FakeStockfish(), chess.Board(), old, "flagged")
    assert set(calls) == {"e2e4", "d2d4"}
    assert result["depth24_top_underperformed"] is True
    assert result["max_forced_better_cp"] == 58
    assert result["status"] == "DIAGNOSTIC_ONLY_NO_PARITY_PROMOTION"


def test_incomplete_higher_depth_analyses_cannot_be_fabricated():
    class FailedEngine:
        def analyse(self, board, limit, multipv, game):
            return [{"pv": [chess.Move.from_uci("e2e4")],
                     "score": chess.engine.PovScore(chess.engine.Cp(30), chess.WHITE),
                     "depth": 23}]
    old = next(iter(_records().values()))
    with pytest.raises(ValueError, match="two-line"):
        audit.inspect_case(FailedEngine(), chess.Board(), old, "control")


def test_immutable_source_report_must_match_original_hash(tmp_path):
    file = tmp_path / "report.json"
    file.write_text(json.dumps({
        "status": "HISTORICAL_MOVE_QUALITY_PARITY_PROXY_PASSED_REVIEW_REQUIRED",
        "release_eligible": True,
    }))
    with pytest.raises(ValueError, match="exact completed run"):
        audit.load_prior(tmp_path, file)
