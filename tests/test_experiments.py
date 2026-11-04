import io
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from chesssim.protocol import OFFICIAL, CALIBRATION
from chesssim.historical import import_mecking, player_side, split_positions
from chesssim.calibration import tune_text, extract_values, PARAM_RANGES, validate_performance
from chesssim.uci_raw import RodentUci, UciFailure


class ProtocolAndFitting(unittest.TestCase):
    def test_protocol_lock(self):
        self.assertEqual(OFFICIAL.time_control, '10+2')
        self.assertEqual(OFFICIAL.threads_per_engine, 1)
        self.assertEqual(OFFICIAL.hash_mb_per_engine, 64)
        self.assertFalse(OFFICIAL.uci_limit_strength)
        self.assertTrue(OFFICIAL.use_book)
        self.assertEqual(len(OFFICIAL.json()['sha256']),64)
        self.assertFalse(CALIBRATION['book'])

    def test_tune_only_named_params(self):
        base = 'setoption name OwnAttack value 110\nsetoption name PawnStructure value 113\n'
        changed = tune_text(base, {'OwnAttack': 155, 'PassedPawns': 150})
        self.assertIn('OwnAttack value 155',changed)
        self.assertIn('PawnStructure value 113',changed)
        self.assertIn('PassedPawns value 150',changed)
        self.assertEqual(extract_values(changed)['OwnAttack'],155)
        self.assertEqual(changed.count('OwnAttack value'),1)
        with self.assertRaises(ValueError): tune_text(base, {'OwnAttack': 999})
        with self.assertRaises(ValueError): tune_text(base, {'Mystery': 10})

    def test_metrics(self):
        r=validate_performance([{'match':True,'color':'white'}, {'match':False,'color':'black'}])
        self.assertEqual(r['top1'], 0.5)
        self.assertEqual(r['white'], 1)

    def test_header_name_checks(self):
        class Game:
            headers={'White':'Mecking, Henrique','Black':'Kortschnoj, Viktor'}
        self.assertIs(player_side(Game(),'mecking'),True)
        self.assertIs(player_side(Game(),'kortschnoj'),False)
        self.assertIsNone(player_side(Game(),'fischer'))

    def test_split_games_disjoint_and_stable(self):
        data=[]
        for i in range(20):
            for j in range(10):
                # Distinct board keys; valid FEN not required for this pure split test.
                data.append({'game': f'g{i:02}', 'fen': f'square{i}-{j} w - - 0 {j+1}',
                             'move':'a2a3', 'color':'white' if i%2 else 'black'})
        t,v,report=split_positions(data,seed=42,train_limit=50,val_limit=20)
        self.assertEqual(report['train_games'],16)
        self.assertEqual(report['val_games'],4)
        self.assertFalse(set(x['game'] for x in t)&set(x['game'] for x in v))
        self.assertEqual((t,v,report),split_positions(data,seed=42,train_limit=50,val_limit=20))

    def test_cross_split_transpositions_removed(self):
        data=[]
        for i in range(10):
            data.append({'game':f'g{i}', 'fen':'sameposition w - - 0 1', 'move':'a2a3', 'color':'white'})
            data.append({'game':f'g{i}', 'fen':f'pos{i} w - - 0 1', 'move':'a2a3', 'color':'white'})
        t,v,_=split_positions(data,seed=3,train_limit=0,val_limit=0)
        key=lambda d:' '.join(d['fen'].split()[:4])
        self.assertFalse({key(x) for x in t}&{key(x) for x in v})


class DownloadSafety(unittest.TestCase):
    def test_fetch_zip_pgn_provenance_and_no_overwrite(self):
        buffer=io.BytesIO()
        with ZipFile(buffer,'w') as z:
            z.writestr('Mecking.pgn', '[Event "Test"]\n[White "Mecking"]\n1. e4 e5 *\n')
        class Response:
            def read(self,n): return buffer.getvalue()
            def __enter__(self): return self
            def __exit__(self,*a): return False
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'datasets/Mecking.pgn'
            with patch('chesssim.historical.urlopen',return_value=Response()):
                report=import_mecking(dest)
            self.assertTrue(dest.is_file())
            self.assertEqual(len(report['pgn_sha256']),64)
            self.assertTrue(dest.with_suffix('.source.json').is_file())
            with self.assertRaises(FileExistsError): import_mecking(dest)


class HiddenUci(unittest.TestCase):
    def test_hidden_personalityfile_is_sent_and_verified(self):
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'fake-engine'
            exe.write_text(textwrap.dedent('''#!/usr/bin/env python3
import sys
for line in sys.stdin:
    line=line.strip()
    if line=='uci': print('id name Fake Rodent', flush=True); print('uciok',flush=True)
    elif line.startswith('setoption name PersonalityFile value '):
        print("info string reading personality 'custom/example.txt' (success)",flush=True)
    elif line=='isready': print('readyok',flush=True)
    elif line.startswith('go '): print('bestmove e2e4',flush=True)
    elif line=='quit': break
'''))
            exe.chmod(0o755)
            with RodentUci(exe,Path(d),profile='custom/example.txt') as e:
                move,logs=e.bestmove('a_fen',depth=4)
                self.assertEqual(move,'e2e4')
                self.assertIn('bestmove',logs[-1])


if __name__=='__main__':unittest.main()

class PipelineOrchestration(unittest.TestCase):
    def test_experimental_profile_emitted_but_not_registered(self):
        from chesssim.calibration import calibrate_personality
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'rodent'
            (root/'personalities').mkdir(parents=True)
            (root/'rodent-iv-plain.exe').write_bytes(b'fake executable')
            (root/'personalities/karpov.txt').write_text(
                'setoption name OwnAttack value 110\nsetoption name PawnStructure value 150\n')
            basic=root/'personalities/basic.ini'
            basic.write_text('HIDE_OPTIONS\nFischer=fischer.txt\nKarpov=karpov.txt\n')
            raw=[{'game':f'g{i}', 'fen':f'pos{i}-{j} w - - 0 10', 'move':'e2e4',
                  'color':'white'} for i in range(30) for j in range(8)]
            source={'pgn_sha256':'ab'*32,'games_accepted':30,'decisions':len(raw)}
            def evaluation(root, rel, positions, depth):
                text=(root/'personalities'/rel).read_text()
                n=len(positions)
                good=int(n*.5)+(int('OwnAttack value 110' not in text) if n>3 else 0)
                return {'decisions':n,'correct':good, 'top1':good/n,
                        'white':n,'black':0}
            with patch('chesssim.calibration.extract_decisions', return_value=(raw,source)), \
                 patch('chesssim.calibration.evaluate_candidate', side_effect=evaluation):
                out=Path(d)/'output'
                report=calibrate_personality(root,Path(d)/'dummy.pgn',out,
                    target='Henrique Mecking',target_slug='mequinho',
                    match_name='Mecking',trials=2,train_positions=20,val_positions=10,min_holdout_games=4)
            self.assertEqual(report['status'],'NO_TRAIN_IMPROVEMENT_BASELINE_RETAINED')
            self.assertTrue(report['profile_is_baseline_copy'])
            self.assertEqual(report['training_gain'], 0)
            self.assertEqual(report['trials'],3)
            self.assertTrue((out/'calibration.json').exists())
            self.assertTrue((out/'mequinho_BASELINE_NAO_PERSONALIZADO.txt').exists())
            self.assertEqual(basic.read_text(), 'HIDE_OPTIONS\nFischer=fischer.txt\nKarpov=karpov.txt\n')
            self.assertEqual(list((root/'personalities/custom').glob('*.txt')),[])


class CalibrationDiagnostics(unittest.TestCase):
    def test_trace_comparison_and_baseline_detection(self):
        from chesssim.calibration import _diagnostics
        original = {'top1': 0.4, 'correct': 2, 'decisions': 5,
                    '_predicted_moves': ['a2a3', 'b2b3', 'c2c3', 'd2d3', 'e2e3']}
        another = {'top1': 0.4, 'correct': 2, 'decisions': 5,
                   '_predicted_moves': ['a2a3', 'b2b4', 'c2c3', 'd2d4', 'e2e3']}
        result = _diagnostics(another, original)
        self.assertEqual(result['moves_changed_vs_baseline'], 2)
        self.assertEqual(result['move_change_ratio'], 0.4)
        self.assertEqual(len(result['move_trace_sha256']), 64)
        self.assertNotIn('_predicted_moves', result)
        with self.assertRaises(ValueError):
            _diagnostics({'_predicted_moves':['e2e4']}, original)

    def test_no_karpov_books_in_output(self):
        from chesssim.calibration import _remove_inherited_opening_books
        txt = ('setoption name OwnAttack value 110\n'
               'setoption name GuideBookFile value players/ph-karpov2.bin\n'
               'setoption name MainBookFile value hist/_81to06.bin\n'
               'setoption name PawnShield value 119\n')
        filtered = _remove_inherited_opening_books(txt)
        self.assertNotIn('BookFile', filtered)
        self.assertIn('OwnAttack value 110', filtered)
        self.assertIn('PawnShield value 119', filtered)

    def test_actual_improvement_creates_experimental_candidate(self):
        from chesssim.calibration import calibrate_personality
        import chesssim.calibration as cal
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'rodent'
            (root/'personalities').mkdir(parents=True)
            (root/'rodent-iv-plain.exe').write_bytes(b'fake executable')
            (root/'personalities/karpov.txt').write_text(
                'setoption name OwnAttack value 110\n'
                'setoption name GuideBookFile value players/ph-karpov2.bin\n')
            raw=[{'game':f'g{i}', 'fen':f'pos{i}-{j} w - - 0 10', 'move':'e2e4',
                  'color':'white'} for i in range(20) for j in range(6)]
            source={'pgn_sha256':'ab'*32, 'games_accepted':20, 'decisions':len(raw)}
            def evaluation(root, rel, positions, depth):
                contents=(root/'personalities'/rel).read_text()
                good = len(positions)//2 + int('OwnAttack value 110' not in contents)
                return {'decisions':len(positions),'correct':good,
                        'top1':good/len(positions), 'white':len(positions), 'black':0,
                        '_predicted_moves':['e2e4' if 'OwnAttack value 110' in contents else 'd2d4'] * len(positions)}
            with patch.dict(cal.PARAM_RANGES, {'OwnAttack': (75,230)}, clear=True), \
                 patch('chesssim.calibration.extract_decisions', return_value=(raw,source)), \
                 patch('chesssim.calibration.evaluate_candidate', side_effect=evaluation):
                out=Path(d)/'output'
                report=calibrate_personality(root,Path(d)/'dummy.pgn',out,
                    target='Henrique Mecking',target_slug='mequinho',
                    match_name='Mecking',trials=2,train_positions=20,val_positions=10,min_holdout_games=4)
            self.assertEqual(report['status'], 'EXPERIMENTAL_NOT_VALIDATED')
            self.assertFalse(report['profile_is_baseline_copy'])
            self.assertGreater(report['training_gain'], 0)
            self.assertGreater(report['trials_with_changed_moves'], 0)
            self.assertEqual(report['candidate_holdout']['moves_changed_vs_baseline'], 10)
            self.assertTrue((out/'mequinho_EXPERIMENTAL_NAO_VALIDADO.txt').is_file())
            self.assertNotIn('ph-karpov2.bin', Path(report['candidate']).read_text())
