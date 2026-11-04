from pathlib import Path

from chesssim.signature_v8 import _cohort_signature
from chesssim.temporal_v8 import _mix_sample, _round_robin_sample


def test_round_robin_sample_limits_games_and_positions():
    rows = []
    for gid in range(5):
        for idx in range(6):
            rows.append({
                'game': f'g{gid}',
                'fen': f'8/8/8/8/8/8/8/8 w - - 0 {idx + 1 + gid*10}',
                'move': 'a2a3',
                'year': 1975,
                'color': 'white',
                'fullmove': idx + 1,
            })
    sample = _round_robin_sample(rows, 9, seed=1, max_games=3, max_positions_per_game=2)
    assert len(sample) <= 6
    assert len({r['game'] for r in sample}) <= 3
    for gid in {r['game'] for r in sample}:
        assert sum(r['game'] == gid for r in sample) <= 2


def test_mix_sample_reaches_requested_cap_when_possible():
    peak = [
        {'game': 'p1', 'fen': f'peak{n} w - - 0 1', 'move': 'a2a3', 'year': 1975, 'color': 'white', 'fullmove': 10}
        for n in range(10)
    ]
    outside = [
        {'game': 'o1', 'fen': f'out{n} w - - 0 1', 'move': 'a2a3', 'year': 1982, 'color': 'white', 'fullmove': 10}
        for n in range(10)
    ]
    sample, meta = _mix_sample(peak, outside, 8, peak_fraction=0.75, seed=7, max_games=4, max_positions_per_game=8)
    assert len(sample) == 8
    assert meta['requested_cap'] == 8


def test_cohort_signature_basic_metrics(tmp_path: Path):
    import pytest
    pytest.importorskip("chess")
    pgn = tmp_path / 'mini.pgn'
    pgn.write_text('''[Event "Test"]\n[Site "?"]\n[Date "1974.01.01"]\n[Round "1"]\n[White "Henrique Mecking"]\n[Black "Someone"]\n[Result "1-0"]\n[Opening "King's Pawn Game"]\n[ECO "C20"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6 5. d3 d6 6. c3 O-O 7. Re1 a6 8. Bb3 Ba7 9. Nbd2 h6 10. Nf1 Be6 1-0\n\n''', encoding='utf-8')
    report = _cohort_signature(pgn, 'Mecking', from_year=1973, to_year=1978)
    assert report['games'] == 1
    assert report['results']['win'] == 1
    assert report['castle_rate'] == 1.0
    assert report['top_eco'][0]['label'] == 'C20'
