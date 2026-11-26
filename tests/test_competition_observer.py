import chess.pgn
from chesssim.competition_observer import Observer, publish, playback


def test_capture_publishes_and_isolates_games():
    store = Observer()
    spec = {"id": "g1", "white": "tal", "black": "fischer"}
    with store.capture("cup", spec, [60, 60]):
        game = chess.pgn.Game()
        node = game.add_variation(chess.Move.from_uci("e2e4"))
        node.set_clock(58)
        publish(game, [58, 60])
        result = store.get("cup", "g1")
        assert result["moves"][0]["san"] == "e4"
        assert result["states"][-1]["clocks"] == [58, 60]
        result["states"].clear()
        assert len(store.get("cup", "g1")["states"]) == 2
        assert store.get("other", "g1") is None
    assert store.get("cup", "g1") is None


def test_replay_initial_position_and_clock():
    result = playback(chess.pgn.Game(), [90, 80])
    assert result["states"][0]["fen"] == chess.STARTING_FEN
    assert result["states"][0]["clocks"] == [90, 80]
