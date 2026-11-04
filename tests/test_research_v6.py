"""v0.6 methodological tests, with engine fakes and real PGN optional."""
import json
from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chesssim.peak_windows import (
    annotation_template,
    default_peak_registry,load_peak_window,classify_mode,_time_control_seconds,
    annotate_decisions,modality_plan,modality_inventory,load_annotations, CLOCK_PRESETS)
from chesssim.research_peak import (prepare_peak_training,_cohort_v5_training,
                                  calibrate_v6,_distinct_positions,peak_audit)
from chesssim.historical import split_three_ways
from chesssim.calibration import file_hash


def rows(n=90,year=1975):
    return [dict(game=f"g{i:04d}", legacy_key=f"old{i:04d}",year=year,
      event='Classic Open',time_control='?',speed_tag='',
      fen=f"fen{i}_{j} w - - 0 19",move='e2e4',fullmove=9+j,
      color='white' if i%2 else 'black')
      for i in range(n) for j in range(4)]


def fake_eval(root,contents,positions,depth):
    # Simulate a player-dependent engine. No external UCI call.
    base = 8 if 'Botvinnik' in contents else 7
    if 'OwnAttack value 90' in contents: base+=1
    p = ['e2e4' if (int(d['game'][1:])+d['fullmove'])%13<base else 'd2d4' for d in positions]
    good=sum(a==d['move'] for a,d in zip(p,positions))
    return {'_predicted_moves':p,'correct':good,'decisions':len(positions),
            'top1':good/len(positions),'white':0,'black':0}


class V6PeaksAndModes(unittest.TestCase):
    def test_roster_complete_without_invented_peaks(self):
        p=default_peak_registry()
        self.assertEqual(len(p['players']),64)
        self.assertEqual(p['players']['mequinho']['status'],'PROVISIONAL')
        self.assertEqual(p['players']['menchik']['status'],'UNREVIEWED')
        self.assertIsNone(p['players']['carlsen']['from_year'])

    def test_provisional_gate_and_unreviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'p.json'
            p.write_text(json.dumps(default_peak_registry()))
            with self.assertRaisesRegex(ValueError,'Provisional'):
                load_peak_window(p,'mequinho')
            self.assertEqual(load_peak_window(p,'mequinho',allow_provisional=True)['to_year'],1978)
            with self.assertRaisesRegex(ValueError,'not reviewed'):
                load_peak_window(p,'carlsen',allow_provisional=True)

    def test_time_control_unambiguous(self):
        self.assertEqual(_time_control_seconds('40/7200:3600:30'),(10830,0))
        self.assertEqual(_time_control_seconds('180+2'),(180,2))
        self.assertIsNone(_time_control_seconds('?'))
        self.assertIsNone(_time_control_seconds('40/abc'))
        self.assertIsNone(_time_control_seconds('40/7200:*'))
        for value,expect in [('60+0','bullet'),('180+2','blitz'),('600+5','rapid'),('3600+30','classical')]:
            r=classify_mode(value)
            self.assertEqual(r['mode'],expect)
            self.assertEqual(r['evidence'],'TIMECONTROL_TAG')

    def test_event_hints_are_not_evidence(self):
        r=classify_mode('?', 'ACP Blitz')
        self.assertEqual((r['mode'],r['evidence']),('blitz','EVENT_LABEL_ONLY'))
        self.assertEqual(classify_mode('?', 'Open Championship')['mode'],'unknown')
        self.assertEqual(classify_mode('?', 'Rapid Blitz')['mode'],'unknown')
        self.assertEqual(classify_mode('900+10', 'Blitz')['mode'],'rapid')

    def test_tiers_and_unknown(self):
        ds=rows(15)
        self.assertEqual(modality_inventory(annotate_decisions(ds,{}))['unknown_games'],15)
        plan=modality_plan(annotate_decisions(ds,{}))
        self.assertEqual(plan['modes']['classical']['tier'],'C')
        for d in ds: d['time_control']='3600+30'
        plan=modality_plan(annotate_decisions(ds,{}),min_positions=50)
        self.assertEqual(plan['modes']['classical']['tier'],'A')
        self.assertEqual(plan['modes']['blitz']['tier'],'B')

    def test_external_override_requires_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'annot.json'
            gid='0'*64
            p.write_text(json.dumps({'schema':'modalities-annotations-v6',
                                     'games':{gid:{'mode':'rapid','evidence':'DOCUMENTED_EXTERNAL'}}}))
            with self.assertRaisesRegex(ValueError,'reference'):
                load_annotations(p)
            p.write_text(json.dumps({'schema':'modalities-annotations-v6',
                                     'games':{gid:{'mode':'rapid','evidence':'DOCUMENTED_EXTERNAL',
                                                 'reference':'book, page 5'}}}))
            self.assertEqual(load_annotations(p)[gid]['mode'],'rapid')

    def test_conflicting_time_control_manual_annotation_rejected(self):
        ds=rows(1)
        ds[0]['time_control']='3600+30'
        with self.assertRaisesRegex(ValueError,'conflicts'):
            annotate_decisions([ds[0]],{ds[0]['game']:{'mode':'blitz','reference':'X'}})

    def test_annotations_template_never_implicitly_labels_hint(self):
        ds=rows(1)
        for d in ds: d['game']='0'*64
        ds[0]['event']='2020 Blitz Cup'
        tpl=annotation_template(ds)
        self.assertIsNone(tpl['games']['0'*64]['mode'])
        self.assertEqual(tpl['games']['0'*64]['event_hint'],'blitz')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'pending.json';p.write_text(json.dumps(tpl))
            self.assertEqual(load_annotations(p),{})

    def test_clock_presets_not_historic_strength(self):
        self.assertEqual(CLOCK_PRESETS['classical'],(3600,30))
        self.assertEqual(CLOCK_PRESETS['bullet'],(60,0))

    def test_unique_position_sample(self):
        d=rows(4)
        d[4]['fen']=d[0]['fen']
        self.assertEqual(len(_distinct_positions(d)),len(d)-1)


class V6Seals(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.home=Path(t.name)
        self.peak=self.home/'peak.json'
        self.peak.write_text(json.dumps(default_peak_registry()))
        self.pgn=self.home/'full.pgn';self.pgn.write_bytes(b'unchanged')
        self.raw=rows()
        self.root=self.home/'rodent';(self.root/'personalities').mkdir(parents=True)
        (self.root/'rodent-iv-plain.exe').write_bytes(b'engine')
        for name,tag in [('karpov','Karpov'),('botvinnik','Botvinnik'),('petrosian','Petrosian'),('fischer','Fischer')]:
            (self.root/'personalities'/f'{name}.txt').write_text(f'; {tag}\nsetoption name OwnAttack value 110\n')
        train,val,test,part=split_three_ways(self.raw,seed=2028,train_limit=90,
                                              val_limit=30,test_limit=20)
        self.part=part
        self.v5=self.home/'v5';self.v5.mkdir()
        self.original={"version":5,"target_slug":"mequinho","pgn_sha256":file_hash(self.pgn),
            "partition_seed":2028,"partition_sha256":part['partition_sha256'],
            "test_manifest_sha256":part['test_sealed']['manifest_sha256'],
            "legacy_reserved_sha256":"dummy-v3"}
        self.report={"protocol":{"version":"rodent-style-calibration-v5"},
            "selection":{"requested":False},"final_test":{"status":"SEALED_NOT_EVALUATED",
            "manifest_sha256":part['test_sealed']['manifest_sha256']},
            "inputs":{"pgn_sha256":file_hash(self.pgn),"train_positions":90,"validation_positions":30},
            "partition":part,"cohort_registry":self.original,
            "cohort_registry_path":str(self.home/'ledger.json'),
            "legacy_embargo":{"excluded_games_present_in_new_corpus":0}}
        (self.v5/'calibration.json').write_text(json.dumps(self.report))
        (self.home/'ledger.json').write_text(json.dumps(self.original))
        self.protected={d['game'] for d in self.raw}-self._training_ids()
        self.v3=self.home/'legacy';self.v3.mkdir()
        self.oldpgn=self.home/'old.pgn';self.oldpgn.write_text('old')

    def _training_ids(self):
        import random
        gids=sorted({d['game'] for d in self.raw});random.Random(2028).shuffle(gids)
        nt=max(3,round(len(gids)*.15));nv=max(3,round(len(gids)*.20))
        return set(gids[nt+nv:])

    def _legacy(self,*a,**kw):
        return set(),{'reserved_game_ids_sha256':'dummy-v3'}

    def test_v5_embargo_all_reserved_games_and_exact_manifest(self):
        with patch('chesssim.research_peak._legacy_embargo',side_effect=self._legacy):
            train,policy=_cohort_v5_training(self.raw,report_path=self.v5,
                legacy_v3=self.v3,legacy_pgn=self.oldpgn,slug='mequinho',pgn=self.pgn)
        self.assertTrue(train)
        self.assertFalse(self.protected & {d['game'] for d in train})
        self.assertEqual(policy['origin'],'V5_FROZEN_TRAIN_GROUPS_ONLY')
        self.assertTrue(policy['v5_reserved_positions_not_evaluated'])

    def test_reject_changed_test_seal_and_changed_source(self):
        with patch('chesssim.research_peak._legacy_embargo',side_effect=self._legacy):
            self.report['cohort_registry']['test_manifest_sha256']='0'*64
            (self.v5/'calibration.json').write_text(json.dumps(self.report))
            with self.assertRaisesRegex(ValueError,'registry|seal'):
                _cohort_v5_training(self.raw,report_path=self.v5,
                    legacy_v3=self.v3,legacy_pgn=self.oldpgn,slug='mequinho',pgn=self.pgn)
            self.pgn.write_text('changed')
            with self.assertRaisesRegex(ValueError,'source PGN'):
                _cohort_v5_training(self.raw,report_path=self.v5,
                    legacy_v3=self.v3,legacy_pgn=self.oldpgn,slug='mequinho',pgn=self.pgn)

    def test_v6_peak_and_mode_do_not_pick_old_heldouts(self):
        with patch('chesssim.research_peak._legacy_embargo',side_effect=self._legacy),\
             patch('chesssim.research_peak.extract_decisions_v5',return_value=(self.raw,{'games_accepted':90})):
            with self.assertRaisesRegex(ValueError,'Provisional'):
                prepare_peak_training(self.pgn,self.peak,slug='mequinho',match_name='Mecking',
                   allow_provisional=False,annotations=None,mode='classical',v5_report=self.v5,
                   legacy_v3=self.v3,legacy_pgn=self.oldpgn)
            ds,info=prepare_peak_training(self.pgn,self.peak,slug='mequinho',match_name='Mecking',
                allow_provisional=True,annotations=None,mode='classical',include_unknown=True,
                v5_report=self.v5,legacy_v3=self.v3,legacy_pgn=self.oldpgn)
            self.assertTrue(ds)
            self.assertFalse(self.protected & {d['game'] for d in ds})
            self.assertEqual(info['training_policy']['training_evidence_tier'],
                             'B_UNVERIFIED_CLASSICAL_ASSUMPTION')
            with self.assertRaisesRegex(ValueError,'only for'):
                prepare_peak_training(self.pgn,self.peak,slug='mequinho',match_name='Mecking',
                    allow_provisional=True,annotations=None,mode='blitz',include_unknown=True,
                    v5_report=self.v5,legacy_v3=self.v3,legacy_pgn=self.oldpgn)

    def test_full_calibration_never_evaluates_v5_reserved_positions(self):
        def spy(root,text,positions,depth):
            self.assertFalse(self.protected & {d['game'] for d in positions})
            return fake_eval(root,text,positions,depth)
        with patch('chesssim.research_peak._legacy_embargo',side_effect=self._legacy),\
             patch('chesssim.research_peak.extract_decisions_v5',return_value=(self.raw,{'games_accepted':90})),\
             patch('chesssim.research_peak.evaluate_text',side_effect=spy),\
             patch('chesssim.research_crossvalidation.evaluate_text',side_effect=spy),\
             patch('chesssim.research_search.evaluate_text',side_effect=spy):
            report=calibrate_v6(self.root,self.pgn,self.home/'run',self.peak,
                slug='mequinho',match_name='Mecking',mode='classical',
                allow_provisional=True,include_unknown=True,v5_report=self.v5,
                legacy_v3=self.v3,legacy_pgn=self.oldpgn,
                positions=45,folds=3,repeats=2,cv_trials=1,trials=1,
                probe_positions=12,probe_params=('OwnAttack',))
        self.assertEqual(report['status'],'EXPLORATORY_CV_ONLY_NOT_HOMOLOGATED')
        self.assertEqual(report['selection']['status'],'NOT_PERMITTED_V6')
        self.assertFalse((self.home/'run'/'final_test.json').exists())
        self.assertEqual(len(report['cv_folds']),6)

    def test_fresh_player_requires_ack_and_cohort_immutable(self):
        data=default_peak_registry()
        data['players']['menchik'].update(status='APPROVED',from_year=1929,to_year=1937,
                                         justification='example independently verified',references=['reference for test'])
        self.peak.write_text(json.dumps(data))
        with patch('chesssim.research_peak.extract_decisions_v5',return_value=(self.raw,{})):
            with self.assertRaisesRegex(ValueError,'fresh-cohort-ack'):
                prepare_peak_training(self.pgn,self.peak,slug='menchik',match_name='Menchik',
                     allow_provisional=False,annotations=None,mode='classical')
            ds,info=prepare_peak_training(self.pgn,self.peak,slug='menchik',match_name='Menchik',
                     allow_provisional=False,annotations=None,mode='classical',include_unknown=True,
                     fresh_cohort_ack=True,cohort_ledger=self.home/'fresh.json')
            self.assertEqual(info['embargo']['origin'],'NEW_V6_SEALED_COHORT')
            self.assertTrue((self.home/'fresh.json').exists())
            self.assertTrue(ds)
            with self.assertRaisesRegex(ValueError,'CONFLICT'):
                prepare_peak_training(self.pgn,self.peak,slug='menchik',match_name='Menchik',
                    allow_provisional=False,annotations=None,mode='classical',include_unknown=True,
                    fresh_cohort_ack=True,cohort_ledger=self.home/'fresh.json',partition_seed=33)

    def test_real_pgn_peak_audit_if_installed(self):
        if find_spec('chess') is None:
            self.skipTest('python-chess is not installed')
        corpus=Path('/mnt/data/mequinho-all-available.pgn')
        if not corpus.exists(): self.skipTest('uploaded corpus missing')
        audit=peak_audit(corpus,self.peak,slug='mequinho',match_name='Mecking',allow_provisional=True)
        self.assertEqual(audit['source']['games_accepted'],90)
        self.assertGreater(audit['inventory']['unknown_games'],0)
        self.assertEqual(audit['modality_plan']['modes']['classical']['tier'],'C')


if __name__=='__main__':unittest.main()
