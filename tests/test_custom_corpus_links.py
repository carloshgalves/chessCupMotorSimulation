"""#63 slice 4: offline attested PGN metadata bind/detach, no engine changes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from hashlib import sha256
import json
import shutil
import socket
import subprocess

import chess.pgn
import pytest

from chesssim import custom_corpus_links as links
from chesssim import custom_profiles as core
from chesssim import custom_profile_history as names
from chesssim.catalog import load_roster
from chesssim.cli import main
from chesssim.licensed_pgn_cache import SCHEMA as RIGHTS_SCHEMA, prepare_cache


NAME = "Fictional Training Source"
OTHER = "Other Player"


def _game(n: int, *, name: str = NAME, played="2026.01.02",
          side="white", result="1-0") -> str:
    game = chess.pgn.Game()
    game.headers["White"] = name if side == "white" else OTHER
    game.headers["Black"] = name if side == "black" else OTHER
    game.headers["Date"] = played
    game.headers["Result"] = result
    board = game.board()
    first_moves = sorted(board.legal_moves, key=lambda m: m.uci())
    white = first_moves[n % 16]
    node = game.add_variation(white)
    board.push(white)
    blacks = sorted(board.legal_moves, key=lambda m: m.uci())
    black = blacks[(n // 16) % 16]
    assert black in board.legal_moves
    node.add_variation(black)
    return str(game) + "\n\n"


def _cache(tmp_path, count=20, *, no_right=False, override_games=None):
    source = tmp_path / "source.pgn"
    records = override_games
    if records is None:
        records = "".join(_game(i, side="white" if i % 2 else "black")
                          for i in range(count))
        if not records:
            records = _game(999, name=OTHER, side="white")
    source.write_text(records, encoding="utf-8")
    rights = {
        "schema": RIGHTS_SCHEMA,
        "sources": [{
            "id": "locally_reviewed",
            "file": "source.pgn",
            "source_url": "https://example.org/study/source.pgn",
            "license_id": "CC-BY-4.0",
            "license_evidence_url": "https://example.org/license",
            "rights_reviewed_on": date.today().isoformat(),
            "rights_attestation": "operator_reviewed",
            "expected_sha256": sha256(source.read_bytes()).hexdigest(),
            "allowed_uses": ["local_cache"] if no_right else
                            ["local_cache", "derived_openings"],
            "attribution": "Synthetic legal games (test fixture)",
        }],
    }
    cat = tmp_path / "rights.json"
    cat.write_text(json.dumps(rights), encoding="utf-8")
    out = tmp_path / "cache"
    prepare_cache(cat, out=out, max_games=500, max_cache_mb=8, apply=True)
    return out


def _setup(tmp_path, *, count=20, no_right=False, override_games=None):
    library = tmp_path / "library"
    item = core.create_fictional(library, "Ava Ravenwood")
    cache = _cache(tmp_path, count=count, no_right=no_right,
                   override_games=override_games)
    return library, item, cache


def _link(library, item, cache, *, pgn_name=NAME, head=None):
    return links.corpus_link(
        library, item["id"], cache, pgn_name=pgn_name,
        expected_head=head or item["profile_sha256"])


def _events(library, id_):
    return sorted(library.glob(f"{id_}.corpus.*.json"))


@pytest.mark.parametrize("count,band,status", [
    (0, "ZERO_GAMES", "NO_MATCHING_GAMES"),
    (19, "LOW_1_TO_19", "SNAPSHOT_NOT_VALIDATED"),
    (20, "EXPLORATORY_20_TO_199", "SNAPSHOT_NOT_VALIDATED"),
    (200, "EXPLORATORY_200_PLUS", "SNAPSHOT_NOT_VALIDATED"),
])
def test_zero_twenty_two_hundred_disclose_coverage_without_uci(
    tmp_path, count, band, status
):
    library, item, cache = _setup(tmp_path, count=count)
    baseline = tuple(load_roster())
    before = (cache / "games.pgn").read_bytes()
    result = _link(library, item, cache)
    assert result["operation"] == "LINKED_METADATA_ONLY"
    assert result["sequence"] == 1
    assert result["corpus"]["games"] == count
    assert result["corpus"]["coverage_band"] == band
    assert result["corpus"]["selection_status"] == status
    assert result["corpus"]["white_games"] + result["corpus"]["black_games"] == count
    assert result["uci_eligible"] is False
    assert result["historically_validated"] is False
    assert result["linked"] is True
    assert result["head_sha256"] != item["profile_sha256"]
    assert len(_events(library, item["id"])) == 1
    assert (cache / "games.pgn").read_bytes() == before
    assert tuple(load_roster()) == baseline and len(baseline) == 64
    # No raw PGN is ever copied to the library or stored inside the receipt.
    assert not list(library.rglob("*.pgn"))
    text = _events(library, item["id"])[0].read_text(encoding="utf-8")
    assert "[Event" not in text and "1. " not in text
    assert links.corpus_status(library, item["id"]) == {
        key: value for key, value in result.items() if key != "operation"}


def test_idempotent_link_detach_relink_and_stale_head_fail_closed(tmp_path):
    library, item, cache = _setup(tmp_path)
    first = _link(library, item, cache)
    assert _link(library, item, cache, head=first["head_sha256"])["operation"] == "ALREADY_LINKED"
    assert len(_events(library, item["id"])) == 1
    with pytest.raises(ValueError, match="obsoleta"):
        _link(library, item, cache)
    detached = links.corpus_detach(
        library, item["id"], expected_head=first["head_sha256"])
    assert detached["operation"] == "DETACHED_METADATA_ONLY"
    assert detached["linked"] is False and detached["corpus"] is None
    assert detached["sequence"] == 2
    assert links.corpus_detach(
        library, item["id"], expected_head=detached["head_sha256"]
    )["operation"] == "ALREADY_DETACHED"
    again = _link(library, item, cache, head=detached["head_sha256"])
    assert again["operation"] == "LINKED_METADATA_ONLY"
    assert again["sequence"] == 3
    assert first["corpus"]["selection_sha256"] == again["corpus"]["selection_sha256"]
    assert len(_events(library, item["id"])) == 3
    assert links.corpus_status(library, item["id"])["head_sha256"] == again["head_sha256"]


def test_name_match_exact_and_ambiguous_self_play_is_excluded(tmp_path):
    content = (
        _game(0, name="Other", side="white") +
        _game(1, name=NAME + " Jr", side="white") +
        _game(2, name=NAME, side="black", played="2026.01.04") +
        _game(3, name=NAME, side="white", played="????.??.??") +
        _game(4, name=NAME, side="white").replace(OTHER, NAME)
    )
    library, item, cache = _setup(tmp_path, override_games=content)
    result = _link(library, item, cache)
    assert result["corpus"]["games"] == 2
    assert result["corpus"]["with_valid_full_date"] == 1
    assert result["corpus"]["unknown_or_invalid_date"] == 1
    assert result["corpus"]["white_games"] == 1
    assert result["corpus"]["black_games"] == 1


def test_retire_blocks_link_but_detach_remains_available(tmp_path):
    library, item, cache = _setup(tmp_path)
    first = _link(library, item, cache)
    core.retire_fictional(library, item["id"])
    with pytest.raises(ValueError, match="desativado"):
        _link(library, item, cache, head=first["head_sha256"])
    ended = links.corpus_detach(library, item["id"],
                                 expected_head=first["head_sha256"])
    assert ended["linked"] is False
    assert core.list_fictional(library)[0]["status"] == "RETIRED_METADATA_ONLY"


def test_renaming_identity_does_not_rebind_old_corpus_or_change_uuid(tmp_path):
    library, item, cache = _setup(tmp_path)
    before = _link(library, item, cache)
    updated = names.rename_fictional(
        library, item["id"], "Mira Ravenwood", item["revision_sha256"])
    after = links.corpus_status(library, item["id"])
    assert after["id"] == item["id"]
    assert after["display_name"] == "Mira Ravenwood"
    assert after["head_sha256"] == before["head_sha256"]
    assert after["corpus"]["selection_sha256"] == before["corpus"]["selection_sha256"]
    assert after["profile_sha256"] == updated["profile_sha256"]


def test_rights_not_permitting_derived_use_fail_before_library_write(tmp_path):
    library, item, cache = _setup(tmp_path, no_right=True)
    original = sorted(p.name for p in library.iterdir())
    with pytest.raises(ValueError, match="derived_openings"):
        _link(library, item, cache)
    assert original == sorted(p.name for p in library.iterdir())
    assert not _events(library, item["id"])


def test_cache_checksum_corruption_fails_before_any_event(tmp_path):
    library, item, cache = _setup(tmp_path)
    source = cache / "games.pgn"
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Checksum"):
        _link(library, item, cache)
    assert not _events(library, item["id"])


def test_cache_manifest_forged_plies_fail_closed(tmp_path):
    library, item, cache = _setup(tmp_path)
    manifest = cache / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["games"][0]["plies"] = 200
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="divergem"):
        _link(library, item, cache)
    assert not _events(library, item["id"])


def test_source_symlink_is_rejected_before_event(tmp_path):
    library, item, cache = _setup(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    shortcut = tmp_path / "shortcut"
    shortcut.symlink_to(cache, target_is_directory=True)
    with pytest.raises(ValueError, match="simbólic"):
        _link(library, item, shortcut)
    assert not _events(library, item["id"])


def test_recomputed_event_hash_cannot_change_id_or_counts(tmp_path):
    library, item, cache = _setup(tmp_path)
    _link(library, item, cache)
    event_file = _events(library, item["id"])[0]
    original = event_file.read_bytes()
    data = json.loads(original)
    data["event"]["link"]["games"] = 99
    data["sha256"] = core._sha(data["event"])
    event_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="incoerentes"):
        links.corpus_status(library, item["id"])
    event_file.write_bytes(original)
    data = json.loads(original)
    data["event"]["id"] = "user-" + "0" * 32
    data["sha256"] = core._sha(data["event"])
    event_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="divergente"):
        links.corpus_status(library, item["id"])


def test_missing_or_symbolic_event_fails_closed(tmp_path):
    library, item, cache = _setup(tmp_path)
    linked = _link(library, item, cache)
    links.corpus_detach(library, item["id"], expected_head=linked["head_sha256"])
    first, second = _events(library, item["id"])
    backup = first.read_bytes()
    first.unlink()
    with pytest.raises(ValueError, match="interrompida"):
        links.corpus_status(library, item["id"])
    first.write_bytes(backup)
    second.unlink()
    outside = tmp_path / "sensitive"
    outside.write_bytes(b"do-not-touch")
    second.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        links.corpus_status(library, item["id"])
    assert outside.read_bytes() == b"do-not-touch"


def test_concurrent_link_with_same_expected_head_only_one_wins(tmp_path):
    library, item, cache = _setup(tmp_path)
    def work():
        return _link(library, item, cache)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(work) for _ in range(2)]
    accepted = [], []
    for future in jobs:
        try:
            accepted[0].append(future.result())
        except ValueError as exc:
            accepted[1].append(str(exc))
    assert len(accepted[0]) == len(accepted[1]) == 1
    assert "obsoleta" in accepted[1][0]
    assert len(_events(library, item["id"])) == 1


def test_external_failure_does_not_publish_partial_receipt(tmp_path, monkeypatch):
    library, item, cache = _setup(tmp_path)
    original = core._publish_exclusive
    def broken(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(core, "_publish_exclusive", broken)
    with pytest.raises(OSError, match="disk full"):
        _link(library, item, cache)
    assert not _events(library, item["id"])
    monkeypatch.setattr(core, "_publish_exclusive", original)
    assert _link(library, item, cache)["linked"] is True


def test_no_network_or_engine_is_invoked_by_binding(tmp_path, monkeypatch):
    library, item, cache = _setup(tmp_path)
    def prohibited(*args, **kwargs):
        raise AssertionError("Network or UCI tool unexpectedly called")
    monkeypatch.setattr(socket, "create_connection", prohibited)
    monkeypatch.setattr(subprocess, "run", prohibited)
    assert _link(library, item, cache)["uci_eligible"] is False


def test_cli_status_link_detach_smoke(tmp_path, capsys):
    library, item, cache = _setup(tmp_path)
    base = ["--library", str(library), "--id", item["id"]]
    assert main(["custom-corpus-status", *base]) == 0
    initial = json.loads(capsys.readouterr().out)
    assert initial["linked"] is False
    assert initial["head_sha256"] == item["profile_sha256"]
    assert main(["custom-corpus-link", *base, "--cache", str(cache),
                 "--pgn-name", NAME,
                 "--expected-head", initial["head_sha256"]]) == 0
    linked = json.loads(capsys.readouterr().out)
    assert linked["corpus"]["games"] == 20
    assert main(["custom-corpus-detach", *base,
                 "--expected-head", linked["head_sha256"]]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["linked"] is False
    assert removed["sequence"] == 2


def test_adversarial_action_type_and_unknown_name_revision_fail_closed(tmp_path):
    library, item, cache = _setup(tmp_path)
    _link(library, item, cache)
    receipt = _events(library, item["id"])[0]
    baseline = receipt.read_bytes()
    data = json.loads(baseline)
    data["event"]["action"] = ["LINK"]
    data["sha256"] = core._sha(data["event"])
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="divergente"):
        links.corpus_status(library, item["id"])
    receipt.write_bytes(baseline)
    data = json.loads(baseline)
    data["event"]["name_revision_sha256"] = "f" * 64
    data["sha256"] = core._sha(data["event"])
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Revisão de nome"):
        links.corpus_status(library, item["id"])
    receipt.write_bytes(baseline)
    assert links.corpus_status(library, item["id"])["linked"] is True


@pytest.mark.parametrize("bad_name", [
    NAME + "\u202e", NAME + "\u200d", NAME + "\nother",
])
def test_invisible_control_and_multiline_names_are_rejected(tmp_path, bad_name):
    library, item, cache = _setup(tmp_path)
    with pytest.raises(ValueError, match="NFC|controles"):
        _link(library, item, cache, pgn_name=bad_name)
    assert not _events(library, item["id"])


def test_link_after_actual_rename_references_existing_name_revision(tmp_path):
    library, item, cache = _setup(tmp_path)
    renamed = names.rename_fictional(
        library, item["id"], "Mira Ravenwood", item["revision_sha256"])
    attached = _link(library, item, cache)
    stored = json.loads(_events(library, item["id"])[0].read_text())
    assert stored["event"]["name_revision_sha256"] == renamed["revision_sha256"]
    assert links.corpus_status(library, item["id"])["head_sha256"] == attached["head_sha256"]


def test_source_deleted_after_link_does_not_claim_liveness(tmp_path):
    library, item, cache = _setup(tmp_path)
    attached = _link(library, item, cache)
    assert attached["linked"] is True
    assert attached["source_availability"] == "NOT_RECHECKED_AFTER_LINK"
    shutil.rmtree(cache)
    status = links.corpus_status(library, item["id"])
    assert status["linked"] is True
    assert status["source_availability"] == "NOT_RECHECKED_AFTER_LINK"
    assert status["head_sha256"] == attached["head_sha256"]
    assert status["uci_eligible"] is False
    detached = links.corpus_detach(
        library, item["id"], expected_head=attached["head_sha256"])
    assert detached["linked"] is False
    assert detached["source_availability"] == "NOT_APPLICABLE"


def test_duplicate_pgn_trajectory_is_counted_once_not_multiple_times(tmp_path):
    repeated = (_game(0, name=NAME, played="2026.01.02") +
                _game(0, name=NAME, played="2026.01.03"))
    library, item, cache = _setup(tmp_path, override_games=repeated)
    manifest = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["games_distinct"] == 1
    assert manifest["games_duplicates"] == 1
    attached = _link(library, item, cache)
    assert attached["corpus"]["games"] == 1
    assert attached["corpus"]["coverage_band"] == "LOW_1_TO_19"


def test_event_quota_32_blocks_33rd_without_overwriting_receipts(
    tmp_path, monkeypatch
):
    library, item, cache = _setup(tmp_path, count=1)
    snapshot = links._selection(cache, NAME)
    monkeypatch.setattr(links, "_selection", lambda _path, _name: snapshot)
    head = item["profile_sha256"]
    for _ in range(16):
        attached = _link(library, item, cache, head=head)
        detached = links.corpus_detach(
            library, item["id"], expected_head=attached["head_sha256"])
        head = detached["head_sha256"]
    originals = [(p.name, p.read_bytes()) for p in _events(library, item["id"])]
    assert len(originals) == 32
    with pytest.raises(ValueError, match="32"):
        _link(library, item, cache, head=head)
    assert originals == [(p.name, p.read_bytes())
                         for p in _events(library, item["id"])]
    assert links.corpus_status(library, item["id"])["sequence"] == 32
