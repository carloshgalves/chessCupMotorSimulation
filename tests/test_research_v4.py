"""v0.4 regression tests with a deterministic fake UCI evaluator.

These tests deliberately do not pretend to prove real Rodent integration.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chesssim.historical import split_three_ways
from chesssim.research_search import (_objective, _folds_by_game, _check_v3_seal,
                                  calibrate_v4)


def fixtures(games=60):
    return [{"game": f"g{i:03d}", "year": 1975+(i%3),
             "event": f"event{i//5}", "fen": f"pos{i}-{j} w - - 0 {12+j}",
             "move": "e2e4", "color": "white" if i%2 else "black",
             "fullmove": 12+j} for i in range(games) for j in range(6)]


def fake_eval(root, source, positions, depth):
    # All returned moves are simplified stand-ins; no chess engine installed.
    if "Petrosian" in source:
        threshold = 7
    elif "Fischer" in source:
        threshold = 5
    elif "Botvinnik" in source:
        threshold = 4
    else:
        threshold = 3
    if "OwnAttack value 90" in source:
        threshold += 1
    if "OwnAttack value 130" in source:
        threshold -= 1
    moves = []
    for d in positions:
        number = int(d["game"][1:])
        move = "e2e4" if (number + d["fullmove"]) % 10 < threshold else "d2d4"
        moves.append(move)
    return {"decisions": len(positions), "correct": moves.count("e2e4"),
            "top1": moves.count("e2e4") / len(positions),
            "white": sum(d["color"]=="white" for d in positions),
            "black": sum(d["color"]=="black" for d in positions),
            "_predicted_moves": moves}


class V4Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.root = self.path / "rodent"
        profiles = self.root / "personalities"
        profiles.mkdir(parents=True)
        (self.root / "rodent-iv-plain.exe").write_bytes(b"test engine")
        for fname, title in [("karpov.txt", "Karpov"), ("petrosian.txt", "Petrosian"),
                             ("fischer.txt", "Fischer"), ("botvinnik.txt", "Botvinnik")]:
            (profiles / fname).write_text(f"; {title}\nsetoption name OwnAttack value 110\n"
                                                "setoption name GuideBookFile value players/example.bin\n")
        self.pgn = self.path / "Mecking.pgn"
        self.pgn.write_bytes(b"fake PGN")
        self.rows = fixtures()
        self.source = {"pgn_sha256": sha256(self.pgn.read_bytes()).hexdigest(),
                       "games_accepted":60, "decisions":360}
        self.reference = self.path / "legacy-v3"
        self.reference.mkdir()
        _, _, _, partition = split_three_ways(self.rows,seed=2027,
            train_limit=60,val_limit=30,test_limit=30)
        self.manifest = partition["test_sealed"]["manifest_sha256"]
        (self.reference / "calibration.json").write_text(json.dumps({
            "protocol":{"version":"rodent-style-calibration-v3"},
            "input":{"pgn_sha256": self.source["pgn_sha256"],"seed":2027,
                     "split_by":"game", "test_positions":30},
            "final_test":{"status":"SEALED_NOT_EVALUATED", "manifest_sha256":self.manifest}
        }))
        self.ledger = self.path / "registry.json"

    def run_v4(self, out="run", **kwargs):
        calls = []
        _, _, sealed, _ = split_three_ways(self.rows,seed=2027,
            train_limit=60,val_limit=30,test_limit=30)
        forbidden = {x["fen"] for x in sealed}
        def spy(root, text, positions, depth):
            self.assertFalse(forbidden & {x["fen"] for x in positions},
                             "a sealed test position entered an engine evaluation")
            calls.append((text, tuple(d["game"] for d in positions)))
            return fake_eval(root, text, positions, depth)
        with patch('chesssim.research_search.extract_decisions',return_value=(self.rows,self.source)), \
             patch('chesssim.research_search.evaluate_text',side_effect=spy), \
             patch.dict('chesssim.research_search.PARAM_RANGES',{"OwnAttack":(75,230)},clear=True):
            report = calibrate_v4(self.root, self.pgn, self.path / out,
                target="Henrique Mecking",target_slug="mecking",match_name="Mecking",
                train_positions=60,val_positions=30,test_positions=30,
                partition_seed=2027, search_seed=33,
                sealed_reference=self.reference,ledger=self.ledger,
                depth=3,trials=3,shortlist=2,folds=3,cv_trials=2,
                probe_params=("OwnAttack",),probe_positions=10, **kwargs)
        return report, calls

    def test_v4_compatibility_seal_no_final_peeking_and_outputs(self):
        report, calls = self.run_v4()
        self.assertGreater(len(calls),20)
        self.assertEqual(report['final_test']['status'],'SEALED_NOT_EVALUATED')
        self.assertEqual(report['sealed_reference']['status'],'MATCHES_V3_SEAL')
        self.assertEqual(report['final_test']['manifest_sha256'],self.manifest)
        self.assertEqual(len(report['cross_validation']),3)
        self.assertEqual(len(report['official_training_comparison']),4)
        self.assertTrue(Path(report['selected_profile']).is_file())
        self.assertTrue(Path(report['best_training_profile']).is_file())
        self.assertNotIn('GuideBookFile',Path(report['selected_profile']).read_text())
        self.assertFalse((self.path/'run'/'final_test.json').exists())
        self.assertEqual(list((self.root/'personalities'/'custom').glob('*.txt')),[])

    def test_cv_development_does_not_read_its_holdout_before_scoring(self):
        train = split_three_ways(self.rows,seed=2027,train_limit=60,
                                 val_limit=30,test_limit=30)[0]
        groups = _folds_by_game(train,3,2108)
        for development, held in groups:
            self.assertFalse({d['game'] for d in development} & {d['game'] for d in held})
        report,_ = self.run_v4()
        self.assertTrue(all('fold_chosen_base' in f for f in report['cross_validation']))
        self.assertIn(report['selection']['starting_base_train_only'],
                      [x['profile'] for x in report['official_training_comparison']])

    def test_changed_moves_never_give_positive_reward_without_acuracy_gain(self):
        a = {"top1": .5,"_predicted_moves":["a","a","b","b"]}
        b = {"top1": .5,"_predicted_moves":["a","b","a","b"]}
        self.assertEqual(_objective(a,a,.1),.5)
        self.assertLess(_objective(b,a,.1),_objective(a,a,.1))

    def test_strict_validation_tie_keeps_unmodified_official_baseline(self):
        def plateau(root, text, positions, depth):
            # Candidate can gain in training but cannot gain on selection validation.
            if len(positions) == 30:
                text = text.replace("OwnAttack value 90", "OwnAttack value 110")
            return fake_eval(root, text, positions, depth)
        with patch('chesssim.research_search.extract_decisions',return_value=(self.rows,self.source)), \
             patch('chesssim.research_search.evaluate_text',side_effect=plateau), \
             patch.dict('chesssim.research_search.PARAM_RANGES',{"OwnAttack":(75,230)},clear=True):
            report = calibrate_v4(self.root,self.pgn,self.path/'tied',
                target='Henrique Mecking', target_slug='mecking', match_name='Mecking',
                train_positions=60,val_positions=30,test_positions=30,
                sealed_reference=self.reference, ledger=self.ledger,
                depth=3,trials=3,shortlist=2,folds=3,cv_trials=2,
                probe_params=('OwnAttack',),probe_positions=10)
        self.assertEqual(report['status'],'VALIDATION_NO_GAIN_BASELINE_RETAINED')
        self.assertFalse(report['selection']['selected_is_personalized'])
        self.assertIn('BASELINE_NAO_PERSONALIZADO',report['selected_profile'])

    def test_ledger_forbids_drift_and_sealed_reference_must_match(self):
        report,_ = self.run_v4()
        with self.assertRaisesRegex(ValueError,'SEALED COHORT CONFLICT'):
            from chesssim.research_search import _assert_ledger
            _assert_ledger(self.ledger, {'different':'manifest'})
        self.assertEqual(json.loads(self.ledger.read_text()),report['cohort_registry'])
        with patch('chesssim.research_search.extract_decisions',return_value=(self.rows,self.source)):
            with self.assertRaisesRegex(ValueError,'V3 TEST SEAL MISMATCH'):
                calibrate_v4(self.root,self.pgn,self.path/'bad',
                     target='Henrique Mecking',target_slug='mecking',match_name='Mecking',
                     partition_seed=17,sealed_reference=self.reference,ledger=self.ledger)
        self.assertFalse((self.path/'bad').exists())

    def test_v3_test_already_open_blocks_v4(self):
        (self.reference/'final_test.json').write_text('{}')
        with patch('chesssim.research_search.extract_decisions',return_value=(self.rows,self.source)):
            with self.assertRaisesRegex(ValueError,'already been opened'):
                calibrate_v4(self.root,self.pgn,self.path/'bad',
                     target='Henrique Mecking',target_slug='mecking',match_name='Mecking',
                     partition_seed=2027, sealed_reference=self.reference,ledger=self.ledger)


if __name__ == '__main__':
    unittest.main()
