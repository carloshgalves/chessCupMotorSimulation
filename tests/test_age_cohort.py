"""#65: exact-age cohort derives only train PGNs from rights-attested caches."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import StringIO
import json
import socket

import chess
import chess.pgn
import pytest

from chesssim.age_cohort import age_at_game, build_age_cohort, SCHEMA
from chesssim.cli import main
from chesssim.licensed_pgn_cache import prepare_cache, SCHEMA as RIGHTS_SCHEMA


WHITE_MOVES = (
    "e2e4", "d2d4", "c2c4", "g1f3", "b1c3", "g2g3", "b2b3", "f2f4",
)
BLACK_MOVES = ("e7e5", "c7c5", "d7d5")
NAME = "Carlsen, Magnus"
BIRTH = "1990-11-30"


def _pgn(index, played, *, white=NAME, black="Opponent",
         result="1-0"):
    assert isinstance(played, str), "fixture date must be a single PGN date"
    board = chess.Board()
    game = chess.pgn.Game()
    game.headers["White"] = white
    game.headers["Black"] = black
    game.headers["Date"] = played
    game.headers["Event"] = "Synthetic authorized local test"
    game.headers["Result"] = result
    node = game
    for text in (WHITE_MOVES[index % len(WHITE_MOVES)],
                 BLACK_MOVES[(index // len(WHITE_MOVES)) % len(BLACK_MOVES)]):
        move = chess.Move.from_uci(text)
        assert move in board.legal_moves
        node = node.add_variation(move)
        board.push(move)
    return str(game) + "\n\n"


def _cache(tmp_path, payload, *, derived=True):
    source = tmp_path / "source.pgn"
    source.write_text(payload, encoding="utf-8")
    rights = {
        "schema": RIGHTS_SCHEMA,
        "sources": [{
            "id": "fixture",
            "file": "source.pgn",
            "source_url": "https://example.org/source.pgn",
            "license_id": "CC-BY-4.0",
            "license_evidence_url": "https://example.org/license",
            "rights_reviewed_on": date.today().isoformat(),
            "rights_attestation": "operator_reviewed",
            "expected_sha256": sha256(source.read_bytes()).hexdigest(),
            "allowed_uses": ["local_cache", "derived_openings"] if derived else ["local_cache"],
            "attribution": "Original synthetic legal-move fixtures, tests only",
        }],
    }
    catalog = tmp_path / "rights.json"
    catalog.write_text(json.dumps(rights), encoding="utf-8")
    folder = tmp_path / "cache"
    prepare_cache(catalog, out=folder, max_games=100, max_cache_mb=8, apply=True)
    return folder


def _trial(tmp_path):
    # Age 21: November 30 2011 through November 29 2012. Keep at least
    # two dates available for validation and holdout, and a prior/future game.
    dates = [
        "2011.11.29",  # still 20
        "2011.11.30", "2011.12.01", "2012.01.01",
        "2012.01.02", "2012.01.03", "2012.01.04",
        "2012.02.10", "2012.03.10", "2012.05.10",
        "2012.09.10", "2012.11.29",
        "2012.11.30",  # now 22
    ]
    text = "".join(_pgn(i, played, black=("Opponent" if i % 2 else "Other"))
                   for i, played in enumerate(dates))
    return _cache(tmp_path, text)


def _cohort(cache, output, *, age=21, min_train=2, name=NAME,
            birth=BIRTH):
    return build_age_cohort(cache, output, pgn_name=name, birth_date=birth,
                            age=age, min_train=min_train)


def test_completed_years_include_birthday_and_february_29_policy():
    assert age_at_game(date(1990, 11, 30), date(2011, 11, 29)) == 20
    assert age_at_game(date(1990, 11, 30), date(2011, 11, 30)) == 21
    assert age_at_game(date(1990, 11, 30), date(2012, 11, 29)) == 21
    assert age_at_game(date(1990, 11, 30), date(2012, 11, 30)) == 22
    assert age_at_game(date(2000, 2, 29), date(2021, 2, 28)) == 20
    assert age_at_game(date(2000, 2, 29), date(2021, 3, 1)) == 21
    assert age_at_game(date(2000, 2, 29), date(2024, 2, 29)) == 24
    with pytest.raises(ValueError, match="anterior"):
        age_at_game(date(2000, 2, 29), date(1999, 12, 31))


def test_age_cohort_only_exports_train_and_preserves_strict_time_order(tmp_path):
    cache = _trial(tmp_path)
    out = tmp_path / "derived"
    report = _cohort(cache, out)
    assert report["schema"] == SCHEMA
    assert report["status"] == "COHORT_PREPARED_NOT_VALIDATED"
    assert report["profile_available"] is False
    assert report["eligible_exact_age_unique_games"] == 11
    assert report["excluded"]["outside_exact_age"] == 2
    assert report["partitions"]["train"]["games"] == 6
    assert report["partitions"]["validation"]["games"] == 2
    assert report["partitions"]["holdout"]["games"] == 3
    partitions = report["partitions"]
    assert partitions["train"]["last_date"] < partitions["validation"]["first_date"]
    assert partitions["validation"]["last_date"] < partitions["holdout"]["first_date"]
    assert set(partitions["train"]["mainline_sha256"]).isdisjoint(
        partitions["validation"]["mainline_sha256"])
    assert set(partitions["train"]["mainline_sha256"]).isdisjoint(
        partitions["holdout"]["mainline_sha256"])
    assert set(partitions["validation"]["mainline_sha256"]).isdisjoint(
        partitions["holdout"]["mainline_sha256"])
    assert sorted(p.name for p in out.iterdir()) == ["cohort.json", "train.pgn"]
    assert json.loads((out / "cohort.json").read_text()) == report
    raw_train = (out / "train.pgn").read_bytes()
    assert sha256(raw_train).hexdigest() == report["train_pgn_sha256"]
    stream = StringIO(raw_train.decode("utf-8"))
    exported = []
    while (game := chess.pgn.read_game(stream)) is not None:
        assert not game.errors
        exported.append(game)
        day = date.fromisoformat(game.headers["Date"].replace(".", "-"))
        assert age_at_game(date.fromisoformat(BIRTH), day) == 21
    assert len(exported) == partitions["train"]["games"]


def test_same_date_never_crosses_training_validation_holdout(tmp_path):
    days = (["2011.12.01"] * 7 + ["2012.01.01"] * 2 +
            ["2012.02.01"] * 2 + ["2012.03.01"] * 2)
    payload = "".join(_pgn(i, day) for i, day in enumerate(days))
    cache = _cache(tmp_path, payload)
    info = _cohort(cache, tmp_path / "derived")
    assert [info["partitions"][arm]["games"] for arm in
            ("train", "validation", "holdout")] == [7, 2, 4]
    assert info["partitions"]["train"]["last_date"] == "2011-12-01"
    assert info["partitions"]["validation"]["first_date"] == "2012-01-01"
    assert info["partitions"]["holdout"]["first_date"] == "2012-02-01"


def test_identical_trajectory_is_one_observation_even_with_changed_headers(tmp_path):
    text = _pgn(0, "2011.12.01") + _pgn(0, "2012.05.01")
    cache = _cache(tmp_path, text)
    info = _cohort(cache, tmp_path / "derived")
    assert info["eligible_exact_age_unique_games"] == 1
    assert info["status"] == "INSUFFICIENT_EXACT_AGE_EVIDENCE"
    assert info["partitions"]["holdout"]["games"] == 0


def test_unknown_date_future_date_and_both_sides_are_not_eligible(tmp_path):
    text = (
        _pgn(0, "2011.12.01") +
        _pgn(1, "????.??.??") +
        _pgn(2, "2012.02.30") +
        _pgn(3, "2999.01.01") +
        _pgn(4, "2012.04.01", white=NAME, black=NAME) +
        _pgn(5, "1980.01.01")
    )
    report = _cohort(_cache(tmp_path, text), tmp_path / "derived")
    assert report["eligible_exact_age_unique_games"] == 1
    assert report["excluded"]["date_unknown_or_invalid"] == 2
    assert report["excluded"]["date_in_future"] == 1
    assert report["excluded"]["ambiguous_both_sides"] == 1
    assert report["excluded"]["before_birth"] == 1


def test_player_name_must_match_full_name_not_surname_or_homonym(tmp_path):
    names = (NAME, "Carlsen, Magnus Jr", "Carlsen", "Other Player")
    text = "".join(_pgn(i, "2012.01.0" + str(i+1), white=name)
                   for i, name in enumerate(names))
    info = _cohort(_cache(tmp_path, text), tmp_path / "derived")
    assert info["eligible_exact_age_unique_games"] == 1
    assert info["excluded"]["other_player"] == 3
    assert info["identity_side_matches"] == {"white": 1}


def test_insufficient_and_empty_cohorts_never_publish_a_playable_profile(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    out = tmp_path / "empty"
    report = _cohort(cache, out, name="Player Not Present")
    assert report["status"] == "INSUFFICIENT_EXACT_AGE_EVIDENCE"
    assert report["profile_available"] is False
    assert report["eligible_exact_age_unique_games"] == 0
    assert (out / "train.pgn").read_bytes() == b""
    assert not (out / "holdout.pgn").exists()


def test_source_license_must_allow_derived_openings(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"), derived=False)
    with pytest.raises(ValueError, match="derived_openings"):
        _cohort(cache, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def test_cache_checksum_failure_rejected_before_any_output(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    (cache / "games.pgn").write_bytes((cache / "games.pgn").read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Checksum"):
        _cohort(cache, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def test_manifest_row_forgery_and_extra_pgn_fail_closed(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    manifest_path = cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["games"][0]["plies"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="divergem"):
        _cohort(cache, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def test_source_cache_symlinks_rejected(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    original = cache / "games.pgn"
    raw = original.read_bytes()
    original.unlink()
    elsewhere = tmp_path / "other.pgn"
    elsewhere.write_bytes(raw)
    original.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="simbólic"):
        _cohort(cache, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def test_existing_output_is_never_overwritten(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    out = tmp_path / "derived"
    out.mkdir()
    sentinel = out / "sentinel"
    sentinel.write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _cohort(cache, out)
    assert sentinel.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize("age,birth,min_train", [
    (True, BIRTH, 1), (-1, BIRTH, 1), (121, BIRTH, 1),
    (21, "1990-11", 1), (21, "1990-02-30", 1),
    (21, BIRTH, 0), (21, BIRTH, True),
    (21, BIRTH, 1001),
])
def test_parameter_errors_never_create_derived_artifacts(tmp_path, age, birth, min_train):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    out = tmp_path / "derived"
    with pytest.raises(ValueError):
        _cohort(cache, out, age=age, birth=birth, min_train=min_train)
    assert not out.exists()


def test_output_under_source_cache_is_rejected(tmp_path):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    with pytest.raises(ValueError, match="dentro do cache"):
        _cohort(cache, cache / "derived")
    assert not (cache / "derived").exists()


def test_age_cohort_never_downloads_or_uses_engines(tmp_path, monkeypatch):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    def forbidden(*args, **kwargs):
        raise AssertionError("Age cohort cannot use a network client or UCI")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    out = tmp_path / "derived"
    assert _cohort(cache, out)["profile_available"] is False


def test_cli_prints_attested_not_validated_cohort(tmp_path, capsys):
    cache = _cache(tmp_path, _pgn(0, "2012.01.01"))
    out = tmp_path / "derived"
    rc = main(["age-cohort", "--cache", str(cache), "--out", str(out),
               "--pgn-name", NAME, "--birth-date", BIRTH, "--age", "21"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["profile_available"] is False
    assert report["eligible_games"] == 1
    assert report["status"] == "INSUFFICIENT_EXACT_AGE_EVIDENCE"
    assert report["train_pgn_sha256"] == sha256((out / "train.pgn").read_bytes()).hexdigest()


def test_unknown_or_missing_participant_headers_are_excluded_not_matched(tmp_path):
    text = _pgn(0, "2012.01.01", white="?", black="?")
    text += _pgn(1, "2012.01.02", white="Another Person", black="Missing")
    text += _pgn(2, "2012.01.03")
    data = _cohort(_cache(tmp_path, text), tmp_path / "derived")
    assert data["eligible_exact_age_unique_games"] == 1
    assert data["excluded"]["unresolved_player_headers"] == 1
    assert data["excluded"]["other_player"] == 1


def test_manifest_source_rights_must_be_typed_list_not_string(tmp_path):
    folder = _cache(tmp_path, _pgn(0, "2012.01.01"))
    manifest = folder / "manifest.json"
    report = json.loads(manifest.read_text(encoding="utf-8"))
    report["sources"][0]["allowed_uses"] = "derived_openings"
    manifest.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="derived_openings"):
        _cohort(folder, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()


def test_unhashable_source_ref_fails_closed_without_type_error(tmp_path):
    folder = _cache(tmp_path, _pgn(0, "2012.01.01"))
    manifest = folder / "manifest.json"
    report = json.loads(manifest.read_text(encoding="utf-8"))
    report["games"][0]["sources"] = [{"unexpected": "nested"}]
    manifest.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="Registro do manifesto"):
        _cohort(folder, tmp_path / "derived")
    assert not (tmp_path / "derived").exists()
