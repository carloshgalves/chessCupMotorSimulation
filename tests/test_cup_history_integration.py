"""Cup #58 acceptance: verified UCI outcomes enter History/Elo, projections do not."""
from __future__ import annotations

import time
from types import SimpleNamespace

import chess
import pytest

from chesssim import cups
from chesssim import cup_games
from chesssim.competition_rating import build_rating_ledger, rating_report
from chesssim.history_stats import player_stats, verified_competition_games


def settings(*, method="engine_full"):
    return {
        "name": "Copa dos Imortais", "edition": "2026",
        "preset": "custom", "participants": ["fischer", "tal"],
        "execution_mode": method, "seed": 2025, "depth": 4,
    }


def until_done(manager: cups.CupManager, ident: str):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        report = manager.detail(ident)
        if not manager.busy and report["status"] != "RUNNING":
            return report
        time.sleep(0.02)
    raise AssertionError("Cup worker did not complete")


class LegalUci:
    def play(self, board, limit, game, info):
        uci = ("f2f3", "e7e5", "g2g4", "d8h4")[board.ply()]
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        return SimpleNamespace(move=move)

    def quit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def local_cups(tmp_path, monkeypatch):
    root = tmp_path / "engine"
    root.mkdir()
    workspace = tmp_path / "local"
    workspace.mkdir()
    exe = root / "rodent-iv-plain.exe"
    exe.write_bytes(b"fixture legal UCI")
    profiles = {}
    for slug in ("fischer", "tal"):
        p = root / (slug + ".txt")
        p.write_text("Rodent profile for " + slug, encoding="utf-8")
        profiles[slug] = p

    def validate(root_arg, white, black):
        assert root_arg == root
        return exe, {white.slug: profiles[white.slug],
                     black.slug: profiles[black.slug]}

    monkeypatch.setattr(cups, "validate", validate)
    manager = cups.CupManager(root, workspace)
    manager.runner = lambda root_arg, spec, cfg, resources, stop: (
        cup_games.play_cup_game(
            root_arg, spec, cfg, resources, stop,
            factory=lambda *args, **kwargs: LegalUci(),
        )
    )
    try:
        yield workspace, manager
    finally:
        manager.shutdown()


def test_cup_uci_game_updates_profile_history_and_classical_rating(local_cups):
    workspace, manager = local_cups
    ident = manager.create(settings())["id"]
    manager.run(ident)
    result = until_done(manager, ident)
    assert result["completed_games"] == 1
    assert result["recent"][0]["source"] == "engine_full"
    assert result["available_pgn"] == 1
    assert result["status"] == "PAUSED"

    before = {p.relative_to(workspace).as_posix(): p.read_bytes()
              for p in (workspace / "cups" / ident).rglob("*") if p.is_file()}
    fischer = player_stats(workspace, "fischer")
    tal = player_stats(workspace, "tal")
    assert fischer["totals"] == {
        "games": 1, "completed": 1, "wins": 0, "draws": 0,
        "losses": 1, "incomplete": 0, "tournaments": 1, "duels": 1,
    }
    assert tal["totals"]["games"] == 1
    assert tal["totals"]["wins"] == 1
    assert tal["totals"]["duels"] == 1
    assert fischer["coverage"]["cup_verified_games"] == 1
    assert fischer["coverage"]["arena_verified_games"] == 0
    assert fischer["coverage"]["tournament_verified_games"] == 0
    assert fischer["coverage"]["cups_seen"] == 1
    assert "cup_local" in fischer["scope"]["sources"]
    assert tal["opponents"][0]["slug"] == "fischer"

    records, coverage = verified_competition_games(workspace)
    assert len(records) == 1
    game = records[0]
    assert game["origin"] == "cup"
    assert game["id"].startswith("cup:" + ident)
    assert game["time_control"] == "classical"
    assert game["clock"] == [5400, 30]
    assert game["rating_eligible"] is True
    assert len(game["evidence_pgn_sha256"]) == 64
    assert coverage["verified_games"] == 1
    assert coverage["cup_verified_games"] == 1
    report = rating_report(workspace, modality="classical")
    assert report["counts"]["scored_games"] == 1
    assert report["available_modalities"]["classical"] == 1
    assert rating_report(workspace, modality="rapid")["counts"]["scored_games"] == 0
    assert rating_report(workspace, modality="blitz")["counts"]["scored_games"] == 0
    after = {p.relative_to(workspace).as_posix(): p.read_bytes()
             for p in (workspace / "cups" / ident).rglob("*") if p.is_file()}
    assert after == before, "GET stats and Elo must not rewrite any Cup artifacts"


def test_synthetic_cup_game_never_adds_history_or_elo(local_cups):
    workspace, manager = local_cups
    ident = manager.create(settings(method="fast_statistical"))["id"]
    manager.run(ident)
    outcome = until_done(manager, ident)
    assert outcome["completed_games"] == 1
    assert outcome["available_pgn"] == 0
    fischer = player_stats(workspace, "fischer")
    assert fischer["totals"]["games"] == 0
    assert fischer["totals"]["tournaments"] == 0
    assert fischer["coverage"]["cup_synthetic_excluded"] == 1
    records, coverage = verified_competition_games(workspace)
    assert records == []
    assert coverage["cup_synthetic_excluded"] == 1
    assert rating_report(workspace, modality="classical")["counts"]["scored_games"] == 0


def test_tampered_cup_pgn_removed_from_statistics_with_coverage(local_cups):
    workspace, manager = local_cups
    ident = manager.create(settings())["id"]
    manager.run(ident)
    game = until_done(manager, ident)["recent"][0]
    pgn = workspace / "cups" / ident / "games" / (game["id"] + ".pgn")
    pgn.write_bytes(pgn.read_bytes() + b"\n; tampered")
    report = player_stats(workspace, "fischer")
    assert report["totals"]["games"] == 0
    assert report["coverage"]["cup_verified_games"] == 0
    assert report["coverage"]["rejected_records"] >= 1
    assert report["coverage"]["partial"] is True
    records, coverage = verified_competition_games(workspace)
    assert records == []
    assert coverage["rejected_records"] >= 1


def test_armageddon_record_counts_in_history_but_not_elo():
    base = {
        "id": "cup:abc:r01-m01-s5-g1",
        "duel_id": "cup:abc:r01-m01",
        "white": "fischer", "black": "tal",
        "status": "COMPLETED", "result": "1/2-1/2",
        "origin": "cup", "recorded_utc": "2026-11-08T00:00:00+00:00",
        "time_control": "unclassified", "clock": None,
        "rating_eligible": False,
        "evidence_pgn_sha256": "a" * 64,
        "engine_strength_config": {
            "engine_sha256": "b" * 64,
            "white_profile_sha256": "c" * 64,
            "black_profile_sha256": "d" * 64,
        },
    }
    report = build_rating_ledger([base], modality="unclassified",
                                  roster=("fischer", "tal"))
    assert report["counts"]["scored_games"] == 0
    assert report["counts"]["ignored_ineligible"] == 1
    with pytest.raises(ValueError, match="booleana"):
        build_rating_ledger([{**base, "rating_eligible": "false"}],
                            modality="unclassified", roster=("fischer", "tal"))


def test_cup_root_symlink_never_leaks_external_pgns(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (workspace / "cups").symlink_to(external, target_is_directory=True)
    report = player_stats(workspace, "fischer")
    assert report["totals"]["games"] == 0
    assert report["coverage"]["rejected_records"] == 1
    assert report["coverage"]["partial"] is True
    assert not any(external.iterdir())
