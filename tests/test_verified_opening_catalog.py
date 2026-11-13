"""Opt-in local bytes + FEN catalog verification for the shared-FEN controls."""
from __future__ import annotations

from hashlib import sha256
import json
import os

import chess
import pytest

from chesssim.cli import main
from chesssim.shared_fen_controls import run_shared_fen_file


def _case(tmp_path):
    board = chess.Board()
    board.fullmove_number = 9
    opening_fen = board.fen()
    board.push_uci("e2e4")
    retained_fen = board.fen()
    positions = [
        {"game": "A-1", "player": "A", "fen": opening_fen,
         "move": "e2e4", "ply": 17},
        {"game": "B-1", "player": "B", "fen": opening_fen,
         "move": "d2d4", "ply": 17},
        {"game": "A-2", "player": "A", "fen": retained_fen,
         "move": "e7e5", "ply": 18},
        {"game": "B-2", "player": "B", "fen": retained_fen,
         "move": "c7c5", "ply": 18},
    ]
    catalog = tmp_path / "opening-catalog.json"
    raw = json.dumps({"fens": [opening_fen]}, separators=(",", ":")).encode()
    catalog.write_bytes(raw)
    declared_hash = sha256(raw).hexdigest()
    payload = {
        "positions": positions,
        "predictions": {
            "profile_A": {retained_fen: "e7e5"},
            "profile_B": {retained_fen: "c7c5"},
        },
        "player_a": "A", "player_b": "B",
        "profile_a": "profile_A", "profile_b": "profile_B",
        "opening_exclusion": {
            "fens": [opening_fen],
            "declared_source_sha256": declared_hash,
        },
    }
    inp = tmp_path / "input.json"
    inp.write_text(json.dumps(payload), encoding="utf-8")
    return inp, catalog, tmp_path / "report.json", payload


def test_verified_catalog_checks_actual_local_bytes_and_exact_fens(tmp_path):
    inp, source, out, _ = _case(tmp_path)
    original = source.read_bytes()
    report = run_shared_fen_file(inp, out, ack_unsealed=True,
                                 opening_catalog_source=source)

    assert source.read_bytes() == original
    assert report["matched_fens"] == 1
    assert report["opening_exclusion"]["matched_fens_excluded"] == 1
    assert report["opening_exclusion"]["provenance"] == "LOCAL_BYTES_AND_FENS_VERIFIED"
    assert report["opening_exclusion"]["declared_source_sha256"] == sha256(original).hexdigest()
    assert report["limitations"]["opening_catalog_source"] == (
        "LOCAL_INTEGRITY_ONLY_LICENSE_COHORT_AND_PREREGISTRATION_NOT_VERIFIED"
    )
    assert json.loads(out.read_text()) == report
    assert "rnbqkbnr" not in out.read_text()
    assert "e7e5" not in out.read_text()


def test_existing_declared_only_mode_preserves_original_contract(tmp_path):
    inp, source, out, _ = _case(tmp_path)
    old = run_shared_fen_file(inp, out, ack_unsealed=True)
    assert old["opening_exclusion"]["provenance"] == "DECLARED_NOT_VERIFIED"
    assert "opening_catalog_source" not in old["limitations"]
    verified = run_shared_fen_file(inp, tmp_path / "verified.json",
                                   ack_unsealed=True, opening_catalog_source=source)
    assert old["input_evidence_sha256"] == verified["input_evidence_sha256"]
    assert old["declared_assignment_score"] == verified["declared_assignment_score"]


def test_cli_verification_is_opt_in_and_preserves_input(tmp_path, capsys):
    inp, source, out, _ = _case(tmp_path)
    initial = inp.read_bytes()
    assert main(["shared-fen-controls", "--input", str(inp),
                 "--out", str(out), "--ack-unsealed",
                 "--opening-catalog-source", str(source)]) == 0
    assert inp.read_bytes() == initial
    assert json.loads(out.read_text())["opening_exclusion"]["provenance"] == (
        "LOCAL_BYTES_AND_FENS_VERIFIED"
    )
    assert "matched_fens" in capsys.readouterr().out


@pytest.mark.parametrize("mutation", [
    "wrong_digest", "different_fen", "extra_source_fen",
    "missing_source", "source_symlink", "source_directory",
    "malformed_json", "duplicate_key", "wrong_schema",
    "invalid_fen", "repeated_fen", "oversize", "no_opening_exclusion",
])
def test_invalid_or_unverifiable_catalog_fails_without_writing_report(tmp_path, mutation):
    inp, source, out, payload = _case(tmp_path)
    fen = payload["opening_exclusion"]["fens"][0]
    if mutation == "wrong_digest":
        payload["opening_exclusion"]["declared_source_sha256"] = "0" * 64
    elif mutation == "different_fen":
        board = chess.Board(fen)
        board.fullmove_number = 10
        raw = json.dumps({"fens": [board.fen()]}).encode()
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "extra_source_fen":
        board = chess.Board(fen)
        board.fullmove_number = 10
        raw = json.dumps({"fens": [fen, board.fen()]}).encode()
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "missing_source":
        source = tmp_path / "missing.json"
    elif mutation == "source_symlink":
        link = tmp_path / "source-link.json"
        link.symlink_to(source)
        source = link
    elif mutation == "source_directory":
        source = tmp_path
    elif mutation == "malformed_json":
        raw = b"{not valid json"
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "duplicate_key":
        raw = ('{"fens": ' + json.dumps([fen]) + ', "fens": ' +
               json.dumps([fen]) + '}').encode()
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "wrong_schema":
        raw = json.dumps({"fens": [fen], "license": "none"}).encode()
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "invalid_fen":
        raw = json.dumps({"fens": ["invalid"]}).encode()
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "repeated_fen":
        raw = json.dumps({"fens": [fen, fen]}).encode()
        source.write_bytes(raw)
        payload["opening_exclusion"]["declared_source_sha256"] = sha256(raw).hexdigest()
    elif mutation == "oversize":
        raw = b"x" * (8 * 1024 * 1024 + 1)
        source.write_bytes(raw)
    elif mutation == "no_opening_exclusion":
        del payload["opening_exclusion"]
    inp.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        run_shared_fen_file(inp, out, ack_unsealed=True,
                            opening_catalog_source=source)
    assert not out.exists()


def test_verified_result_is_order_invariant_without_rewriting_source(tmp_path):
    inp, source, out, payload = _case(tmp_path)
    first = run_shared_fen_file(inp, out, ack_unsealed=True,
                                opening_catalog_source=source)
    payload["positions"].reverse()
    inp.write_text(json.dumps(payload))
    second = run_shared_fen_file(inp, tmp_path / "second.json",
                                 ack_unsealed=True, opening_catalog_source=source)
    assert first["input_evidence_sha256"] == second["input_evidence_sha256"]
    assert first["opening_exclusion"] == second["opening_exclusion"]


def test_no_source_files_read_without_acknowledgement(tmp_path):
    inp, source, out, _ = _case(tmp_path)
    source.unlink()
    with pytest.raises(ValueError, match="acknowledgement"):
        run_shared_fen_file(inp, out, ack_unsealed=False,
                            opening_catalog_source=source)
    assert not out.exists()



@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX-only FIFO regression")
def test_catalog_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    inp, source, out, _ = _case(tmp_path)
    source.unlink()
    os.mkfifo(source)
    with pytest.raises(ValueError, match="regular file"):
        run_shared_fen_file(inp, out, ack_unsealed=True,
                            opening_catalog_source=source)
    assert not out.exists()
