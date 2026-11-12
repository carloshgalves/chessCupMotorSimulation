"""#63 slice 2: append-only rename lineage with portable, non-executable history."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from chesssim import custom_profiles as core
from chesssim import custom_profile_history as history
from chesssim.catalog import load_roster
from chesssim.cli import main


def _created(tmp_path, name="Ava Ravenwood"):
    library = tmp_path / "library"
    profile = core.create_fictional(library, name)
    return library, profile


def _rename(library, profile, new_name):
    return history.rename_fictional(
        library, profile["id"], new_name, profile["revision_sha256"])


def _rows(library, identity):
    return sorted(library.glob(f"{identity}.rename.*.json"))


def test_original_identity_byte_immutable_and_name_projections_consistent(tmp_path):
    library, original = _created(tmp_path)
    raw = (library / f"{original['id']}.json").read_bytes()
    roster = tuple(load_roster())
    assert original["revision"] == 0
    assert original["revision_sha256"] == original["profile_sha256"]
    first = _rename(library, original, "Mira Ravenwood")
    assert first["operation"] == "RENAMED_METADATA_ONLY"
    assert first["id"] == original["id"]
    assert first["display_name"] == "Mira Ravenwood"
    assert first["revision"] == 1
    assert first["revision_sha256"] != original["revision_sha256"]
    second = _rename(library, first, "Élise Ravenwood")
    assert second["revision"] == 2
    assert second["display_name"] == "Élise Ravenwood"
    assert (library / f"{original['id']}.json").read_bytes() == raw
    assert tuple(load_roster()) == roster and len(roster) == 64
    listing = core.list_fictional(library)[0]
    assert listing["display_name"] == "Élise Ravenwood"
    assert listing["profile_sha256"] == original["profile_sha256"]
    assert listing["revision"] == 2
    report = history.history_fictional(library, original["id"])
    assert report["original_display_name"] == "Ava Ravenwood"
    assert [row["new_name"] for row in report["history"]] == [
        "Mira Ravenwood", "Élise Ravenwood"]
    assert report["history"][0]["previous_sha256"] == original["profile_sha256"]
    assert report["history"][1]["previous_sha256"] == first["revision_sha256"]
    assert report["history"][1]["revision_sha256"] == second["revision_sha256"]


def test_stale_head_noop_validation_and_retired_guard(tmp_path):
    library, original = _created(tmp_path)
    first = _rename(library, original, "Mira Ravenwood")
    with pytest.raises(ValueError, match="obsoleta"):
        _rename(library, original, "Another Name")
    assert len(_rows(library, original["id"])) == 1
    assert _rename(library, first, "Mira Ravenwood")["operation"] == "ALREADY_NAMED"
    with pytest.raises(ValueError, match="obsoleta"):
        history.rename_fictional(library, original["id"], "Mira Ravenwood",
                                 original["profile_sha256"])
    core.retire_fictional(library, original["id"])
    with pytest.raises(ValueError, match="Reative"):
        _rename(library, first, "Someone Else")
    assert core.list_fictional(library)[0]["status"] == "RETIRED_METADATA_ONLY"
    restored = core.restore_fictional(library, original["id"])
    assert restored["status"] == core.STATUS
    assert restored["display_name"] == "Mira Ravenwood"


def test_legacy_export_stays_identical_and_renamed_exports_full_bundle(tmp_path):
    library, original = _created(tmp_path)
    plain = tmp_path / "plain.json"
    core.export_fictional(library, original["id"], plain)
    assert plain.read_bytes() == (library / f"{original['id']}.json").read_bytes()
    first = _rename(library, original, "Mira Ravenwood")
    second = _rename(library, first, "Mira D’Ávila")
    exported = tmp_path / "portable.json"
    core.export_fictional(library, original["id"], exported)
    obj = json.loads(exported.read_text(encoding="utf-8"))
    assert obj["schema"] == history.BUNDLE_SCHEMA
    assert obj["identity"]["profile"]["display_name"] == "Ava Ravenwood"
    assert len(obj["revisions"]) == 2
    replica = tmp_path / "replica"
    imported = core.import_fictional(replica, exported)
    assert imported["operation"] == "IMPORTED_METADATA_ONLY"
    assert imported["revision"] == 2
    assert imported["display_name"] == second["display_name"]
    assert imported["revision_sha256"] == second["revision_sha256"]
    assert core.import_fictional(replica, exported)["operation"] == "ALREADY_PRESENT"
    assert len(_rows(replica, original["id"])) == 2
    older = core.import_fictional(replica, plain)
    assert older["operation"] == "ALREADY_PRESENT"
    assert older["revision"] == 2, "legacy import must not downgrade the display name"


def test_import_reconciles_existing_prefix_and_never_rewrites_base(tmp_path):
    library, original = _created(tmp_path)
    one = _rename(library, original, "Mira Ravenwood")
    partial = tmp_path / "partial.json"
    core.export_fictional(library, original["id"], partial)
    two = _rename(library, one, "Élise Ravenwood")
    complete = tmp_path / "complete.json"
    core.export_fictional(library, original["id"], complete)
    clone = tmp_path / "clone"
    core.import_fictional(clone, partial)
    base_before = (clone / f"{original['id']}.json").read_bytes()
    result = core.import_fictional(clone, complete)
    assert result["operation"] == "IMPORTED_NEW_REVISIONS"
    assert result["revision"] == 2
    assert result["revision_sha256"] == two["revision_sha256"]
    assert (clone / f"{original['id']}.json").read_bytes() == base_before
    assert core.import_fictional(clone, partial)["revision"] == 2


def test_diverging_history_with_valid_recomputed_hash_refused(tmp_path):
    library, original = _created(tmp_path)
    first = _rename(library, original, "Mira Ravenwood")
    bundle = tmp_path / "original.json"
    core.export_fictional(library, original["id"], bundle)
    clone = tmp_path / "clone"
    core.import_fictional(clone, bundle)
    original_revision = (clone / f"{original['id']}.rename.000001.json").read_bytes()
    doc = json.loads(bundle.read_text())
    doc["revisions"][0]["event"]["new_name"] = "Other Name"
    doc["revisions"][0]["sha256"] = core._sha(doc["revisions"][0]["event"])
    payload = {"schema": doc["schema"], "identity": doc["identity"],
               "revisions": doc["revisions"]}
    doc["sha256"] = core._sha(payload)
    conflict = tmp_path / "forked.json"
    conflict.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(FileExistsError, match="divergente"):
        core.import_fictional(clone, conflict)
    assert (clone / f"{original['id']}.rename.000001.json").read_bytes() == original_revision
    assert core.list_fictional(clone)[0]["revision_sha256"] == first["revision_sha256"]


def test_corrupt_or_missing_intermediate_event_fails_closed(tmp_path):
    library, original = _created(tmp_path)
    first = _rename(library, original, "Mira Ravenwood")
    _rename(library, first, "Élise Ravenwood")
    version1, version2 = _rows(library, original["id"])
    original_bytes = version1.read_bytes()
    version1.write_bytes(b'{"schema":"invalid"}')
    with pytest.raises(ValueError):
        core.list_fictional(library)
    with pytest.raises(ValueError):
        history.history_fictional(library, original["id"])
    version1.write_bytes(original_bytes)
    version1.unlink()
    with pytest.raises(ValueError, match="interrompida"):
        core.list_fictional(library)
    assert version2.exists()


def test_unknown_or_symbolic_revision_file_fails_closed(tmp_path):
    library, original = _created(tmp_path)
    another = tmp_path / "outside.json"
    another.write_text("preserve", encoding="utf-8")
    imposter = library / f"{original['id']}.rename.000001.json"
    imposter.symlink_to(another)
    with pytest.raises(ValueError, match="symlink"):
        history.history_fictional(library, original["id"])
    imposter.unlink()
    illegal = library / f"{original['id']}.rename.1.json"
    illegal.write_bytes(b"bad")
    with pytest.raises(ValueError, match="inesperado"):
        core.list_fictional(library)
    assert another.read_text() == "preserve"


def test_malicious_bundle_duplicate_keys_sha_corruption_and_extra_field(tmp_path):
    library, original = _created(tmp_path)
    _rename(library, original, "Mira Ravenwood")
    bundle = tmp_path / "bundle.json"
    core.export_fictional(library, original["id"], bundle)
    body = json.loads(bundle.read_text(encoding="utf-8"))
    bad = tmp_path / "malicious.json"
    dest = tmp_path / "destination"
    altered = json.loads(json.dumps(body))
    altered["revisions"][0]["event"]["new_name"] = "Other Name"
    bad.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="Checksum"):
        core.import_fictional(dest, bad)
    assert not dest.exists()
    altered = json.loads(json.dumps(body))
    altered["revisions"][0]["event"]["uci_eligible"] = True
    bad.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="Campos"):
        core.import_fictional(dest, bad)
    assert not dest.exists()
    bad.write_bytes(b'{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="duplicada"):
        core.import_fictional(dest, bad)
    assert not dest.exists()


def test_broken_bundle_chain_prohibited_even_when_bundle_hash_recomputed(tmp_path):
    library, original = _created(tmp_path)
    first = _rename(library, original, "Mira Ravenwood")
    _rename(library, first, "Élise Ravenwood")
    file = tmp_path / "complete.json"
    core.export_fictional(library, original["id"], file)
    body = json.loads(file.read_text())
    body["revisions"][1]["event"]["previous_sha256"] = "f" * 64
    body["revisions"][1]["sha256"] = core._sha(body["revisions"][1]["event"])
    body["sha256"] = core._sha({key: value for key, value in body.items() if key != "sha256"})
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(body), encoding="utf-8")
    dest = tmp_path / "dest"
    with pytest.raises(ValueError, match="Cadeia"):
        core.import_fictional(dest, bad)
    assert not dest.exists()


def test_import_is_retriable_after_failure_with_valid_prefix(tmp_path, monkeypatch):
    library, original = _created(tmp_path)
    one = _rename(library, original, "Mira Ravenwood")
    _rename(library, one, "Élise Ravenwood")
    package = tmp_path / "package.json"
    core.export_fictional(library, original["id"], package)
    target = tmp_path / "dest"
    publish = core._publish_exclusive
    calls = 0
    def interrupted(path, raw):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated no space")
        return publish(path, raw)
    monkeypatch.setattr(core, "_publish_exclusive", interrupted)
    with pytest.raises(OSError, match="no space"):
        core.import_fictional(target, package)
    assert len(_rows(target, original["id"])) == 1
    monkeypatch.setattr(core, "_publish_exclusive", publish)
    assert core.import_fictional(target, package)["revision"] == 2
    assert len(_rows(target, original["id"])) == 2


def test_concurrent_stale_rename_one_wins_and_loser_cannot_append(tmp_path):
    library, original = _created(tmp_path)
    def work(name):
        return history.rename_fictional(
            library, original["id"], name, original["revision_sha256"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work, name) for name in ("Mira", "Élise")]
    good, rejected = [], []
    for future in futures:
        try:
            good.append(future.result())
        except ValueError as exc:
            rejected.append(str(exc))
    assert len(good) == len(rejected) == 1
    assert "obsoleta" in rejected[0]
    assert len(_rows(library, original["id"])) == 1
    assert core.list_fictional(library)[0]["display_name"] == good[0]["display_name"]


def test_cli_rename_and_history_and_portable_reimport(tmp_path, capsys):
    library, original = _created(tmp_path)
    assert main([
        "custom-player-rename", "--library", str(library),
        "--id", original["id"], "--name", "Élise Ravenwood",
        "--expected-head", original["revision_sha256"],
    ]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["revision"] == 1
    assert main([
        "custom-player-history", "--library", str(library),
        "--id", original["id"],
    ]) == 0
    hist = json.loads(capsys.readouterr().out)
    assert hist["history"][0]["previous_name"] == "Ava Ravenwood"
    assert hist["history"][0]["new_name"] == "Élise Ravenwood"
    assert hist["revision_sha256"] == updated["revision_sha256"]


@pytest.mark.parametrize("name", [
    "", "A", "a/b", "Mira\nInjected", "<script>hi</script>", "Mira  Ravenwood",
])
def test_rename_invalid_name_does_not_publish_revision(tmp_path, name):
    library, original = _created(tmp_path)
    with pytest.raises(ValueError):
        _rename(library, original, name)
    assert not _rows(library, original["id"])


def test_hard_limit_sixty_four_revisions_and_no_sixty_fifth(tmp_path):
    library, original = _created(tmp_path)
    previous = original
    for number in range(1, 65):
        previous = _rename(library, previous, f"Character {number}")
        assert previous["revision"] == number
    with pytest.raises(ValueError, match="máximo"):
        _rename(library, previous, "Character 65")
    assert len(_rows(library, original["id"])) == 64
    assert core.list_fictional(library)[0]["revision"] == 64
