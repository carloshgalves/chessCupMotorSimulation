"""#27 crash-consistent persistence: deterministic fault injection, no UCI required.

These tests verify *ordering and failure propagation*. They do not simulate a
real SSD power-loss or certify a storage device's volatile write cache.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from chesssim import matches, tournament


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory sync only")
def test_durable_atomic_commit_fsyncs_file_before_rename_and_directories_after(
    tmp_path, monkeypatch
):
    target = tmp_path / "nested" / "summary.json"
    target.parent.mkdir()
    events = []
    real_fsync = os.fsync
    real_replace = Path.replace

    def traced_fsync(fd):
        kind = "data" if stat.S_ISREG(os.fstat(fd).st_mode) else "directory"
        events.append(kind)
        return real_fsync(fd)

    def traced_replace(self, other):
        assert Path(self).parent == Path(other).parent
        events.append("replace")
        return real_replace(self, other)

    monkeypatch.setattr(matches.os, "fsync", traced_fsync)
    monkeypatch.setattr(Path, "replace", traced_replace)
    matches._atomic_text(target, "confirmed\n", durable=True)
    assert target.read_text(encoding="utf-8") == "confirmed\n"
    assert events[0] == "data"
    assert events[1] == "replace"
    assert all(event == "directory" for event in events[2:])
    assert len(events) >= 3, "parent directory entry must be synced"
    assert not list(target.parent.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory sync only")
def test_durable_write_survives_new_parent_directory_structure(tmp_path, monkeypatch):
    events = []
    real_fsync = os.fsync

    def traced_fsync(fd):
        kind = "file" if stat.S_ISREG(os.fstat(fd).st_mode) else "directory"
        events.append(kind)
        return real_fsync(fd)

    monkeypatch.setattr(matches.os, "fsync", traced_fsync)
    target = tmp_path / "new" / "attempt" / "game.pgn"
    matches._atomic_text(target, "valid\n", durable=True)
    assert target.read_text(encoding="utf-8") == "valid\n"
    # New attempt directories need their ancestors' metadata synced too.
    assert events[0] == "file"
    assert events.count("directory") >= 3


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory sync only")
def test_file_fsync_failure_preserves_old_checkpoint_and_removes_temporary(
    tmp_path, monkeypatch
):
    target = tmp_path / "tournament.json"
    target.write_text("old", encoding="utf-8")
    def interrupted_fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("injected data sync failure")
        raise AssertionError("must not sync directory before data")

    monkeypatch.setattr(matches.os, "fsync", interrupted_fsync)
    with pytest.raises(OSError, match="injected data sync failure"):
        matches._atomic_text(target, "new", durable=True)
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".tournament.json.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory sync only")
def test_directory_fsync_failure_does_not_claim_committed_durability(
    tmp_path, monkeypatch
):
    target = tmp_path / "summary.json"
    target.write_text("old", encoding="utf-8")
    real_fsync = os.fsync
    def interrupted_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory sync failure")
        return real_fsync(fd)

    monkeypatch.setattr(matches.os, "fsync", interrupted_fsync)
    with pytest.raises(OSError, match="injected directory sync failure"):
        matches._atomic_text(target, "new", durable=True)
    # POSIX rename happened; callers must know the new bytes *may* be visible
    # even though the directory commit was not confirmed durable.
    assert target.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".summary.json.*.tmp"))


def test_fast_partial_checkpoint_preserves_existing_atomic_contract(
    tmp_path, monkeypatch
):
    target = tmp_path / "jogo_1.partial.pgn"
    target.write_text("earlier", encoding="utf-8")
    def forbidden_sync(fd):
        raise AssertionError("fast per-ply checkpoint unexpectedly called fsync")
    monkeypatch.setattr(matches.os, "fsync", forbidden_sync)
    matches._atomic_text(target, "most recent", durable=False)
    assert target.read_text(encoding="utf-8") == "most recent"


def test_tournament_save_selects_posix_durability(tmp_path, monkeypatch):
    calls = []
    def probe(path, contents, *, durable=False):
        calls.append((path, contents, durable))
    monkeypatch.setattr(tournament, "_atomic_text", probe)
    tournament._save(tmp_path, {"status": "RUNNING"})
    assert len(calls) == 1
    assert calls[0][0] == tmp_path / "tournament.json"
    assert json.loads(calls[0][1]) == {"status": "RUNNING"}
    assert calls[0][2] is (os.name == "posix")


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory sync only")
def test_stop_marker_is_synced_and_clear_commits_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "load_tournament", lambda run: {})
    monkeypatch.setattr(tournament, "tournament_status", lambda run: {
        "stop_requested": (Path(run) / tournament.STOP_FILE).exists()})
    events = []
    real_fsync = os.fsync
    real_tree = tournament._fsync_directory_tree

    def observed_fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            events.append("marker-data")
        return real_fsync(fd)

    def observed_tree(path):
        events.append("directory-chain")
        return real_tree(path)

    monkeypatch.setattr(tournament.os, "fsync", observed_fsync)
    monkeypatch.setattr(tournament, "_fsync_directory_tree", observed_tree)
    assert tournament.request_stop(tmp_path, reason="controlled shutdown")["stop_requested"]
    assert events[:2] == ["marker-data", "directory-chain"]
    first_bytes = (tmp_path / tournament.STOP_FILE).read_bytes()
    tournament.request_stop(tmp_path, reason="other reason")
    assert (tmp_path / tournament.STOP_FILE).read_bytes() == first_bytes
    assert tournament.clear_stop(tmp_path)["stop_requested"] is False
    assert events[-1] == "directory-chain"


@pytest.mark.skipif(os.name != "posix", reason="POSIX hardlink publication only")
def test_exclusive_flag_is_published_only_after_data_sync(tmp_path, monkeypatch):
    path = tmp_path / ".stop-requested.json"
    events = []
    real_fsync = os.fsync
    real_link = os.link
    def traced_fsync(fd):
        events.append("file-fsync" if stat.S_ISREG(os.fstat(fd).st_mode) else "dir-fsync")
        return real_fsync(fd)
    def traced_link(src, dst):
        assert Path(src).parent == Path(dst).parent
        assert not path.exists(), "stop flag must not exist during initial write"
        events.append("publish")
        return real_link(src, dst)
    monkeypatch.setattr(matches.os, "fsync", traced_fsync)
    monkeypatch.setattr(matches.os, "link", traced_link)
    matches._publish_exclusive_text(path, '{"reason":"operator"}\n')
    assert events[:3] == ["file-fsync", "publish", "dir-fsync"]
    assert path.read_text(encoding="utf-8") == '{"reason":"operator"}\n'
    assert not list(tmp_path.glob("..stop-requested.json.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX hardlink publication only")
def test_exclusive_flag_data_sync_failure_never_publishes_partial_marker(
    tmp_path, monkeypatch
):
    path = tmp_path / ".stop-requested.json"
    def fail_on_sync(fd):
        raise OSError("injected stop data fsync failure")
    monkeypatch.setattr(matches.os, "fsync", fail_on_sync)
    with pytest.raises(OSError, match="stop data"):
        matches._publish_exclusive_text(path, '{"reason":"operator"}\n')
    assert not path.exists()
    assert not list(tmp_path.glob("..stop-requested.json.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX hardlink publication only")
def test_exclusive_flag_is_idempotent_and_does_not_replace_existing(tmp_path):
    path = tmp_path / ".stop-requested.json"
    path.write_text("first", encoding="utf-8")
    with pytest.raises(FileExistsError):
        matches._publish_exclusive_text(path, "second")
    assert path.read_text(encoding="utf-8") == "first"
    assert not list(tmp_path.glob("..stop-requested.json.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX hardlink publication only")
def test_stop_request_refuses_existing_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "load_tournament", lambda run: {})
    outside = tmp_path / "elsewhere.json"
    path = tmp_path / tournament.STOP_FILE
    path.symlink_to(outside)  # dangling symlink must also be rejected
    with pytest.raises(ValueError, match="not a regular file"):
        tournament.request_stop(tmp_path, reason="stop")
    assert not outside.exists()
    assert path.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="POSIX hardlink publication only")
def test_failed_postpublication_sync_is_retryable_without_replacing_stop(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tournament, "load_tournament", lambda run: {})
    monkeypatch.setattr(tournament, "tournament_status", lambda run: {
        "stop_requested": (Path(run) / tournament.STOP_FILE).is_file()})
    original_sync = tournament._fsync_directory_tree
    calls = []
    def intermittent(path):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("injected stop directory sync failure")
        return original_sync(path)
    monkeypatch.setattr(tournament, "_fsync_directory_tree", intermittent)
    monkeypatch.setattr(matches, "_fsync_directory_tree", intermittent)
    with pytest.raises(OSError, match="directory sync"):
        tournament.request_stop(tmp_path, reason="first request")
    marker = tmp_path / tournament.STOP_FILE
    original = marker.read_bytes()
    assert json.loads(original)["reason"] == "first request"
    assert tournament.request_stop(tmp_path, reason="second request")["stop_requested"]
    assert marker.read_bytes() == original
    assert len(calls) == 2
