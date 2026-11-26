from threading import Barrier
import pytest
from chesssim.competition_execution import batch, execute, options


def test_independent_batch_excludes_same_player():
    games = [{"white": "a", "black": "b"}, {"white": "a", "black": "c"},
             {"white": "c", "black": "d"}]
    assert batch(games, 4) == [games[0], games[2]]
    assert batch(games, 1) == [games[0]]


def test_parallel_workers_execute_and_join():
    barrier = Barrier(2, timeout=5)
    completed = []
    def run(value):
        barrier.wait()
        completed.append(value)
    execute([1, 2], run)
    assert sorted(completed) == [1, 2]


def test_options_reject_invalid_workers_and_keep_manual_defaults():
    assert options({}, "game") == ("game", 1)
    assert options({"scope": "competition", "workers": 4}, "round") == ("competition", 4)
    for raw in ({"workers": True}, {"workers": 5}, {"scope": "bad"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            options(raw, "round")
