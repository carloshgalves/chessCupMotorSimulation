from types import SimpleNamespace

import chess
import chess.engine

from chesssim.product_strength_ladder import DepthControl, PAIRS


def test_control_preserves_clock_and_nodes():
    class Engine:
        def play(self, board, limit, **kwargs):
            assert limit.depth == 12
            assert limit.nodes == 5_000_000
            assert limit.white_clock == 600
            assert limit.black_inc == 2
            return SimpleNamespace(info={"depth": 12})

    control = DepthControl(Engine(), 12)
    original = chess.engine.Limit(depth=16, nodes=5_000_000,
                                  white_clock=600, black_inc=2)
    control.play(chess.Board(), original)
    assert original.depth == 16
    assert control.reported == {"searches": 1, "reached": 1}
    assert len(PAIRS) == 6
