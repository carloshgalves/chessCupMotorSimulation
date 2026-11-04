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
