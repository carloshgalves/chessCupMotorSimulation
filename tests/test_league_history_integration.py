"""End-to-end: League journal -> archive/replay/exports and isolated Elo."""
from __future__ import annotations

import json
import time

import chess
import chess.pgn
import pytest

from chesssim import history_archive, leagues, competition_rating, history_stats
from chesssim.catalog import find_player


def config(*, mode="engine_full"):
    return {
        "name": "Liga Testada", "season": "2026", "format": "single",
        "modality": "rapid", "execution_mode": mode,
        "participants": ["fischer", "tal"], "depth": 2, "seed": 42,
    }


def complete(manager, ident):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = manager.detail(ident)
        if not manager.busy and state["status"] != "RUNNING":
            return state
        time.sleep(.02)
    raise AssertionError("Liga não terminou")


@pytest.fixture
def league(tmp_path, monkeypatch):
    root = tmp_path / "engine"
    root.mkdir()
    work = tmp_path / "workspace"
    work.mkdir()
    exe = root / "rodent-iv-plain.exe"
    exe.write_bytes(b"test-engine")
    for slug in ("fischer", "tal"):
        (root / (slug + ".txt")).write_bytes(("profile:" + slug).encode())
    def validate(base, a, b):
        return exe, {p.slug: root / (p.slug + ".txt") for p in (a, b)}
    monkeypatch.setattr(leagues, "validate", validate)
    def runner(base, match, cfg, stopping):
        game = chess.pgn.Game()
        game.headers.update({
            "White": find_player(match["white"]).name,
            "Black": find_player(match["black"]).name,
            "Round": str(match["round"]), "Result": "0-1",
            "Termination": "CHECKMATE",
            "TimeControl": f"{cfg['clock'][0]}+{cfg['clock'][1]}",
            "EngineSHA256": leagues.file_hash(exe),
            "WhiteProfileSHA256": leagues.file_hash(root / (match["white"] + ".txt")),
            "BlackProfileSHA256": leagues.file_hash(root / (match["black"] + ".txt")),
        })
        board = game.board()
        node = game
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            node = node.add_variation(move)
            board.push(move)
        assert board.is_checkmate()
        return game, {"status": "COMPLETED", "result": "0-1",
                      "termination": "CHECKMATE"}
    mgr = leagues.LeagueManager(root, work, runner=runner)
    try:
        yield mgr, work
    finally:
        mgr.shutdown()


def test_league_verified_pgn_is_scored_replayed_and_exported(league):
    manager, work = league
    ident = manager.create(config())["id"]
    manager.run(ident)
    assert complete(manager, ident)["completed_games"] == 1
    before = {p.relative_to(work): p.read_bytes()
              for p in (work / "leagues").rglob("*") if p.is_file()}
    report = history_archive.archive(work, "fischer", "origin=league")
    assert report["total"] == report["overview"]["verified"] == 1
    row = report["games"][0]
    assert row["id"].startswith("league:" + ident + ":")
    assert row["competition"] == "Liga · Liga Testada / 2026"
    assert row["origin"] == "league"
    assert row["source_kind"] == "engine_full"
    assert row["started_at_utc"] and row["finished_at_utc"]
    assert row["duration_seconds"] >= 0
    assert row["evidence_pgn_sha256"] and row["engine_sha256"]
    assert row["receipt_sha256"] and row["rating_eligible"]
    assert history_archive.replay(work, "fischer", row["id"])["states"][-1]["san"] == "Qh4#"
    pgn, _, _ = history_archive.export_page(work, "fischer", "origin=league", "pgn")
    assert b"ChessSimOrigin" in pgn
    assert row["evidence_pgn_sha256"].encode("ascii") in pgn
    csv, _, _ = history_archive.export_page(work, "fischer", "origin=league", "csv")
    assert b"receipt_sha256" in csv
    json_data, _, _ = history_archive.export_page(work, "fischer", "origin=league", "json")
    assert json.loads(json_data)["games"][0]["id"] == row["id"]
    legacy = history_stats.player_stats(work, "fischer")
    assert legacy["totals"]["completed"] == 1
    assert legacy["coverage"]["league_verified_games"] == 1
    assert "league_local" in legacy["scope"]["sources"]
    rated = competition_rating.rating_report(work, modality="rapid", include_ledger=True)
    assert rated["counts"]["scored_games"] == 1
    assert rated["rating_ledger"][0]["source"] == "league"
    assert sum(r["games"] for r in rated["ratings"]) == 2
    after = {p.relative_to(work): p.read_bytes()
             for p in (work / "leagues").rglob("*") if p.is_file()}
    assert before == after, "Archive/Elo GET must not mutate league receipts"


def test_projection_not_scored_and_tampered_uci_excluded(league):
    manager, work = league
    simulated = manager.create(config(mode="fast_statistical"))["id"]
    manager.run(simulated)
    assert complete(manager, simulated)["completed_games"] == 1
    data = history_archive.archive(work, "fischer", "origin=projection")
    assert data["total"] == 1
    assert data["games"][0]["pgn"] is False
    assert data["games"][0]["rating_eligible"] is False
    assert competition_rating.rating_report(work, modality="rapid")["counts"]["scored_games"] == 0
    with pytest.raises(history_archive.ArchiveInput):
        history_archive.replay(work, "fischer", data["games"][0]["id"])
    actual = manager.create(config())["id"]
    manager.run(actual)
    assert complete(manager, actual)["completed_games"] == 1
    assert history_archive.archive(work, "fischer", "origin=league")["total"] == 1
    receipt = next((work / "leagues" / actual / "games").glob("*.pgn"))
    receipt.write_bytes(receipt.read_bytes() + b"\n[Event \"tampered\"]\n")
    assert history_archive.archive(work, "fischer", "origin=league")["total"] == 0
    assert competition_rating.rating_report(work, modality="rapid")["counts"]["scored_games"] == 0
