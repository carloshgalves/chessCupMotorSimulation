"""Adversarial game/trace integrity and original-FEN forced-root comparisons."""
import copy
from hashlib import sha256
import json

import chess
import chess.engine
import chess.pgn
import pytest

from chesssim import real85_neural_counterfactual as deep
from chesssim.real85_neural_guarded_uci import SCHEMA as GAME_SCHEMA


def _fixture(tmp_path):
    players = ["fischer", "tal", "karpov", "kasparov"]
    engine_sha = "a" * 64
    sf_sha = "b" * 64
    records, games = [], []
    for player in players:
        for arm in ("shadow", "guided"):
            for color in ("white", "black"):
                board = chess.Board()
                game = chess.pgn.Game()
                game.headers.update({
                    "Result": "0-1", "Termination": "CHECKMATE",
                    "NeuralMode": arm, "EngineSHA256": engine_sha,
                    "IndependentStockfishSHA256": sf_sha,
                })
                trace = []
                node = game
                for ply, encoded in enumerate(("f2f3", "e7e5", "g2g4", "d8h4"), 1):
                    mover = player if board.turn == (color == "white") else "control-simple"
                    move = chess.Move.from_uci(encoded)
                    assert move in board.legal_moves
                    trace.append({
                        "ply": ply, "player": mover,
                        "fen_sha256": sha256(board.fen().encode()).hexdigest(),
                        "played_move": encoded, "rodent_move": encoded,
                        "outcome": ("CONTROL_RODENT_UNMODIFIED" if
                                    mover == "control-simple" else
                                    "WARMUP_RODENT_UNMODIFIED"),
                        "sampled": False,
                    })
                    node = node.add_variation(move)
                    board.push(move)
                assert board.outcome(claim_draw=True).result() == "0-1"
                games.append(str(game))
                records.append({
                    "player": player, "color": color, "arm": arm,
                    "status": "COMPLETED", "result": "0-1",
                    "termination": "CHECKMATE", "plies": 4,
                    "policy_trace": trace, "opening_trace": [],
                    "focal_historical_book_plies": 0, "simple_book_plies": 0,
                    "actual_overrides": 0,
                })
    data = {
        "schema": GAME_SCHEMA, "status": "ALL_REAL_UCI_GAMES_COMPLETE",
        "historical_focal_book_enabled": True,
        "historical_book_training": "real85_game_disjoint_85pct_train",
        "official_personality_files_changed": False,
        "rated_official_games": False,
        "historical_elo_estimated": False, "maia_dsc_weights_changed": False,
        "min_ply": 41, "cp_budget": 25,
        "rodent_original_sha256": engine_sha,
        "stockfish_sha256": sf_sha,
        "players": players, "games": records,
    }
    results = tmp_path / "results.json"
    pgn = tmp_path / "games.pgn"
    results.write_text(json.dumps(data))
    pgn.write_text("\n\n".join(games) + "\n")
    return data, results, pgn


def test_replay_rejects_detached_trace_and_forged_terminal_game(tmp_path):
    data, results, pgn = _fixture(tmp_path)
    read = deep._load_replayed(data, pgn)
    assert set(read) == set(data["players"])
    assert not any(read.values())
    with pytest.raises(ValueError, match="No actual guided neural override"):
        deep.audit(results, pgn, tmp_path / "missing-stockfish",
                   tmp_path / "never-created.json")
    wrong = copy.deepcopy(data)
    wrong["games"][0]["policy_trace"][0]["played_move"] = "e2e4"
    with pytest.raises(ValueError, match="trace diverge"):
        deep._load_replayed(wrong, pgn)
    wrong = copy.deepcopy(data)
    wrong["games"][0]["actual_overrides"] = 1
    with pytest.raises(ValueError, match="count does not reconcile"):
        deep._load_replayed(wrong, pgn)
    wrong = copy.deepcopy(data)
    wrong["games"][0]["result"] = "1-0"
    with pytest.raises(ValueError, match="PGN provenance"):
        deep._load_replayed(wrong, pgn)
    wrong = copy.deepcopy(data)
    wrong["games"][0]["policy_trace"][0]["outcome"] = "EXTERNAL_POLICY_OVERRIDE"
    with pytest.raises(ValueError, match="unsafe, unproven"):
        deep._load_replayed(wrong, pgn)


def test_bad_games_missing_colors_and_duplicate_evidence_fails_closed(tmp_path):
    data, results, pgn = _fixture(tmp_path)
    broken = copy.deepcopy(data)
    broken["games"][-1]["color"] = "white"
    with pytest.raises(ValueError, match="coverage"):
        deep._load_replayed(broken, pgn)
    results.write_text('{"schema":"x","schema":"x"}')
    with pytest.raises(ValueError, match="Duplicated evidence JSON key"):
        deep._read_json(results)
    results.write_text('{"x":NaN}')
    with pytest.raises(ValueError, match="Non-finite evidence"):
        deep._read_json(results)


class FakeDeeperStockfish:
    def __init__(self, mate=False):
        self.calls = []
        self.mate = mate

    def analyse(self, board, limit, root_moves, info, game):
        assert limit.depth == 12
        assert len(root_moves) == 1
        assert info == chess.engine.INFO_SCORE | chess.engine.INFO_PV
        move = root_moves[0]
        assert move in board.legal_moves
        self.calls.append((board.fen(), move.uci()))
        numeric = 45 if move.uci() == "e2e4" else 5
        sc = chess.engine.Mate(3) if self.mate else chess.engine.Cp(numeric)
        return {"score": chess.engine.PovScore(sc, board.turn), "pv": [move]}


def test_independent_forced_root_search_uses_identical_fen_and_depth():
    board = chess.Board()
    old, new = chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4")
    engine = FakeDeeperStockfish()
    first = deep._root_score(engine, board, old, 12)
    second = deep._root_score(engine, board, new, 12)
    assert first == {"cp": 45, "mate_in": None}
    assert second == {"cp": 5, "mate_in": None}
    assert second["cp"] - first["cp"] == -40
    assert len(engine.calls) == 2
    assert engine.calls[0][0] == engine.calls[1][0] == chess.STARTING_FEN
    assert board.fen() == chess.STARTING_FEN
    mate = deep._root_score(FakeDeeperStockfish(mate=True), board, new, 12)
    assert mate == {"cp": None, "mate_in": 3}
    with pytest.raises(ValueError, match="not legal"):
        deep._root_score(engine, board, chess.Move.from_uci("e2e5"), 12)


def test_evidence_summary_does_not_make_up_strength_ci_or_hide_negative_delta():
    records = [
        {"game_index": 0, "mate_affected": False,
         "deep_neural_minus_rodent_cp": -130},
        {"game_index": 0, "mate_affected": False,
         "deep_neural_minus_rodent_cp": -30},
        {"game_index": 1, "mate_affected": False,
         "deep_neural_minus_rodent_cp": 12},
        {"game_index": 1, "mate_affected": True,
         "deep_neural_minus_rodent_cp": None},
    ]
    outcome = deep._summary(records)
    assert outcome["actual_guided_overrides"] == 4
    assert outcome["mate_affected_excluded_from_cp"] == 1
    assert outcome["cp_comparisons"] == 3
    assert outcome["neural_deeper_worse_more_than_100cp"] == 1
    assert outcome["neural_deeper_worse_more_than_25cp"] == 2
    assert outcome["nonmate_median_neural_minus_original_cp"] == -30
    assert outcome["confidence_interval"] is None
    assert len(outcome["guided_game_means_cp"]) == 2
