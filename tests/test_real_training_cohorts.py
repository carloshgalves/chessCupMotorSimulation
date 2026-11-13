"""Regression tests for actual-player/year cohort construction.

Synthetic fixtures test failure modes only; the separate GitHub Actions job
downloads four pinned REAL historical PGNs and runs the complete training gate.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import chess
import chess.pgn
import pytest

from chesssim.real_training_cohorts import (
    SCHEMA, _career_band, _exact_age, _player_configuration,
    build_real_training_cohorts, source_file_for_slug,
)


def _game(focal: str, year: int, variant: int, *, color="white"):
    game = chess.pgn.Game()
    name = {"fischer": "Robert Fischer", "tal": "Mikhail Tal"}[focal]
    game.headers["Event"] = "Real-test fixture ONLY"
    game.headers["White"] = name if color == "white" else "Opponent"
    game.headers["Black"] = name if color == "black" else "Opponent"
    game.headers["Date"] = f"{year}.02.03"
    game.headers["Result"] = "1/2-1/2"
    game.headers["ECO"] = "B12"
    node = game
    board = game.board()
    for ply in range(26):
        legal = sorted(board.legal_moves, key=lambda mv: mv.uci())
        move = legal[(variant * 17 + year * 13 + ply * 11) % len(legal)]
        node = node.add_variation(move)
        board.push(move)
    return game


def _write(folder: Path, slug: str, games: list[chess.pgn.Game]):
    path = folder / (source_file_for_slug(slug) + ".pgn")
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    path.write_text("\n\n".join(g.accept(exporter) for g in games) + "\n",
                    encoding="utf-8")


def _prepare(tmp_path: Path):
    source = tmp_path / "real-input"
    source.mkdir()
    for slug in ("fischer", "tal"):
        games = [_game(slug, year, year * 7 + k,
                       color="white" if k == 0 else "black")
                 for year in range(1990, 2002) for k in range(2)]
        _write(source, slug, games)
    return source


def test_real_cohorts_train_only_player_year_and_deterministic(tmp_path):
    source = _prepare(tmp_path)
    options = {"players": ["fischer", "tal"], "archive_dir": source, "min_ply": 17}
    first = build_real_training_cohorts(tmp_path / "first", **options)
    second = build_real_training_cohorts(tmp_path / "second", **options)
    assert first["schema"] == SCHEMA
    assert first["status"] == "REAL_PGN_TRAIN_ONLY_DATASET_NOT_MODEL_VALIDATED"
    assert first["accepted_player_game_entries"] == 48
    assert first["globally_distinct_move_trajectories"] <= 48
    assert first["training_examples_exported"] > 0
    assert first["training_files"]
    assert first["training_examples_exported"] == second["training_examples_exported"]
    assert first["players"] == second["players"]
    assert list((tmp_path / "first").rglob("*.pgn")) == []
    assert list((tmp_path / "first").rglob("*.sqlite3")) == []
    observed = 0
    for rel in first["training_files"]:
        path = tmp_path / "first" / rel
        slug = path.parent.name
        year = int(path.stem)
        assert slug in first["players"]
        assert year in first["players"][slug]["partition_by_whole_year"]["train"]["years"]
        text = path.read_text(encoding="utf-8")
        assert text == (tmp_path / "second" / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            row = json.loads(line)
            assert row["year"] == year and row["player"] == slug
            assert row["source_sha256"] == first["sources"][slug]["pgn_sha256"]
            assert chess.Move.from_uci(row["move_uci"]) in chess.Board(row["fen"]).legal_moves
            assert row["eco"] == "B12"
            assert row["phase_proxy"] in ("middle_proxy", "late_proxy")
            assert row["age_at_game"] is None
            assert row["result_for_player"] == "draw"
            assert row["policy_input"]["fen"] == row["fen"]
            assert row["policy_target"]["move_uci"] == row["move_uci"]
            assert "result_for_player" not in row["policy_input"]
            assert "eco" not in row["policy_input"]
            assert "opponent_elo" not in row["policy_input"]
            assert set(row["policy_target"]) == {"move_uci"}
            observed += 1
    assert observed == first["training_examples_exported"]
    assert first["holdout_moves_exported"] is False
    assert first["validation_moves_exported"] is False
    assert json.loads((tmp_path / "first" / "manifest.json").read_text()) == first


def test_cross_arm_duplicate_cannot_enter_training(tmp_path):
    source = _prepare(tmp_path)
    fischer = [_game("fischer", year, year * 7 + k)
               for year in range(1990, 2002) for k in range(2)]
    tal = [_game("tal", year, year * 7 + k)
           for year in range(1990, 2002) for k in range(2)]
    shared = _game("fischer", 1990, 891)
    shared.headers["Black"] = "Mikhail Tal"
    fischer.append(shared)
    duplicate = deepcopy(shared)
    duplicate.headers["Date"] = "2001.01.01"
    tal.append(duplicate)
    _write(source, "fischer", fischer)
    _write(source, "tal", tal)
    result = build_real_training_cohorts(
        tmp_path / "out", players=["fischer", "tal"],
        archive_dir=source, min_ply=17)
    assert result["cross_arm_conflicting_trajectories"] >= 1
    for file in (tmp_path / "out" / "train").rglob("*.jsonl"):
        assert all(json.loads(line)["game_sha256"] !=
                   chesssim_digest(shared) for line in file.read_text().splitlines())


def chesssim_digest(game):
    from chesssim.game_diversity import mainline_identity
    return mainline_identity(game)[0]


def test_year_only_kept_without_fabricated_age_and_unknown_quarantined(tmp_path):
    folder = tmp_path / "raw"
    folder.mkdir()
    games = [_game("fischer", year, year + 23) for year in range(1990, 2003)]
    games[0].headers["Date"] = "1990.??.??"
    games[1].headers["Date"] = "????.??.??"
    _write(folder, "fischer", games)
    result = build_real_training_cohorts(
        tmp_path / "out", players=["fischer"], archive_dir=folder,
        min_ply=1, birthdates={"fischer": "1943-03-09"})
    assert result["players"]["fischer"]["undated_quarantined"] == 1
    assert result["sources"]["fischer"]["counters"]["year_only"] == 1
    rows = [json.loads(line) for f in (tmp_path / "out" / "train").rglob("*.jsonl")
            for line in f.read_text().splitlines()]
    assert rows and any(r["year"] == 1990 and r["age_at_game"] is None for r in rows)


def test_reject_incorrect_identity_symlink_and_existing_output(tmp_path):
    source = _prepare(tmp_path)
    kwargs = {"players": ["fischer"], "archive_dir": source}
    build_real_training_cohorts(tmp_path / "out", **kwargs)
    with pytest.raises(ValueError, match="NEW directory"):
        build_real_training_cohorts(tmp_path / "out", **kwargs)
    evil = tmp_path / "linked"
    evil.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="Symbolic"):
        build_real_training_cohorts(tmp_path / "next", players=["fischer"],
                                    archive_dir=evil)
    assert not (tmp_path / "next").exists()


def test_allow_missing_only_for_missing_source(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()
    _write(source, "tal", [_game("tal", y, y) for y in range(1990, 2000)])
    with pytest.raises(ValueError, match="missing"):
        build_real_training_cohorts(
            tmp_path / "fail", players=["fischer", "tal"], archive_dir=source)
    assert not (tmp_path / "fail").exists()
    result = build_real_training_cohorts(
        tmp_path / "ok", players=["fischer", "tal"], archive_dir=source,
        allow_missing=True)
    assert result["missing_sources"]["fischer"] == "LOCAL_SOURCE_MISSING"
    assert set(result["players"]) == {"tal"}


def test_validates_player_selection_and_source_names():
    with pytest.raises(ValueError, match="distinct"):
        _player_configuration(["tal", "tal"], None, None)
    with pytest.raises(ValueError, match="distinct"):
        _player_configuration(["nonexistent"], None, None)
    with pytest.raises(ValueError, match="filename"):
        _player_configuration(["tal"], None, {"tal": "../Tal"})
    with pytest.raises(ValueError, match="birthdate"):
        _player_configuration(["tal"], {"tal": "1960-99-01"}, None)
    with pytest.raises(ValueError, match="only selected"):
        _player_configuration(["tal"], {"fischer": "1943-03-09"}, None)
    assert source_file_for_slug("vachier-lagrave") == "VachierLagrave"
    assert source_file_for_slug("ding-liren") == "Ding"
    assert source_file_for_slug("judit-polgar") == "PolgarJ"
    assert _exact_age("1943-03-09", "1972.07.11") == 29
    assert _exact_age("1943-03-09", "1972.??.??") is None
    assert _career_band(2000, [2000, 2001]) == "insufficient_year_coverage"



def test_career_stratified_years_cover_observed_career_without_year_leakage(tmp_path):
    source = _prepare(tmp_path)
    options = {"players": ["fischer", "tal"], "archive_dir": source, "min_ply": 17}
    chronological = build_real_training_cohorts(tmp_path / "chrono", **options)
    balanced = build_real_training_cohorts(
        tmp_path / "career", split_policy="career-stratified", **options)
    assert balanced["partition_policy"] == "career-stratified"
    assert chronological["partition_policy"] == "chronological"
    for player, details in balanced["players"].items():
        arms = details["partition_by_whole_year"]
        years_by_arm = {key: set(value["years"]) for key, value in arms.items()}
        assert all(years_by_arm.values())
        assert not (years_by_arm["train"] & years_by_arm["validation"])
        assert not (years_by_arm["train"] & years_by_arm["holdout"])
        assert not (years_by_arm["validation"] & years_by_arm["holdout"])
        assert max(years_by_arm["train"]) > max(
            chronological["players"][player]["partition_by_whole_year"]["train"]["years"])
        assert len(years_by_arm["train"]) >= 3
    assert balanced["training_examples_exported"] > 0
    assert balanced["holdout_moves_exported"] is False


def test_sparsely_observed_career_band_is_quarantined_not_falsely_validated():
    from chesssim.real_training_cohorts import _assign
    rows = [
        {"date": "1900", "mainline_sha256": "a" * 64},
        {"date": "1901", "mainline_sha256": "b" * 64},
    ]
    partitions, sparse = _assign(rows, "career-stratified")
    assert all(partitions[arm]["games"] == 0 for arm in
               ("train", "validation", "holdout"))
    assert sparse == ["insufficient_year_coverage"]
    assert all(row["arm"] == "quarantine" for row in rows)



def test_conservative_age_bounds_and_unreported_confounders():
    from chesssim.real_training_cohorts import (
        _age_bounds, _header_elo, _bounded_header,
    )
    assert _age_bounds("1943-03-09", "1972.??.??") == [28, 29]
    assert _age_bounds("1943-03-09", "1972.02.??") == [28, 28]
    assert _age_bounds("1943-03-09", "1972.04.??") == [29, 29]
    assert _age_bounds("1943-03-09", "1972.03.??") == [28, 29]
    assert _age_bounds("1943-03-09", "1972.03.09") == [29, 29]
    assert _age_bounds("1943-03-09", "????.??.??") is None
    assert _age_bounds(None, "1972.03.09") is None
    assert _header_elo("2780") == 2780
    assert _header_elo("?") is None
    assert _bounded_header("?") is None
    assert _bounded_header("Classical event") == "Classical event"
