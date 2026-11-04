"""Pure unit regressions for the new v0.9 boundaries (no live engine needed)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from chesssim.opening_books import (
    SCHEMA, _game_id, build_opening_book, load_opening_book, select_book_move,
)
from chesssim.quality_baseline import _sample_indices


def test_mequinho_requires_original_embargo_even_if_ack_unpartitioned(tmp_path):
    pgn = tmp_path / 'any.pgn'
    pgn.write_text('example')
    with pytest.raises(ValueError, match='Mequinho requires'):
        build_opening_book(pgn, tmp_path / 'book.json', slug='mequinho', match_name='Mecking', ack_unpartitioned=True)
    assert not (tmp_path / 'book.json').exists()


def test_other_player_explicitly_acknowledges_unpartitioned(tmp_path):
    pgn = tmp_path / 'any.pgn'
    pgn.write_text('example')
    with pytest.raises(ValueError, match='ack-unpartitioned'):
        build_opening_book(pgn, tmp_path / 'book.json', slug='fischer', match_name='Fischer')


def test_output_not_overwritten(tmp_path):
    pgn = tmp_path / 'any.pgn'
    pgn.write_text('example')
    output = tmp_path / 'book.json'
    output.write_text('keep')
    with pytest.raises(FileExistsError):
        build_opening_book(pgn, output, slug='fischer', match_name='Fischer', ack_unpartitioned=True)
    assert output.read_text() == 'keep'


def test_invalid_player_reference_rejected(tmp_path):
    f = tmp_path / 'book.json'
    f.write_text(json.dumps({'schema': SCHEMA, 'player': 'fischer', 'policy': {'max_plies': 16},
                             'positions': {'white': {}, 'black': {}}}))
    with pytest.raises(ValueError, match='mismatch'):
        load_opening_book(f, 'mequinho')


def test_sampling_spreads_positions():
    assert _sample_indices(10, 3) == {0, 4, 9}
    assert _sample_indices(3, 5) == {0, 1, 2}
    assert _sample_indices(0, 5) == set()


def test_book_sampling_respects_count_and_board_legality(monkeypatch):
    class Move:
        def __init__(self, text):
            self.text = text
        @classmethod
        def from_uci(cls, s):
            return cls(s)
        def __eq__(self, other):
            return isinstance(other, Move) and other.text == self.text
        def __hash__(self):
            return hash(self.text)
    monkeypatch.setitem(sys.modules, 'chess', SimpleNamespace(Move=Move, WHITE=True))
    class Board:
        turn = True
        legal_moves = {Move('e2e4'), Move('d2d4')}
        def fen(self):
            return 'foo w KQkq - 0 1'
        def ply(self):
            return 0
    doc = {'schema': SCHEMA, 'player': 'mequinho', 'policy': {'max_plies': 16},
           'positions': {'white': {'foo w KQkq -': {'e2e4': 10, 'a1a8': 9, 'd2d4': 1}}, 'black': {}}}
    assert select_book_move(doc, Board(), seed=9, min_count=2) == Move('e2e4')
    assert select_book_move(doc, Board(), seed=9, min_count=11) is None


def test_mainline_id_uses_metadata_and_moves():
    class Move:
        def __init__(self, uci): self.value = uci
        def uci(self): return self.value
    class Game:
        headers = {'Date': '1974.01.01', 'White': 'Mecking', 'Black': 'Fischer',
                   'Event': 'Friendly', 'Site': 'Brazil', 'Round': '1'}
        def mainline_moves(self): return (Move(x) for x in ['e2e4','e7e5'])
    assert _game_id(Game()) == _game_id(Game())


def test_cli_commands_parse_without_chess_module(capsys):
    from chesssim.cli import main
    with pytest.raises(SystemExit) as err:
        main(['play-styles', '--help'])
    assert err.value.code == 0
    assert '--white-book' in capsys.readouterr().out


def test_mequinho_opening_book_uses_only_frozen_train_ids(tmp_path, monkeypatch):
    """Reserved historical games cannot appear in the book, even when in the PGN."""
    import types
    from chesssim import opening_books

    class Move:
        def __init__(self, value): self.value = value
        def uci(self): return self.value

    class Board:
        def __init__(self): self.n = 0
        def ply(self): return self.n
        @property
        def turn(self): return (self.n % 2) == 0
        def fen(self): return f'position{self.n} w KQkq - 0 1'
        def push(self, move): self.n += 1

    class Game:
        def __init__(self, date, moves):
            self.headers = {'Date': date, 'White': 'Henrique Mecking', 'Black': 'Fischer',
                            'Event': 'Test', 'Site': 'Somewhere', 'Round': '1', 'Variant': 'Standard'}
            self.moves = [Move(m) for m in moves]
            self.errors = []
        def mainline_moves(self): return iter(self.moves)
        def board(self): return Board()

    train = Game('1974.01.01', ['e2e4', 'e7e5', 'g1f3', 'b8c6'])
    reserved = Game('1975.01.01', ['d2d4', 'd7d5', 'c2c4', 'e7e6'])
    game_iter = iter([train, reserved])
    fake_chess = types.ModuleType('chess'); fake_chess.__path__ = []
    fake_pgn = types.ModuleType('chess.pgn')
    fake_pgn.read_game = lambda _: next(game_iter, None)
    fake_chess.pgn = fake_pgn
    monkeypatch.setitem(sys.modules, 'chess', fake_chess)
    monkeypatch.setitem(sys.modules, 'chess.pgn', fake_pgn)
    monkeypatch.setattr(opening_books, 'extract_decisions_v5', lambda *a, **kw: ([{'game': 'fake'}], {}))
    monkeypatch.setattr(opening_books, '_cohort_v5_training',
                        lambda *a, **kw: ([{'game': _game_id(train)}], {'embargo_protected': True}))

    pgn = tmp_path / 'corpus.pgn'; pgn.write_text('mock corpus', encoding='utf-8')
    legacy = tmp_path / 'legacy.pgn'; legacy.write_text('mock legacy', encoding='utf-8')
    report = tmp_path / 'v5.json'; report.write_text('{}', encoding='utf-8')
    result = build_opening_book(pgn, tmp_path/'book.json', slug='mequinho', match_name='Mecking',
                                v5_report=report, legacy_v3=report, legacy_pgn=legacy)
    data = json.loads((tmp_path/'book.json').read_text(encoding='utf-8'))
    assert result['games'] == 1
    assert 'e2e4' in data['positions']['white']['position0 w KQkq -']
    assert 'd2d4' not in data['positions']['white']['position0 w KQkq -']
    assert data['source']['provenance']['status'] == 'FROZEN_V5_TRAIN_ONLY'
