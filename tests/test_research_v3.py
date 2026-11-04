"""Pure and mocked v0.3 tests; real Rodent/Stockfish gates run on user's WSL."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chesssim.historical import split_three_ways, position_key
from chesssim.research_sealed import calibrate_v3, sensitivity_v3, paired_game_bootstrap
from chesssim.final_validation import final_evaluate


def sample_data(n=40):
    return [{"game": f"g{i:03}", "year": 1975+i%3,
             "event": "event"+str(i//4),
             "fen": f"position-{i}-{j} {'w' if i%2 else 'b'} - - 0 {10+j}",
             "move": "e2e4", "fullmove": 10+j,
             "color": "white" if i%2 else "black"}
            for i in range(n) for j in range(5)]


def fake_eval(root, contents, positions, depth):
    # Train+val must never evaluate sealed test. A changed candidate gains
    # a point on training and validation with deterministic traces.
    modified = "OwnAttack value 110" not in contents
    correct = len(positions)//2 + int(modified)
    trace = ["e2e4" if idx < correct else "d2d4" for idx in range(len(positions))]
    return {"decisions": len(positions), "correct": correct, "top1": correct/len(positions),
            "white": sum(p["color"] == "white" for p in positions),
            "black": sum(p["color"] == "black" for p in positions),
            "_predicted_moves": trace}


class ThreeWayDatasetTests(unittest.TestCase):
    def test_three_partitions_disjoint_deterministic_and_balanced(self):
        data = sample_data(60)
        a, b, c, manifest = split_three_ways(data, seed=42, train_limit=30,
                                               val_limit=12, test_limit=10)
        self.assertEqual((a,b,c,manifest), split_three_ways(data, seed=42,
                            train_limit=30, val_limit=12, test_limit=10))
        self.assertEqual((len(a),len(b),len(c)), (30,12,10))
        self.assertEqual(sum(manifest[k]["positions"] for k in ("train","validation"))+manifest["test_sealed"]["positions"],52)
        for x,y in ((a,b),(a,c),(b,c)):
            self.assertFalse({d["game"] for d in x}&{d["game"] for d in y})
            self.assertFalse({position_key(d) for d in x}&{position_key(d) for d in y})
        self.assertNotIn("test_game_ids", manifest)
        self.assertLessEqual(abs(manifest["train"]["white"]-manifest["train"]["black"]),2)

    def test_event_group_keeps_whole_events_together(self):
        a,b,c,_ = split_three_ways(sample_data(100), seed=21, group_by="event",
                                    train_limit=30, val_limit=10, test_limit=9)
        events = lambda vals: {(d["year"],d["event"]) for d in vals}
        self.assertFalse(events(a)&events(b) | events(a)&events(c) | events(b)&events(c))

    def test_cross_partition_transposition_discarded(self):
        data = sample_data(40)
        for d in data:
            if d["fen"].startswith("position-") and d["fen"].endswith("0 10"):
                d["fen"] = "common w - - 0 10"
        a,b,c,split = split_three_ways(data,seed=21,train_limit=20,val_limit=9,test_limit=8)
        self.assertGreater(split["removed_cross_partition_states"],0)
        self.assertTrue(all(position_key(d)!="common w - -" for d in [*a,*b,*c]))

    def test_too_few_groups_rejected(self):
        with self.assertRaisesRegex(ValueError,"15 grupos"):
            split_three_ways(sample_data(10))

    def test_game_bootstrap_zero_gain(self):
        data=sample_data(12)[:24]
        a={"_predicted_moves":["e2e4"]*len(data)}
        b={"_predicted_moves":["e2e4"]*len(data)}
        r=paired_game_bootstrap(a,b,data)
        self.assertEqual(r["macro_gain"],0)
        self.assertEqual(r["bootstrap_95pct_interval"],[0,0])


class FrozenProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.root=self.home/'rodent'
        (self.root/'personalities').mkdir(parents=True)
        (self.root/'rodent-iv-plain.exe').write_bytes(b'fake-rodent-engine-v3')
        (self.root/'personalities/karpov.txt').write_text('setoption name OwnAttack value 110\n'
               'setoption name PawnShield value 119\n'
               'setoption name GuideBookFile value players/ph-karpov2.bin\n')
        self.pgn=self.home/'mecking.pgn'
        self.pgn.write_text('mock data contents')
        self.data=sample_data(40)
        self.source={"pgn_sha256":sha256(self.pgn.read_bytes()).hexdigest(),"games_accepted":40,"decisions":200}

    def _calibrate(self, out=None, func=fake_eval):
        with patch('chesssim.research_sealed.extract_decisions',return_value=(self.data,self.source)), \
             patch('chesssim.research_sealed.evaluate_text',side_effect=func), \
             patch.dict('chesssim.research_sealed.PARAM_RANGES', {'OwnAttack':(75,230)},clear=True):
            return calibrate_v3(self.root,self.pgn,out or self.home/'run',target='Mecking',
                  target_slug='mequinho',match_name='Mecking',depth=4,
                  trials=2,shortlist=2,train_positions=16,val_positions=10,test_positions=9)

    def test_no_test_evaluated_during_tuning_and_separate_selection(self):
        test_data = split_three_ways(self.data,seed=2026,train_limit=16,
                                     val_limit=10,test_limit=9)[2]
        forbidden={d['fen'] for d in test_data}
        calls=[]
        def spy(root, text, positions, depth):
            self.assertFalse(forbidden & {d['fen'] for d in positions})
            calls.append(len(positions))
            return fake_eval(root,text,positions,depth)
        report=self._calibrate(func=spy)
        self.assertEqual(report['final_test']['status'], 'SEALED_NOT_EVALUATED')
        self.assertEqual(calls.count(9),0)
        self.assertEqual(calls.count(16),3)
        self.assertEqual(calls.count(10),3)
        self.assertIn('selected_trial',report['selection'])
        self.assertTrue(Path(report['selected_profile']).is_file())
        self.assertNotIn('ph-karpov2.bin',Path(report['selected_profile']).read_text())
        self.assertFalse((self.home/'run'/'final_test.json').exists())
        self.assertEqual(list((self.root/'personalities'/'custom').glob('*.txt')),[])

    def test_validation_tie_retains_baseline_despite_training_gain(self):
        def candidate_fails_on_val(root,text,positions,depth):
            base=fake_eval(root,text,positions,depth)
            if len(positions)==10:
                # All candidates tie on validation, regardless of training.
                base['correct']=5
                base['top1']=.5
                base['_predicted_moves']=['e2e4']*5+['d2d4']*5
            return base
        report=self._calibrate(func=candidate_fails_on_val)
        self.assertEqual(report['status'],'VALIDATION_NO_GAIN_BASELINE_RETAINED')
        self.assertEqual(report['selection']['selected_trial'],0)
        self.assertTrue(report['selected_profile'].endswith('BASELINE_NAO_PERSONALIZADO.txt'))
        self.assertTrue(Path(report['best_training_profile']).exists())

    def test_final_eval_once_hash_guard_and_independent_partition(self):
        self._calibrate()
        def evaluation(root,text,positions,depth):
            self.assertEqual(len(positions),9)
            return fake_eval(root,text,positions,depth)
        with patch('chesssim.final_validation.extract_decisions',return_value=(self.data,self.source)), \
             patch('chesssim.final_validation.evaluate_text',side_effect=evaluation):
            final=final_evaluate(self.root,self.home/'run')
            self.assertEqual(final['positions'],9)
            self.assertEqual(final['status'],'FINAL_TEST_EVALUATED_ONCE_NOT_VALIDATED')
            with self.assertRaises(FileExistsError):
                final_evaluate(self.root,self.home/'run')

    def test_missing_stockfish_preflight_does_not_open_final_test(self):
        self._calibrate()
        with patch('chesssim.final_validation.evaluate_text',side_effect=AssertionError('test must stay closed')):
            with self.assertRaises(FileNotFoundError):
                final_evaluate(self.root,self.home/'run',stockfish=self.home/'stockfish-not-installed')
        self.assertFalse((self.home/'run'/'final_test.json').exists())

    def test_final_eval_refuses_edited_candidate(self):
        report=self._calibrate()
        Path(report['selected_profile']).write_text('tampered')
        with patch('chesssim.final_validation.extract_decisions',return_value=(self.data,self.source)):
            with self.assertRaisesRegex(ValueError,'Candidato modificado'):
                final_evaluate(self.root,self.home/'run')

    def test_sensitivity_sees_train_only(self):
        result=[]
        def spy(root,text,positions,depth):
            result.append(len(positions))
            return fake_eval(root,text,positions,depth)
        with patch('chesssim.research_sealed.extract_decisions',return_value=(self.data,self.source)), \
             patch('chesssim.research_sealed.evaluate_text',side_effect=spy), \
             patch.dict('chesssim.research_sealed.PARAM_RANGES',{'OwnAttack':(75,230)},clear=True):
            report=sensitivity_v3(self.root,self.pgn,self.home/'sensitivity.json',
                 match_name='Mecking',positions=8,depth=3)
        self.assertTrue(result and all(k==8 for k in result))
        self.assertEqual(len(report['sensitivity']),2)
        self.assertEqual(report['status'],'EXPLORATORY_TRAIN_ONLY')

if __name__=='__main__':unittest.main()
