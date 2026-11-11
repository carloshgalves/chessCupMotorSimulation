#!/usr/bin/env python3
"""Local Git hygiene gate and redacted, opt-in history audit.

The default 'tracked' scan reads Git INDEX blobs, not possibly different working
tree files. The 'history' scan reads all reachable objects and historical paths.
No matching secret bytes, private paths, or original file names are printed.
Not a substitute for specialized secret scanners or a legal redistribution audit.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterator


MAX_BLOB_BYTES = 3 * 1024 * 1024
MAX_REPORT_FINDINGS = 120
BAD_EXTENSIONS = (
    ".pgn", ".pgn.gz", ".pgn.zst", ".pdf", ".zip", ".7z", ".rar",
    ".tar", ".tgz", ".tar.gz", ".bz2", ".xz", ".zst", ".exe", ".dll",
    ".so", ".dylib", ".pyd", ".a", ".lib", ".o", ".obj", ".wasm",
    ".bin", ".nnue", ".log", ".sqlite", ".sqlite3", ".db", ".pem",
    ".key", ".p12", ".pfx", ".kdbx",
)
BAD_DIRS = frozenset({
    "datasets", "calibracoes", "auditorias", "benchmarks", "reports",
    "runs", "engines", "books", "personalities", "private", "secrets",
    "credentials",
})
# Match real leaked credential syntaxes, not every documentation example
# containing 'token=' or 'password='.
SIGNATURES = {
    "private_key": re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "github_fine_grained_token": re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "google_api_key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    "openai_api_key": re.compile(
        rb"\bsk-(?:proj-|svcacct-)?[0-9a-zA-Z_-]{24,}\b"),
    "slack_token": re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{24,}\b"),
}
HOME_PATHS = (
    re.compile(rb"/home/(?!runner/|user/|username/|oai/)[A-Za-z0-9._-]{3,}/"),
    re.compile(rb"(?i)\b[A-Z]:\\Users\\(?!User\\|Public\\|Default\\)[^\\\r\n]{3,}\\"),
)
BINARY_PREFIXES = (
    b"\x7fELF", b"MZ", b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07", b"\xfd7zXZ", b"\x28\xb5\x2f\xfd",
)


class AuditError(RuntimeError):
    """Git cannot be completely inspected; the gate must fail closed."""


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("git unavailable or command timed out") from exc
    if result.returncode:
        raise AuditError("git command failed: " + args[0])
    return result.stdout


def _identity(data: bytes) -> str:
    return sha256(data).hexdigest()[:16]


def _path_rules(path: bytes) -> set[str]:
    # NUL-delimited Git output means filenames with newlines are still safe.
    name = path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
    lower = name.casefold()
    segments = PurePosixPath(lower).parts
    found = set()
    if not segments or lower.startswith("/") or ".." in segments:
        found.add("unsafe_path")
    if any(s in BAD_DIRS or s.startswith("partidas_chesssim") for s in segments[:-1]):
        found.add("local_artifact_dir")
    if any(lower.endswith(ext) for ext in BAD_EXTENSIONS):
        found.add("prohibited_payload_extension")
    leaf = segments[-1] if segments else ""
    if ((leaf == ".env" or leaf.startswith(".env.")) and leaf != ".env.example"):
        found.add("environment_secret_file")
    if leaf in {"id_rsa", "id_ed25519", "id_ecdsa", "known_hosts"}:
        found.add("local_identity_file")
    if re.search(r"(^|/)(?:users|home)/[^/]+/(?:documents|desktop|downloads)/", lower):
        found.add("personal_directory_path")
    if not name.isascii():
        # Non-ASCII filenames are legitimate; this is not a finding.
        pass
    return found


def _content_rules(path: bytes, content: bytes, total_size: int) -> set[str]:
    found = set()
    if total_size > MAX_BLOB_BYTES:
        found.add("oversized_blob")
        return found
    # Search credential signatures even inside allowed JPEG metadata. Early
    # binary returns otherwise let a token embedded in EXIF bypass the gate.
    for code, regex in SIGNATURES.items():
        if regex.search(content):
            found.add(code)
    if any(regex.search(content) for regex in HOME_PATHS):
        found.add("absolute_home_path")
    name = path.decode("utf-8", "surrogateescape").casefold()
    portrait = (name.startswith("src/chesssim/data/history/portraits/")
                and name.endswith(".jpg") and name.count("/") == 5)
    if portrait:
        if not content.startswith(b"\xff\xd8\xff"):
            found.add("portrait_not_jpeg")
        return found
    if (b"\x00" in content or content.startswith(
            (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")) or
            any(content.startswith(sig) for sig in BINARY_PREFIXES)):
        found.add("unexpected_binary_payload")
    return found


@contextmanager
def _blobs(root: Path) -> Iterator[object]:
    """Git batch reader: bounded memory, streaming even for oversized objects."""
    try:
        process = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise AuditError("unable to open Git object reader") from exc
    try:
        if process.stdin is None or process.stdout is None:
            raise AuditError("Git object pipes unavailable")

        def reader(oid: bytes) -> tuple[str, bytes, int]:
            if not re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", oid):
                raise AuditError("invalid object identifier")
            process.stdin.write(oid + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3 or header[0] != oid:
                raise AuditError("invalid Git object header")
            kind = header[1].decode("ascii")
            try:
                size = int(header[2])
            except ValueError as exc:
                raise AuditError("invalid Git object size") from exc
            if size < 0:
                raise AuditError("invalid Git object size")
            remaining = size
            chunks = []
            while remaining:
                chunk = process.stdout.read(min(remaining, 65536))
                if not chunk:
                    raise AuditError("truncated Git object")
                if size <= MAX_BLOB_BYTES:
                    chunks.append(chunk)
                remaining -= len(chunk)
            if process.stdout.read(1) != b"\n":
                raise AuditError("malformed Git object terminator")
            return kind, b"".join(chunks), size

        yield reader
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _tracked(root: Path) -> list[tuple[bytes, bytes, bytes]]:
    entries = []
    for entry in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        try:
            metadata, path = entry.split(b"\t", 1)
            mode, oid, stage = metadata.split()
        except ValueError as exc:
            raise AuditError("invalid index entry") from exc
        if stage != b"0":
            raise AuditError("unmerged index entry")
        entries.append((path, oid, mode))
    return entries


def _history(root: Path) -> tuple[list[tuple[bytes, bytes, bytes]], int]:
    objects = []
    for line in _git(root, "rev-list", "--objects", "--all").splitlines():
        oid, _, path = line.partition(b" ")
        objects.append((path, oid, b"historical"))
    count_raw = _git(root, "rev-list", "--all", "--count").strip()
    try:
        commits = int(count_raw)
    except ValueError as exc:
        raise AuditError("invalid Git history count") from exc
    return objects, commits


def scan(root: Path, scope: str) -> dict:
    if scope not in {"tracked", "history"}:
        raise ValueError("unknown scan scope")
    root = root.resolve()
    if scope == "history":
        entries, commit_count = _history(root)
        # Historical names are evaluated separately, including names of
        # deleted paths or paths reused by different blobs in later commits.
        historical_names = [
            name for name in _git(root, "log", "--all",
                                  "--pretty=format:", "--name-only", "-z").split(b"\0")
            if name and name.strip()
        ]
    else:
        entries = _tracked(root)
        commit_count = None
        historical_names = []
    findings = []
    findings_count = 0
    dedupe = set()

    def record(code: str, path: bytes, oid: bytes | None = None) -> None:
        nonlocal findings_count
        fingerprint = _identity(path)
        key = (code, fingerprint, oid if code not in {
            "unsafe_path", "local_artifact_dir", "prohibited_payload_extension",
            "environment_secret_file", "local_identity_file",
            "personal_directory_path"} else None)
        if key in dedupe:
            return
        dedupe.add(key)
        findings_count += 1
        if len(findings) < MAX_REPORT_FINDINGS:
            findings.append({
                "code": code, "path_hash": fingerprint,
                "object_prefix": oid.hex()[:12] if oid else None,
            })

    for path in historical_names:
        for code in sorted(_path_rules(path)):
            record(code, path)
    blobs = 0
    with _blobs(root) as get_blob:
        for path, oid, mode in entries:
            # rev-list --objects emits commits and trees without a path.
            # They are Git metadata, not empty-named tracked files.
            if path:
                for code in sorted(_path_rules(path)):
                    record(code, path)
            elif scope == "tracked":
                raise AuditError("tracked index entry has an empty name")
            if mode == b"120000":
                record("tracked_symlink", path, oid)
            kind, content, size = get_blob(oid)
            if kind != "blob":
                continue
            blobs += 1
            for code in sorted(_content_rules(path, content, size)):
                record(code, path, oid)
    return {
        "schema": "chesssim-git-hygiene-v1", "scope": scope,
        "commits_seen": commit_count, "objects_examined": len(entries),
        "blobs_examined": blobs, "historical_paths_examined": len(historical_names),
        "finding_count": findings_count, "findings_truncated": findings_count > len(findings),
        "findings": findings,
        "status": "FAIL" if findings_count else "PASS",
        "limits": {"blob_bytes": MAX_BLOB_BYTES, "report_findings": MAX_REPORT_FINDINGS},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--scope", choices=("tracked", "history"), default="tracked")
    parser.add_argument("--output", type=Path, help="Write the same redacted JSON to a local file")
    args = parser.parse_args(argv)
    # Validate output destination before emitting PASS/FAIL: failures must not
    # leave misleading audit evidence, and reports stay outside Git.
    report_path = None
    if args.output:
        report_path = args.output.expanduser().resolve()
        root = args.repo.expanduser().resolve()
        if report_path == root or root in report_path.parents:
            print(json.dumps({
                "schema": "chesssim-git-hygiene-v1", "scope": args.scope,
                "status": "ERROR", "error_class": "UnsafeOutputPath",
            }))
            return 2
    try:
        report = scan(args.repo, args.scope)
        result = 1 if report["finding_count"] else 0
    except (AuditError, OSError) as exc:
        # Error messages from git may contain secret paths; do not echo them.
        report = {"schema": "chesssim-git-hygiene-v1", "scope": args.scope,
                  "status": "ERROR", "error_class": type(exc).__name__}
        result = 2
    encoded = json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2) + "\n"
    print(encoded, end="")
    if report_path:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(encoded, encoding="utf-8")
        except OSError:
            # Writing the report is part of this operation's contract.
            print('{"status":"ERROR","error_class":"ReportWriteError"}', file=sys.stderr)
            return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
