from pathlib import Path
import json

import pytest

from chesssim.provisional_profiles import provisional_install, PROVISIONAL_MECKING_ALIAS
from chesssim.registry import registered_aliases
from chesssim.temporal_controlled import _mix_sample


def _fixtures(tmp_path: Path):
    root = tmp_path / 'rodent'
    personalities = root / 'personalities'
    personalities.mkdir(parents=True)
    (root / 'rodent-iv-plain.exe').write_bytes(b'engine-for-fixture')
    (personalities / 'basic.ini').write_text('Fischer=fischer.txt\n', encoding='utf-8')
    source = tmp_path / 'source'
    source.mkdir()
    profile = source / 'mequinho_v08_PROVISIONAL.txt'
    profile.write_text('; ChessSimulation64 v0.8 provisional profile\nsetoption name OppAttack value 85\n', encoding='utf-8')
    (source / 'provisional_profile_report.json').write_text(json.dumps({
        'status': 'PROVISIONAL_PROFILE_NOT_VALIDATED',
        'player': 'mequinho',
        'profile_file': profile.name,
    }))
    return root, profile


def test_install_preview_then_apply(tmp_path):
    root, profile = _fixtures(tmp_path)
    original = (root / 'personalities' / 'basic.ini').read_bytes()
    preview = provisional_install(root, profile)
    assert preview['status'] == 'PREVIEW'
    assert (root / 'personalities' / 'basic.ini').read_bytes() == original
    result = provisional_install(root, profile, apply=True)
    assert result['status'] == 'REGISTERED'
    assert registered_aliases(root / 'personalities' / 'basic.ini')[PROVISIONAL_MECKING_ALIAS] == profile.name
    assert (root / 'personalities' / profile.name).read_bytes() == profile.read_bytes()
    assert Path(result['backup']).read_bytes() == original
    again = provisional_install(root, profile, apply=True)
    assert again['status'] == 'ALREADY_REGISTERED_SAME_PROFILE'


def test_install_refuses_to_overwrite_different_profile(tmp_path):
    root, profile = _fixtures(tmp_path)
    (root / 'personalities' / profile.name).write_text('another profile', encoding='utf-8')
    with pytest.raises(FileExistsError):
        provisional_install(root, profile, apply=True)


def test_career_arm_includes_peak_examples():
    peak = [
        {'game': f'p{g}', 'fen': f'peak{g}-{n} w - - 0 1', 'move': 'a2a3', 'year': 1975}
        for g in range(4) for n in range(3)
    ]
    outside = [
        {'game': f'o{g}', 'fen': f'outside{g}-{n} w - - 0 1', 'move': 'a2a3', 'year': 2000}
        for g in range(4) for n in range(3)
    ]
    result, meta = _mix_sample(peak, outside, 8, peak_fraction=0.0, seed=2, max_games=8, max_positions_per_game=2)
    assert len(result) == 8
    assert meta['career_arm_includes_peak_games']
    assert any(r['year'] == 1975 for r in result)
    assert any(r['year'] == 2000 for r in result)


def test_fraction_caps_never_exceeded():
    peak = [
        {'game': f'p{g}', 'fen': f'peak{g}-{n} w - - 0 1', 'move': 'a2a3', 'year': 1975}
        for g in range(6) for n in range(8)
    ]
    outside = [
        {'game': f'o{g}', 'fen': f'outside{g}-{n} w - - 0 1', 'move': 'a2a3', 'year': 2000}
        for g in range(6) for n in range(8)
    ]
    result, meta = _mix_sample(peak, outside, 24, peak_fraction=0.75, seed=2, max_games=8, max_positions_per_game=4)
    assert len(result) == 24
    assert meta['distinct_games'] <= 8
    assert meta['peak_positions'] == 18
    assert meta['outside_positions'] == 6
    assert max(sum(r['game'] == x for r in result) for x in {r['game'] for r in result}) <= 4
