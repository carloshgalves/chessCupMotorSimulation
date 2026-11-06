"""Adversarial contract for local, non-historical player statistics."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import random
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import arena, history_stats, tournament
from chesssim.catalog import find_player
from chesssim.game_diversity import mainline_identity
from chesssim.protocol import OFFICIAL

MATES = {
    "0-1": ("f2f3", "e7e5", "g2g4", "d8h4"),
    "1-0": ("e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"),
    "1/2-1/2": ("g1f3", "g8f6", "f3g1", "f6g8") * 2,
}


def game(white: str, black: str, result: str, *, tournament_game=False):
    obj = chess.pgn.Game()
    obj.headers["White"] = find_player(white).name
    obj.headers["Black"] = find_player(black).name
    obj.headers["Event"] = "ChessSimulation local"
    obj.headers["Date"] = "2026.11.06"
    obj.headers["Result"] = result
    if tournament_game:
        obj.headers["EngineSHA256"] = "engine"
        obj.headers["WhiteProfileSHA256"] = "profile-" + white
        obj.headers["BlackProfileSHA256"] = "profile-" + black
        obj.headers["TimeControl"] = "600+2"
    pos = obj.board()
    node = obj
    sequence = MATES["0-1"][:2] if result == "*" else MATES[result]
    for move_uci in sequence:
        move = chess.Move.from_uci(move_uci)
        assert move in pos.legal_moves
        node = node.add_variation(move)
        pos.push(move)
    if tournament_game:
        result_on_board = pos.outcome(claim_draw=True)
        assert result_on_board and result_on_board.result() == result
        obj.headers["Termination"] = result_on_board.termination.name
    return obj


def session(workspace: Path, index: int, *, results=("1-0", "0-1"),
            broken=False, duplicate=False, status=None):
    sid = f"session-20261106T12{index:02d}00Z-{index:012x}"
    folder = workspace / sid
    folder.mkdir(parents=True)
    session_info = {
        "schema": "chesssim-arena-session-v1", "id": sid,
        "created_utc": "2026-11-06T12:00:00+00:00",
        "status": status or ("INCOMPLETE" if "*" in results else "COMPLETED"),
        "config": {"white": "fischer", "black": "tal", "reverse": len(results) > 1},
    }
    (folder / "session.json").write_text(json.dumps(session_info))
    duel = folder / "duels/fischer-vs-tal/20261106T120000Z-attempt"
    duel.mkdir(parents=True)
    games = []
    for i, result in enumerate(results, 1):
        white, black = ("fischer", "tal") if i == 1 else ("tal", "fischer")
        pgn = game(white, black, result)
        raw = (str(pgn) + "\n").encode()
        (duel / f"jogo_{i}.pgn").write_bytes(raw)
        signature, plies = mainline_identity(pgn)
        games.append({"status": "INCOMPLETE" if result == "*" else "COMPLETED",
                      "result": result, "mainline_sha256": signature, "plies": plies})
    if broken:
        games[0]["result"] = "0-1" if results[0] == "1-0" else "1-0"
    summary = {"status": "INCOMPLETE" if "*" in results else "COMPLETED",
               "players": ["fischer", "tal"], "games": games}
    (duel / "summary.json").write_text(json.dumps(summary))
    return folder, duel


def cup(workspace: Path, name: str = "cup-01", *, results=("1-0", "0-1"),
        attempt_name="attempt-01"):
    root = workspace / "tournaments" / name
    root.mkdir(parents=True, exist_ok=True)
    players = ["fischer", "tal"]
    shuffled = list(players)
    random.Random(2026).shuffle(shuffled)
    rd = tournament._round(1, shuffled)
    state = {
        "schema": tournament.SCHEMA,
        "participants": players, "initial_bracket": shuffled,
        "seed": 2026, "rounds": [rd],
        "max_tiebreak_pairs": 3, "max_game_wall_seconds": None,
        "champion": None, "status": "READY",
        "created_utc": "2026-11-06T13:00:00Z",
        "preset": "official", "clock": [600, 2],
        "resources": {
            "engine_sha256": "engine",
            "profile_sha256": {p: "profile-" + p for p in players},
            "book_sha256": {}, "books_by_player": {p: [] for p in players},
        },
    }
    match = rd["matches"][0]
    a, b = match["players"]
    folder_rel = (f"matches/{match['id']}/duels/"
                  f"{a}-vs-{b}/{attempt_name}")
    folder = root / folder_rel
    folder.mkdir(parents=True)
    pgns = [game(a, b, results[0], tournament_game=True),
            game(b, a, results[1], tournament_game=True)]
    combined = ("\n\n".join(str(p) for p in pgns) + "\n").encode()
    (folder / "duelo.pgn").write_bytes(combined)
    metadata = []
    identities = []
    for pgn, result in zip(pgns, results):
        identity, plies = mainline_identity(pgn)
        identities.append(identity)
        metadata.append({
            "status": "COMPLETED", "result": result,
            "termination": pgn.headers["Termination"],
            "mainline_sha256": identity, "plies": plies,
        })
    summary = {
        "status": "COMPLETED", "players": [a, b],
        "file_hashes": {"exe": "engine", a: "profile-" + a,
                        b: "profile-" + b},
        "book_hashes": {}, "protocol": OFFICIAL.json(),
        "games": metadata,
    }
    raw = json.dumps(summary).encode()
    (folder / "summary.json").write_bytes(raw)
    attempt = {
        "folder": folder_rel, "results": list(results),
        "mainline_sha256": identities,
        "summary_sha256": sha256(raw).hexdigest(),
        "pgn_sha256": sha256(combined).hexdigest(),
    }
    match["attempts"].append(attempt)
    (root / "tournament.json").write_text(json.dumps(state))
    return root, state, folder


def stat(workspace: Path, slug="fischer"):
    return history_stats.player_stats(workspace, slug)


def test_empty_workspace_explicitly_zero_no_fake_ratings(tmp_path):
    report = stat(tmp_path)
    assert report["schema"] == history_stats.SCHEMA
    assert report["slug"] == "fischer"
    assert all(value == 0 for value in report["totals"].values())
    assert report["scope"]["from_utc"] is None
    assert report["scope"]["to_utc"] is None
    assert report["opponents"] == []
    assert report["coverage"]["partial"] is False
    assert "elo" not in json.dumps(report).lower()
    with pytest.raises(ValueError):
        stat(tmp_path, "../../etc/passwd")


def test_reversed_colors_are_two_games_one_duel_with_correct_perspective(tmp_path):
    session(tmp_path, 0, results=("1-0", "1-0"))
    report = stat(tmp_path)
    assert report["totals"] == {
        "tournaments": 0, "duels": 1, "games": 2, "completed": 2,
        "wins": 1, "draws": 0, "losses": 1, "incomplete": 0,
    }
    assert report["opponents"][0]["slug"] == "tal"
    assert report["opponents"][0]["wins"] == 1
    assert report["opponents"][0]["duels"] == 1
    tal = stat(tmp_path, "tal")
    assert tal["totals"]["wins"] == 1 and tal["totals"]["losses"] == 1
    assert report["scope"]["from_utc"].startswith("2026-11-06")


def test_rematch_and_identical_game_mainlines_are_independent_games(tmp_path):
    session(tmp_path, 0, results=("0-1", "0-1"))
    session(tmp_path, 1, results=("0-1", "0-1"))
    report = stat(tmp_path)
    assert report["totals"]["games"] == 4
    assert report["totals"]["duels"] == 2
    assert report["totals"]["wins"] == 2
    assert report["totals"]["losses"] == 2
    assert report["coverage"]["arena_verified_games"] == 4
    assert report["coverage"]["tournament_verified_games"] == 0


def test_partial_and_failed_attempts_never_create_wins(tmp_path):
    session(tmp_path, 0, results=("*",), status="INCOMPLETE")
    _, folder = session(tmp_path, 1, results=("1-0",))
    summary = json.loads((folder / "summary.json").read_text())
    summary["status"] = "FAILED"
    (folder / "summary.json").write_text(json.dumps(summary))
    manifest = folder.parents[2] / "session.json"
    session_state = json.loads(manifest.read_text())
    session_state["status"] = "FAILED"
    manifest.write_text(json.dumps(session_state))
    result = stat(tmp_path)
    assert result["totals"]["games"] == 1
    assert result["totals"]["incomplete"] == 1
    assert result["totals"]["completed"] == 0
    assert result["totals"]["wins"] == result["totals"]["draws"] == 0
    assert result["coverage"]["failed_attempts"] == 1


def test_corrupt_game_is_excluded_and_coverage_explains_partial(tmp_path):
    session(tmp_path, 0, results=("1-0", "0-1"), broken=True)
    result = stat(tmp_path)
    assert result["totals"]["games"] == 1
    assert result["totals"]["completed"] == 1
    assert result["coverage"]["candidate_games"] == 2
    assert result["coverage"]["rejected_games"] == 1
    assert result["coverage"]["partial"] is True


def test_tournament_ledger_two_colors_and_manifest_no_double_count(tmp_path):
    root, state, _ = cup(tmp_path, results=("1-0", "0-1"))
    match = state["rounds"][0]["matches"][0]
    match["attempts"].append(dict(match["attempts"][0]))
    (root / "tournament.json").write_text(json.dumps(state))
    p = stat(tmp_path, "fischer")
    assert p["totals"]["tournaments"] == 1
    assert p["totals"]["duels"] == 1
    assert p["totals"]["games"] == 2
    assert p["coverage"]["verified_games"] == 2
    assert p["coverage"]["arena_verified_games"] == 0
    assert p["coverage"]["tournament_verified_games"] == 2
    assert p["coverage"]["rejected_games"] == 0


def test_tournament_participation_no_bye_as_game(tmp_path):
    root, state, folder = cup(tmp_path)
    state["rounds"][0]["matches"][0]["attempts"] = []
    (root / "tournament.json").write_text(json.dumps(state))
    assert stat(tmp_path)["totals"]["tournaments"] == 1
    assert stat(tmp_path)["totals"]["duels"] == 0
    assert stat(tmp_path)["totals"]["games"] == 0


def test_tournament_draw_and_rematch_attempts(tmp_path):
    root, state, _ = cup(tmp_path, "cup-01", results=("1/2-1/2", "1/2-1/2"))
    _, second, _ = cup(tmp_path, "cup-02", results=("1-0", "0-1"))
    p = stat(tmp_path)
    assert p["totals"]["tournaments"] == 2
    assert p["totals"]["duels"] == 2
    assert p["totals"]["games"] == 4
    assert p["totals"]["draws"] == 2
    assert p["totals"]["completed"] == 4


def test_tampered_tournament_file_hash_excludes_both_games(tmp_path):
    root, state, folder = cup(tmp_path)
    (folder / "duelo.pgn").write_bytes(
        (folder / "duelo.pgn").read_bytes() + b"\n")
    report = stat(tmp_path)
    assert report["totals"]["tournaments"] == 1
    assert report["totals"]["games"] == 0
    assert report["coverage"]["rejected_games"] == 2
    assert report["coverage"]["partial"] is True


def test_tournament_duplicate_conflicting_ledger_entries_are_rejected(tmp_path):
    root, state, _ = cup(tmp_path)
    match = state["rounds"][0]["matches"][0]
    altered = dict(match["attempts"][0])
    altered["pgn_sha256"] = "0" * 64
    match["attempts"].append(altered)
    (root / "tournament.json").write_text(json.dumps(state))
    result = stat(tmp_path)
    assert result["totals"]["games"] == 0
    assert result["coverage"]["partial"] is True


def test_session_symlink_to_unrelated_summary_is_excluded(tmp_path):
    _, folder = session(tmp_path, 0)
    copy = tmp_path / "other-summary.json"
    copy.write_bytes((folder / "summary.json").read_bytes())
    (folder / "summary.json").unlink()
    (folder / "summary.json").symlink_to(copy)
    report = stat(tmp_path)
    assert report["totals"]["games"] == 0
    assert report["coverage"]["partial"] is True


def test_arena_get_stats_is_read_only_and_404_for_unknown(tmp_path):
    server = arena.create_server(tmp_path, tmp_path / "arena", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        session(tmp_path / "arena", 0, results=("1-0",))
        with urlopen(base + "/api/history/players/fischer/stats") as response:
            payload = json.load(response)
        assert payload["totals"]["games"] == 1
        assert payload["totals"]["wins"] == 1
        for path in ("/api/history/players/../stats",
                     "/api/history/players/not-a-player/stats",
                     "/api/history/players/fischer/stats?source=../../"):
            with pytest.raises(HTTPError) as err:
                urlopen(base + path)
            assert err.value.code == 404
        with pytest.raises(HTTPError) as err:
            urlopen(Request(base + "/api/history/players/fischer/stats",
                            data=b"{}", method="POST"))
        assert err.value.code in (403, 404, 405)
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=2)



def test_orphan_summary_is_reported_not_counted(tmp_path):
    folder, duel = session(tmp_path, 0)
    (folder / "session.json").unlink()
    p = stat(tmp_path)
    assert p["totals"]["games"] == 0
    assert p["coverage"]["rejected_records"] == 1
    assert p["coverage"]["partial"] is True


def test_string_reverse_flag_does_not_bypass_session_contract(tmp_path):
    folder, _ = session(tmp_path, 0, results=("1-0",))
    path = folder / "session.json"
    obj = json.loads(path.read_text())
    obj["config"]["reverse"] = "false"
    path.write_text(json.dumps(obj))
    p = stat(tmp_path)
    assert p["totals"]["games"] == 0
    assert p["coverage"]["rejected_records"] == 1


def test_naive_timestamp_does_not_become_invented_utc(tmp_path):
    folder, _ = session(tmp_path, 0, results=("1-0",))
    path = folder / "session.json"
    obj = json.loads(path.read_text())
    obj["created_utc"] = "2026-11-06T12:00:00"
    path.write_text(json.dumps(obj))
    p = stat(tmp_path)
    assert p["totals"]["games"] == 1
    assert p["scope"]["from_utc"] is None


def test_manifest_timezone_is_normalized_before_minmax(tmp_path):
    folder, _ = session(tmp_path, 0, results=("1-0",))
    path = folder / "session.json"
    obj = json.loads(path.read_text())
    obj["created_utc"] = "2026-11-06T09:00:00-03:00"
    path.write_text(json.dumps(obj))
    p = stat(tmp_path)
    assert p["scope"]["from_utc"] == "2026-11-06T12:00:00+00:00"


def test_tournament_symlink_summary_rejected_even_when_file_exists(tmp_path):
    root, _, folder = cup(tmp_path)
    original = folder / "summary.json"
    external = tmp_path / "summary-shadow.json"
    external.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(external)
    p = stat(tmp_path)
    assert p["totals"]["tournaments"] == 1
    assert p["totals"]["games"] == 0
    assert p["coverage"]["partial"] is True


def test_statistics_do_not_mutate_ledger_or_session(tmp_path):
    folder, duel = session(tmp_path, 0)
    cup_root, _, cup_folder = cup(tmp_path)
    files = [folder / "session.json", duel / "summary.json",
             cup_root / "tournament.json", cup_folder / "duelo.pgn"]
    before = [file.read_bytes() for file in files]
    report = stat(tmp_path)
    assert report["totals"]["games"] == 4
    assert report["coverage"]["arena_verified_games"] == 2
    assert report["coverage"]["tournament_verified_games"] == 2
    assert [file.read_bytes() for file in files] == before
    assert not list(tmp_path.rglob("*.lock"))


def test_unrelated_historical_pgn_cannot_pollute_simulation_stats(tmp_path):
    hist = tmp_path / "history-games"
    hist.mkdir()
    (hist / "historic.pgn").write_text(str(game("fischer", "tal", "1-0")))
    assert stat(tmp_path)["totals"]["games"] == 0


def test_cap_is_enforced_without_partial_fabricated_result(tmp_path, monkeypatch):
    session(tmp_path, 0)
    monkeypatch.setattr(history_stats, "MAX_SESSIONS", 0)
    with pytest.raises(ValueError, match="Limite"):
        stat(tmp_path)


def test_contradictory_session_and_summary_status_fail_closed(tmp_path):
    folder, duel = session(tmp_path, 0, results=("1-0",))
    session_path = folder / "session.json"
    session_info = json.loads(session_path.read_text())
    session_info["status"] = "FAILED"
    session_path.write_text(json.dumps(session_info))
    p = stat(tmp_path)
    assert p["totals"]["games"] == 0
    assert p["coverage"]["partial"] is True


def test_excluded_tournament_attempt_requires_real_noncompleted_summary(tmp_path):
    root, state, _ = cup(tmp_path)
    match = state["rounds"][0]["matches"][0]
    a, b = match["players"]
    excluded_folder = (root / "matches" / match["id"] / "duels" /
                       f"{a}-vs-{b}" / "attempt-aborted")
    excluded_folder.mkdir(parents=True)
    (excluded_folder / "summary.json").write_text(
        json.dumps({"status": "INCOMPLETE", "players": [a, b]}))
    match["excluded_attempts"] = [{
        "folder": str(excluded_folder.relative_to(root)),
        "reason": "NOT_COMPLETED",
    }]
    (root / "tournament.json").write_text(json.dumps(state))
    p = stat(tmp_path)
    assert p["totals"]["games"] == 2
    assert p["coverage"]["excluded_attempts"] == 1
    assert p["coverage"]["partial"] is False
    match["excluded_attempts"][0]["folder"] = "../etc/passwd"
    (root / "tournament.json").write_text(json.dumps(state))
    p = stat(tmp_path)
    assert p["coverage"]["excluded_attempts"] == 0
    assert p["coverage"]["partial"] is True


def test_one_game_session_cannot_count_two_games_even_when_incomplete(tmp_path):
    folder, _ = session(tmp_path, 0, results=("1-0", "0-1"))
    manifest = folder / "session.json"
    entry = json.loads(manifest.read_text())
    entry["config"]["reverse"] = False
    entry["status"] = "INCOMPLETE"
    manifest.write_text(json.dumps(entry))
    summary = next(folder.glob("duels/*/*/summary.json"))
    content = json.loads(summary.read_text())
    content["status"] = "INCOMPLETE"
    summary.write_text(json.dumps(content))
    output = stat(tmp_path)
    assert output["totals"]["games"] == 0
    assert output["coverage"]["partial"] is True


@pytest.mark.parametrize("injection", [
    b'"status": "COMPLETED", "status": "COMPLETED"',
    b'"status": "COMPLETED", "value": NaN',
])
def test_tournament_summary_ambiguous_json_is_not_accepted_even_with_new_hash(
        tmp_path, injection):
    root, state, folder = cup(tmp_path)
    summary_file = folder / "summary.json"
    original = summary_file.read_bytes()
    mutated = original.replace(b'"status": "COMPLETED"', injection, 1)
    assert mutated != original
    summary_file.write_bytes(mutated)
    state["rounds"][0]["matches"][0]["attempts"][0]["summary_sha256"] = (
        sha256(mutated).hexdigest()
    )
    (root / "tournament.json").write_text(json.dumps(state))
    report = stat(tmp_path)
    assert report["totals"]["games"] == 0
    assert report["coverage"]["rejected_games"] == 2
    assert report["coverage"]["partial"] is True


def test_unproven_win_in_a_nonterminal_arena_pgn_is_not_counted(tmp_path):
    _, folder = session(tmp_path, 0, results=("1-0",))
    pgn = game("fischer", "tal", "*")
    pgn.headers["Result"] = "1-0"
    pgn.headers["Termination"] = "Normal"
    (folder / "jogo_1.pgn").write_text(str(pgn) + "\n")
    path = folder / "summary.json"
    summary = json.loads(path.read_text())
    signature, plies = mainline_identity(pgn)
    summary["games"][0].update({
        "result": "1-0", "termination": "Normal",
        "mainline_sha256": signature, "plies": plies,
    })
    path.write_text(json.dumps(summary))
    report = stat(tmp_path)
    assert report["totals"]["games"] == 0
    assert report["totals"]["wins"] == 0
    assert report["coverage"]["rejected_games"] == 1
    assert report["coverage"]["partial"] is True


def test_time_forfeit_requires_clock_proof_and_correct_winner(tmp_path):
    _, folder = session(tmp_path, 0, results=("1-0",))
    pgn = game("fischer", "tal", "*")  # two plies; White to move
    pgn.headers["Result"] = "0-1"
    pgn.headers["Termination"] = "TimeForfeit"
    (folder / "jogo_1.pgn").write_text(str(pgn) + "\n")
    path = folder / "summary.json"
    summary = json.loads(path.read_text())
    signature, plies = mainline_identity(pgn)
    summary["games"][0].update({
        "result": "0-1", "termination": "TimeForfeit",
        "mainline_sha256": signature, "plies": plies,
        "clock_left": [-0.1, 120.0],
    })
    path.write_text(json.dumps(summary))
    valid = stat(tmp_path)
    assert valid["totals"]["games"] == 1
    assert valid["totals"]["losses"] == 1
    for clocks in ([10.0, 120.0], [float("nan"), 120.0], [False, 120.0]):
        summary["games"][0]["clock_left"] = clocks
        path.write_text(json.dumps(summary))
        rejected = stat(tmp_path)
        assert rejected["totals"]["games"] == 0
        assert rejected["coverage"]["partial"] is True
