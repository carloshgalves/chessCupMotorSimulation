"""Issue #15: duel, multi-pair and statistical duplicate regressions."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chesssim.catalog import find_player
from chesssim.game_diversity import mainline_identity


def _game(*uci: str):
    chess = pytest.importorskip("chess")
    pytest.importorskip("chess.pgn")

    game = chess.pgn.Game()
    board = game.board()
    node = game
    for move_text in uci:
        move = chess.Move.from_uci(move_text)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    return game


def test_play_duel_persists_duplicate_flags_and_seed_trace(tmp_path):
    from chesssim import matches

    white, black = find_player("fischer"), find_player("tal")
    game = _game("e2e4", "e7e5", "g1f3")
    count = 0

    def fake_one(_root, _exe, w, b, _depth, _movetime, _max_plies,
                 round_number, _hashes, **kwargs):
        nonlocal count
        count += 1
        return game, {
            "round": round_number, "white": w.slug, "black": b.slug,
            "result": "*", "status": "INCOMPLETE", "termination": "TEST",
            "plies": 3, "elapsed_seconds": [0, 0], "clock_left": None,
            "opening_moves": {"white": 0, "black": 0}, "opening_trace": [],
            "opening_seed": kwargs["opening_seed"], "opening_mode": "historical-v09-exploratory",
            "color_order": f"{w.slug}|{b.slug}",
        }

    book = {"player": white.slug, "source": {"pgn_sha256": "a" * 64}}
    with patch.object(matches, "validate", return_value=(tmp_path / "engine", {})), \
         patch.object(matches, "file_hash", return_value="b" * 64), \
         patch.object(matches, "_play_one", side_effect=fake_one):
        folder = matches.play_duel(tmp_path, tmp_path / "out", white, black,
                                   depth=5, historical_openings={white.slug: book},
                                   opening_seed=991, use_books=False)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert count == 2
    assert summary["status"] == "INCOMPLETE"
    assert summary["diversity"]["games_total"] == 2
    assert summary["diversity"]["games_distinct"] == 1
    assert summary["diversity"]["games_duplicate"] == 1
    assert summary["games"][1]["first_occurrence"] == "game_1"
    assert summary["games"][0]["opening_seed"] == 991
    assert summary["variation_control"]["historical_opening_seed_effective"] is True
    assert (folder / "jogo_1.pgn").exists()
    assert (folder / "jogo_2.pgn").exists()


def test_play_styles_series_detects_cross_pair_repetition(tmp_path, monkeypatch):
    from chesssim import duel_styles, matches

    game_a = _game("e2e4", "e7e5")
    game_b = _game("d2d4", "d7d5")
    sig_a, n_a = mainline_identity(game_a)
    sig_b, n_b = mainline_identity(game_b)
    count = 0
    seeds = []

    def fake_duel(root, output, white, black, **kwargs):
        nonlocal count
        count += 1
        seeds.append(kwargs["opening_seed"])
        folder = output / f"pair_{count}"
        folder.mkdir()
        games = [{
            "round": i, "mainline_sha256": digest, "plies": plies,
            "opening_seed": None, "opening_mode": "disabled",
            "color_order": "white|black" if i == 1 else "black|white",
            "status": "COMPLETED", "result": "1/2-1/2",
        } for i, (digest, plies) in enumerate(((sig_a, n_a), (sig_b, n_b)), 1)]
        (folder / "summary.json").write_text(
            json.dumps({"games": games, "status": "COMPLETED"}), encoding="utf-8"
        )
        (folder / "duelo.pgn").write_text("synthetic", encoding="utf-8")
        return folder

    monkeypatch.setattr(matches, "play_duel", fake_duel)
    report = duel_styles.play_styles(
        tmp_path, tmp_path, white_name="fischer", black_name="tal",
        rounds=3, replay=False, seed=2029
    )
    assert seeds == [2029, 2030, 2031]
    assert report["status"] == "COMPLETED"
    assert report["diversity"]["games_total"] == 6
    assert report["diversity"]["games_distinct"] == 2
    assert report["diversity"]["games_duplicate"] == 4
    assert report["diversity"]["variation_sources"]["historical_opening_seeds"] == []
    assert report["pairs"][1]["game_evidence"][0]["first_occurrence"] == "pair_1/game_1"
    assert report["pairs"][2]["opening_seed_effective"] is False
    assert Path(report["series_root"]).is_dir()
    saved = json.loads(Path(report["series_root"]).joinpath("series-report.json").read_text())
    assert saved["diversity"]["games_distinct"] == 2


def test_style_audit_skips_duplicate_games_but_counts_raw_games(tmp_path):
    from chesssim.style_postgame import fingerprint

    pytest.importorskip("chess.pgn")

    game = _game("e2e4", "e7e5", "g1f3", "b8c6")
    game.headers["White"] = "Historical A"
    game.headers["Black"] = "Historical B"
    game.headers["OpeningSeed"] = "2029"
    altered = _game("e2e4", "e7e5", "g1f3", "b8c6")
    altered.headers["White"] = "Other alias"
    altered.headers["Black"] = "Other opponent"
    file = tmp_path / "games.pgn"
    file.write_text(str(game) + "\n\n" + str(altered) + "\n\n", encoding="utf-8")
    report = fingerprint(file, tmp_path / "style.json", opening_plies=2)
    assert report["games"] == 2
    assert report["distinct_games"] == 1
    assert report["duplicate_games"] == 1
    assert report["players"]["Historical A"]["games"] == 1
    assert "Other alias" not in report["players"]
    assert report["most_common_first_eight"][0]["games"] == 1
    assert report["diversity"]["variation_sources"]["historical_opening_seeds"] == [2029]


def test_quality_audit_does_not_merge_distinct_starting_positions(tmp_path):
    pytest.importorskip("chess")
    from chesssim.quality_audit import _load_positions

    first = '[Event "A"]\n[White "W"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 *\n\n'
    changed_header = first.replace('Event "A"', 'Event "B"')
    different_castling = (
        '[Event "C"]\n[SetUp "1"]\n'
        '[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1"]\n'
        '[White "W"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 *\n\n'
    )
    pgn = tmp_path / "sample.pgn"
    pgn.write_text(first + changed_header + different_castling, encoding="utf-8")
    rows, games = _load_positions(pgn, min_fullmove=1, dedupe_games=True)
    assert len(games) == 3
    assert [g["duplicate_mainline"] for g in games] == [False, True, False]
    assert [g["eligible_for_independent_evidence"] for g in games] == [True, False, True]
    assert {r["game_index"] for r in rows} == {0, 2}


def test_real_opening_policy_records_actual_choices_and_seed(tmp_path):
    """Integration without engine binaries: two real chess book selections."""
    chess = pytest.importorskip("chess")
    from chesssim import matches
    from chesssim.opening_books import SCHEMA, position_token

    a, b = find_player("fischer"), find_player("tal")
    initial = chess.Board()
    white_token = position_token(initial)
    initial.push_uci("e2e4")
    black_token = position_token(initial)

    def book(slug, color, token, move):
        return {
            "schema": SCHEMA, "player": slug,
            "policy": {"max_plies": 2},
            "positions": {
                "white": {token: {move: 1}} if color == "white" else {},
                "black": {token: {move: 1}} if color == "black" else {},
            },
            "source": {"pgn_sha256": ("a" if color == "white" else "b") * 64},
        }

    class FakeEngine:
        def __init__(self):
            self.closed = False

        def play(self, *_args, **_kwargs):
            raise AssertionError("Book moves should not invoke UCI engine.play")

        def quit(self):
            self.closed = True

    engines = [FakeEngine(), FakeEngine()]
    with patch.object(matches, "_open_engine", side_effect=engines):
        game, meta = matches._play_one(
            tmp_path, tmp_path / "engine", a, b, 3, None, 2, 2,
            {"exe": "1" * 64, a.slug: "2" * 64, b.slug: "3" * 64},
            use_books=False,
            historical_openings={
                a.slug: book(a.slug, "white", white_token, "e2e4"),
                b.slug: book(b.slug, "black", black_token, "e7e5"),
            },
            opening_seed=8181,
        )
    assert all(engine.closed for engine in engines)
    assert game.headers["OpeningSeed"] == "8181"
    assert game.headers["OpeningMinCount"] == "1"
    assert meta["opening_seed"] == 8181
    assert meta["opening_moves"] == {"white": 1, "black": 1}
    assert [r["move"] for r in meta["opening_trace"]] == ["e2e4", "e7e5"]
    assert [r["book_source_sha256"] for r in meta["opening_trace"]] == ["a" * 64, "b" * 64]
    assert [r["round"] for r in meta["opening_trace"]] == [2, 2]


def test_quality_diagnostic_keeps_rows_but_excludes_duplicate_from_summary(
    tmp_path, monkeypatch,
):
    """The opt-in diagnostic must never silently inflate independent estimates."""
    pytest.importorskip("chess.engine")
    import chess.engine
    from chesssim import quality_audit

    game = _game("e2e4", "e7e5", "g1f3", "b8c6")
    game.headers["White"] = "A"
    game.headers["Black"] = "B"
    sample = tmp_path / "duplicate.pgn"
    sample.write_text(str(game) + "\n\n" + str(game) + "\n\n", encoding="utf-8")
    engine_path = tmp_path / "fake-stockfish"
    engine_path.write_bytes(b"test")

    class FakeJudge:
        options = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", lambda *_args, **_kw: FakeJudge())
    monkeypatch.setattr(
        quality_audit, "_evaluate_choice",
        lambda *_args, **_kw: {"cp_loss": 25, "raw_gap_cp": 25, "mate_affected": False},
    )

    report = quality_audit.analyze_games_v91(
        sample, engine_path, tmp_path / "diagnostic.json",
        depth=3, max_positions=5, min_fullmove=1,
        dedupe_games=False, sample_mode="balanced",
    )
    assert report["diversity"]["games_total"] == 2
    assert report["diversity"]["games_distinct"] == 1
    assert report["inputs"]["sampled_positions"] == 5
    assert report["independent_decisions_sampled"] == 4
    assert report["duplicate_decisions_excluded_from_summary"] == 1
    assert sum(row["decisions_analyzed"] for row in report["summary"].values()) == 4
    without_duplicates = quality_audit.analyze_games_v91(
        sample, engine_path, tmp_path / "primary.json",
        depth=3, max_positions=5, min_fullmove=1,
        dedupe_games=True, sample_mode="balanced",
    )
    assert report["summary"] == without_duplicates["summary"]
    assert report["sampling"]["independent_positions_selected"] == 4
    assert report["sampling"]["diagnostic_duplicate_positions_selected"] == 1


def test_series_failure_retains_atomic_checkpoint(tmp_path, monkeypatch):
    from chesssim import duel_styles, matches

    def crash(*_args, **_kwargs):
        raise RuntimeError("synthetic engine failure")

    monkeypatch.setattr(matches, "play_duel", crash)
    with pytest.raises(RuntimeError, match="synthetic engine failure"):
        duel_styles.play_styles(
            tmp_path, tmp_path, white_name="fischer", black_name="tal",
            rounds=2, replay=False,
        )
    roots = list(tmp_path.glob("style-duels-*"))
    assert len(roots) == 1
    doc = json.loads((roots[0] / "series-report.json").read_text(encoding="utf-8"))
    assert doc["status"] == "FAILED"
    assert doc["pairs_completed"] == 0
    assert "synthetic engine failure" in doc["error"]
    assert doc["planned_pairs"] == 2
