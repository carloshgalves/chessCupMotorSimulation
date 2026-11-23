"""Historical Fischer/Tal normalized move-quality scientific safeguards."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json

import chess
import pytest

from chesssim import historical_quality_parity as quality


def fake_rows(name):
    rows = []
    for i in range(300):
        year = (1970 if name == "fischer" else 1959) if i < 140 else 1980
        rows.append({
            "mainline_sha256": sha256(f"{name}:{i}".encode()).hexdigest(),
            "date": year,
            "color": "white" if i % 2 else "black",
        })
    return rows


def test_select_same_number_of_peak_and_career_fit_games_without_test_leak(monkeypatch):
    monkeypatch.setattr(quality, "neural_arm", lambda gid: "fit")
    a, b = fake_rows("fischer"), fake_rows("tal")
    source = {"fischer": (None, a, None, None, None),
              "tal": (None, b, None, None, None)}
    chosen = quality.choose_games(source)
    assert all(len(chosen[p]) == 2*quality.GAMES_PER_ERA for p in chosen)
    assert all(sum(quality.era(p, row["date"]) == period
                   for row in chosen[p]) == quality.GAMES_PER_ERA
               for p in chosen for period in ("peak", "other"))
    assert len({row["mainline_sha256"] for rows in chosen.values()
                for row in rows}) == 4*quality.GAMES_PER_ERA
    assert chosen == quality.choose_games(source)
    monkeypatch.setattr(quality, "neural_arm",
                        lambda gid: "excluded_test" if gid == a[0]["mainline_sha256"]
                        else "fit")
    updated = quality.choose_games(source)
    assert a[0] not in updated["fischer"]


def test_historical_position_selection_prechoice_and_nonforced():
    import chess.pgn
    game = chess.pgn.Game()
    node = game
    for uci in ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6",
                "d2d3", "f8c5", "b1c3", "d7d6"):
        move = chess.Move.from_uci(uci)
        assert move in node.board().legal_moves
        node = node.add_variation(move)
    row = {"mainline_sha256": "f"*64, "color": "white", "date": 1970}
    positions = list(quality._positions(game, row, "fischer"))
    assert positions
    for entry, board in positions:
        assert entry["game"] == "f"*64
        assert entry["arm"] == "fit"
        assert entry["player"] == "fischer"
        assert entry["era"] == "peak"
        assert entry["legal_moves"] > 1
        assert chess.Move.from_uci(entry["human_move"]) in board.legal_moves
        assert entry["fen_sha256"] == sha256(board.fen().encode()).hexdigest()
    assert len(positions) == len(set(e["phase"] for e, _ in positions))


def test_same_source_game_all_models_always_same_position(monkeypatch):
    board = chess.Board()
    human = chess.Move.from_uci("e2e4")
    best = {"move": human, "cp": 45, "mate": None}
    runner = {"move": chess.Move.from_uci("d2d4"), "cp": 30, "mate": None}
    monkeypatch.setattr(quality, "_reference_top2", lambda sf, b: (best, runner))
    move_map = {
        "fischer": "d2d4", "tal_balanced": "g1f3",
        "tal_original": "b1c3", "simple": "e2e3",
    }
    monkeypatch.setattr(quality, "choose", lambda engine, b, depth: (
        chess.Move.from_uci(move_map[engine]), {
            "requested_depth": 16, "reported_depth": 16,
            "reached_requested": True, "reported_nodes": 100,
        }))
    scores = {
        "e2e4": 45, "d2d4": 30, "g1f3": 20,
        "b1c3": -90, "e2e3": 15,
    }
    monkeypatch.setattr(quality, "_score", lambda sf, b, move, depth: {
        "move": move.uci(), "cp": scores[move.uci()], "mate": None,
    })
    result = quality.measure(board, human,
                             {key: key for key in move_map}, object())
    assert result["scores"]["human"]["cp_loss"] == 0
    assert result["scores"]["fischer"]["cp_loss"] == 15
    assert result["scores"]["tal_balanced"]["cp_loss"] == 25
    assert result["scores"]["tal_original"]["over100"] is True
    assert result["joint_numeric"] is True
    assert result["reference"]["best"] == "e2e4"
    assert result["reference"]["inconsistent"] is False
    assert set(result["search_telemetry"]) == set(move_map)


def test_mate_is_never_converted_into_arbitrary_centipawn_loss():
    best = {"move": "e2e4", "cp": None, "mate": 4}
    selected = {"move": "e2e4", "cp": 20, "mate": None}
    metric = quality._policy_metric(best, selected)
    assert metric["cp_loss"] is None
    assert metric["mate_sensitive"] is True
    assert metric["over100"] is None
    assert metric["missed_winning_mate"] is True
    another = {"move": "e2e4", "cp": None, "mate": -3}
    assert quality._policy_metric(
        {"cp": -90, "mate": None}, another)["selected_losing_mate"] is True
    assert quality._numeric_loss({"cp": 30, "mate": None},
                                 {"cp": 45, "mate": None}) == 0


def make_row(name, game, stage, human_loss, candidate_loss, tactical=True):
    roles = {
        "human": human_loss, "fischer": 10,
        "tal_balanced": 10, "tal_original": 10, "simple": 10,
    }
    roles["fischer" if name == "fischer" else "tal_balanced"] = candidate_loss
    return {
        "case": {"player": name, "game": game, "era": "peak"},
        "measured": {
            "phase": stage,
            "tactical_pre_move": tactical, "joint_numeric": True,
            "scores": {role: {"cp_loss": loss, "over100": loss > 100}
                       for role, loss in roles.items()},
        },
    }


def test_human_relative_parity_bootstrap_accounts_for_game_clusters(monkeypatch):
    monkeypatch.setattr(quality, "BOOTSTRAPS", 100)
    records = []
    for name in ("fischer", "tal"):
        for g in range(40):
            for stage in quality.PHASES:
                records.append(make_row(
                    name, f"{name}{g}", stage, 10, 20 if name == "fischer" else 25))
    assert quality._macro(
        [x["measured"] for x in records[:120]], "fischer") == 10
    assert quality._macro(
        [x["measured"] for x in records[120:]], "tal_balanced") == 15
    assert quality._bootstrap(records) == [5, 5]
    assert quality._bootstrap(records, tactical=True) == [5, 5]
    assert quality._bootstrap(records, metric="severe") == [0.0, 0.0]
    other = make_row("tal", "x", "endgame", 10, 25, tactical=False)
    assert quality._macro([other["measured"]], "tal_balanced", tactical=True) is None


def test_input_checks_refuse_phase_and_root_tampering():
    board = chess.Board()
    human = "e2e4"
    meta = {
        "id": "a"*64, "game": "b"*64, "era": "peak",
        "phase": "opening", "player": "tal",
        "human_move": human, "ply": 0,
        "fen_sha256": sha256(board.fen().encode()).hexdigest(),
        "legal_moves": board.legal_moves.count(),
        "arm": "fit",
    }
    mapping = {key: human for key in quality.POLICIES}
    raw = {"e2e4": {"move": "e2e4", "cp": 35, "mate": None}}
    metrics = {key: quality._policy_metric(raw[human], raw[human])
               for key in quality.POLICIES}
    evaluated = {
        "phase": "opening", "tactical_pre_move": False, "pre_move_tags": [],
        "reference": {"best": human, "second": "d2d4",
                      "evals": raw, "inconsistent": False},
        "moves": mapping, "scores": metrics,
        "joint_numeric": True,
        "search_telemetry": {key: {"reached_requested": True}
                             for key in quality.POLICIES if key != "human"},
    }
    doc = {"schema": quality.SCHEMA, "case": meta,
           "manifest_sha256": "c"*64, "measured": evaluated}
    assert quality.validate_measurement(doc, meta, board, "c"*64) == evaluated
    with pytest.raises(ValueError, match="detached"):
        quality.validate_measurement(
            {**doc, "manifest_sha256": "d"*64}, meta, board, "c"*64)
    with pytest.raises(ValueError, match="illegal"):
        quality.validate_measurement(
            {**doc, "measured": {**evaluated,
                                "moves": {**mapping, "tal_balanced": "e2e5"}}},
            meta, board, "c"*64)
    bad = json.loads(json.dumps(doc))
    bad["measured"]["scores"]["tal_balanced"]["cp_loss"] = 200
    with pytest.raises(ValueError, match="Manipulated"):
        quality.validate_measurement(bad, meta, board, "c"*64)


def test_game_root_shards_keep_joint_candidates_together(monkeypatch):
    monkeypatch.setattr(quality, "SHARDS", 4)
    cases = {}
    for player in ("fischer", "tal"):
        for span in ("peak", "other"):
            for g in range(12):
                for stage in ("opening", "middlegame", "endgame"):
                    ident = f"{player}-{span}-{g}-{stage}"
                    cases[ident] = {
                        "id": ident, "player": player, "era": span,
                        "game": f"{player}-{span}-{g}", "phase": stage,
                    }
    all_cases = [x for i in range(4)
                 for x in quality.shard_cases(cases, i)]
    assert len(all_cases) == len(cases)
    assert len(set(x["id"] for x in all_cases)) == len(cases)
    assert Counter(x["game"] for x in all_cases) == Counter({
        f"{p}-{span}-{i}": 3 for p in ("fischer", "tal")
        for span in ("peak", "other") for i in range(12)
    })
    for i in range(4):
        assert len(quality.shard_cases(cases, i)) == 36
    with pytest.raises(ValueError, match="shard"):
        quality.shard_cases(cases, 4)
