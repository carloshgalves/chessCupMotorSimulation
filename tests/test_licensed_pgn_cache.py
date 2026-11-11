"""Rights-attested, offline PGN ingest test matrix for issue #62."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import chess
import chess.pgn
import pytest

from chesssim.licensed_pgn_cache import prepare_cache, SCHEMA, CACHE_SCHEMA


ROOT = Path(__file__).resolve().parents[1]


def game_text(moves: tuple[str, ...] = ("e2e4", "e7e5", "g1f3"),
              result: str = "1-0", *, white="Example White",
              black="Example Black") -> str:
    g = chess.pgn.Game()
    g.headers["Event"] = "Synthetic consented test fixture"
    g.headers["White"] = white
    g.headers["Black"] = black
    g.headers["Date"] = "2020.01.01"
    g.headers["Result"] = result
    board = g.board()
    node = g
    for txt in moves:
        move = chess.Move.from_uci(txt)
        assert move in board.legal_moves, txt
        node = node.add_variation(move)
        board.push(move)
    return str(g) + "\n\n"


def write_source(tmp_path: Path, name: str, payload: str,
                 *, license_id="CC-BY-SA-4.0",
                 attestation="operator_reviewed", use=None):
    file = tmp_path / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(payload, encoding="utf-8")
    return {
        "id": file.stem.replace("-", "_"),
        "file": name,
        "source_url": "https://example.org/licensed/" + name,
        "license_id": license_id,
        "license_evidence_url": "https://example.org/rights",
        "rights_reviewed_on": str(date.today()),
        "rights_attestation": attestation,
        "expected_sha256": sha256(file.read_bytes()).hexdigest(),
        "allowed_uses": use or ["local_cache", "derived_openings"],
        "attribution": "Synthetic test data; author recorded in local catalog",
    }


def catalog(tmp_path: Path, *sources: dict) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"schema": SCHEMA, "sources": list(sources)}),
                    encoding="utf-8")
    return path


def test_dry_run_exact_same_plan_then_atomic_apply_and_second_apply_refused(tmp_path):
    a = write_source(tmp_path, "a.pgn", game_text())
    b = write_source(tmp_path, "b.pgn", game_text() + game_text(("d2d4", "d7d5")))
    sources = catalog(tmp_path, a, b)
    out = tmp_path / "cache"
    before = sorted(p.name for p in tmp_path.iterdir())
    preview = prepare_cache(sources, out=out)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not out.exists()
    assert preview["schema"] == CACHE_SCHEMA
    assert preview["rights_status"] == "OPERATOR_ATTESTED_NOT_INDEPENDENTLY_VERIFIED"
    assert preview["operation"] == "DRY_RUN"
    assert preview["games_examined"] == 3
    assert preview["games_distinct"] == 2
    assert preview["games_duplicates"] == 1
    assert preview["complete_results"] == 2
    assert {s["id"] for s in preview["sources"]} == {"a", "b"}
    applied = prepare_cache(sources, out=out, apply=True)
    assert applied["operation"] == "APPLIED"
    assert (out / "games.pgn").is_file()
    assert (out / "manifest.json").is_file()
    assert sha256((out / "games.pgn").read_bytes()).hexdigest() == (
        preview["cache_pgn_sha256"])
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == applied
    assert (out / "games.pgn").read_bytes() == (
        (out / "games.pgn").read_bytes())  # immutable content after first commit
    with pytest.raises(FileExistsError):
        prepare_cache(sources, out=out, apply=True)


def test_no_download_no_engine_no_any_write_during_preview(tmp_path, monkeypatch):
    import socket
    import urllib.request

    def fail(*args, **kwargs):
        raise AssertionError("network/engine execution forbidden")
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    p = write_source(tmp_path, "local.pgn", game_text())
    path = catalog(tmp_path, p)
    initial = {x.name: x.read_bytes() for x in tmp_path.iterdir()}
    out = tmp_path / "cache"
    prepare_cache(path, out=out, apply=False)
    assert not out.exists()
    assert {x.name: x.read_bytes() for x in tmp_path.iterdir()} == initial


@pytest.mark.parametrize("replacement", [
    {"license_id": "UNKNOWN"},
    {"license_id": []},
    {"license_id": "PDM-1.0"},
    {"rights_attestation": "pending"},
    {"license_evidence_url": "http://example.org/rights"},
    {"source_url": "https://user:password@example.org/download"},
    {"allowed_uses": ["derived_openings"]},
    {"allowed_uses": [["local_cache"]]},
    {"source_url": "https://example.org/file.pgn?token=private"},
    {"rights_reviewed_on": "3020-01-01"},
    {"expected_sha256": "bad"},
    {"attribution": ""},
])
def test_rights_not_claimed_when_metadata_missing_or_prohibited(tmp_path, replacement):
    item = write_source(tmp_path, "sample.pgn", game_text())
    item.update(replacement)
    p = catalog(tmp_path, item)
    with pytest.raises(ValueError):
        prepare_cache(p, out=tmp_path / "cache", apply=True)
    assert not (tmp_path / "cache").exists()


def test_mandatory_sha256_prevents_stale_or_replaced_content(tmp_path):
    item = write_source(tmp_path, "sample.pgn", game_text())
    path = catalog(tmp_path, item)
    (tmp_path / "sample.pgn").write_text(game_text(("d2d4", "d7d5")),
                                         encoding="utf-8")
    with pytest.raises(ValueError, match="Checksum"):
        prepare_cache(path, out=tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_missing_or_illegal_pgn_is_failed_not_silently_skipped(tmp_path):
    item = write_source(tmp_path, "sample.pgn",
                        game_text().replace("Nf3", "Nf99"))
    path = catalog(tmp_path, item)
    with pytest.raises(ValueError):
        prepare_cache(path, out=tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_comment_and_nag_tokens_accepted_without_exporting_annotations(tmp_path):
    pgn = game_text().replace("e4 e5", "e4 {annotated [%eval 0.2]} e5 $1")
    item = write_source(tmp_path, "annotated.pgn", pgn)
    rights = catalog(tmp_path, item)
    report = prepare_cache(rights, out=tmp_path / "cache", apply=True)
    assert report["games_distinct"] == 1
    raw = (tmp_path / "cache" / "games.pgn").read_text(encoding="utf-8")
    assert "[%eval" not in raw and "annotated" not in raw
    assert "e4" in raw and "e5" in raw


def test_conflicting_results_same_mainline_fail_closed(tmp_path):
    item = write_source(tmp_path, "a.pgn", game_text(result="1-0"))
    item2 = write_source(tmp_path, "b.pgn", game_text(result="0-1"))
    path = catalog(tmp_path, item, item2)
    with pytest.raises(ValueError, match="contraditórios"):
        prepare_cache(path, out=tmp_path / "cache", apply=True)
    assert not (tmp_path / "cache").exists()


def test_paths_must_be_local_relative_and_non_symlinked(tmp_path):
    data = write_source(tmp_path, "valid.pgn", game_text())
    target = tmp_path / "real.pgn"
    target.write_bytes((tmp_path / "valid.pgn").read_bytes())
    link = tmp_path / "bad.pgn"
    link.symlink_to(target)
    data["file"] = "bad.pgn"
    path = catalog(tmp_path, data)
    with pytest.raises(ValueError):
        prepare_cache(path, out=tmp_path / "cache")
    data["file"] = "../valid.pgn"
    catalog(tmp_path, data)
    with pytest.raises(ValueError):
        prepare_cache(path, out=tmp_path / "cache")
    data["file"] = "valid.pgn"
    nested = tmp_path / "linked"
    nested.symlink_to(tmp_path, target_is_directory=True)
    data["file"] = "linked/valid.pgn"
    catalog(tmp_path, data)
    with pytest.raises(ValueError):
        prepare_cache(path, out=tmp_path / "cache")


def test_zero_ply_and_unknown_date_not_presented_as_complete_game(tmp_path):
    item = write_source(tmp_path, "sample.pgn",
                        game_text(moves=(), result="*") +
                        game_text(("e2e4", "e7e5"), result="*"))
    path = catalog(tmp_path, item)
    report = prepare_cache(path, out=tmp_path / "cache")
    assert report["games_examined"] == 2
    assert report["games_excluded"] == {"zero_ply": 1}
    assert report["games_distinct"] == 1
    assert report["complete_results"] == 0
    assert report["incomplete_results"] == 1


def test_budget_enforced_before_writing(tmp_path):
    p = write_source(tmp_path, "local.pgn", game_text())
    path = catalog(tmp_path, p)
    with pytest.raises(ValueError):
        prepare_cache(path, out=tmp_path / "cache", max_games=0, apply=True)
    with pytest.raises(ValueError):
        prepare_cache(path, out=tmp_path / "cache", max_cache_mb=0, apply=True)
    assert not (tmp_path / "cache").exists()


def test_160_game_sample_offline_quota_and_deterministic_cache(tmp_path):
    whites = [
        "e2e4", "d2d4", "c2c4", "b2b4", "g2g3", "b1c3",
        "g1f3", "a2a3", "h2h4", "f2f3", "e2e3", "d2d3",
        "c2c3", "g2g4", "b2b3", "a2a4",
    ]
    blacks = [
        "e7e5", "d7d5", "c7c5", "g8f6", "b8c6",
        "a7a6", "h7h6", "g7g6", "f7f5", "b7b6",
    ]
    games = [game_text((white, black), white="Research Player",
                       black="Other Player")
             for white in whites for black in blacks]
    assert len(games) == 160
    item = write_source(tmp_path, "allowed.pgn", "".join(games),
                        license_id="CC0-1.0")
    path = catalog(tmp_path, item)
    a = prepare_cache(path, out=tmp_path / "cache", max_games=200)
    b = prepare_cache(path, out=tmp_path / "cache", max_games=200)
    assert a == b
    assert a["games_examined"] == 160
    assert a["games_distinct"] == 160
    assert a["sources"][0]["players_seen_in_new_games"]["Research Player"] == 160
    assert a["estimated_pgn_bytes"] > 0
    with pytest.raises(ValueError, match="quota"):
        prepare_cache(path, out=tmp_path / "cache", max_games=100, apply=True)
    assert not (tmp_path / "cache").exists()


def test_cli_dry_run_then_apply_does_not_write_in_preview(tmp_path):
    p = write_source(tmp_path, "a.pgn", game_text())
    sources = catalog(tmp_path, p)
    output = tmp_path / "cache"
    command = [
        sys.executable, "-m", "chesssim", "pgn-cache",
        "--catalog", str(sources), "--out", str(output),
    ]
    preview = subprocess.run(command, cwd=ROOT, text=True,
                             capture_output=True, timeout=20)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["operation"] == "DRY_RUN"
    assert not output.exists()
    applied = subprocess.run(command + ["--apply"], cwd=ROOT,
                             text=True, capture_output=True, timeout=20)
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["operation"] == "APPLIED"
    assert (output / "games.pgn").exists()
