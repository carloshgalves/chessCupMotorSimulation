"""Installation and audit regressions without network or external binaries."""
from pathlib import Path

import pytest

from chesssim import human_roster, rodent
from chesssim.catalog import load_roster
from chesssim.registry import RegistryError, registered_aliases


@pytest.fixture
def installation(tmp_path):
    root = tmp_path
    base = root / "personalities"
    base.mkdir()
    entries = {p.rodent_alias: p.profile_filename for p in load_roster() if p.rodent_alias}
    (base / "basic.ini").write_text(
        "".join(f"{alias}={name}\n" for alias, name in entries.items()), encoding="utf-8")
    for name in entries.values():
        (base / name).write_text("setoption name SearchSkill value 10\n", encoding="utf-8")
    executable = root / "rodent-iv-plain.exe"
    executable.write_text("synthetic", encoding="utf-8")
    executable.chmod(0o700)
    return root


def test_install_preserves_21_and_only_adds_simple(installation, monkeypatch):
    root = installation
    base = root / "personalities"
    before = {p: p.read_bytes() for p in base.glob("*.txt")}
    calls = []
    def fetch(path):
        calls.append(path)
        assert path == "personalities/simple.txt"
        return b"setoption name SearchSkill value 10\n"
    monkeypatch.setattr(rodent, "_fetch", fetch)
    assert rodent.setup_official(root)["missing_profiles"] == []
    preview = rodent.setup_official(root, human_roster=True)
    assert preview["missing_profiles"] == ["personalities/simple.txt"]
    assert preview["new_aliases"] == {"Simple": "simple.txt"}
    assert calls == [] and not (base / "simple.txt").exists()
    assert human_roster.audit_human_roster(root)["available"] == 21
    applied = rodent.setup_official(root, apply=True, human_roster=True)
    assert calls == ["personalities/simple.txt"]
    assert Path(applied["backup"]).is_file()
    assert registered_aliases(base / "basic.ini")["Simple"] == "simple.txt"
    assert all(p.read_bytes() == value for p, value in before.items())
    assert len(list(base.glob("*.txt"))) == 22
    assert not rodent.setup_official(root, apply=True, human_roster=True)["downloads"]
    assert len(calls) == 1
    assert human_roster.audit_human_roster(root)["status"] == "FILES_READY"


def test_conflicting_simple_is_not_overwritten(installation):
    root = installation
    ini = root / "personalities/basic.ini"
    ini.write_text(ini.read_text() + "Simple=custom.txt\n", encoding="utf-8")
    before = ini.read_bytes()
    with pytest.raises(RegistryError, match="Simple divergente"):
        rodent.setup_official(root, apply=True, human_roster=True)
    assert ini.read_bytes() == before


def test_probe_counts_unique_aliases_and_fails_closed(installation, monkeypatch):
    base = installation / "personalities"
    ini = base / "basic.ini"
    ini.write_text(ini.read_text() + "Simple=simple.txt\n", encoding="utf-8")
    (base / "simple.txt").write_text("generic", encoding="utf-8")
    probes = []
    def probe(root, exe, alias):
        probes.append(alias)
        return {"uci": "FAIL"} if alias == "Simple" else {"uci": "PASS", "legal_move": "e2e4"}
    monkeypatch.setattr(human_roster, "_probe", probe)
    result = human_roster.audit_human_roster(installation, probe=True)
    assert result["available"] == 64 and result["uci_verified"] == 21
    assert result["status"] == "UCI_INCOMPLETE"
    assert result["distinct_uci_probes"] == len(probes) == 22
    assert probes.count("Simple") == 1
    assert not result["historically_validated"]
    monkeypatch.setattr(human_roster, "_probe", lambda *args: {"uci": "PASS"})
    assert human_roster.audit_human_roster(installation, probe=True)["status"] == "UCI_READY"
