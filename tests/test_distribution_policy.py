"""Guard the documented third-party policy and repository distribution boundary."""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "THIRD_PARTY.md"


def _tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("This distribution-boundary check requires a Git checkout.")
    return [
        Path(item.decode("utf-8"))
        for item in result.stdout.split(b"\0")
        if item
    ]


def test_readme_and_usage_link_to_the_third_party_policy() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    usage = (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8")

    assert "[docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)" in readme
    assert "[auditoria de dependências, fontes e distribuição](THIRD_PARTY.md)" in usage


def test_policy_covers_every_directly_declared_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = project["project"]["dependencies"]
    build = project["build-system"]["requires"]
    development = project["project"]["optional-dependencies"]["dev"]
    policy = POLICY.read_text(encoding="utf-8")

    declared = runtime + build + development
    for requirement in declared:
        distribution = requirement.split(">=")[0].split("<")[0].strip()
        assert chr(96) + distribution + chr(96) in policy

    for license_id in ("GPL-3.0-or-later", "GPL-3.0", "MIT"):
        assert license_id in policy

    assert "transitivas" in policy
    assert "SBOM" in policy


def test_gitignore_excludes_external_payload_extensions() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    required = (
        "*.pgn",
        "*.pgn.gz",
        "*.pgn.zst",
        "*.zip",
        "*.7z",
        "*.rar",
        "*.tar",
        "*.tar.gz",
        "*.tgz",
        "*.bz2",
        "*.xz",
        "*.zst",
        "*.exe",
        "*.dll",
        "*.so",
        "*.dylib",
        "*.pyd",
        "*.a",
        "*.lib",
        "*.o",
        "*.obj",
        "*.wasm",
        "*.bin",
        "*.nnue",
    )
    for pattern in required:
        assert pattern in ignored


def test_no_external_engine_or_corpus_payload_is_tracked() -> None:
    paths = _tracked_paths()
    forbidden_suffixes = {
        ".pgn",
        ".pdf",
        ".zip",
        ".7z",
        ".rar",
        ".tar",
        ".tgz",
        ".bz2",
        ".xz",
        ".zst",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyd",
        ".a",
        ".lib",
        ".o",
        ".obj",
        ".wasm",
        ".bin",
        ".nnue",
    }
    forbidden_endings = (
        ".pgn.gz",
        ".pgn.zst",
        ".pgn.tar.gz",
    )
    forbidden_directories = {
        "datasets",
        "calibracoes",
        "auditorias",
        "benchmarks",
        "reports",
        "runs",
        "books",
        "personalities",
        "engines",
    }
    violations = []

    for path in paths:
        relative = path.as_posix().casefold()
        if path.suffix.casefold() in forbidden_suffixes:
            violations.append(relative)
        if relative.endswith(forbidden_endings):
            violations.append(relative)
        if any(
            part.casefold() in forbidden_directories
            or part.casefold().startswith("partidas_chesssim")
            for part in path.parts[:-1]
        ):
            violations.append(relative)

    assert not violations, "External or local artifacts are tracked: " + ", ".join(violations)


def test_no_known_binary_payload_signature_is_tracked() -> None:
    signatures = (
        b"\x7fELF",
        b"MZ",
        b"PK\x03\x04",
        b"\x1f\x8b",
        b"7z\xbc\xaf\x27\x1c",
        b"Rar!\x1a\x07",
        b"\xfd7zXZ",
        b"\x28\xb5\x2f\xfd",
        b"!<arch>\n",
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    )
    binary_paths = []
    for relative in _tracked_paths():
        path = ROOT / relative
        try:
            with path.open("rb") as stream:
                prefix = stream.read(8)
        except OSError:
            continue
        if any(prefix.startswith(signature) for signature in signatures):
            binary_paths.append(relative.as_posix())

    assert not binary_paths, "Binary payloads are tracked: " + ", ".join(binary_paths)
