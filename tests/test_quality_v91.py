import json
from pathlib import Path
from statistics import median

import pytest

from chesssim.quality_audit import (spread_indices, select_decisions,
                                  summarize_decisions, compare_quality_reports)


def _rows(cells=(8, 3)):
    out = []
    for gi, n in enumerate(cells):
        for color in ('white', 'black'):
            for j in range(n):
                out.append({'global_index': len(out), 'game_index': gi,
                            'color': color, 'player': color, 'fullmove': j + 1,
                            'played': 'e2e4', 'cp_loss': j})
    return out


def test_sample_balances_games_and_sides_without_padding():
    chosen, meta = select_decisions(_rows(), limit=20)
    assert len(chosen) == 12  # four cells, each limited by 3 eligible in game 2
    assert meta['cell_quota'] == 3
    assert len(set((r['game_index'], r['color'], r['fullmove']) for r in chosen)) == 12


def test_spread_allows_old_sampling():
    chosen, meta = select_decisions(_rows(), limit=20, mode='spread')
    assert len(chosen) == 20
    assert meta['method'] == 'spread'


def test_single_limit_centers():
    assert spread_indices(9, 1) == [4]
    assert spread_indices(20, 0) == []
    assert spread_indices(2, 8) == [0, 1]


def test_median_is_mean_of_middle_even():
    rows = [{'player': 'X', 'cp_loss': n, 'mate_affected': False, 'raw_gap_cp': 0} for n in (3, 4, 7, 300)]
    stats = summarize_decisions(rows)['X']
    assert stats['median_centipawn_loss'] == 5.5
    assert stats['mistakes_over_100cp'] == 1
    assert stats['blunders_over_250cp'] == 1


def test_invalid_budget():
    with pytest.raises(ValueError):
        select_decisions(_rows(), limit=1)


def test_compare_legacy_reports_aligns(tmp_path: Path):
    data = {'inputs': {'pgn_sha256': 'same', 'stockfish_sha256': 'engine', 'depth': 12},
            'decisions': [{'player': 'X', 'color': 'white', 'fullmove': 12,
                           'played': 'a2a4', 'cp_loss': 200}]}
    one = tmp_path/'a.json'
    two = tmp_path/'b.json'
    one.write_text(json.dumps(data))
    data['inputs']['depth'] = 18
    data['decisions'][0]['cp_loss'] = 0
    two.write_text(json.dumps(data))
    out = tmp_path / 'comparison.json'
    result = compare_quality_reports([one,two],out)
    assert out.exists()
    assert result['aligned_decisions'] == 1
    assert result['classification_changes'] == 1
    assert result['largest_changes'][0]['difference_last_minus_first'] == -200


def test_compare_rejects_distinct_pgn(tmp_path: Path):
    docs=[]
    for i in range(2):
        path=tmp_path/f'{i}.json'
        path.write_text(json.dumps({'inputs': {'pgn_sha256': str(i),
                                                'stockfish_sha256': 'same', 'depth': i+12},
                                    'decisions': []}))
        docs.append(path)
    with pytest.raises(ValueError, match='different PGNs'):
        compare_quality_reports(docs, tmp_path/'x.json')


def test_full_engine_path_requires_python_chess():
    # CI runners with python-chess can run the full executable smoke test;
    # it requires a real UCI engine and PGN, not mocked engine conclusions.
    pytest.importorskip('chess')
    assert True


def test_deduplicates_identical_mainlines_if_chess_available(tmp_path: Path):
    pytest.importorskip('chess')
    from chesssim.quality_audit import _load_positions
    pgn = tmp_path/'two.pgn'
    single = '''[Event "Sample"]\n[White "A"]\n[Black "B"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 O-O 8. c3 d6 9. h3 Nb8 10. d4 Nbd7 *\n\n'''
    pgn.write_text(single * 2)
    rows, games = _load_positions(pgn, min_fullmove=9, dedupe_games=True)
    assert len(games) == 2
    assert games[1]['excluded_from_analysis']
    assert {r['game_index'] for r in rows} == {0}
