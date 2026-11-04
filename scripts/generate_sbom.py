#!/usr/bin/env python3
"""Generate CycloneDX SBOMs from two actually resolved, isolated Python environments."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_VERSION = "7.6.0"
SCOPES = ("runtime", "development")


def command(argv: list[str], *, output: Path | None = None) -> None:
    if output is None:
        subprocess.run(argv, cwd=ROOT, check=True)
    else:
        with output.open("x", encoding="utf-8") as stream:
            subprocess.run(argv, cwd=ROOT, stdout=stream, check=True)


def python_at(folder: Path) -> Path:
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def scanner_at(folder: Path) -> Path:
    return folder / ("Scripts/cyclonedx-py.exe" if os.name == "nt" else "bin/cyclonedx-py")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_bom(bom: dict, pip_packages: list[dict], *, scope: str) -> dict:
    """Reject missing packages, malformed graphs and mislabeled inventories."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown SBOM scope: {scope}")
    if bom.get("bomFormat") != "CycloneDX" or bom.get("specVersion") != "1.6":
        raise ValueError("SBOM does not declare CycloneDX JSON 1.6")
    if not isinstance(bom.get("dependencies"), list):
        raise ValueError("SBOM is missing its dependency graph")
    components = bom.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("SBOM is missing package components")
    in_bom = {
        (canonical(c["name"]), str(c["version"]))
        for c in components if c.get("name") and c.get("version")
    }
    missing = [
        f"{p['name']}=={p['version']}" for p in pip_packages
        if (canonical(p["name"]), str(p["version"])) not in in_bom
    ]
    if missing:
        raise ValueError("Installed packages missing from SBOM: " + ", ".join(sorted(missing)))
    required = {"chesssim64", "chess"}
    if scope == "development":
        required |= {"pytest", "ruff", "build", "setuptools"}
    installed = {canonical(p["name"]) for p in pip_packages}
    if not required <= installed:
        raise ValueError(f"Required installed packages missing in {scope}: {sorted(required - installed)}")
    return {
        "packages_resolved": len(pip_packages),
        "components_in_sbom": len(components),
        "dependency_edges": len(bom["dependencies"]),
        "package_versions": {
            canonical(p["name"]): str(p["version"])
            for p in sorted(pip_packages, key=lambda p: canonical(p["name"]))
        },
    }


def create_environment(folder: Path) -> Path:
    venv.EnvBuilder(with_pip=True).create(folder)
    return python_at(folder)


def generate(output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing SBOM directory: {output}")
    output.mkdir(parents=True)
    with TemporaryDirectory(prefix="chesssim-sbom-") as temp_path:
        temp = Path(temp_path)
        runtime = create_environment(temp / "runtime")
        development = create_environment(temp / "development")
        generator = create_environment(temp / "generator")
        # The scanner is outside the inventoried environments: it cannot
        # accidentally contaminate the application's dependency inventory.
        command([str(generator), "-m", "pip", "install", "--disable-pip-version-check",
                 "--no-input", f"cyclonedx-bom=={GENERATOR_VERSION}"])
        command([str(runtime), "-m", "pip", "install", "--disable-pip-version-check",
                 "--no-input", "."])
        command([str(development), "-m", "pip", "install", "--disable-pip-version-check",
                 "--no-input", ".[dev]", "setuptools>=77"])
        inventory = {}
        for scope, interpreter in (("runtime", runtime), ("development", development)):
            command([str(interpreter), "-m", "pip", "check"])
            packages_path = output / f"{scope}.packages.json"
            command([str(interpreter), "-m", "pip", "list", "--format=json"],
                    output=packages_path)
            command([str(interpreter), "-m", "pip", "inspect", "--local"],
                    output=output / f"{scope}.inspect.json")
            command([str(interpreter), "-m", "pip", "freeze", "--all"],
                    output=output / f"{scope}.resolved.txt")
            bom_path = output / f"{scope}.cdx.json"
            command([str(scanner_at(temp / "generator")), "environment", str(interpreter),
                     "--spec-version", "1.6", "--output-format", "JSON",
                     "--output-file", str(bom_path)])
            packages = json.loads(packages_path.read_text(encoding="utf-8"))
            bom = json.loads(bom_path.read_text(encoding="utf-8"))
            inventory[scope] = validate_bom(bom, packages, scope=scope)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit = None
    manifest = {
        "schema": "chesssim-resolved-python-dependencies-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": commit,
        "project": "chesssim64",
        "scanner": {"name": "cyclonedx-bom", "version": GENERATOR_VERSION},
        "generator_python": sys.version,
        "scopes": {
            "runtime": "pip install .; runtime and transitive dependencies",
            "development": "pip install .[dev] setuptools>=77; dev and build dependencies",
        },
        "inventory": inventory,
        "files_sha256": {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in sorted(output.iterdir()) if path.is_file()
        },
        "excluded": [
            "Rodent IV executable, personalities, Polyglot books",
            "Stockfish executable, NNUE networks",
            "PGN corpora and experiment artifacts",
        ],
        "limitations": (
            "Snapshot of installed Python packages for this platform/interpreter only. "
            "Does not inventory transient PEP517 build-isolation environments, "
            "operating-system libraries, external engines or third-party data."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path,
                        help="A new output directory (never overwritten)")
    args = parser.parse_args()
    try:
        manifest = generate(args.out.expanduser().resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"SBOM generation failed: {error}\n")
    print(json.dumps({
        scope: manifest["inventory"][scope]["packages_resolved"]
        for scope in SCOPES
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
