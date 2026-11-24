import chess
import chess.pgn
import pytest

from chesssim import product_repertoires as p
from chesssim.opening_books import select_book_move


def game(*moves):
    g = chess.pgn.Game()
    node = g
    for move in moves:
        node = node.add_variation(chess.Move.from_uci(move))
    return g


def test_dated_fit_books_and_fallback(monkeypatch):
    rows = [
        {"date": 1960, "color": "white", "mainline_sha256": "fit1"},
        {"date": 1961, "color": "black", "mainline_sha256": "fit2"},
        {"date": 1962, "color": "white", "mainline_sha256": "fit3"},
        {"date": 1960, "color": "white", "mainline_sha256": "test"},
        {"date": "?", "color": "white", "mainline_sha256": "fit4"},
    ]
    games = {
        "fit1": game("e2e4", "e7e5", "g1f3"),
        "fit2": game("e2e4", "c7c5"),
    }
    monkeypatch.setattr(p, "neural_arm", lambda gid: "fit" if gid.startswith("fit") else "excluded_test")
    monkeypatch.setattr(p, "_iter_verified_games",
                        lambda raw, chosen, name: ((games[r["mainline_sha256"]], r) for r in chosen))
    book = p.build("tal", (b"pgn", rows, {}, "fixture", "Tal"))
    assert book["source"]["accepted_games"] == 2
    assert p.audit(book)["white"]["observations"] == 2
    board = chess.Board()
    assert select_book_move(book, board) == chess.Move.from_uci("e2e4")
    board.push_uci("e2e4")
    assert select_book_move(book, board) == chess.Move.from_uci("c7c5")
    board.push_uci("e7e6")
    board.push_uci("d2d4")
    assert select_book_move(book, board) is None
    with pytest.raises(ValueError, match="Cutoff"):
        p.build("tal", (b"pgn", rows, {}, "fixture", "Tal"), through_year=1962)


def test_invalid_stored_move_is_rejected():
    book = {"positions": {"white": {p.position_token(chess.Board()): {"e2e5": 1}}}}
    with pytest.raises(ValueError, match="Illegal"):
        p.audit(book)
