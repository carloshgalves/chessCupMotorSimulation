"""Contract tests for the large real-PGN cohort audit.

Small synthetic archives exercise failure paths; a separate required
GitHub Actions job downloads and parses >6,500 *real* historical games.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import os
import re
from zipfile import ZIP_DEFLATED, ZipFile

import chess
import chess.pgn
import pytest

from chesssim import historical_real_corpus as real


NAMES = {
    "Fischer": "Robert Fischer",
    "Tal": "Mikhail Tal",
    "Karpov": "Anatoly Karpov",
    "Kasparov": "Garry Kasparov",
}


def _game(name: str, year: int, index: int, *, side: bool = chess.WHITE):
    game = chess.pgn.Game()
    game.headers["Event"] = "Synthetic fixture only"
    game.headers["White"] = NAMES[name] if side else "Opponent"
    game.headers["Black"] = "Opponent" if side else NAMES[name]
    game.headers["Date"] = f"{year}.01.15"
    game.headers["Result"] = "*"
    board, node = game.board(), game
    for ply in range(30):
        moves = sorted(board.legal_moves, key=lambda m: m.uci())
        move = moves[(index * 7 + year * 3 + ply) % len(moves)]
        node = node.add_variation(move)
        board.push(move)
    return game


def _zip(name: str, games: list[chess.pgn.Game], *, member: str | None = None):
    pgn = "".join(
        game.accept(chess.pgn.StringExporter(
            headers=True, variations=False, comments=False)).rstrip() + "\n\n"
        for game in games)
    raw = BytesIO()
    with ZipFile(raw, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(member or name + ".pgn", pgn.encode())
    return raw.getvalue()


def _cohorts():
    return {
        name: _zip(name, [
            _game(name, 1990 + i, j, side=(j % 2 == 0))
            for i in range(5) for j in range(2)
        ])
        for name in sorted(NAMES)
    }


def _lower_limits(monkeypatch):
    monkeypatch.setattr(real, "MIN_TOTAL_GAMES", 32)
    for config in real.PLAYER_FILES.values():
        monkeypatch.setitem(config, "min_usable", 7)


def test_valid_four_player_cohort_audit_is_deterministic_and_private(monkeypatch):
    _lower_limits(monkeypatch)
    archives = _cohorts()
    result = real.audit_historical_archives(archives, min_ply=1)
    assert result["schema"] == real.SCHEMA
    assert result["status"] == (
        "REAL_HISTORICAL_PUBLIC_PGN_EXPLORATORY_NOT_STYLE_VALIDATED")
    assert result["total_unique_cohort_games"] >= 32
    assert result["total_training_games"] >= 20
    assert set(result["sources"]) == set(NAMES)
    assert len(result["shared_fen_pairs"]) == 6
    assert result == real.audit_historical_archives(dict(reversed(list(archives.items()))),
                                                     min_ply=1)
    serialized = json.dumps(result)
    for name in NAMES:
        assert NAMES[name] not in serialized
    # A full SHA-256 can randomly contain four hex digits spelling "e2e4":
    # exclude complete hashes from token-level leaked-move checks.
    def strings(obj):
        if isinstance(obj, dict):
            for value in obj.values():
                yield from strings(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from strings(value)
        elif isinstance(obj, str):
            yield obj
    for token in strings(result):
        if re.fullmatch(r"[0-9a-f]{64}", token):
            continue
        assert "e2e4" not in token
        assert "rnbqkbnr/pppp" not in token
    for section in result["sources"].values():
        assert section["partitions"]["train"]["games"] > 0
        assert section["partitions"]["validation"]["games"] > 0
        assert section["partitions"]["holdout"]["games"] > 0
        assert section["training_decision_observations"] > 0
        assert "_train_entries" not in section
        assert "_all_digests" not in section


def test_requires_substantial_real_total_by_default():
    with pytest.raises(ValueError, match="real|minimum|threshold|6500|below"):
        real.audit_historical_archives(_cohorts(), min_ply=17)


def test_source_zip_member_filename_and_limit_fail_closed(monkeypatch):
    monkeypatch.setitem(real.PLAYER_FILES["Fischer"], "min_usable", 1)
    examples = [
        b"not a ZIP",
        _zip("Fischer", [_game("Fischer", 1990, 1)], member="../Fischer.pgn"),
        _zip("Fischer", [_game("Fischer", 1990, 1)], member="Other.pgn"),
    ]
    for item in examples:
        with pytest.raises(ValueError):
            real.analyze_cohort("Fischer", item)
    with pytest.raises(ValueError):
        real._extract_pgn("Fischer", b"x" * (real.MAX_ARCHIVE_BYTES + 1))


def test_no_implicit_remote_access_and_reject_unknown_cohort(tmp_path):
    with pytest.raises(ValueError):
        real._read_archive("Fischer", archive_dir=None, fetch=False)
    with pytest.raises(ValueError):
        real.archive_url("Not a player")
    with pytest.raises(ValueError):
        real._read_archive("Unknown", archive_dir=tmp_path, fetch=True)


def test_local_archive_runner_keeps_source_files_and_refuses_overwrite(
        tmp_path, monkeypatch):
    _lower_limits(monkeypatch)
    for name, payload in _cohorts().items():
        (tmp_path / f"{name}.zip").write_bytes(payload)
    before = {name: (tmp_path / (name + ".zip")).read_bytes() for name in NAMES}
    out = tmp_path / "audit.json"
    report = real.run_real_historical_audit(
        out, archive_dir=tmp_path, min_ply=1)
    assert json.loads(out.read_text()) == report
    assert all((tmp_path / f"{name}.zip").read_bytes() == before[name]
               for name in NAMES)
    with pytest.raises(FileExistsError):
        real.run_real_historical_audit(out, archive_dir=tmp_path)


def test_local_symlink_archive_is_rejected(tmp_path):
    target = tmp_path / "Fischer.zip"
    target.write_bytes(_zip("Fischer", [_game("Fischer", 1990, 1)]))
    link_dir = tmp_path / "linked"
    link_dir.mkdir()
    (link_dir / "Fischer.zip").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        real._read_archive("Fischer", archive_dir=link_dir, fetch=False)


def test_duplicate_and_partial_date_are_counted_not_inflated(monkeypatch):
    monkeypatch.setitem(real.PLAYER_FILES["Tal"], "min_usable", 1)
    a = _game("Tal", 1990, 1)
    dup = deepcopy(a)
    undecided = _game("Tal", 1991, 4)
    undecided.headers["Date"] = "1991.??.??"
    more = [_game("Tal", 1992 + i, i + 5) for i in range(4)]
    report = real.analyze_cohort("Tal", _zip("Tal", [a, dup, undecided, *more]),
                                 min_ply=17)
    assert report["counters"]["pgn_records"] == 7
    assert report["counters"]["duplicate_mainline"] >= 1
    assert report["counters"]["missing_or_invalid_full_date"] >= 1
    assert report["dated_unique_games"] == report["unique_games"]
    assert report["counters"]["year_only_date"] == 1
    assert report["counters"]["unknown_year"] == 0


def test_full_fen_key_differs_with_halfmove_or_fullmove_counter():
    board = chess.Board()
    a = board.fen()
    board.fullmove_number = 9
    b = board.fen()
    assert a != b
    votes1 = {a: {"e2e4": 3}}
    votes2 = {b: {"e2e4": 2}}
    result = real._compare_two(votes1, votes2)
    assert result["exact_full_fens_shared"] == 0
    assert result["fens_with_different_modal_moves"] == 0



@pytest.mark.parametrize("input_date,expected", [
    ("1955.??.??", ("1955", "year_only")),
    ("1969.01.??", ("1969", "year_only")),
    ("1971.??.15", ("1971", "year_only")),
    ("1992.01.15", ("1992", "day_known")),
    ("1982.13.??", (None, "unknown")),
    ("1982.02.31", (None, "unknown")),
    ("????.??.??", (None, "unknown")),
])
def test_real_historical_date_precision_without_fabricating_days(input_date, expected):
    assert real._played_year(input_date) == expected


def test_year_only_games_are_eligible_for_whole_year_partitions(monkeypatch):
    monkeypatch.setitem(real.PLAYER_FILES["Fischer"], "min_usable", 1)
    games = [_game("Fischer", 1950 + i, i) for i in range(5)]
    for game in games:
        game.headers["Date"] = game.headers["Date"][:4] + ".??.??"
    cohort = real.analyze_cohort("Fischer", _zip("Fischer", games))
    assert cohort["dated_unique_games"] == 5
    assert cohort["counters"]["year_only_date"] == 5
    assert cohort["partitions"]["train"]["games"] == 3
    assert cohort["partitions"]["validation"]["games"] == 1
    assert cohort["partitions"]["holdout"]["games"] == 1
    assert cohort["partitions"]["train"]["first_year"] == 1950
    assert cohort["partitions"]["train"]["last_year"] == 1952
    assert "first_date" not in cohort["partitions"]["train"]



def test_offline_input_never_claims_download_from_public_mirror(tmp_path, monkeypatch):
    _lower_limits(monkeypatch)
    for name, blob in _cohorts().items():
        (tmp_path / (name + ".zip")).write_bytes(blob)
    report = real.run_real_historical_audit(
        tmp_path / "local-report.json", archive_dir=tmp_path)
    assert all(item["source_url"] == "LOCAL_SUPPLIED_SOURCE_NOT_AUTHENTICATED"
               for item in report["sources"].values())


def test_atomic_report_publication_removes_staging_after_link_failure(
        tmp_path, monkeypatch):
    _lower_limits(monkeypatch)
    for name, blob in _cohorts().items():
        (tmp_path / (name + ".zip")).write_bytes(blob)
    output = tmp_path / "report.json"

    def fail_link(*args):
        raise OSError("simulated atomic publish failure")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(OSError, match="simulated atomic publish failure"):
        real.run_real_historical_audit(output, archive_dir=tmp_path)
    assert not output.exists()
    assert list(tmp_path.glob(".chesssim-real-historical-*.tmp")) == []


def test_atomic_report_publication_preserves_concurrent_owner(
        tmp_path, monkeypatch):
    _lower_limits(monkeypatch)
    for name, blob in _cohorts().items():
        (tmp_path / (name + ".zip")).write_bytes(blob)
    output = tmp_path / "report.json"
    real_link = os.link

    def competing_link(source, destination):
        with open(destination, "x", encoding="utf-8") as stream:
            stream.write("owned by another process")
        return real_link(source, destination)

    monkeypatch.setattr(os, "link", competing_link)
    with pytest.raises(FileExistsError):
        real.run_real_historical_audit(output, archive_dir=tmp_path)
    assert output.read_text() == "owned by another process"
    assert list(tmp_path.glob(".chesssim-real-historical-*.tmp")) == []



def test_source_urls_are_pinned_to_immutable_mirror_snapshot():
    assert len(real.MIRROR_COMMIT) == 40
    assert "master" not in real.SOURCE_BASE
    assert real.MIRROR_COMMIT in real.SOURCE_BASE
    assert real.archive_url("Fischer") == (
        real.SOURCE_BASE + "Fischer.pgn")
