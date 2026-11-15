"""Full-probability true-policy engineering fixtures, not fake scientific evidence."""
import chess
import pytest
from chesssim.dsc_true_probability_style import _probabilities, _best, _tune_alpha


def test_original_dsc_probability_is_not_a_rank_surrogate():
    board=chess.Board()
    moves=sorted(m.uci() for m in board.legal_moves)
    p={move: .5 if move=="e2e4" else .5/(len(moves)-1) for move in moves}
    out=_probabilities(p,board,{},0.0)
    assert sum(out.values())==pytest.approx(1)
    assert _best(out)=="e2e4"
    with pytest.raises(ValueError, match="distribution"):
        _probabilities({"e2e4":1.0},board,{},0.0)
    with pytest.raises(ValueError, match="normal"):
        _probabilities({k: v*2 for k,v in p.items()},board,{},0.0)


def test_tuning_does_not_select_nonzero_when_every_profile_is_neutral():
    board=chess.Board()
    moves=sorted(m.uci() for m in board.legal_moves)
    p={move:1/len(moves) for move in moves}
    rows=[{"fen":board.fen(),"move":"e2e4",
           "game_sha256":f"{i:064x}"} for i in range(24)]
    assert _tune_alpha(rows,[p]*24,{})==0.0
