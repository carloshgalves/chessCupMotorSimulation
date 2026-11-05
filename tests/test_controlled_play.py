"""Unit-level checks for graceful UCI stops and durable partial PGNs."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import chess
import chess.pgn
import pytest

from chesssim import matches
from chesssim.catalog import find_player


FOOLS_MATE = ["f2f3", "e7e5", "g2g4", "d8h4"]


class FakeEngine:
    def __init__(self, moves=None, fail_quit=False):
        self.moves = moves
        self.fail_quit = fail_quit
        self.played = 0
        self.closed = False
        self.quit_called = False

    def play(self, board, limit, game, info):
        del limit, game, info
        if self.moves is not None:
            move = chess.Move.from_uci(self.moves[board.ply()])
        else:
            move = next(iter(board.legal_moves))
        assert move in board.legal_moves
        self.played += 1
        return SimpleNamespace(move=move)

    def quit(self):
        self.quit_called = True
        if self.fail_quit:
            raise RuntimeError("quit command failed")

    def close(self):
        self.closed = True


@pytest.fixture
def engine_fixture(tmp_path, monkeypatch):
    root = tmp_path / "rodent"
    root.mkdir()
    engine = root / "rodent-iv-plain.exe"
    engine.write_bytes(b"fake-uci")
    fischer = find_player("fischer")
    tal = find_player("tal")
    profiles = {}
    for p in (fischer, tal):
        path = root / p.profile_filename
        path.write_text("fake personality", encoding="utf-8")
        profiles[p.slug] = path

    monkeypatch.setattr(matches, "validate",
                        lambda r, w, b: (engine, {w.slug: profiles[w.slug],
                                                  b.slug: profiles[b.slug]}))
    engines = []

    def open_engine(root_arg, executable, player, *, book):
        del root_arg, executable, player, book
        current = FakeEngine()
        engines.append(current)
        return current

    monkeypatch.setattr(matches, "_open_engine", open_engine)
    return root, engine, fischer, tal, profiles, engines


def test_graceful_stop_at_ply_boundary_and_release_both_engines(engine_fixture):
    root, engine, white, black, profiles, engines = engine_fixture
    seen = []

    def checkpoint(game):
        seen.append(game.end().ply())

    stopped = lambda: len(seen) >= 3
    game, meta = matches._play_one(
        root, engine, white, black, 2, None, 20, 1,
        {"exe": "sha", white.slug: "white", black.slug: "black"},
        clock=None, use_books=False, stop_check=stopped, on_progress=checkpoint)

    assert seen == [0, 1, 2]
    assert meta["status"] == "INCOMPLETE"
    assert meta["termination"] == "OperatorStop"
    assert meta["result"] == "*"
    assert meta["plies"] == 2
    assert game.headers["Result"] == "*"
    assert len(engines) == 2
    assert all(e.quit_called and e.closed for e in engines)


def test_close_fallback_when_uci_quit_fails(engine_fixture, monkeypatch):
    root, engine, white, black, profiles, engines = engine_fixture

    def bad_quit(*args, **kwargs):
        instance = FakeEngine(fail_quit=True)
        engines.append(instance)
        return instance

    monkeypatch.setattr(matches, "_open_engine", bad_quit)
    result = matches._play_one(root, engine, white, black, 2, None, 10, 1,
                               {"exe": "sha", white.slug: "white",
                                black.slug: "black"},
                               stop_check=lambda: True)
    assert result[1]["status"] == "INCOMPLETE"
    assert all(engine.closed for engine in engines)


def test_first_engine_closed_when_second_engine_open_fails(engine_fixture, monkeypatch):
    root, engine, white, black, profiles, engines = engine_fixture

    def open_second_fails(*args, **kwargs):
        if engines:
            raise RuntimeError("Second engine failed to initialize")
        current = FakeEngine()
        engines.append(current)
        return current

    monkeypatch.setattr(matches, "_open_engine", open_second_fails)
    with pytest.raises(RuntimeError, match="Second engine"):
        matches._play_one(root, engine, white, black, 2, None, 10, 1,
                          {"exe": "sha", white.slug: "white",
                           black.slug: "black"})
    assert len(engines) == 1
    assert engines[0].closed


def test_wall_limit_stops_without_declaring_result(engine_fixture, monkeypatch):
    root, engine, white, black, _, engines = engine_fixture
    sequence = iter([0.0, 100.0])
    monkeypatch.setattr(matches.time, "monotonic", lambda: next(sequence))
    game, meta = matches._play_one(
        root, engine, white, black, 2, None, 10, 1,
        {"exe": "sha", white.slug: "white", black.slug: "black"},
        max_wall_seconds=10)
    assert meta["termination"] == "WallTimeLimit"
    assert meta["status"] == "INCOMPLETE"
    assert meta["result"] == "*"
    assert meta["plies"] == 0
    assert all(e.closed for e in engines)


def test_partial_pgn_is_recoverable_and_second_game_not_started(engine_fixture, tmp_path):
    root, engine, white, black, profiles, engines = engine_fixture
    count = [0]
    original = matches._open_engine

    def open_count(*args, **kwargs):
        instance = original(*args, **kwargs)
        method = instance.play

        def play(*p, **kw):
            count[0] += 1
            return method(*p, **kw)

        instance.play = play
        return instance

    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setattr(matches, "_open_engine", open_count)
        folder = matches.play_duel(
            root, tmp_path / "runs", white, black, depth=2,
            max_plies=20, reverse=True, use_books=False,
            stop_check=lambda: count[0] >= 2)
    report = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert report["status"] == "INCOMPLETE"
    assert len(report["games"]) == 1
    assert report["games"][0]["termination"] == "OperatorStop"
    assert not (folder / "jogo_2.pgn").exists()
    assert (folder / "jogo_1.partial.pgn").exists()
    with (folder / "jogo_1.partial.pgn").open(encoding="utf-8") as stream:
        partial = chess.pgn.read_game(stream)
    assert partial is not None and not partial.errors
    assert partial.headers["Result"] == "*"
    assert partial.end().ply() == 2
    assert all(e.closed for e in engines)


def test_stop_after_first_completed_game_does_not_complete_duel(engine_fixture, tmp_path, monkeypatch):
    root, engine, white, black, _, engines = engine_fixture
    move_counter = [0]

    def open_checkmate(*args, **kwargs):
        current = FakeEngine(moves=FOOLS_MATE)
        original = current.play

        def play(*p, **kw):
            move_counter[0] += 1
            return original(*p, **kw)

        current.play = play
        engines.append(current)
        return current

    monkeypatch.setattr(matches, "_open_engine", open_checkmate)
    folder = matches.play_duel(
        root, tmp_path / "duels", white, black, depth=2,
        max_plies=20, reverse=True, use_books=False,
        stop_check=lambda: move_counter[0] >= 4)

    state = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert state["status"] == "INCOMPLETE"
    assert len(state["games"]) == 1
    assert state["games"][0]["status"] == "COMPLETED"
    assert state["games"][0]["result"] == "0-1"
    assert not (folder / "jogo_2.pgn").exists()
    assert len(engines) == 2


def test_invalid_wall_limit_preflight(engine_fixture, tmp_path):
    root, _, white, black, _, _ = engine_fixture
    with pytest.raises(ValueError, match="max_wall_seconds"):
        matches.play_duel(root, tmp_path / "duels", white, black, max_wall_seconds=0)
    assert not (tmp_path / "duels").exists()
