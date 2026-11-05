"""Contract tests for persistent knockout scheduling and crash recovery (no external engine)."""
from __future__ import annotations

import json
import chess
import chess.pgn
import pytest

from chesssim import tournament
from chesssim.catalog import find_player
from chesssim.game_diversity import mainline_identity
from chesssim.protocol import OFFICIAL


SEQUENCES = {
    "1-0": ["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"],
    "0-1": ["f2f3", "e7e5", "g2g4", "d8h4"],
    "1/2-1/2": ["g1f3", "g8f6", "f3g1", "f6g8"] * 2,
}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    created = []
    calls = []
    results = []

    def preflight(root, slugs, preset):
        del root, preset
        return {"engine_sha256": "test-engine",
                "profile_sha256": {s: "profile-" + s for s in slugs},
                "book_sha256": {}, "books_by_player": {s: [] for s in slugs}}

    monkeypatch.setattr(tournament, "_preflight", preflight)

    def fake_play_duel(root, output_dir, a, b, *, depth, clock, reverse,
                       max_plies, use_books):
        del root, depth, max_plies
        assert reverse is True
        assert clock in tournament.CLOCKS.values()
        assert use_books == (clock == (600, 2))
        results_pair = results.pop(0) if results else ["1-0", "0-1"]
        folder = output_dir / f"{a.slug}-vs-{b.slug}" / f"attempt-{len(calls):04d}"
        folder.mkdir(parents=True)
        state = tournament.load_tournament(output_dir.parents[2])
        games = []
        metadata = []
        for i, result in enumerate(results_pair):
            white, black = ((a, b) if i == 0 else (b, a))
            game = chess.pgn.Game()
            game.headers["White"] = white.name
            game.headers["Black"] = black.name
            node = game
            board = game.board()
            for uci in SEQUENCES[result]:
                move = chess.Move.from_uci(uci)
                assert move in board.legal_moves
                node = node.add_variation(move)
                board.push(move)
            outcome = board.outcome(claim_draw=True)
            assert outcome is not None
            assert outcome.result() == result
            game.headers["Result"] = result
            game.headers["Termination"] = outcome.termination.name
            digest, plies = mainline_identity(game)
            games.append(game)
            metadata.append({"status": "COMPLETED", "result": result,
                             "termination": outcome.termination.name,
                             "mainline_sha256": digest, "plies": plies})
        (folder / "duelo.pgn").write_text(
            "\n\n".join(str(g) for g in games) + "\n", encoding="utf-8")
        summary = {
            "status": "COMPLETED", "players": [a.slug, b.slug],
            "file_hashes": {"exe": state["resources"]["engine_sha256"],
                            a.slug: state["resources"]["profile_sha256"][a.slug],
                            b.slug: state["resources"]["profile_sha256"][b.slug]},
            "book_hashes": {},
            "protocol": OFFICIAL.json() if clock == (600, 2)
                        else {"time_control": f"{clock[0]}+{clock[1]}"},
            "games": metadata,
        }
        (folder / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8")
        calls.append(folder)
        return folder

    monkeypatch.setattr(tournament, "play_duel", fake_play_duel)

    def begin(players=("fischer", "tal"), *, preset="official", max_pairs=3):
        out = tmp_path / f"copa-{len(created)}"
        tournament.create_tournament(
            tmp_path, out, participants=list(players), seed=2026, preset=preset,
            max_tiebreak_pairs=max_pairs)
        created.append(out)
        return out

    return begin, calls, results, fake_play_duel, tmp_path


def test_two_players_complete_two_legal_games(harness):
    begin, calls, _, _, root = harness
    run = begin()
    report = tournament.run_tournament(root, run)
    assert report["status"] == "COMPLETED"
    assert report["champion"] in ("fischer", "tal")
    assert len(calls) == 1
    state = tournament.load_tournament(run)
    match = state["rounds"][0]["matches"][0]
    assert report["champion"] == match["players"][0]
    assert len(match["attempts"][0]["mainline_sha256"]) == 2
    assert tournament.run_tournament(root, run) == report
    assert len(calls) == 1


def test_bracket_rounds_resume_without_replaying_finished_pairs(harness):
    begin, calls, _, _, root = harness
    run = begin(("lasker", "marshall", "fischer", "tal"), preset="rapid")
    first = tournament.run_tournament(root, run, limit_pairs=1)
    assert first["status"] == "PAUSED"
    assert first["matches_completed"] == 1
    second = tournament.run_tournament(root, run, limit_pairs=1)
    assert second["status"] == "PAUSED"
    assert second["matches_completed"] == 2
    assert second["rounds"] == 2
    final = tournament.run_tournament(root, run, limit_pairs=1)
    assert final["status"] == "COMPLETED"
    assert final["matches_completed"] == 3
    assert len(calls) == 3
    assert len({str(x) for x in calls}) == 3


def test_tie_never_auto_advances_and_requires_arbitration(harness):
    begin, calls, _, _, root = harness
    results.extend([["1/2-1/2", "1/2-1/2"]])
    run = begin(max_pairs=1)
    report = tournament.run_tournament(root, run)
    assert report["status"] == "BLOCKED"
    assert report["champion"] is None
    assert report["matches_pending_tiebreak"] == ["r01-m01"]
    match = tournament.load_tournament(run)["rounds"][0]["matches"][0]
    with pytest.raises(ValueError, match="Somente empate"):
        tournament.resolve_tiebreak(run, "r01-m01", "spassky",
                                   "Decisão externa documentada")
    champion = match["players"][0]
    solved = tournament.resolve_tiebreak(run, "r01-m01", champion,
                                         "Desempate externo registrado pelo árbitro")
    assert solved["status"] == "COMPLETED"
    assert solved["champion"] == champion
    assert tournament.load_tournament(run)["resolution_log"][0]["method"].startswith("EXPLICIT_")


def test_tie_second_pair_can_decide_match(harness):
    begin, calls, _, _, root = harness
    results.extend([["1-0", "1-0"], ["1-0", "0-1"]])
    run = begin()
    report = tournament.run_tournament(root, run)
    assert report["status"] == "COMPLETED"
    assert len(calls) == 2
    assert len(tournament.load_tournament(run)["rounds"][0]["matches"][0]["attempts"]) == 2


def test_crash_after_completed_duel_is_recovered_from_disk(harness):
    begin, calls, _, fake, root = harness
    run = begin()
    match = tournament.load_tournament(run)["rounds"][0]["matches"][0]
    a, b = (find_player(x) for x in match["players"])
    fake(root, tournament._duel_dir(run, match), a, b, depth=None,
         clock=(600, 2), reverse=True, max_plies=600, use_books=True)
    report = tournament.run_tournament(root, run)
    assert report["status"] == "COMPLETED"
    assert len(calls) == 1
    assert len(tournament.load_tournament(run)["rounds"][0]["matches"][0]["attempts"]) == 1


def test_mutated_pgn_after_checkpoint_is_rejected(harness):
    begin, calls, _, _, root = harness
    run = begin()
    tournament.run_tournament(root, run)
    pgn = calls[0] / "duelo.pgn"
    pgn.write_text(pgn.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="alterado"):
        tournament.run_tournament(root, run)


def test_incomplete_attempt_requires_explicit_retry(harness, monkeypatch):
    begin, calls, _, fake, root = harness
    run = begin()

    def interrupted(root_arg, output_dir, a, b, **kwargs):
        folder = output_dir / f"{a.slug}-vs-{b.slug}" / "attempt-aborted"
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(
            json.dumps({"status": "INCOMPLETE"}), encoding="utf-8")
        return folder

    monkeypatch.setattr(tournament, "play_duel", interrupted)
    result = tournament.run_tournament(root, run)
    assert result["matches_retry_required"] == ["r01-m01"]
    assert result["champion"] is None
    blocked = tournament.run_tournament(root, run)
    assert blocked["status"] == "BLOCKED"
    monkeypatch.setattr(tournament, "play_duel", fake)
    recovered = tournament.run_tournament(root, run, retry_incomplete=True)
    assert recovered["status"] == "COMPLETED"
    assert len(calls) == 1


def test_freezes_engine_and_profiles_before_resume(harness, monkeypatch):
    begin, calls, _, _, root = harness
    run = begin()
    monkeypatch.setattr(tournament, "_preflight", lambda *args: {"changed": True})
    with pytest.raises(RuntimeError, match="divergiram"):
        tournament.run_tournament(root, run)
    assert not calls


def test_invalid_roster_rejected_before_creating_files(harness):
    begin, _, _, _, root = harness
    with pytest.raises(ValueError, match="repetidos"):
        begin(("fischer", "fischer"))
    assert not (root / "copa-0").exists()
    with pytest.raises(ValueError, match="indisponíveis"):
        begin(("fischer", "mequinho"))
    assert not (root / "copa-0").exists()
    with pytest.raises(ValueError, match="2, 4, 8"):
        begin(("fischer", "tal", "lasker"))
    assert not (root / "copa-0").exists()


def test_cli_status_without_rodent(harness, capsys):
    begin, _, _, _, _ = harness
    run = begin()
    from chesssim.cli import main
    assert main(["tournament-status", "--run", str(run)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["participants"] == 2
    assert payload["status"] == "READY"
