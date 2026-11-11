"""Adversarial Git hygiene regressions; no real credentials or private data."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_git_artifacts", ROOT / "scripts" / "audit_git_artifacts.py")
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True).stdout


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "QA Example")
    git(repo, "config", "user.email", "qa@example.invalid")
    (repo / "README.md").write_text("Public chess project\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "initial")
    return repo


def tracked(repo):
    return audit.scan(repo, "tracked")


def test_clean_index_and_untouched_working_tree(checkout):
    # Scan index objects, never accidentally scan uncommitted data in the worktree.
    (checkout / "README.md").write_text(
        "ghp_" + "A" * 36, encoding="utf-8")
    result = tracked(checkout)
    assert result["status"] == "PASS"
    assert result["finding_count"] == 0
    assert result["objects_examined"] == 1


def test_staged_secret_even_when_worktree_was_cleaned(checkout):
    token = "ghp_" + "B" * 37
    (checkout / "README.md").write_text(token, encoding="utf-8")
    git(checkout, "add", "README.md")
    (checkout / "README.md").write_text("Secret cleaned in worktree", encoding="utf-8")
    result = tracked(checkout)
    assert result["status"] == "FAIL"
    assert {x["code"] for x in result["findings"]} == {"github_token"}
    encoded = json.dumps(result)
    assert token not in encoded
    assert "README.md" not in encoded
    assert result["findings"][0]["path_hash"] == audit._identity(b"README.md")


@pytest.mark.parametrize(("file_name", "kind"), [
    (".env", "environment_secret_file"),
    (".env.production", "environment_secret_file"),
    ("games/match.pgn", "prohibited_payload_extension"),
    ("runs/simulation/output.json", "local_artifact_dir"),
    ("private/cache.txt", "local_artifact_dir"),
    ("output/database.sqlite3", "prohibited_payload_extension"),
    ("id_ed25519", "local_identity_file"),
])
def test_forbidden_artifact_paths_fail_closed(checkout, file_name, kind):
    path = checkout / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder", encoding="utf-8")
    git(checkout, "add", "-f", file_name)
    result = tracked(checkout)
    assert kind in {x["code"] for x in result["findings"]}
    assert file_name not in json.dumps(result)


def test_nonsecret_example_config_is_allowed(checkout):
    (checkout / ".env.example").write_text(
        "TOKEN=SET_ME_IN_LOCAL_ENV\n", encoding="utf-8")
    git(checkout, "add", "-f", ".env.example")
    assert tracked(checkout)["status"] == "PASS"


def test_binary_disguised_as_text_and_oversized_blob(checkout, monkeypatch):
    (checkout / "foo.json").write_bytes(b"\x7fELF" + b"\x00" * 6)
    git(checkout, "add", "foo.json")
    assert "unexpected_binary_payload" in {
        f["code"] for f in tracked(checkout)["findings"]}
    monkeypatch.setattr(audit, "MAX_BLOB_BYTES", 20)
    (checkout / "foo.json").write_text("x" * 50, encoding="utf-8")
    git(checkout, "add", "foo.json")
    assert "oversized_blob" in {f["code"] for f in tracked(checkout)["findings"]}


def test_portraits_whitelisted_only_if_real_jpeg(checkout):
    folder = checkout / "src/chesssim/data/history/portraits"
    folder.mkdir(parents=True)
    image = folder / "example.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    git(checkout, "add", "-f", image.relative_to(checkout).as_posix())
    assert tracked(checkout)["status"] == "PASS"
    image.write_bytes(b"not a jpeg")
    git(checkout, "add", "-f", image.relative_to(checkout).as_posix())
    assert "portrait_not_jpeg" in {x["code"] for x in tracked(checkout)["findings"]}


def test_allowed_jpeg_cannot_hide_a_token_and_rogue_image_is_rejected(checkout):
    folder = checkout / "src/chesssim/data/history/portraits"
    folder.mkdir(parents=True)
    image = folder / "example.jpg"
    token = "ghp_" + "T" * 36
    image.write_bytes(b"\xff\xd8\xff\xe0" + token.encode() + b"\x00")
    git(checkout, "add", "src/chesssim/data/history/portraits/example.jpg")
    result = tracked(checkout)
    assert "github_token" in {x["code"] for x in result["findings"]}
    assert token not in json.dumps(result)
    rogue = checkout / "public.jpg"
    rogue.write_bytes(b"\xff\xd8\xff\xe0" + b"fake bytes")
    git(checkout, "add", "public.jpg")
    assert "unexpected_binary_payload" in {
        x["code"] for x in tracked(checkout)["findings"]}


def test_symlink_in_index_is_rejected(checkout):
    (checkout / "outside").symlink_to("../private-file")
    git(checkout, "add", "outside")
    assert "tracked_symlink" in {f["code"] for f in tracked(checkout)["findings"]}


def test_history_finds_deleted_payload_and_secret_without_printing_them(checkout):
    token = "github_pat_" + "R" * 70
    candidate = checkout / "notes.pgn"
    candidate.write_text(token, encoding="utf-8")
    git(checkout, "add", "-f", "notes.pgn")
    git(checkout, "commit", "-qm", "historical unsafe blob")
    candidate.unlink()
    git(checkout, "add", "-u")
    git(checkout, "commit", "-qm", "remove artifact")
    assert tracked(checkout)["status"] == "PASS", "clean HEAD cannot certify history"
    report = audit.scan(checkout, "history")
    codes = {x["code"] for x in report["findings"]}
    assert "prohibited_payload_extension" in codes
    assert "github_fine_grained_token" in codes
    assert report["commits_seen"] == 3
    assert report["historical_paths_examined"] >= 2
    assert token not in json.dumps(report)
    assert "notes.pgn" not in json.dumps(report)
    assert len(report["findings"]) >= 2


def test_synthetic_private_key_marker_and_absolute_home_path(checkout):
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    personal = "/home/" + "myrealperson/Downloads"
    (checkout / "README.md").write_text(
        marker + "\n" + personal + "/db.txt\n", encoding="utf-8")
    git(checkout, "add", "README.md")
    codes = {f["code"] for f in tracked(checkout)["findings"]}
    assert {"private_key", "absolute_home_path"} <= codes
    out = json.dumps(tracked(checkout))
    assert personal not in out and marker not in out


def test_unavailable_repo_is_error_not_clean_state(tmp_path):
    with pytest.raises(audit.AuditError):
        tracked(tmp_path)


def test_cli_never_writes_report_inside_repository(checkout, capsys):
    target = checkout / "evidence.json"
    assert audit.main(["--repo", str(checkout), "--output", str(target)]) == 2
    assert not target.exists()
    out = capsys.readouterr().out
    assert '"status": "ERROR"' in out
    assert '"status": "PASS"' not in out


def test_bad_index_stage_is_not_read_as_clean(checkout):
    # This probes the fail-closed parsing rule without manufacturing conflicts.
    with pytest.raises(audit.AuditError):
        original = audit._git
        def conflicting(root, *args):
            if args[:2] == ("ls-files", "--stage"):
                return b"100644 " + b"a" * 40 + b" 2\tREADME.md\0"
            return original(root, *args)
        audit._git = conflicting
        try:
            tracked(checkout)
        finally:
            audit._git = original
