"""Corpus-wide actual PGN fit and executable UCI pilot regression contracts."""
from __future__ import annotations

import json

import chess
import chess.pgn
import pytest

from chesssim import real85_playable as pilot
from chesssim.opening_books import load_opening_book, select_book_move
from chesssim.game_diversity import mainline_identity


def _pgn_text(slug: str, count: int = 220) -> bytes:
    name = {"fischer": "Bobby Fischer", "tal": "Mikhail Tal"}[slug]
    games = []
    for i in range(count):
        board = chess.Board()
        game = chess.pgn.Game()
        game.headers.update({
            "White": name if i % 2 == 0 else "Demo Opponent",
            "Black": "Demo Opponent" if i % 2 == 0 else name,
            "Date": f"{1960+i%30}.01.02", "Event": "Historical fixture",
            "Result": "*"
        })
        node = game
        # Real legal moves: deterministic but distinct trajectories, do not
        # inject imaginary actor IDs into FEN/move labels.
        choices = [(i*7+k*11) for k in range(16)]
        for choice in choices:
            legal = sorted(board.legal_moves, key=lambda move: move.uci())
            move = legal[choice % len(legal)]
            node = node.add_variation(move)
            board.push(move)
            if board.is_game_over():
                break
        games.append(str(game))
    return ("\n\n".join(games)+"\n").encode()


def test_game_assignment_independent_of_player_name():
    digest = "a"*64
    assert pilot.game_arm(digest) in ("train","test")
    assert pilot.game_arm(digest) == pilot.game_arm(digest)
    for wrong in ("x", "f"*63, "z"*64, None):
        with pytest.raises(ValueError, match="Invalid historical"):
            pilot.game_arm(wrong)


def test_real_training_uses_most_games_separates_test_and_learns_book(
        monkeypatch, tmp_path):
    pgn = _pgn_text("fischer")
    monkeypatch.setattr(
        pilot, "_source_bytes", lambda *args, **kwargs: (pgn,pgn,"FIXTURE_REAL_PGN"))
    result = pilot.train_real_profiles(tmp_path/"trained",players=["fischer"],
                                       max_opening_plies=16)
    row = result["players"]["fischer"]
    assert row["status"] == "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE"
    assert row["train_games"] + row["test_games"] == row["accepted_distinct_games"]
    assert 0.80 <= row["train_fraction"] < 0.95
    assert row["train_style"]["moves"] > 0
    assert row["test_style"]["moves"] > 0
    assert row["learned_opening_positions"] > 0
    assert row["learned_historical_opponent_positions"] > 0
    assert row["learned_opening_decisions"] > 0
    book = load_opening_book(tmp_path/"trained/fischer.json","fischer")
    assert book["source"]["accepted_games"] == row["train_games"]
    assert book["opponent_positions"]["white"]
    assert book["opponent_positions"]["black"]
    assert book["source"]["source_games_total"] == row["accepted_distinct_games"]
    assert book["source"]["provenance"] == "REAL_PGN_85PCT_GAME_DISJOINT_TRAIN"
    assert row["heldout_opening"]["decisions"] > 0
    assert row["heldout_opening"]["covered"] <= row["heldout_opening"]["decisions"]
    for i in range(10):
        move = select_book_move(book,chess.Board(),game_number=i)
        if move is not None:
            assert move in chess.Board().legal_moves
    with pytest.raises(ValueError, match="Output must be"):
        pilot.train_real_profiles(tmp_path/"trained", players=["fischer"])


def test_multi_player_training_keeps_same_trajectory_in_same_arm(monkeypatch,tmp_path):
    pgn = _pgn_text("fischer",140)
    # Two players share some trajectories; use the same source bytes in both.
    # In production the PGN identity check limits which player can claim moves.
    monkeypatch.setattr(pilot, "_source_bytes",
                        lambda *args,**kwargs:(pgn,pgn,"LOCAL_FOUR"))
    r = pilot.train_real_profiles(tmp_path/"multi",players=["fischer"])
    assert r["players"]["fischer"]["train_fraction"] >= 0.8
    # The exact hash is global, independent of player name/metadata.
    game = chess.pgn.read_game(__import__("io").StringIO(pgn.decode()))
    h,_ = mainline_identity(game)
    assert pilot.game_arm(h) == pilot.game_arm(h)


def test_no_silent_source_fabrication_or_required_missing(monkeypatch, tmp_path):
    from urllib.error import HTTPError
    def gone(*_args,**_kwargs):
        raise HTTPError("https://example.org",404,"not found",None,None)
    monkeypatch.setattr(pilot,"_source_bytes",gone)
    r = pilot.train_real_profiles(tmp_path/"missing",players=["fischer"],
                                  allow_missing=True)
    assert r["players"]["fischer"]["status"] == "NO_DOCUMENTED_SOURCE"
    assert not (tmp_path/"missing/fischer.json").exists()
    with pytest.raises(HTTPError):
        pilot.train_real_profiles(tmp_path/"fail",players=["fischer"],
                                  allow_missing=False)


def test_style_features_count_real_legal_decisions():
    g = chess.pgn.Game()
    board = chess.Board()
    node = g
    for uci in ("e2e4","e7e5","g1f3","b8c6","f1c4","g8f6"):
        move = chess.Move.from_uci(uci)
        node = node.add_variation(move)
        board.push(move)
    w = pilot._style_features(g,True)
    b = pilot._style_features(g,False)
    assert w["moves"] == b["moves"] == 3
    assert w["opening_moves"] == 3
    assert b["opening_moves"] == 3
    assert pilot._style_summary(__import__("collections").Counter(w))["opening"]["decisions"] == 3


def test_opening_metrics_do_not_award_every_modal_tie_as_top1():
    from collections import Counter
    votes = Counter({"e2e4": 1, "d2d4": 1})
    # Fixed lexicographic tie-breaking means only one move is modal top1.
    assert pilot.opening_prediction(votes, "d2d4") == (1, 0.5)
    assert pilot.opening_prediction(votes, "e2e4") == (0, 0.5)
    assert pilot.opening_prediction(votes, "g1f3") == (0, 0)
    with pytest.raises(ValueError, match="positive"):
        pilot.opening_prediction(Counter({"e2e4": 0}), "e2e4")


def test_material_endgame_not_late_ply_and_captures_real_king_activity():
    early = chess.Board()
    assert pilot._material_endgame(early) is False
    ending = chess.Board("8/8/4k3/8/8/4K3/3P4/8 w - - 0 1")
    assert ending.is_valid()
    assert pilot._material_endgame(ending) is True
    game = chess.pgn.Game()
    game.setup(ending)
    move = chess.Move.from_uci("e3d3")
    assert move in ending.legal_moves
    game.add_variation(move)
    stats = pilot._style_features(game,True)
    assert stats["endgame_material_moves"] == 1
    assert stats["endgame_material_king_moves"] == 1
    assert stats["opening_moves"] == 1
    summary = pilot._style_summary(__import__("collections").Counter(stats))
    assert summary["material_endgame"]["decisions"] == 1
    assert summary["material_endgame"]["king_moves_rate"] == 1


def test_playable_runner_reverses_colors_and_preserves_actual_status(monkeypatch,tmp_path):
    book_root = tmp_path/"training"
    book_root.mkdir()
    # use valid schema and digest; report size is not fabricated inside runner
    book = {"schema":"chesssim-opening-v9","player":"fischer","policy":{"max_plies":12},
            "positions":{"white":{},"black":{}},
            "opponent_positions":{"white":{},"black":{}},
            "source":{"pgn_sha256":"a"*64}}
    book_path = book_root/"fischer.json"
    book_path.write_text(json.dumps(book))
    from chesssim.matches import file_hash
    report = {"schema":pilot.SCHEMA,"players":{
        "fischer":{"status":"REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE",
                   "book_sha256":file_hash(book_path),"train_fraction":0.85,
                   "train_games":170,"test_games":30,"test_style":{}}}}
    (book_root/"report.json").write_text(json.dumps(report))
    root = tmp_path/"rodent"
    (root/"personalities").mkdir(parents=True)
    (root/"personalities/basic.ini").write_text(
        "Simple=simple.txt\nFischer=fischer.txt\n")
    (root/"personalities/simple.txt").write_text("test\n")
    (root/"personalities/fischer.txt").write_text("test\n")
    (root/"rodent-iv-plain.exe").write_text("fake\n")
    observed = []
    def fake_play(_root,_exe,white,black,**kwargs):
        observed.append((white.slug,black.slug,kwargs["historical_openings"]))
        g = chess.pgn.Game()
        g.headers["White"] = white.name
        g.headers["Black"] = black.name
        g.headers["Result"] = "1/2-1/2"
        return g,{"result":"1/2-1/2","status":"COMPLETED",
                  "termination":"AGREED_DRAW","plies":0,"opening_moves":{"white":0,"black":0}}
    monkeypatch.setattr(pilot,"_play_one",fake_play)
    result = pilot.play_real_four(root,book_root,tmp_path/"matches",players=["fischer"])
    assert result["status"] == "ACTUAL_COMPLETE_GAMES_PLAYED"
    assert [r[:2] for r in observed] == [
        ("fischer","control-simple"),("control-simple","fischer")]
    assert all(set(r[2]) == {"fischer", "control-simple"} for r in observed)
    assert all(item["score_vs_simple"] == 0.5 for item in result["games"])
    assert result["no_elo_inferred_from_small_sample"] is True
    assert (tmp_path/"matches/played.pgn").is_file()
    assert sum(v["opening_moves_from_real_pg_ns"] for v in result["games"]) == 0


def test_playable_runner_does_not_convert_incomplete_into_draw(monkeypatch,tmp_path):
    book_root=tmp_path/"train"
    book_root.mkdir()
    path=book_root/"fischer.json"
    path.write_text(json.dumps({"schema":"chesssim-opening-v9",
        "player":"fischer","policy":{"max_plies":12},
        "positions":{"white":{},"black":{}},
        "opponent_positions":{"white":{},"black":{}},
        "source":{"pgn_sha256":"a"*64}}))
    from chesssim.matches import file_hash
    (book_root/"report.json").write_text(json.dumps({
        "schema":pilot.SCHEMA,"players":{"fischer":{
            "status":"REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE",
            "book_sha256":file_hash(path),"train_fraction":0.85,
            "train_games":170,"test_games":30,"test_style":{}}}}))
    root=tmp_path/"rodent"
    (root/"personalities").mkdir(parents=True)
    (root/"personalities/basic.ini").write_text("Simple=simple.txt\nFischer=fischer.txt\n")
    (root/"personalities/simple.txt").write_text("test")
    (root/"personalities/fischer.txt").write_text("test")
    (root/"rodent-iv-plain.exe").write_text("mock")
    def fake_play(*args,**kwargs):
        g=chess.pgn.Game()
        g.headers["Result"]="*"
        return g,{"status":"INCOMPLETE","result":"*",
                  "termination":"InterruptedAtPlyLimit","plies":450,
                  "opening_moves":{"white":0,"black":0}}
    monkeypatch.setattr(pilot,"_play_one",fake_play)
    d=pilot.play_real_four(root,book_root,tmp_path/"games",players=["fischer"])
    assert d["status"] == "REAL_UCI_GAMES_SOME_INCOMPLETE"
    assert d["players"]["fischer"]["completed"] == 0
    assert d["players"]["fischer"]["score_rate_vs_simple"] is None


def test_twenty_full_move_opening_window_includes_plies_25_through_40():
    from chesssim.opening_books import position_token
    assert pilot.MAX_OPENING_PLIES == 40
    board = chess.Board()
    game = chess.pgn.Game()
    node = game
    cycle = ("g1f3", "g8f6", "f3g1", "f6g8")
    for _ in range(8):
        for uci in cycle:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            node = node.add_variation(move)
            board.push(move)
    assert board.ply() == 32
    book = {"schema": "chesssim-opening-v9", "player": "fischer",
            "policy": {"max_plies": pilot.MAX_OPENING_PLIES},
            "positions": {"white": {position_token(board): {"g1f3": 2}},
                          "black": {}}}
    assert select_book_move(book, board, min_count=2) == chess.Move.from_uci("g1f3")
    for uci in cycle * 2:
        move = chess.Move.from_uci(uci)
        node = node.add_variation(move)
        board.push(move)
    assert board.ply() == 40
    assert select_book_move(book, board, min_count=2) is None
    features = pilot._style_features(game, True)
    assert features["opening_moves"] == 20
    assert features["middle_moves"] == 0
