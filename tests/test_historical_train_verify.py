"""Issue #10: verify *only* a training PGN against a frozen unsealed index.

All PGNs below are synthetic. Validation and holdout PGNs are NEVER passed
to the verifier. This does not attest source rights or prevent external access.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os

import chess
import chess.pgn
import pytest

from chesssim.cli import main
from chesssim.game_diversity import mainline_identity
from chesssim.historical_split_index import build_historical_split_manifest
from chesssim import historical_train_verify as verifier
from chesssim.historical_train_verify import (
    verify_historical_training_pgn, run_historical_train_verification,
)


def _game(n):
    game = chess.pgn.Game()
    game.headers["White"] = "Synthetic White"
    game.headers["Black"] = "Synthetic Black"
    game.headers["Event"] = "Synthetic research fixture"
    game.headers["Date"] = f"{1980 + n // 2}.{1 + n % 2:02d}.15"
    game.headers["TimeControl"] = "600+0"
    game.headers["Result"] = "1-0"
    board, node = game.board(), game
    for ply in range(4):
        move = sorted(board.legal_moves, key=lambda m: m.uci())[
            (n * 3 + ply) % board.legal_moves.count()]
        node = node.add_variation(move)
        board.push(move)
    return game


def _export(games):
    return ("".join(game.accept(chess.pgn.StringExporter(
        headers=True, variations=False, comments=False)).rstrip() + "\n\n"
        for game in games)).encode("utf-8")


def _fixture(tmp_path):
    games = [_game(n) for n in range(10)]
    index = {
        "schema": "chesssim-historical-game-index-v1",
        "target_slug": "synthetic-player",
        "sources": [{
            "id": "source",
            "sha256": sha256(b"source-original-not-verified").hexdigest(),
            "license_id": "OPERATOR_ATTESTED",
            "license_evidence_uri": "https://example.org/license",
        }],
        "games": [{
            "mainline_sha256": mainline_identity(game)[0],
            "date": game.headers["Date"].replace(".", "-"),
            "source_ids": ["source"],
            "metadata": {
                "white_known": True, "black_known": True,
                "event_known": True, "time_control_known": True,
            },
        } for game in games],
    }
    assert len({row["mainline_sha256"] for row in index["games"]}) == 10
    index_path = tmp_path / "index.json"
    training_path = tmp_path / "training.pgn"
    report_path = tmp_path / "report.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    training_path.write_bytes(_export(games[:6]))
    return index, games, index_path, training_path, report_path


def test_verified_train_partition_exact_match_and_no_reserved_identifiers(tmp_path):
    index, games, inp, train, out = _fixture(tmp_path)
    before = train.read_bytes()
    report = run_historical_train_verification(
        inp, train, out, ack_train_only=True)
    indexed = build_historical_split_manifest(index)
    assert report["schema"] == "chesssim-historical-train-pgn-verification-v1"
    assert report["status"] == "TRAIN_PARTITION_MATCHED_SOURCE_NOT_VERIFIED"
    assert report["training_games_verified"] == 6
    assert report["train_pgn_sha256"] == sha256(before).hexdigest()
    assert report["index_evidence_sha256"] == indexed["input_evidence_sha256"]
    assert report["train_partition_commitment_sha256"] == (
        indexed["partitions"]["train"]["game_commitment_sha256"])
    assert report["validated_partitions"] == ["train"]
    assert report["reserved_partitions_opened"] is False
    assert report["source_rights_status"] == "OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED"
    assert report["embargo_status"] == "RESERVED_GAMES_NOT_OPENED_EMBARGO_NOT_PROVED"
    assert train.read_bytes() == before
    assert json.loads(out.read_text()) == report
    serialized = json.dumps(report)
    for row in index["games"]:
        assert row["mainline_sha256"] not in serialized
    for game in games:
        assert game.headers["White"] not in serialized
        assert game.headers["Black"] not in serialized
    assert not (tmp_path / "validation.pgn").exists()
    assert not (tmp_path / "holdout.pgn").exists()


def test_parsing_and_commits_ignore_game_order_but_hash_input_bytes(tmp_path):
    index, games, inp, train, out = _fixture(tmp_path)
    expected = verify_historical_training_pgn(index, train.read_bytes())
    reversed_games = _export(list(reversed(games[:6])))
    other = verify_historical_training_pgn(index, reversed_games)
    assert expected["train_partition_commitment_sha256"] == (
        other["train_partition_commitment_sha256"])
    assert expected["index_evidence_sha256"] == other["index_evidence_sha256"]
    assert expected["train_pgn_sha256"] != other["train_pgn_sha256"]
    assert expected["status"] == other["status"]
    idx = deepcopy(index)
    idx["games"].reverse()
    assert verify_historical_training_pgn(idx, train.read_bytes()) == expected


@pytest.mark.parametrize("which", [
    "missing", "extra_reserved_validation", "extra_reserved_holdout",
    "duplicate", "tampered_move", "wrong_date", "false_quality_flag",
    "truncated", "illegal_san", "trailing_garbage", "zero_moves",
])
def test_training_pgn_mismatch_or_invalid_game_fails_closed(tmp_path, which):
    index, games, inp, train, out = _fixture(tmp_path)
    altered = games[:6]
    if which == "missing":
        altered = altered[:-1]
    elif which == "extra_reserved_validation":
        altered = [*altered, games[6]]
    elif which == "extra_reserved_holdout":
        altered = [*altered, games[9]]
    elif which == "duplicate":
        altered = [*altered, games[0]]
    elif which == "tampered_move":
        replacement = _game(0)
        replacement.end().add_variation(next(iter(replacement.end().board().legal_moves)))
        altered = [replacement, *altered[1:]]
    elif which == "wrong_date":
        replacement = _game(0)
        replacement.headers["Date"] = "1978.01.15"
        altered = [replacement, *altered[1:]]
    elif which == "false_quality_flag":
        index["games"][0]["metadata"]["event_known"] = False
        inp.write_text(json.dumps(index))
    elif which == "truncated":
        train.write_bytes(train.read_bytes()[:-7])
    elif which == "illegal_san":
        train.write_bytes(train.read_bytes() + b"\n1. Qh9 1-0\n")
    elif which == "trailing_garbage":
        train.write_bytes(train.read_bytes() + b"\nUNRECOGNIZED_GARBAGE\n")
    elif which == "zero_moves":
        replacement = chess.pgn.Game()
        replacement.headers.update(_game(0).headers)
        altered = [replacement, *altered[1:]]
    if which not in {"truncated", "illegal_san", "trailing_garbage",
                     "false_quality_flag"}:
        train.write_bytes(_export(altered))
    with pytest.raises(ValueError) as err:
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()
    assert not any(game["mainline_sha256"] in str(err.value)
                   for game in index["games"])


def test_same_moves_from_different_initial_fen_are_not_confused(tmp_path):
    index, games, inp, train, out = _fixture(tmp_path)
    altered = _game(0)
    board = altered.board()
    board.halfmove_clock = 5
    altered.setup(board)
    altered.headers["Date"] = games[0].headers["Date"]
    altered.headers["White"] = games[0].headers["White"]
    altered.headers["Black"] = games[0].headers["Black"]
    altered.headers["Event"] = games[0].headers["Event"]
    altered.headers["TimeControl"] = games[0].headers["TimeControl"]
    altered.headers["Result"] = games[0].headers["Result"]
    assert [m.uci() for m in altered.mainline_moves()] == [
        m.uci() for m in games[0].mainline_moves()]
    assert mainline_identity(altered)[0] != mainline_identity(games[0])[0]
    train.write_bytes(_export([altered, *games[1:6]]))
    with pytest.raises(ValueError, match="Training"):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()


def test_source_or_reserved_index_change_does_not_misreport_source_verification(tmp_path):
    index, games, _, train, _ = _fixture(tmp_path)
    first = verify_historical_training_pgn(index, train.read_bytes())
    modified = deepcopy(index)
    modified["sources"][0]["license_id"] = "OTHER_OPERATOR_CLAIM"
    second = verify_historical_training_pgn(modified, train.read_bytes())
    assert first["index_evidence_sha256"] != second["index_evidence_sha256"]
    assert first["train_partition_commitment_sha256"] == (
        second["train_partition_commitment_sha256"])
    assert second["source_rights_status"] == "OPERATOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED"

    reserved = deepcopy(index)
    reserved["games"][9]["mainline_sha256"] = sha256(b"changed sealed claim").hexdigest()
    third = verify_historical_training_pgn(reserved, train.read_bytes())
    assert first["index_evidence_sha256"] != third["index_evidence_sha256"]
    assert first["train_partition_commitment_sha256"] == (
        third["train_partition_commitment_sha256"])
    assert third["reserved_partitions_opened"] is False


@pytest.mark.parametrize("case", ["too_few_years", "unknown_train_date", "bad_index"])
def test_unreliable_index_cannot_produce_positive_train_verification(tmp_path, case):
    index, _, _, train, _ = _fixture(tmp_path)
    index = deepcopy(index)
    if case == "too_few_years":
        index["games"] = index["games"][:3]
    elif case == "unknown_train_date":
        index["games"][0]["date"] = None
    else:
        index["games"][0]["mainline_sha256"] = "bad"
    with pytest.raises(ValueError):
        verify_historical_training_pgn(index, train.read_bytes())


def test_cli_requires_ack_and_does_not_read_unsealed_pgn_implicitly(tmp_path):
    index, _, inp, train, out = _fixture(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["historical-train-verify", "--index", str(inp),
              "--train-pgn", str(train), "--out", str(out)])
    assert exc.value.code == 2
    assert not out.exists()
    assert main(["historical-train-verify", "--index", str(inp),
                 "--train-pgn", str(train), "--out", str(out),
                 "--ack-train-only"]) == 0
    assert json.loads(out.read_text())["status"] == (
        "TRAIN_PARTITION_MATCHED_SOURCE_NOT_VERIFIED")


@pytest.mark.parametrize("case", [
    "bad_index_json", "duplicate_index_key", "train_symlink",
    "train_directory", "train_fifo", "train_missing",
    "train_oversize", "train_invalid_utf8", "out_symlink",
    "out_existing", "same_index_output", "same_train_output",
])
def test_runner_fail_closed_for_unsafe_io(tmp_path, case):
    _, _, inp, train, out = _fixture(tmp_path)
    if case == "bad_index_json":
        inp.write_text("{")
    elif case == "duplicate_index_key":
        raw = inp.read_text()
        inp.write_text(raw[:-1] + ', "target_slug": "forged"}')
    elif case == "train_symlink":
        link = tmp_path / "training-link.pgn"
        link.symlink_to(train)
        train = link
    elif case == "train_directory":
        train = tmp_path
    elif case == "train_fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("POSIX only")
        train.unlink()
        os.mkfifo(train)
    elif case == "train_missing":
        train = tmp_path / "does-not-exist.pgn"
    elif case == "train_oversize":
        train.write_bytes(b"X" * (8 * 1024 * 1024 + 1))
    elif case == "train_invalid_utf8":
        train.write_bytes(b"\xff\xfe")
    elif case == "out_symlink":
        out.symlink_to(tmp_path / "missing")
    elif case == "out_existing":
        out.write_text("DO NOT ALTER")
    elif case == "same_index_output":
        out = inp
    elif case == "same_train_output":
        out = train
    with pytest.raises((ValueError, FileExistsError)):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    if case == "out_existing":
        assert out.read_text() == "DO NOT ALTER"
    else:
        assert not (tmp_path / "report.json").exists()


def test_failure_before_publish_leaves_no_partial_output(tmp_path, monkeypatch):
    _, _, inp, train, out = _fixture(tmp_path)
    orig = train.read_bytes()

    def fail_link(*args, **kwargs):
        raise OSError("synthetic commit failure")

    monkeypatch.setattr(verifier.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic commit failure"):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()
    assert train.read_bytes() == orig
    assert list(tmp_path.glob(".chesssim-historical-train-*.tmp")) == []


def test_no_file_read_without_acknowledgement(tmp_path):
    with pytest.raises(ValueError, match="acknowledgement"):
        run_historical_train_verification(
            tmp_path / "missing-index.json", tmp_path / "missing.pgn",
            tmp_path / "result.json", ack_train_only=False)
    assert not (tmp_path / "result.json").exists()



def test_intermediate_pgn_missing_result_token_must_not_pass(tmp_path):
    _, _, inp, train, out = _fixture(tmp_path)
    # Same indexed moves + headers, but first game has no movetext terminator.
    raw = train.read_text(encoding="utf-8")
    assert "1-0\n\n" in raw
    train.write_text(raw.replace("1-0\n\n", "\n\n", 1), encoding="utf-8")
    with pytest.raises(ValueError):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()


def test_concurrent_report_owner_is_not_overwritten(tmp_path, monkeypatch):
    _, _, inp, train, out = _fixture(tmp_path)
    actual_link = verifier.os.link

    def competing_writer(staged, destination):
        destination = type(out)(destination)
        destination.write_text("other process owns report")
        return actual_link(staged, destination)

    monkeypatch.setattr(verifier.os, "link", competing_writer)
    with pytest.raises(FileExistsError):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert out.read_text() == "other process owns report"
    assert list(tmp_path.glob(".chesssim-historical-train-*.tmp")) == []



@pytest.mark.parametrize("annotation", [
    "inline_comment", "semicolon_comment", "side_variation",
    "nag", "symbolic_nag", "escape_line",
])
def test_training_pgn_rejects_nonmainline_material_even_if_hashes_match(
        tmp_path, annotation):
    _, games, inp, train, out = _fixture(tmp_path)
    raw = train.read_text(encoding="utf-8")
    if annotation == "inline_comment":
        raw = raw.replace("1-0\n\n", "{synthetic reserved-side note} 1-0\n\n", 1)
    elif annotation == "semicolon_comment":
        raw = raw.replace("1-0\n\n", ";synthetic reserved-side note\n1-0\n\n", 1)
    elif annotation == "nag":
        raw = raw.replace("1-0\n\n", "$1 1-0\n\n", 1)
    elif annotation == "symbolic_nag":
        raw = raw.replace("1-0\n\n", "!? 1-0\n\n", 1)
    elif annotation == "escape_line":
        raw = raw.replace("1-0\n\n", "% synthetic reserved-side note\n1-0\n\n", 1)
    else:
        candidate = _game(0)
        first = candidate.variations[0].move
        other = next(move for move in candidate.board().legal_moves if move != first)
        candidate.add_variation(other)
        raw = (candidate.accept(chess.pgn.StringExporter(
            headers=True, variations=True, comments=False)).rstrip()
            + "\n\n" + _export(games[1:6]).decode("utf-8"))
    train.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()



@pytest.mark.parametrize("mutation", ["unknown_header", "oversize_header"])
def test_unhashed_extra_header_data_cannot_hide_in_verified_training_pgn(
        tmp_path, mutation):
    _, games, inp, train, out = _fixture(tmp_path)
    modified = _game(0)
    if mutation == "unknown_header":
        modified.headers["ReservedMoveLine"] = "synthetic hidden PGN move list"
    else:
        modified.headers["Event"] = "Event-" + "x" * 550
    train.write_bytes(_export([modified, *games[1:6]]))
    with pytest.raises(ValueError):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()


def test_valid_setup_fen_with_matching_index_hash_is_accepted(tmp_path):
    index, games, inp, train, out = _fixture(tmp_path)
    special = _game(0)
    initial = special.board()
    initial.halfmove_clock = 5
    special.setup(initial)
    # setup() rewrites a few headers; keep all audited headers equivalent.
    for header in ("Date", "White", "Black", "Event", "TimeControl", "Result"):
        special.headers[header] = games[0].headers[header]
    index["games"][0]["mainline_sha256"] = mainline_identity(special)[0]
    inp.write_text(json.dumps(index), encoding="utf-8")
    train.write_bytes(_export([special, *games[1:6]]))
    result = run_historical_train_verification(
        inp, train, out, ack_train_only=True)
    assert result["training_games_verified"] == 6
    assert result["status"] == "TRAIN_PARTITION_MATCHED_SOURCE_NOT_VERIFIED"



def test_invalid_setup_header_without_fen_does_not_pass_by_default(tmp_path):
    _, games, inp, train, out = _fixture(tmp_path)
    forged = _game(0)
    forged.headers["SetUp"] = "unexpected"
    train.write_bytes(_export([forged, *games[1:6]]))
    with pytest.raises(ValueError):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()


def test_result_header_conflicting_with_movetext_is_rejected(tmp_path):
    _, _, inp, train, out = _fixture(tmp_path)
    raw = train.read_text(encoding="utf-8")
    assert '[Result "1-0"]' in raw
    train.write_text(raw.replace('[Result "1-0"]', '[Result "0-1"]', 1))
    with pytest.raises(ValueError):
        run_historical_train_verification(inp, train, out, ack_train_only=True)
    assert not out.exists()
