"""#63 slice 5: one-snapshot train-only split of an operator-attested corpus."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
import socket
import subprocess

import chess.pgn
import pytest

from chesssim import custom_corpus_links as links
from chesssim import custom_corpus_train as train
from chesssim import custom_profiles as core
from chesssim.catalog import load_roster
from chesssim.cli import main
from chesssim.licensed_pgn_cache import prepare_cache, SCHEMA as RIGHTS_SCHEMA


NAME = "Fictional Source Player"
OTHER = "Unrelated Opponent"


def _game(n, *, date_text=None, target=NAME, both=False):
    game = chess.pgn.Game()
    game.headers["White"] = target
    game.headers["Black"] = target if both else OTHER
    game.headers["Date"] = date_text if date_text is not None else (
        f"2026.01.{n + 1:02d}")
    game.headers["Result"] = "1-0"
    board = game.board()
    white = sorted(board.legal_moves, key=lambda m: m.uci())[n % 16]
    node = game.add_variation(white)
    board.push(white)
    black = sorted(board.legal_moves, key=lambda m: m.uci())[(n // 16) % 16]
    node.add_variation(black)
    return str(game) + "\n\n"


def _setup(tmp_path, *, games=None):
    library = tmp_path / "library"
    profile = core.create_fictional(library, "Ava Ravenwood")
    pgn = tmp_path / "source.pgn"
    pgn.write_text("".join(games if games is not None else
                           [_game(n) for n in range(18)]), encoding="utf-8")
    rights = {
        "schema": RIGHTS_SCHEMA,
        "sources": [{
            "id": "operator_approved",
            "file": "source.pgn",
            "source_url": "https://example.org/chess/original.pgn",
            "license_id": "CC-BY-4.0",
            "license_evidence_url": "https://example.org/chess/permission",
            "rights_reviewed_on": date.today().isoformat(),
            "rights_attestation": "operator_reviewed",
            "expected_sha256": sha256(pgn.read_bytes()).hexdigest(),
            "allowed_uses": ["local_cache", "derived_openings"],
            "attribution": "Synthetic test fixtures, not a real player's games",
        }],
    }
    rights_path = tmp_path / "rights.json"
    rights_path.write_text(json.dumps(rights), encoding="utf-8")
    cache = tmp_path / "cache"
    prepare_cache(rights_path, out=cache, max_cache_mb=8, apply=True)
    link = links.corpus_link(
        library, profile["id"], cache, pgn_name=NAME,
        expected_head=profile["profile_sha256"])
    return library, profile, cache, link


def _prepare(library, profile, cache, link, dest, **options):
    return train.prepare_custom_train(
        library, profile["id"], cache, dest,
        expected_head=link["head_sha256"], **options)


def test_train_only_export_revalidates_link_and_preserves_holdout(tmp_path):
    library, profile, cache, link = _setup(tmp_path)
    roster = tuple(load_roster())
    original_cache = (cache / "games.pgn").read_bytes()
    original_id = (library / f"{profile['id']}.json").read_bytes()
    out = tmp_path / "split"
    doc = _prepare(library, profile, cache, link, out, min_train=5)
    assert doc["status"] == "TRAIN_ONLY_PREPARED_NOT_VALIDATED"
    assert doc["uci_eligible"] is False
    assert doc["historically_validated"] is False
    assert doc["linked_unique_games"] == 18
    assert doc["fully_dated_nonfuture_games"] == 18
    assert doc["corpus_head_sha256"] == link["head_sha256"]
    assert doc["source"]["selection_sha256"] == link["corpus"]["selection_sha256"]
    assert doc["source"]["cache_pgn_sha256"] == link["corpus"]["cache_pgn_sha256"]
    assert sorted(p.name for p in out.iterdir()) == ["split.json", "train.pgn"]
    assert json.loads((out / "split.json").read_text()) == doc
    assert sha256((out / "train.pgn").read_bytes()).hexdigest() == doc["train_pgn_sha256"]
    assert len((out / "train.pgn").read_bytes()) == doc["train_pgn_bytes"]
    pieces = doc["partitions"]
    assert sum(p["games"] for p in pieces.values()) == 18
    assert pieces["train"]["games"] >= 5
    assert pieces["validation"]["games"] >= 1
    assert pieces["holdout"]["games"] >= 1
    assert pieces["validation"]["exported_pgn"] is False
    assert pieces["holdout"]["exported_pgn"] is False
    assert pieces["validation"]["mainline_sha256"] is None
    assert pieces["holdout"]["mainline_sha256"] is None
    assert len(pieces["train"]["mainline_sha256"]) == pieces["train"]["games"]
    assert pieces["train"]["last_date"] < pieces["validation"]["first_date"]
    assert pieces["validation"]["last_date"] < pieces["holdout"]["first_date"]
    # The only replayable PGN is from the training partition.
    stream = StringIO((out / "train.pgn").read_text(encoding="utf-8"))
    count = 0
    while (game := chess.pgn.read_game(stream)) is not None:
        assert not game.errors
        count += 1
    assert count == pieces["train"]["games"]
    assert (cache / "games.pgn").read_bytes() == original_cache
    assert (library / f"{profile['id']}.json").read_bytes() == original_id
    assert not list(library.rglob("*.pgn"))
    assert tuple(load_roster()) == roster and len(roster) == 64


def test_all_games_same_civil_date_stay_in_one_partition(tmp_path):
    games = [_game(n, date_text="2026.02.04") for n in range(18)]
    library, profile, cache, link = _setup(tmp_path, games=games)
    result = _prepare(library, profile, cache, link, tmp_path / "same-day", min_train=5)
    assert result["status"] == "INSUFFICIENT_PARTITION_EVIDENCE"
    assert result["partitions"]["train"]["games"] == 18
    assert result["partitions"]["validation"]["games"] == 0
    assert result["partitions"]["holdout"]["games"] == 0
    assert result["historically_validated"] is False


def test_unknown_future_and_ambiguous_games_quarantined(tmp_path):
    games = [_game(n) for n in range(14)]
    games += [_game(14, date_text="????.??.??"),
              _game(15, date_text="2999.02.05"),
              _game(16, date_text="2026.03.01", both=True)]
    library, profile, cache, link = _setup(tmp_path, games=games)
    assert link["corpus"]["games"] == 16
    out = tmp_path / "quarantine"
    result = _prepare(library, profile, cache, link, out, min_train=5)
    assert result["linked_unique_games"] == 16
    assert result["fully_dated_nonfuture_games"] == 14
    assert result["excluded"]["unknown_or_invalid_date"] == 1
    assert result["excluded"]["future_date"] == 1
    assert result["excluded"]["ambiguous_both_sides"] == 1
    assert sum(row["games"] for row in result["partitions"].values()) == 14
    assert "2999.02.05" not in (out / "train.pgn").read_text()
    assert "????.??.??" not in (out / "train.pgn").read_text()


def test_link_cannot_be_replaced_by_an_unrelated_cache(tmp_path):
    library, profile, cache, link = _setup(tmp_path)
    original = (cache / "games.pgn").read_bytes()
    (cache / "games.pgn").write_bytes(original + b"\n")
    out = tmp_path / "invalid-source"
    with pytest.raises(ValueError, match="Checksum"):
        _prepare(library, profile, cache, link, out, min_train=5)
    assert not out.exists()
    assert (library / f"{profile['id']}.corpus.000001.json").exists()


def test_detached_and_stale_heads_never_publish_training(tmp_path):
    library, profile, cache, link = _setup(tmp_path)
    links.corpus_detach(library, profile["id"], expected_head=link["head_sha256"])
    with pytest.raises(ValueError, match="não possui corpus ativo"):
        _prepare(library, profile, cache, link, tmp_path / "after-detach")
    assert not (tmp_path / "after-detach").exists()


def test_retired_identity_cannot_export_training(tmp_path):
    library, profile, cache, link = _setup(tmp_path)
    core.retire_fictional(library, profile["id"])
    with pytest.raises(ValueError, match="desativado"):
        _prepare(library, profile, cache, link, tmp_path / "retired")
    assert not (tmp_path / "retired").exists()


def test_zero_game_link_cannot_produce_empty_training_directory(tmp_path):
    games = [_game(n, target="Different Name") for n in range(4)]
    library, profile, cache, _ = _setup(tmp_path, games=games)
    first = links.corpus_status(library, profile["id"])
    # _setup has a link, but it contains zero matching games, not data.
    assert first["corpus"]["games"] == 0
    dest = tmp_path / "zero"
    with pytest.raises(ValueError, match="sem partidas"):
        train.prepare_custom_train(
            library, profile["id"], cache, dest,
            expected_head=first["head_sha256"])
    assert not dest.exists()


@pytest.mark.parametrize("min_train,max_mb", [(0, 32), (1001, 32),
                                               (True, 32), (5, 0), (5, 33),
                                               (5, True)])
def test_invalid_budget_or_training_limit_fails_before_output(
    tmp_path, min_train, max_mb
):
    library, profile, cache, link = _setup(tmp_path)
    dest = tmp_path / "invalid-budget"
    with pytest.raises(ValueError):
        _prepare(library, profile, cache, link, dest,
                 min_train=min_train, max_output_mb=max_mb)
    assert not dest.exists()


def test_output_collision_symlink_and_forbidden_in_library_cache(tmp_path):
    library, profile, cache, link = _setup(tmp_path)
    exists = tmp_path / "existing"
    exists.mkdir()
    sentinel = exists / "owned.txt"
    sentinel.write_bytes(b"do not overwrite")
    with pytest.raises(FileExistsError):
        _prepare(library, profile, cache, link, exists)
    assert sentinel.read_bytes() == b"do not overwrite"
    with pytest.raises(ValueError, match="fora"):
        _prepare(library, profile, cache, link, library / "derived")
    with pytest.raises(ValueError, match="fora"):
        _prepare(library, profile, cache, link, cache / "derived")
    link_path = tmp_path / "symlink-output"
    link_path.symlink_to(exists, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _prepare(library, profile, cache, link, link_path)
    missing_parent = tmp_path / "missing" / "nested"
    with pytest.raises(ValueError, match="pai"):
        _prepare(library, profile, cache, link, missing_parent)
    assert sentinel.read_bytes() == b"do not overwrite"


def test_no_second_read_of_mutable_cache_after_selection_preflight(tmp_path, monkeypatch):
    library, profile, cache, link = _setup(tmp_path)
    original = train._cache_snapshot
    calls = 0
    def one_snapshot(folder):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("Mutable source was parsed twice")
        return original(folder)
    monkeypatch.setattr(train, "_cache_snapshot", one_snapshot)
    out = tmp_path / "one-read"
    _prepare(library, profile, cache, link, out, min_train=5)
    assert calls == 1


def test_failed_publication_removes_new_output_not_source(tmp_path, monkeypatch):
    library, profile, cache, link = _setup(tmp_path)
    before = (cache / "games.pgn").read_bytes()
    original = train._atomic_text
    written = 0
    def fail_second(path, text, **kwargs):
        nonlocal written
        written += 1
        if written == 2:
            raise OSError("simulated disk exhausted")
        return original(path, text, **kwargs)
    monkeypatch.setattr(train, "_atomic_text", fail_second)
    dest = tmp_path / "failed"
    with pytest.raises(OSError, match="exhausted"):
        _prepare(library, profile, cache, link, dest, min_train=5)
    assert not dest.exists()
    assert (cache / "games.pgn").read_bytes() == before
    monkeypatch.setattr(train, "_atomic_text", original)
    assert _prepare(library, profile, cache, link, dest, min_train=5)["training_only"]


def test_source_cache_symlink_is_rejected_even_with_valid_link(tmp_path):
    library, profile, cache, link = _setup(tmp_path)
    symlink = tmp_path / "alias"
    symlink.symlink_to(cache, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink|simbólic"):
        _prepare(library, profile, symlink, link, tmp_path / "alias-out")
    assert not (tmp_path / "alias-out").exists()


def test_no_uci_engine_or_network_for_training(tmp_path, monkeypatch):
    library, profile, cache, link = _setup(tmp_path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Network/engine invocation forbidden")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = _prepare(library, profile, cache, link, tmp_path / "offline", min_train=5)
    assert result["uci_eligible"] is False


def test_cli_export_integration(tmp_path, capsys):
    library, profile, cache, link = _setup(tmp_path)
    dest = tmp_path / "cli-train"
    args = [
        "custom-corpus-train", "--library", str(library), "--id", profile["id"],
        "--cache", str(cache), "--expected-head", link["head_sha256"],
        "--out", str(dest), "--min-train", "5",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "TRAIN_ONLY_PREPARED_NOT_VALIDATED"
    assert (dest / "split.json").is_file()
    assert (dest / "train.pgn").is_file()


def test_same_name_in_both_pgn_headers_never_reaches_training(tmp_path):
    games = [_game(n) for n in range(12)] + [
        _game(14, date_text="2026.05.01", both=True)]
    library, profile, cache, link = _setup(tmp_path, games=games)
    doc = _prepare(library, profile, cache, link, tmp_path / "ambiguous", min_train=4)
    assert doc["excluded"]["ambiguous_both_sides"] == 1
    assert doc["linked_unique_games"] == 12
    assert sum(part["games"] for part in doc["partitions"].values()) == 12
    assert (tmp_path / "ambiguous" / "train.pgn").is_file()
