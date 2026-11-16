"""Independent experimental player-specific UCI limits cannot leak into official play."""
from types import SimpleNamespace

import chess
import pytest

from chesssim.catalog import Player
from chesssim import matches


class RecordingUci:
    def __init__(self, slug, seen):
        self.slug = slug
        self.seen = seen

    def play(self, board, limit, **kwargs):
        self.seen.append((self.slug, limit.depth))
        return SimpleNamespace(move=next(iter(board.legal_moves)))

    def quit(self):
        pass

    def close(self):
        pass


def test_each_side_receives_its_own_actual_search_budget(monkeypatch, tmp_path):
    white = Player(1, "White Test", "white-test", "baseline")
    black = Player(2, "Black Test", "black-test", "baseline")
    seen = []
    monkeypatch.setattr(matches, "_open_engine",
                        lambda root, executable, player, book=None:
                        RecordingUci(player.slug, seen))
    common = dict(root=tmp_path, exe=tmp_path/"not-executed", white=white,
                  black=black, depth=4, movetime=None, max_plies=2,
                  round_number=1,
                  hashes={"exe":"a"*64,"white-test":"b"*64,"black-test":"c"*64},
                  use_books=False)
    pgn, meta = matches._play_one(**common,
        experimental_depths={"white-test":2,"black-test":6})
    assert seen == [("white-test",2),("black-test",6)]
    assert pgn.headers["ExperimentalDepthWhite"] == "2"
    assert pgn.headers["ExperimentalDepthBlack"] == "6"
    assert pgn.headers["DepthByPlayerOptIn"] == "true"
    assert meta["status"] == "INCOMPLETE"
    assert meta["result"] == "*"  # no artificial draw at ply cap
    seen.clear()
    pgn, _ = matches._play_one(**common)
    assert seen == [("white-test",4),("black-test",4)]
    assert "DepthByPlayerOptIn" not in pgn.headers


@pytest.mark.parametrize("invalid", [
    {}, {"white-test":3}, {"white-test":True,"black-test":4},
    {"white-test":0,"black-test":4}, {"white-test":13,"black-test":4},
    {"white-test":4.0,"black-test":4},
    {"white-test":3,"black-test":4,"extra":2}
])
def test_depth_plan_fail_closed_before_launch(invalid, tmp_path):
    white = Player(1,"White Test","white-test","baseline")
    black = Player(2,"Black Test","black-test","baseline")
    with pytest.raises(ValueError, match="Depth overrides"):
        matches._play_one(tmp_path,tmp_path/"exe",white,black,4,None,2,1,
                          {"exe":"a","white-test":"b","black-test":"c"},
                          experimental_depths=invalid)


def test_per_player_depth_cannot_affect_clocked_official_play(tmp_path):
    w=Player(1,"W","w","baseline")
    b=Player(2,"B","b","baseline")
    with pytest.raises(ValueError,match="unclocked UCI-only"):
        matches._play_one(tmp_path,tmp_path/"exe",w,b,4,None,2,1,
                          {"exe":"a","w":"b","b":"c"},clock=(600,2),
                          experimental_depths={"w":4,"b":4})
