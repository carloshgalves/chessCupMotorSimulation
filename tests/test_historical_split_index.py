"""Issue #10: deterministic historical *index-only* corpus split preflight.

Synthetic identifiers are used: no actual historical or sealed PGNs are opened.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os

import pytest

from chesssim.cli import main
from chesssim.historical_split_index import (
    build_historical_split_manifest,
    run_historical_split_preflight,
)


def _hash(label):
    return sha256(label.encode("utf-8")).hexdigest()


def _index(*, years=(1980, 1981, 1982, 1983, 1984), per_year=2):
    games = []
    for year in years:
        for j in range(per_year):
            n = len(games)
            games.append({
                "mainline_sha256": _hash(f"mainline-{n}"),
                "date": f"{year}-{j + 1:02d}-15",
                "source_ids": ["archive"],
                "metadata": {
                    "white_known": True,
                    "black_known": True,
                    "event_known": j != 0,
                    "time_control_known": False,
                },
            })
    return {
        "schema": "chesssim-historical-game-index-v1",
        "target_slug": "fictional-synthetic-target",
        "sources": [{
            "id": "archive",
            "sha256": _hash("archive-source"),
            "license_id": "UNVERIFIED_OPERATOR_CLAIM",
            "license_evidence_uri": "https://example.org/catalog-evidence",
        }],
        "games": games,
    }


def _write(tmp_path, data):
    inp = tmp_path / "game-index.json"
    inp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return inp


def test_whole_year_partition_is_reproducible_and_hides_reserved_game_ids():
    data = _index()
    report = build_historical_split_manifest(data)
    assert report["schema"] == "chesssim-historical-split-preflight-v1"
    assert report["status"] == "INDEXED_NOT_SOURCE_VERIFIED"
    assert report["partition_policy"] == "CHRONOLOGICAL_WHOLE_CALENDAR_YEAR_60_20_20_TARGETS"
    assert report["game_index_count"] == 10
    assert report["quarantined_unknown_date"] == 0
    assert {name: report["partitions"][name]["games"]
            for name in ("train", "validation", "holdout")} == {
                "train": 6, "validation": 2, "holdout": 2}
    assert report["partitions"]["train"]["years"] == [1980, 1981, 1982]
    assert report["partitions"]["validation"]["years"] == [1983]
    assert report["partitions"]["holdout"]["years"] == [1984]
    assert all(len(report["partitions"][name]["game_commitment_sha256"]) == 64
               for name in ("train", "validation", "holdout"))
    assert report["source_provenance"] == "OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED"
    assert report["embargo_status"] == "NOT_VERIFIED_BY_THIS_INDEX"
    assert report["reserved_game_ids_exported"] is False
    serialized = json.dumps(report)
    for game in data["games"]:
        assert game["mainline_sha256"] not in serialized


def test_same_index_different_order_produces_same_partition_commitments():
    data = _index()
    first = build_historical_split_manifest(data)
    reordered = deepcopy(data)
    reordered["games"].reverse()
    second = build_historical_split_manifest(reordered)
    assert first == second
    modified = deepcopy(data)
    modified["games"][0]["mainline_sha256"] = _hash("new-history")
    changed = build_historical_split_manifest(modified)
    assert changed["input_evidence_sha256"] != first["input_evidence_sha256"]
    assert changed["partitions"]["train"]["game_commitment_sha256"] != (
        first["partitions"]["train"]["game_commitment_sha256"])
    assert changed["partitions"]["holdout"] == first["partitions"]["holdout"]


def test_unknown_dates_quarantined_without_guessing_or_leaking_id():
    data = _index()
    data["games"].append({
        "mainline_sha256": _hash("unknown-dated"),
        "date": None,
        "source_ids": ["archive"],
        "metadata": {key: False for key in
                     ("white_known", "black_known", "event_known", "time_control_known")},
    })
    report = build_historical_split_manifest(data)
    assert report["game_index_count"] == 11
    assert report["eligible_dated_games"] == 10
    assert report["quarantined_unknown_date"] == 1
    assert sum(x["games"] for x in report["partitions"].values()) == 10
    assert _hash("unknown-dated") not in str(report)
    assert report["metadata_quality"]["unknown_date"] == 1
    assert report["metadata_quality"]["missing_white_name"] == 1


@pytest.mark.parametrize("years,per_year,expected", [
    ((1980,), 10, {"train": 10, "validation": 0, "holdout": 0}),
    ((1980, 1981), 3, {"train": 3, "validation": 3, "holdout": 0}),
])
def test_insufficient_year_groups_do_not_create_false_ready_status(years, per_year, expected):
    report = build_historical_split_manifest(_index(years=years, per_year=per_year))
    assert report["status"] == "INSUFFICIENT_PERIOD_COVERAGE"
    assert {part: info["games"] for part, info in report["partitions"].items()} == expected
    assert report["embargo_status"] == "NOT_VERIFIED_BY_THIS_INDEX"


@pytest.mark.parametrize("mutation", [
    "duplicate_game", "different_date_same_mainline",
    "duplicate_source", "bad_source_digest", "unknown_source",
    "duplicate_game_source", "missing_license", "invalid_metadata_type",
    "bad_date", "bad_date_format", "bad_game_sha", "unknown_extra_field",
    "noncanonical_ordered_source_ids", "missing_required_metadata",
    "bad_target_slug", "source_extra_field",
])
def test_invalid_or_ambiguous_game_index_fails_closed(mutation):
    data = _index()
    first = data["games"][0]
    if mutation == "duplicate_game":
        data["games"].append(deepcopy(first))
    elif mutation == "different_date_same_mainline":
        new = deepcopy(first)
        new["date"] = "1987-01-01"
        data["games"].append(new)
    elif mutation == "duplicate_source":
        data["sources"].append(deepcopy(data["sources"][0]))
    elif mutation == "bad_source_digest":
        data["sources"][0]["sha256"] = "0" * 63
    elif mutation == "unknown_source":
        first["source_ids"] = ["unlisted"]
    elif mutation == "duplicate_game_source":
        first["source_ids"] = ["archive", "archive"]
    elif mutation == "missing_license":
        data["sources"][0]["license_id"] = ""
    elif mutation == "invalid_metadata_type":
        first["metadata"]["event_known"] = 1
    elif mutation == "bad_date":
        first["date"] = "1980-02-30"
    elif mutation == "bad_date_format":
        first["date"] = "1980.01.15"
    elif mutation == "bad_game_sha":
        first["mainline_sha256"] = "invalid"
    elif mutation == "unknown_extra_field":
        first["fen"] = "reserved-hidden-fen"
    elif mutation == "noncanonical_ordered_source_ids":
        data["sources"].append({
            "id": "second", "sha256": _hash("source2"),
            "license_id": "CC-BY-4.0",
            "license_evidence_uri": "https://example.org/license-2",
        })
        first["source_ids"] = ["second", "archive"]
    elif mutation == "missing_required_metadata":
        del first["metadata"]["white_known"]
    elif mutation == "bad_target_slug":
        data["target_slug"] = "../unsafe"
    elif mutation == "source_extra_field":
        data["sources"][0]["secret"] = "unverified"
    with pytest.raises(ValueError):
        build_historical_split_manifest(data)


def test_loss_of_source_provenance_changes_evidence_commitment():
    data = _index()
    report = build_historical_split_manifest(data)
    changed = deepcopy(data)
    changed["sources"][0]["license_id"] = "different-operator-claim"
    new = build_historical_split_manifest(changed)
    assert report["input_evidence_sha256"] != new["input_evidence_sha256"]
    assert report["partitions"] == new["partitions"]
    assert "not independently" in " ".join(report["limitations"]).lower()


def test_file_runner_creates_exclusive_manifest_and_preserves_source(tmp_path):
    data = _index()
    inp = _write(tmp_path, data)
    raw = inp.read_bytes()
    out = tmp_path / "manifest.json"
    report = run_historical_split_preflight(inp, out, ack_unsealed=True)
    assert inp.read_bytes() == raw
    assert json.loads(out.read_text(encoding="utf-8")) == report
    assert report["input_file_sha256"] == sha256(raw).hexdigest()
    assert report["input_evidence_sha256"] == (
        build_historical_split_manifest(data)["input_evidence_sha256"])
    assert not (tmp_path / "training.pgn").exists()
    with pytest.raises(FileExistsError):
        run_historical_split_preflight(inp, out, ack_unsealed=True)
    assert json.loads(out.read_text(encoding="utf-8")) == report


def test_cli_requires_ack_and_runs_without_pg_ns_or_engine(tmp_path, capsys):
    inp = _write(tmp_path, _index())
    out = tmp_path / "manifest.json"
    with pytest.raises(SystemExit) as exc:
        main(["historical-split-preflight", "--input", str(inp), "--out", str(out)])
    assert exc.value.code == 2
    assert not out.exists()
    assert main(["historical-split-preflight", "--input", str(inp), "--out",
                 str(out), "--ack-unsealed"]) == 0
    assert "INDEXED_NOT_SOURCE_VERIFIED" in capsys.readouterr().out
    assert out.exists()


@pytest.mark.parametrize("mutation", [
    "invalid_json", "duplicate_key", "directory", "symlink",
    "missing_file", "oversize", "fifo", "output_symlink",
])
def test_runner_rejects_unsafe_files_without_creating_report(tmp_path, mutation):
    inp = _write(tmp_path, _index())
    out = tmp_path / "manifest.json"
    if mutation == "invalid_json":
        inp.write_text("{")
    elif mutation == "duplicate_key":
        raw = inp.read_text()
        inp.write_text(raw[:-1] + ', "target_slug":"not-original"}')
    elif mutation == "directory":
        inp = tmp_path
    elif mutation == "symlink":
        link = tmp_path / "linked-index.json"
        link.symlink_to(inp)
        inp = link
    elif mutation == "missing_file":
        inp = tmp_path / "missing.json"
    elif mutation == "oversize":
        inp.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    elif mutation == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("requires POSIX FIFO")
        inp.unlink()
        os.mkfifo(inp)
    elif mutation == "output_symlink":
        out.symlink_to(tmp_path / "missing-output")
    with pytest.raises((ValueError, FileNotFoundError, FileExistsError)):
        run_historical_split_preflight(inp, out, ack_unsealed=True)
    assert not out.is_file()


def test_no_read_or_file_creation_when_unsealed_ack_is_absent(tmp_path):
    inp, out = tmp_path / "missing.json", tmp_path / "result.json"
    with pytest.raises(ValueError, match="acknowledgement"):
        run_historical_split_preflight(inp, out, ack_unsealed=False)
    assert not out.exists()
