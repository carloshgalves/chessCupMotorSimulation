"""Cross-duel diversity reports: actual PGN and metadata invariants (#69)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import chess
import chess.pgn
import pytest

from chesssim.diversity_report import cross_duel_report, export_diversity
from chesssim.game_diversity import mainline_identity

ROOT = Path(__file__).resolve().parents[1]


def write_duel(directory: Path, lines: list[list[str]], *,
               seed: int | None = None, mode: str = "disabled",
               players=("fischer", "tal"), status: str = "COMPLETED") -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    for number, moves in enumerate(lines, 1):
        game = chess.pgn.Game()
        game.headers["White"] = players[(number - 1) % 2]
        game.headers["Black"] = players[number % 2]
        game.headers["Result"] = "1/2-1/2" if status == "COMPLETED" else "*"
        game.headers["BookMode"] = mode
        if seed is not None:
            game.headers["OpeningSeed"] = str(seed)
        node = game
        board = game.board()
        for text in moves:
            move = chess.Move.from_uci(text)
            assert move in board.legal_moves
            node = node.add_variation(move)
            board.push(move)
        digest, plies = mainline_identity(game)
        (directory / f"jogo_{number}.pgn").write_text(str(game) + "\n", encoding="utf-8")
        rows.append({
            "white": players[(number - 1) % 2],
            "black": players[number % 2],
            "plies": plies, "mainline_sha256": digest,
            "status": status, "result": game.headers["Result"],
            "opening_mode": mode, "opening_seed": seed,
        })
    (directory / "summary.json").write_text(
        json.dumps({"players": list(players), "games": rows,
                    "status": status}), encoding="utf-8")
    return directory


OPENING_A = ["g1f3", "g8f6", "g2g3", "g7g6"]
OPENING_B = ["g2g3", "g7g6", "g1f3", "g8f6"]
E4 = ["e2e4", "e7e5", "g1f3", "b8c6"]


def test_cross_duel_exact_duplicates_and_transpositions_are_distinct(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "first", [OPENING_A], seed=100, mode="historical")
    write_duel(root / "second", [OPENING_A, OPENING_B], seed=101, mode="historical")
    report = cross_duel_report([root], opening_plies=4)
    assert report["schema"] == "chesssim-cross-duel-diversity-v1"
    assert report["games_total"] == 3
    assert report["completed_games"] == 3
    assert report["distinct_mainlines"] == 2
    assert report["exact_duplicate_games"] == 1
    assert report["distinct_completed_mainlines"] == 2
    assert report["completed_duplicate_games"] == 1
    assert report["transposed_opening_position_groups"] == 1
    assert report["games"][1]["duplicate_of"] == report["games"][0]["source"]
    assert report["games"][2]["duplicate_of"] is None
    assert report["games"][0]["opening_position_sha256"] == (
        report["games"][2]["opening_position_sha256"])
    assert report["comparisons"][0]["seeds"] == [
        {"seed": "100", "games": 1, "distinct_mainlines": 1},
        {"seed": "101", "games": 2, "distinct_mainlines": 2},
    ]


def test_same_opening_prefix_with_different_suffix_is_not_transposition(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "first", [OPENING_A + ["f1g2", "f8g7"]])
    write_duel(root / "second", [OPENING_A + ["d2d4", "d7d5"]])
    report = cross_duel_report([root], opening_plies=4)
    assert report["distinct_mainlines"] == 2
    assert report["transposed_opening_position_groups"] == 0
    assert report["games"][0]["opening_prefix_sha256"] == (
        report["games"][1]["opening_prefix_sha256"])


def test_incomplete_and_zero_ply_never_claim_finished_evidence(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "empty", [[]], status="INCOMPLETE")
    write_duel(root / "partial", [E4[:2]], status="INCOMPLETE")
    report = cross_duel_report([root], opening_plies=4)
    assert report["completed_games"] == 0
    assert report["incomplete_or_unscored_games"] == 2
    assert report["games_with_moves"] == 1
    assert report["distinct_mainlines"] == 1
    assert report["distinct_completed_mainlines"] == 0
    assert report["completed_duplicate_games"] == 0
    assert report["games"][0]["opening_position_sha256"] is None
    assert report["games"][1]["opening_position_sha256"] is None


def test_only_summary_verified_against_pgn_and_never_overwrites(tmp_path):
    root = tmp_path / "runs"
    folder = write_duel(root / "pair", [E4], seed=2)
    out = tmp_path / "diversity.json"
    report = export_diversity([root], out, opening_plies=4)
    assert json.loads(out.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError):
        export_diversity([root], out)
    assert report["games"][0]["source"].startswith("root-01/")
    assert str(tmp_path) not in json.dumps(report)
    # Replacing a move while preserving the stale checksum is a hard failure.
    game_file = folder / "jogo_1.pgn"
    game_file.write_text(game_file.read_text(encoding="utf-8").replace("e4", "d4"),
                         encoding="utf-8")
    with pytest.raises(ValueError, match="PGN"):
        cross_duel_report([root], opening_plies=4)


def test_provenance_rejects_mislabelled_seed_mode_and_colors(tmp_path):
    root = tmp_path / "runs"
    folder = write_duel(root / "pair", [E4], seed=7, mode="historical")
    summary = folder / "summary.json"
    original = json.loads(summary.read_text(encoding="utf-8"))
    for field, value in (
        ("opening_seed", 8), ("opening_mode", "disabled"), ("white", "tal")
    ):
        changed = json.loads(json.dumps(original))
        changed["games"][0][field] = value
        summary.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            cross_duel_report([root], opening_plies=4)
    summary.write_text(json.dumps(original), encoding="utf-8")
    assert cross_duel_report([root], opening_plies=4)["games_total"] == 1


def test_incomplete_duplicate_does_not_falsify_completed_games(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "partial", [E4], status="INCOMPLETE")
    write_duel(root / "complete", [E4], status="COMPLETED")
    report = cross_duel_report([root], opening_plies=4)
    assert report["exact_duplicate_games"] == 1
    assert report["completed_games"] == 1
    assert report["distinct_completed_mainlines"] == 1
    assert report["completed_duplicate_games"] == 0


def test_shared_opening_position_from_unrelated_players_not_called_transposition(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "a", [OPENING_A], players=("fischer", "tal"))
    write_duel(root / "b", [OPENING_B], players=("kasparov", "carlsen"))
    report = cross_duel_report([root], opening_plies=4)
    assert report["transposed_opening_position_groups"] == 0


def test_history_not_accidentally_read_from_unrelated_summary(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "pair", [E4])
    extra = root / "other"
    extra.mkdir()
    (extra / "summary.json").write_text('{"schema":"not-a-duel"}', encoding="utf-8")
    report = cross_duel_report([root])
    assert report["non_duel_manifests_ignored"] == 1
    assert report["games_total"] == 1


def test_symlinked_pgn_and_summary_are_rejected(tmp_path):
    root = tmp_path / "runs"
    folder = write_duel(root / "pair", [E4])
    original = folder / "jogo_1.pgn"
    mirror = tmp_path / "mirror.pgn"
    mirror.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(mirror)
    with pytest.raises(ValueError):
        cross_duel_report([root])
    original.unlink()
    original.write_bytes(mirror.read_bytes())
    summary = folder / "summary.json"
    alternate = tmp_path / "summary-data.json"
    alternate.write_bytes(summary.read_bytes())
    summary.unlink()
    summary.symlink_to(alternate)
    with pytest.raises(ValueError):
        cross_duel_report([root])


def test_invalid_input_and_missing_pgn_fail_closed(tmp_path):
    root = tmp_path / "runs"
    folder = write_duel(root / "pair", [E4])
    with pytest.raises(ValueError):
        cross_duel_report([root], opening_plies=True)
    with pytest.raises(ValueError):
        cross_duel_report([root], opening_plies=41)
    (folder / "jogo_1.pgn").unlink()
    with pytest.raises(ValueError):
        cross_duel_report([root])


def test_duplicate_roots_do_not_double_count_games(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "pair", [E4])
    report = cross_duel_report([root, root / "pair"])
    assert report["games_total"] == 1


def test_uci_and_official_outputs_are_never_modified(tmp_path):
    root = tmp_path / "runs"
    folder = write_duel(root / "pair", [E4])
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    _ = cross_duel_report([root], opening_plies=4)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_cli_exports_json_without_engine_or_network(tmp_path):
    root = tmp_path / "runs"
    write_duel(root / "pair", [E4])
    out = tmp_path / "report.json"
    result = subprocess.run([
        sys.executable, "-m", "chesssim", "diversity-report",
        "--runs", str(root), "--opening-plies", "4", "--out", str(out),
    ], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '"exact_duplicate_games": 0' in result.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["games_total"] == 1
