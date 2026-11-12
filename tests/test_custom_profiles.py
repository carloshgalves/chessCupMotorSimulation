"""#63 slice 1: local fictional identities cannot mutate the canonical roster."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import socket
import subprocess
import unicodedata

import pytest

from chesssim import custom_profiles as profiles
from chesssim.catalog import load_roster
from chesssim.cli import main


def _new(tmp_path, name="Ava Ravenwood"):
    root = tmp_path / "custom"
    created = profiles.create_fictional(root, name)
    return root, created


def _record(root, identity):
    return root / (identity + ".json")


def test_two_namesakes_keep_independent_ids_and_never_touch_roster(tmp_path):
    original = tuple(load_roster())
    root, one = _new(tmp_path, "Simon Ravenwood")
    other = profiles.create_fictional(root, "Simon Ravenwood")
    historical = profiles.create_fictional(root, "Mikhail Tal")
    assert len({one["id"], other["id"], historical["id"]}) == 3
    assert all(item["id"].startswith("user-") for item in
               (one, other, historical))
    assert one["display_name"] == other["display_name"]
    assert all(item["status"] == profiles.STATUS for item in
               (one, other, historical))
    assert all(item["uci_eligible"] is False and
               item["historical_fidelity_validated"] is False
               for item in profiles.list_fictional(root))
    assert tuple(load_roster()) == original
    assert len(load_roster()) == 64


def test_export_import_preserves_stable_identity_and_exact_semantics(tmp_path):
    source, created = _new(tmp_path)
    original = _record(source, created["id"]).read_bytes()
    exported = tmp_path / "portable.json"
    exported_result = profiles.export_fictional(source, created["id"], exported)
    assert exported_result["profile_sha256"] == created["profile_sha256"]
    assert exported.read_bytes() == original
    target = tmp_path / "imported"
    imported = profiles.import_fictional(target, exported)
    assert imported["operation"] == "IMPORTED_METADATA_ONLY"
    assert imported["id"] == created["id"]
    assert profiles.list_fictional(target)[0]["id"] == created["id"]
    assert _record(target, created["id"]).read_bytes() == original
    repeated = profiles.import_fictional(target, exported)
    assert repeated["operation"] == "ALREADY_PRESENT"
    assert len(profiles.list_fictional(target)) == 1


def test_retire_is_exclusive_idempotent_and_does_not_delete_record(tmp_path):
    root, item = _new(tmp_path)
    record = _record(root, item["id"])
    before = record.read_bytes()
    retired = profiles.retire_fictional(root, item["id"])
    assert retired["operation"] == "RETIRED_METADATA_ONLY"
    assert retired["status"] == "RETIRED_METADATA_ONLY"
    assert record.read_bytes() == before
    assert profiles.retire_fictional(root, item["id"])["operation"] == "ALREADY_RETIRED"
    assert profiles.list_fictional(root)[0]["status"] == "RETIRED_METADATA_ONLY"
    portable = tmp_path / "exp.json"
    profiles.export_fictional(root, item["id"], portable)
    imported = profiles.import_fictional(root, portable)
    assert imported["operation"] == "ALREADY_PRESENT"
    assert imported["status"] == "RETIRED_METADATA_ONLY", (
        "Reimport must not silently reactivate a retired identity")


def test_reimport_with_same_id_but_changed_profile_must_fail(tmp_path):
    root, item = _new(tmp_path)
    forged = json.loads(_record(root, item["id"]).read_text())
    forged["profile"]["display_name"] = "Mira Ravenwood"
    forged["sha256"] = profiles._sha(forged["profile"])
    outside = tmp_path / "mutated.json"
    outside.write_text(json.dumps(forged), encoding="utf-8")
    initial = _record(root, item["id"]).read_bytes()
    with pytest.raises(FileExistsError, match="divergente"):
        profiles.import_fictional(root, outside)
    assert _record(root, item["id"]).read_bytes() == initial


@pytest.mark.parametrize("field,value", [
    ("status", "PLAYABLE"),
    ("kind", "real-player-experimental"),
    ("uci_eligible", True),
    ("historical_fidelity_validated", True),
    ("identity_basis", "TRUSTED_HISTORICAL"),
    ("created_utc", "2000-02-31T01:02:03Z"),
    ("display_name", "<script>broken</script>"),
    ("id", "fischer"),
    ("id", "../user-" + "f" * 32),
])
def test_import_rejects_forged_status_type_or_identity_without_writing(
    tmp_path, field, value
):
    root, created = _new(tmp_path)
    raw = json.loads(_record(root, created["id"]).read_text())
    raw["profile"][field] = value
    raw["sha256"] = profiles._sha(raw["profile"])
    file = tmp_path / "attempt.json"
    file.write_text(json.dumps(raw), encoding="utf-8")
    other = tmp_path / "other-library"
    with pytest.raises(ValueError):
        profiles.import_fictional(other, file)
    assert not other.exists()


@pytest.mark.parametrize("bad", [
    "", "a", " ", "A  B", " A", "A ", "A/B", "A\\B",
    "A\nB", "A\tB", "<img>", "A\u202eB", "A\u200bB",
    "12345", "A" * 81, unicodedata.normalize("NFD", "Élise"),
])
def test_creation_refuses_unsafe_names_without_creating_library(tmp_path, bad):
    root = tmp_path / "new-root"
    with pytest.raises(ValueError):
        profiles.create_fictional(root, bad)
    assert not root.exists()


def test_full_unicode_names_and_punctuation_normalized_without_aliases(tmp_path):
    root, entry = _new(tmp_path, "Élise (III) D’Ávila")
    assert entry["display_name"] == "Élise (III) D’Ávila"
    assert profiles.list_fictional(root)[0]["display_name"] == entry["display_name"]


def test_import_rejects_checksum_corruption_duplicate_json_keys_and_size(tmp_path):
    root, entry = _new(tmp_path)
    doc = _record(root, entry["id"]).read_bytes()
    destination = tmp_path / "another-library"
    file = tmp_path / "file.json"
    file.write_bytes(doc.replace(b"Ravenwood", b"Brokenbar"))
    with pytest.raises(ValueError, match="SHA-256"):
        profiles.import_fictional(destination, file)
    assert not destination.exists()
    file.write_bytes(b'{"schema":"x","schema":"y"}')
    with pytest.raises(ValueError, match="duplicada"):
        profiles.import_fictional(destination, file)
    file.write_bytes(b"x" * (profiles.MAX_RECORD_BYTES + 1))
    with pytest.raises(ValueError, match="excessivo"):
        profiles.import_fictional(destination, file)
    assert not destination.exists()


def test_import_rejects_symbolic_file_and_symbolic_ancestor(tmp_path):
    root, entry = _new(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(_record(root, entry["id"]))
    target = tmp_path / "target"
    with pytest.raises(ValueError, match="symlink"):
        profiles.import_fictional(target, alias)
    original = tmp_path / "actual"
    original.mkdir()
    root_alias = tmp_path / "rootalias"
    root_alias.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        profiles.create_fictional(root_alias, "Robin Ravenwood")
    assert not list(original.iterdir())


def test_retirement_marker_corruption_and_symbolic_link_fail_closed(tmp_path):
    root, entry = _new(tmp_path)
    profiles.retire_fictional(root, entry["id"])
    retired = root / f"{entry['id']}.retired.json"
    retired.write_text('{"status":"ACTIVE"}', encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistente"):
        profiles.list_fictional(root)
    retired.unlink()
    unrelated = tmp_path / "outside.json"
    unrelated.write_text("sensitive", encoding="utf-8")
    retired.symlink_to(unrelated)
    with pytest.raises(ValueError, match="symlink"):
        profiles.list_fictional(root)
    assert unrelated.read_text() == "sensitive"


def test_import_races_are_idempotent_without_replacing_record(tmp_path):
    a, created = _new(tmp_path)
    source = tmp_path / "record.json"
    source.write_bytes(_record(a, created["id"]).read_bytes())
    target = tmp_path / "target"
    # The two threads may race on the same target directory and hardlink;
    # only one identity may ever appear, and no byte overwrites are allowed.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(profiles.import_fictional, target, source)
                   for _ in range(2)]
    results = [f.result() for f in futures]
    assert sorted(x["operation"] for x in results) == [
        "ALREADY_PRESENT", "IMPORTED_METADATA_ONLY"]
    assert len(profiles.list_fictional(target)) == 1
    assert _record(target, created["id"]).read_bytes() == source.read_bytes()


def test_export_existing_destination_cannot_be_overwritten(tmp_path):
    root, entry = _new(tmp_path)
    dest = tmp_path / "export.json"
    dest.write_bytes(b"sentinel")
    with pytest.raises(FileExistsError):
        profiles.export_fictional(root, entry["id"], dest)
    assert dest.read_bytes() == b"sentinel"
    with pytest.raises(ValueError, match="fora da biblioteca"):
        profiles.export_fictional(root, entry["id"], root / "export.json")


def test_failed_hardlink_publication_leaves_no_partial_final_path(
    tmp_path, monkeypatch
):
    root = tmp_path / "custom"
    original = profiles.os.link
    def refuse_link(src, dest, *args, **kwargs):
        raise OSError("hard links unavailable")
    monkeypatch.setattr(profiles.os, "link", refuse_link)
    with pytest.raises(OSError, match="hard links"):
        profiles.create_fictional(root, "Ava Ravenwood")
    assert profiles.list_fictional(root) == []
    assert [item.name for item in root.iterdir()] == [".custom-player.lock"], (
        "failed publication may leave only the persistent advisory lock, not a temp or profile")
    monkeypatch.setattr(profiles.os, "link", original)


def test_full_capacity_checks_do_not_change_existing_record(tmp_path, monkeypatch):
    root, entry = _new(tmp_path)
    original = _record(root, entry["id"]).read_bytes()
    monkeypatch.setattr(profiles, "MAX_LIBRARY_PROFILES", 1)
    with pytest.raises(ValueError, match="cheia"):
        profiles.create_fictional(root, "Robin Ravenwood")
    assert _record(root, entry["id"]).read_bytes() == original


def test_no_network_no_engines_and_roster_remains_canonical(tmp_path, monkeypatch):
    root = tmp_path / "library"
    def fail(*args, **kwargs):
        raise AssertionError("network/executable forbidden by this feature")
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(subprocess, "run", fail)
    entry = profiles.create_fictional(root, "Ava Ravenwood")
    export = tmp_path / "portable.json"
    profiles.export_fictional(root, entry["id"], export)
    profiles.import_fictional(tmp_path / "imported", export)
    assert len(load_roster()) == 64


def test_cli_create_list_export_import_retire_smoke(tmp_path, capsys):
    root = tmp_path / "profiles"
    assert main(["custom-player-create", "--library", str(root),
                 "--name", "Ava Ravenwood"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == profiles.STATUS
    assert main(["custom-player-list", "--library", str(root)]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows["source"] == "LOCAL_METADATA_ONLY_NOT_ROSTER"
    assert rows["profiles"][0]["id"] == created["id"]
    dest = tmp_path / "portable.json"
    assert main(["custom-player-export", "--library", str(root),
                 "--id", created["id"], "--out", str(dest)]) == 0
    capsys.readouterr()
    target = tmp_path / "second"
    assert main(["custom-player-import", "--library", str(target),
                 "--file", str(dest)]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["id"] == created["id"]
    assert main(["custom-player-retire", "--library", str(root),
                 "--id", created["id"]]) == 0
    retired = json.loads(capsys.readouterr().out)
    assert retired["status"] == "RETIRED_METADATA_ONLY"
    assert len(load_roster()) == 64


def test_restore_requires_valid_receipt_and_preserves_the_same_uuid(tmp_path):
    root, created = _new(tmp_path)
    record = _record(root, created["id"])
    original_bytes = record.read_bytes()
    assert profiles.restore_fictional(root, created["id"])["operation"] == "ALREADY_ACTIVE"
    profiles.retire_fictional(root, created["id"])
    restored = profiles.restore_fictional(root, created["id"])
    assert restored["operation"] == "RESTORED_METADATA_ONLY"
    assert restored["status"] == profiles.STATUS
    assert restored["id"] == created["id"]
    assert record.read_bytes() == original_bytes
    assert not (root / (created["id"] + ".retired.json")).exists()
    assert profiles.restore_fictional(root, created["id"])["operation"] == "ALREADY_ACTIVE"
    profiles.retire_fictional(root, created["id"])
    assert profiles.list_fictional(root)[0]["status"] == "RETIRED_METADATA_ONLY"
    assert len(load_roster()) == 64


def test_restore_rejects_forged_retirement_marker(tmp_path):
    root, created = _new(tmp_path)
    profiles.retire_fictional(root, created["id"])
    marker = root / (created["id"] + ".retired.json")
    marker.write_text('{"schema":"unexpected"}', encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistente"):
        profiles.restore_fictional(root, created["id"])
    assert marker.exists(), "corrupt retirement marker not silently deleted"


def test_concurrent_creation_respects_capacity_under_library_lock(
    tmp_path, monkeypatch
):
    root = tmp_path / "same"
    monkeypatch.setattr(profiles, "MAX_LIBRARY_PROFILES", 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(profiles.create_fictional, root, name)
                   for name in ("Ava Ravenwood", "Robin Ravenwood")]
    successes = []
    failures = []
    for future in futures:
        try:
            successes.append(future.result())
        except ValueError as exc:
            failures.append(str(exc))
    assert len(successes) == 1
    assert len(failures) == 1
    assert "cheia" in failures[0]
    assert len(profiles.list_fictional(root)) == 1


def test_lock_symlink_cannot_redirect_writes_to_external_file(tmp_path):
    root = tmp_path / "custom"
    root.mkdir()
    outside = tmp_path / "external"
    outside.write_bytes(b"safe sentinel")
    (root / ".custom-player.lock").symlink_to(outside)
    with pytest.raises(OSError):
        profiles.create_fictional(root, "Ava Ravenwood")
    assert not list(root.glob("user-*.json"))
    assert outside.read_bytes() == b"safe sentinel"


def test_concurrent_retire_and_restore_serialize_without_corrupt_marker(
    tmp_path
):
    root, created = _new(tmp_path)
    # Concurrent retirement requests are serialized and idempotent;
    # the second observes retired, never a partial marker.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(profiles.retire_fictional, root, created["id"])
                   for _ in range(2)]
    statuses = [f.result()["operation"] for f in futures]
    assert sorted(statuses) == ["ALREADY_RETIRED", "RETIRED_METADATA_ONLY"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(profiles.restore_fictional, root, created["id"])
                   for _ in range(2)]
    statuses = [f.result()["operation"] for f in futures]
    assert sorted(statuses) == ["ALREADY_ACTIVE", "RESTORED_METADATA_ONLY"]
    assert profiles.list_fictional(root)[0]["status"] == profiles.STATUS
