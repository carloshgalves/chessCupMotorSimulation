import json
import pytest
from chesssim.cp_checkpoints import Checkpoints, Paused
from chesssim.bilateral_cp_local import bounded_fine, MAX_NEW_RECIPES


def test_requested_stop_saves_and_resumes_without_repeating(tmp_path):
    stop = tmp_path / "STOP"
    directory = tmp_path / "checkpoints"
    c = Checkpoints(directory, stop, ["identity"])
    panel = [{"id": str(i)} for i in range(5)]
    seen = []
    def measure(ref, engines, cases, rows):
        seen.extend(rows)
        if len(seen) == 2:
            stop.touch()
        return rows
    def verify(rows, expected, cases):
        assert rows == expected
    args = ("fit", None, {}, {"baseline": ("original", 16, 5000000)}, panel, {})
    with pytest.raises(Paused):
        c.measure(*args, measure=measure, verify=verify)
    assert len(json.loads((directory / "fit.json").read_text())["rows"]) == 2
    stop.unlink()
    resumed = Checkpoints(directory, stop, ["identity"])
    assert resumed.measure(*args, measure=measure, verify=verify) == panel
    assert seen == panel
    with pytest.raises(ValueError, match="identity mismatch"):
        Checkpoints(directory, stop, ["other"]).measure(*args, measure=measure, verify=verify)


def test_no_position_writes_without_stop(tmp_path, monkeypatch):
    c = Checkpoints(tmp_path, None, "id")
    writes = []
    original = c.write
    def write(path, doc):
        writes.append(len(doc["rows"]))
        original(path, doc)
    monkeypatch.setattr(c, "write", write)
    panel = [{"id": str(i)} for i in range(4)]
    def verify(rows, expected, cases):
        assert rows == expected
    c.measure("fit", None, {}, {}, panel, {}, measure=lambda r, e, c, rows: rows, verify=verify)
    assert writes == [4]


def test_fine_budget():
    coarse = {"baseline": ("original", 16, 5000000), "away": ("away", 16, 5000000)}
    fine = bounded_fine(coarse, "baseline", ["mid-away-50"])
    assert len(fine) <= MAX_NEW_RECIPES
    assert not set(fine) & set(coarse)
    assert len(set(fine.values())) == len(fine)
