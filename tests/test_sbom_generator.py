"""Pure regression checks for the resolved-environment SBOM pipeline."""
from __future__ import annotations

import pytest

from scripts.generate_sbom import SCOPES, canonical, validate_bom


def _sample(scope: str):
    assert scope in SCOPES
    packages = [
        {"name": "chesssim64", "version": "0.1.0"},
        {"name": "chess", "version": "1.11.2"},
        {"name": "pip", "version": "25.0"},
    ]
    if scope == "development":
        packages.extend([
            {"name": "pytest", "version": "8.4.0"},
            {"name": "ruff", "version": "0.13.0"},
            {"name": "build", "version": "1.4.0"},
            {"name": "setuptools", "version": "80.0.0"},
        ])
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "components": [
            {"type": "library", "name": item["name"], "version": item["version"]}
            for item in packages
        ],
        "dependencies": [{"ref": "pkg:pypi/chesssim64@0.1.0", "dependsOn": []}],
    }
    return packages, bom


def test_normalization_handles_pep503_package_names():
    assert canonical("A_package.Name") == "a-package-name"


@pytest.mark.parametrize("scope", SCOPES)
def test_sbom_accepts_exact_resolved_packages(scope):
    packages, bom = _sample(scope)
    result = validate_bom(bom, packages, scope=scope)
    assert result["packages_resolved"] == len(packages)
    assert result["components_in_sbom"] == len(packages)
    assert result["package_versions"]["chess"] == "1.11.2"


def test_sbom_rejects_missing_installed_transitive_dependency():
    packages, bom = _sample("runtime")
    packages.append({"name": "some_transitive", "version": "2.1.0"})
    with pytest.raises(ValueError, match="some_transitive==2.1.0"):
        validate_bom(bom, packages, scope="runtime")


def test_sbom_rejects_missing_graph():
    packages, bom = _sample("runtime")
    bom.pop("dependencies")
    with pytest.raises(ValueError, match="dependency graph"):
        validate_bom(bom, packages, scope="runtime")


def test_sbom_rejects_mislabelled_format():
    packages, bom = _sample("runtime")
    bom["specVersion"] = "1.5"
    with pytest.raises(ValueError, match="CycloneDX JSON 1.6"):
        validate_bom(bom, packages, scope="runtime")


def test_dev_scope_requires_build_and_test_tools():
    packages, bom = _sample("development")
    packages = [item for item in packages if item["name"] != "setuptools"]
    bom["components"] = [item for item in bom["components"] if item["name"] != "setuptools"]
    with pytest.raises(ValueError, match="setuptools"):
        validate_bom(bom, packages, scope="development")


def test_sbom_rejects_an_unknown_scope():
    packages, bom = _sample("runtime")
    with pytest.raises(ValueError, match="Unknown SBOM scope"):
        validate_bom(bom, packages, scope="unrecognized")
