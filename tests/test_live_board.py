"""Local live viewer: changing checkpoints, valid FEN, isolation and read-only HTTP."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import chess
import chess.pgn
import pytest

from chesssim import live_board, tournament
from chesssim.cli import main


OPENING = ("e2e4", "e7e5", "g1f3", "b8c6")


def write_game(folder: Path, number: int, moves=OPENING, *, final=False,
               white="Bobby Fischer", black="Mikhail Tal") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    game = chess.pgn.Game()
    game.headers["White"] = white
    game.headers["Black"] = black
    game.headers["TimeControl"] = "600+2"
    game.headers["Result"] = "*"  # no result fabricated in a live checkpoint
    board = game.board()
    node = game
    for i, uci in enumerate(moves, 1):
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        node = node.add_variation(move)
        node.set_clock(600 - i * 2)
        board.push(move)
    filename = f"jogo_{number}.pgn" if final else f"jogo_{number}.partial.pgn"
    target = folder / filename
    target.write_text(str(game) + "\n", encoding="utf-8")
    return target


def write_duel(folder: Path, status="RUNNING", games=None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(
        json.dumps({"status": status, "games": games or []}), encoding="utf-8")


@contextmanager
def server_at(folder: Path):
    server = live_board.create_server(folder, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def load_api(base: str):
    with urlopen(base + "/api/state", timeout=3) as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        return json.load(response)


def test_partial_game_is_available_before_duel_finishes(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    write_game(folder, 1, moves=OPENING[:2])
    report = live_board.snapshot(folder)
    assert report["mode"] == "duel"
    game = report["duel"]["games"][0]
    assert game["live"] is True
    assert game["result"] == "*"
    assert [state["san"] for state in game["states"]] == ["Início", "e4", "e5"]
    assert game["states"][-1]["last"] == ["e7", "e5"]
    assert game["states"][-1]["fen"].startswith("rnbqkbnr/pppp1ppp")
    assert game["states"][-1]["clocks"] == [598.0, 596.0]
    assert game["states"][-1]["turn"] == "white"


def test_http_repoll_delivers_new_moves_without_final_pgn(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    write_game(folder, 1, moves=OPENING[:1])
    with server_at(folder) as base:
        first = load_api(base)
        assert len(first["duel"]["games"][0]["states"]) == 2
        write_game(folder, 1, moves=OPENING)
        second = load_api(base)
        assert len(second["duel"]["games"][0]["states"]) == 5
        assert second["duel"]["games"][0]["live"] is True
        assert second["duel"]["games"][0]["states"][-1]["san"] == "Nc6"


def test_latest_finished_game_is_preferred_to_stale_partial(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder, status="COMPLETED",
               games=[{"status": "COMPLETED"}, {"status": "COMPLETED"}])
    write_game(folder, 1, moves=OPENING[:1], final=False)
    write_game(folder, 1, moves=OPENING, final=True)
    write_game(folder, 2, moves=OPENING, final=True,
               white="Mikhail Tal", black="Bobby Fischer")
    report = live_board.snapshot(folder)
    one, two = report["duel"]["games"]
    assert one["status"] == "COMPLETED"
    assert not one["live"]
    assert len(one["states"]) == 5
    assert two["white"] == "Mikhail Tal"
    assert two["black"] == "Bobby Fischer"
    assert two["number"] == 2


def test_unfinished_duel_without_checkpoint_is_displayable(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    report = live_board.snapshot(folder)
    assert report["duel"]["games"] == []


def test_tournament_selects_running_attempt_then_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_preflight", lambda root, slugs, preset: {
        "engine_sha256": "test", "profile_sha256": {s: "test" for s in slugs},
        "book_sha256": {}, "books_by_player": {s: [] for s in slugs}})
    folder = tmp_path / "copa"
    state = tournament.create_tournament(
        tmp_path, folder, participants=["fischer", "tal"], seed=2026, preset="rapid")
    match = state["rounds"][0]["matches"][0]
    a, b = match["players"]
    root = folder / "matches" / match["id"] / "duels" / f"{a}-vs-{b}"
    first = root / "first"
    write_duel(first, status="COMPLETED")
    write_game(first, 1, final=True)
    second = root / "second"
    write_duel(second, status="RUNNING")
    write_game(second, 1, moves=OPENING[:1])
    data = live_board.snapshot(folder)
    assert data["mode"] == "tournament"
    assert data["tournament"]["participants"] == 2
    assert data["duel"]["folder"] == "second"
    assert data["duel"]["match_id"] == "r01-m01"
    assert data["duel"]["games"][0]["live"] is True


def test_no_external_files_served_via_symlinks(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    outside = tmp_path / "outside.pgn"
    outside.write_text(str(chess.pgn.Game()), encoding="utf-8")
    (folder / "jogo_1.partial.pgn").symlink_to(outside)
    with pytest.raises(ValueError, match="fora do diretório"):
        live_board.snapshot(folder)


def test_invalid_pgn_rejected_instead_of_showing_invented_moves(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    path = folder / "jogo_1.partial.pgn"
    path.write_text('[Event "?"]\n\n1. e4 e5 2. Qh5 Qxh5 *\n', encoding="utf-8")
    with pytest.raises(ValueError, match="PGN vazio, inválido"):
        live_board.snapshot(folder)


def test_oversized_partial_checkpoint_refused(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    (folder / "jogo_1.partial.pgn").write_bytes(b"x" * (live_board.MAX_PGN_BYTES + 1))
    with pytest.raises(ValueError, match="limite"):
        live_board.snapshot(folder)


def test_host_filter_routes_and_no_mutations(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    write_game(folder, 1)
    with server_at(folder) as base:
        for resource in ("/", "/app.js", "/app.css"):
            with urlopen(base + resource, timeout=3) as resp:
                assert resp.status == 200
                assert len(resp.read()) > 300
                assert resp.headers["X-Frame-Options"] == "DENY"
        with pytest.raises(HTTPError) as forbidden:
            urlopen(Request(base + "/api/state", headers={"Host": "malicious.test"}), timeout=3)
        assert forbidden.value.code == 403
        with pytest.raises(HTTPError) as post:
            urlopen(Request(base + "/api/state", data=b"delete=true", method="POST"), timeout=3)
        assert post.value.code == 405
        with pytest.raises(HTTPError) as missing:
            urlopen(base + "/secret-file", timeout=3)
        assert missing.value.code == 404
        assert load_api(base)["duel"]["status"] == "RUNNING"


def test_static_assets_use_safe_text_updates_and_no_external_content(tmp_path):
    assets = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"
    html = (assets / "monitor.html").read_text(encoding="utf-8")
    script = (assets / "monitor.js").read_text(encoding="utf-8")
    assert 'src="/app.js"' in html
    assert 'href="/app.css"' in html
    assert "https://" not in html and "http://" not in html
    assert "innerHTML" not in script and "eval(" not in script
    assert "textContent" in script and 'fetch("/api/state" + query' in script
    assert 'id="bracket"' in html and 'id="attempts"' in html
    assert "setSelection(match.id)" in script and "hashchange" in script
    assert "URLSearchParams" in script and 'aria-pressed' in script
    assert 'id="pause"' in html and 'id="live"' in html
    assert 'id="white-clock"' in html and 'id="black-clock"' in html


def test_board_css_has_fixed_8x8_geometry_and_pieces_visible_on_both_colors():
    # Regression for the compressed empty ranks seen in the Arena iframe.
    assets = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data"
    css = (assets / "chess_board.css").read_text(encoding="utf-8")
    script = (assets / "chess_board.js").read_text(encoding="utf-8")
    viewer_script = (assets / "monitor.js").read_text(encoding="utf-8")

    assert "grid-template-columns:repeat(8,minmax(0,1fr))" in css
    assert "grid-template-rows:repeat(8,minmax(0,1fr))" in css
    assert "aspect-ratio:1 / 1" in css
    assert "min-width:0;min-height:0;container-type:inline-size" in css
    assert ".square{position:relative;display:grid;place-items:center" in css
    assert "width:100%;height:100%;min-width:0;min-height:0;overflow:hidden" in css
    assert ".square .piece-glyph" in css and "9.4cqw" in css

    # Foreground AND outline are side-specific, so neither the white or
    # black pieces disappear on the light beige or dark teal board squares.
    assert ".square.whitepiece .piece-glyph{color:#faf7eb" in css
    assert "-webkit-text-stroke:1.15px #192933" in css
    assert ".square.blackpiece .piece-glyph{color:#15232b" in css
    assert "-webkit-text-stroke:1.15px #f7efdf" in css
    assert "label.className = \"piece-glyph\"" in script
    assert "ChessSimBoard.paint" in viewer_script
    assert 'label.setAttribute("aria-hidden", "true")' in script

    # Both standalone watch and Arena's embedded viewer load this asset.
    assert 'href="/app.css"' in (assets / "monitor.html").read_text(encoding="utf-8")
    assert 'href="/chess-board.css"' in (assets / "monitor.html").read_text(encoding="utf-8")
    assert 'src="/chess-board.js"' in (assets / "monitor.html").read_text(encoding="utf-8")
    arena = (assets / "arena.html").read_text(encoding="utf-8")
    assert 'src="/viewer"' in arena


def test_cli_watch_appears_in_help(capsys):
    # argparse's built-in help exits cleanly instead of returning to main.
    with pytest.raises(SystemExit) as exited:
        main(["watch", "--help"])
    assert exited.value.code == 0
    assert "--run" in capsys.readouterr().out


def test_legacy_pgn_without_clock_comments_shows_unknown_time(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    game = chess.pgn.Game()
    game.headers["TimeControl"] = "600+2"
    node = game
    move = chess.Move.from_uci("e2e4")
    node.add_variation(move)
    (folder / "jogo_1.partial.pgn").write_text(str(game), encoding="utf-8")
    data = live_board.snapshot(folder)["duel"]["games"][0]
    assert data["states"][0]["clocks"] == [600.0, 600.0]
    assert data["states"][1]["clocks"] == [None, 600.0]


def test_fresh_completed_attempt_beats_stale_running_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_preflight", lambda root, slugs, preset: {
        "engine_sha256": "test", "profile_sha256": {s: "test" for s in slugs},
        "book_sha256": {}, "books_by_player": {s: [] for s in slugs}})
    run = tmp_path / "copa"
    state = tournament.create_tournament(
        tmp_path, run, participants=["fischer", "tal"], seed=2026, preset="rapid")
    match = state["rounds"][0]["matches"][0]
    a, b = match["players"]
    base = run / "matches" / match["id"] / "duels" / f"{a}-vs-{b}"
    old = base / "crashed"
    write_duel(old, status="RUNNING")
    old_pgn = write_game(old, 1, moves=OPENING[:1])
    new = base / "finished"
    write_duel(new, status="COMPLETED")
    new_pgn = write_game(new, 1, moves=OPENING, final=True)
    for path in (old / "summary.json", old_pgn):
        os.utime(path, (1000, 1000))
    for path in (new / "summary.json", new_pgn):
        os.utime(path, (2000, 2000))
    report = live_board.snapshot(run)
    assert report["duel"]["folder"] == "finished"
    assert len(report["duel"]["games"][0]["states"]) == 5


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed; browser JS syntax optional")
def test_live_board_javascript_syntax():
    asset = Path(__file__).resolve().parents[1] / "src" / "chesssim" / "data" / "monitor.js"
    result = subprocess.run(["node", "--check", str(asset)], capture_output=True,
                            text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr


def test_time_forfeit_displays_authoritative_final_clocks(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder, status="COMPLETED",
               games=[{"status": "COMPLETED", "termination": "TimeForfeit",
                       "clock_left": [-0.4, 35.6]}])
    write_game(folder, 1, final=True)
    game = live_board.snapshot(folder)["duel"]["games"][0]
    assert game["status"] == "COMPLETED"
    assert game["states"][-1]["clocks"] == [0.0, 35.6]


def test_malformed_summary_yields_sanitized_http_error(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    (folder / "summary.json").write_text('["not an object"]', encoding="utf-8")
    with pytest.raises(ValueError, match="Resumo de confronto inválido"):
        live_board.snapshot(folder)
    # Server preflight rejects invalid data without binding a listening socket.
    with pytest.raises(ValueError, match="Resumo de confronto inválido"):
        live_board.create_server(folder, port=0)


def test_latest_corrupt_tournament_attempt_returns_503_not_old_game(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_preflight", lambda root, slugs, preset: {
        "engine_sha256": "test", "profile_sha256": {s: "test" for s in slugs},
        "book_sha256": {}, "books_by_player": {s: [] for s in slugs}})
    run = tmp_path / "copa"
    state = tournament.create_tournament(
        tmp_path, run, participants=["fischer", "tal"], seed=2026, preset="rapid")
    match = state["rounds"][0]["matches"][0]
    a, b = match["players"]
    base_dir = run / "matches" / match["id"] / "duels" / f"{a}-vs-{b}"
    old = base_dir / "old"
    write_duel(old, "COMPLETED")
    write_game(old, 1, final=True)
    latest = base_dir / "latest"
    write_duel(latest)
    write_game(latest, 1)
    with server_at(run) as base:
        assert load_api(base)["duel"]["folder"] == "latest"
        (latest / "summary.json").write_text("not json", encoding="utf-8")
        with pytest.raises(HTTPError) as failed:
            urlopen(base + "/api/state", timeout=3)
        assert failed.value.code == 503
        body = failed.value.read().decode("utf-8")
        assert "Nao foi possivel ler" in body
        assert str(tmp_path) not in body


@pytest.fixture
def four_player_cup(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_preflight", lambda root, slugs, preset: {
        "engine_sha256": "test", "profile_sha256": {s: "test" for s in slugs},
        "book_sha256": {}, "books_by_player": {s: [] for s in slugs}})
    run = tmp_path / "copa-4"
    state = tournament.create_tournament(
        tmp_path, run, participants=["fischer", "tal", "lasker", "marshall"],
        seed=2026, preset="rapid")
    return run, state


def attempt_folder(run, match, name):
    a, b = match["players"]
    return run / "matches" / match["id"] / "duels" / f"{a}-vs-{b}" / name


def test_api_navigates_previous_matches_and_attempts_without_disk_path_input(four_player_cup):
    run, state = four_player_cup
    match1, match2 = state["rounds"][0]["matches"]
    first = attempt_folder(run, match1, "20261105T100000Z-aaa")
    second = attempt_folder(run, match1, "20261105T100010Z-bbb")
    write_duel(first, "INCOMPLETE")
    write_game(first, 1, moves=OPENING[:1])
    write_duel(second, "COMPLETED", games=[{"status": "COMPLETED"}] * 2)
    write_game(second, 1, moves=OPENING, final=True)
    write_game(second, 2, moves=OPENING, final=True,
               white="Mikhail Tal", black="Bobby Fischer")
    selected = live_board.snapshot(run, match_id=match1["id"], attempt=1)
    assert selected["selection"] == {"match_id": match1["id"], "attempt": 1}
    assert selected["duel"]["folder"] == first.name
    assert selected["duel"]["status"] == "INCOMPLETE"
    assert selected["duel"]["games"][0]["result"] == "*"
    assert [a["status"] for a in selected["attempts"]] == ["INCOMPLETE", "COMPLETED"]
    with server_at(run) as base:
        from urllib.parse import urlencode
        params = urlencode({"match": match1["id"], "attempt": 2})
        with urlopen(base + "/api/state?" + params, timeout=3) as response:
            data = json.load(response)
        assert data["duel"]["folder"] == second.name
        assert len(data["duel"]["games"]) == 2
        assert data["duel"]["games"][1]["white"] == "Mikhail Tal"
        assert data["selection"]["attempt"] == 2
        empty = urlencode({"match": match2["id"]})
        with urlopen(base + "/api/state?" + empty, timeout=3) as response:
            data = json.load(response)
        assert data["duel"] is None
        assert data["selection"]["match_id"] == match2["id"]
        assert len(data["attempts"]) == 0
        with urlopen(base + "/api/state", timeout=3) as response:
            default = json.load(response)
        assert default["duel"]["folder"] == second.name


def test_reject_unknown_match_and_invalid_attempt_query(four_player_cup):
    run, state = four_player_cup
    match = state["rounds"][0]["matches"][0]
    for params in ({"match": "r99-m99"}, {"match": "../secret"},
                   {"match": match["id"], "attempt": "0"},
                   {"match": match["id"], "attempt": "100000"},
                   {"match": match["id"], "attempt": "-1"},
                   {"attempt": "1"}, {"unknown": "x"}):
        from urllib.parse import urlencode
        with server_at(run) as base:
            with pytest.raises(HTTPError) as exc:
                urlopen(base + "/api/state?" + urlencode(params), timeout=3)
            assert exc.value.code == 400
    with server_at(run) as base:
        for suffix in ("match=r01-m01&match=r01-m02",
                       "match=r01-m01&attempt=1&x=1"):
            with pytest.raises(HTTPError) as exc:
                urlopen(base + "/api/state?" + suffix, timeout=3)
            assert exc.value.code == 400
    with pytest.raises(live_board.InvalidSelection):
        live_board.snapshot(run, match_id="r01-m99")
    with pytest.raises(live_board.InvalidSelection):
        live_board.snapshot(run, match_id=match["id"], attempt=1)


def test_scores_rounds_and_replays_survive_monitor_restart(four_player_cup):
    run, state = four_player_cup
    left, right = state["rounds"][0]["matches"]
    for match in (left, right):
        match["attempts"].append({"folder": "saved", "results": ["1-0", "0-1"]})
        match["winner"] = match["players"][0]
        match["status"] = "COMPLETED"
        folder = attempt_folder(run, match, "saved")
        write_duel(folder, "COMPLETED", games=[{"status": "COMPLETED"}] * 2)
        write_game(folder, 1, final=True)
        write_game(folder, 2, final=True)
    finalists = [m["winner"] for m in (left, right)]
    state["rounds"].append(tournament._round(2, finalists))
    tournament._save(run, state)
    with server_at(run) as base:
        before = load_api(base)
        assert len(before["tournament"]["bracket"]) == 2
        assert before["tournament"]["bracket"][0]["matches"][0]["winner"] == finalists[0]
        assert before["tournament"]["bracket"][0]["matches"][0]["score"][finalists[0]] == 2.0
    with server_at(run) as base:
        after = load_api(base)
        assert after["tournament"]["bracket"] == before["tournament"]["bracket"]
        with urlopen(base + "/api/state?match=" + left["id"], timeout=3) as response:
            historical = json.load(response)
        assert len(historical["duel"]["games"]) == 2
        assert historical["duel"]["games"][0]["states"][-1]["san"] == "Nc6"
        assert historical["tournament"]["rounds"] == 2
        assert historical["tournament"]["names"][finalists[0]]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_bracket_browser_interaction_contract():
    root = Path(__file__).resolve().parents[1]
    harness = root / "tests" / "assets" / "monitor_browser_harness.cjs"
    javascript = root / "src" / "chesssim" / "data" / "monitor.js"
    board = root / "src" / "chesssim" / "data" / "chess_board.js"
    result = subprocess.run(["node", str(harness), str(javascript), str(board)],
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "smoke: PASS" in result.stdout


def test_internal_symlink_cannot_relabel_other_match_as_history(four_player_cup):
    run, state = four_player_cup
    first, second = state["rounds"][0]["matches"]
    target = attempt_folder(run, second, "genuine")
    write_duel(target, "COMPLETED")
    write_game(target, 1, final=True)
    impostor = attempt_folder(run, first, "impostor")
    impostor.mkdir(parents=True)
    (impostor / "summary.json").symlink_to(target / "summary.json")
    with pytest.raises(ValueError, match="Link simbólico"):
        live_board.snapshot(run, match_id=first["id"])


def test_internal_pgn_symlink_refused_for_archived_attempt(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    original = write_game(folder, 2, moves=OPENING, final=True)
    (folder / "jogo_1.partial.pgn").symlink_to(original)
    with pytest.raises(ValueError, match="Link simbólico"):
        live_board.snapshot(folder)


def test_excessive_duel_summary_is_refused_before_json_parse(tmp_path):
    folder = tmp_path / "duel"
    write_duel(folder)
    (folder / "summary.json").write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="excedeu limite"):
        live_board.snapshot(folder)


def test_bracket_and_selection_use_same_manifest_read(four_player_cup, monkeypatch):
    run, _ = four_player_cup
    original = tournament.load_tournament
    calls = []

    def spy(folder):
        calls.append(folder)
        return original(folder)

    monkeypatch.setattr(tournament, "load_tournament", spy)
    report = live_board.snapshot(run)
    assert report["tournament"]["participants"] == 4
    assert len(calls) == 1
