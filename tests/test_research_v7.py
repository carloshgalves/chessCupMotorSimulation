"""v0.7 safety-focused tests: no legacy cohort opened, paired folds, evidence gates."""
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chesssim.corpus_annex import plan_annex,_allocation
from chesssim.time_control_evidence import match_tournament_evidence
from chesssim.temporal_paired import temporal_compare
from chesssim.peak_windows import default_peak_registry


def _rows(n=80,early=40):
    out=[]
    for i in range(n):
        for j in range(4):
            out.append({'game':f'g{i:03d}', 'legacy_key':f'legacy_{i:03d}',
                'year':1975 if i<early else 1998,
                'event':'Unknown', 'date': '1975.01.01' if i<early else '1998.01.01',
                'time_control':'?', 'speed_tag':'',
                'fen':f'position_{i}_{j} w - - 0 20','move':'e2e4',
                'color':'white' if i%2 else 'black','fullmove':10+j})
    return out


class PairedTemporal(unittest.TestCase):
    def test_identical_heldout_positions_and_no_training_group_leak(self):
        with tempfile.TemporaryDirectory() as dirname:
            p=Path(dirname); root=p/'rodent';root.mkdir();(root/'rodent-iv-plain.exe').write_bytes(b'fake')
            profile=root/'personality.txt';profile.write_text('fake personality')
            corpus=p/'career.pgn';corpus.write_text('unchanged')
            registry=p/'peak.json';registry.write_text(json.dumps(default_peak_registry()))
            all_rows=_rows();groups=[];evaluated=[]
            def choose_base(bases,dev,r,d):
                groups.append({row['game'] for row in dev})
                return 'karpov.txt',{}
            def evaluator(root,text,positions,depth):
                evaluated.append({row['game'] for row in positions})
                return {'_predicted_moves':['e2e4']*len(positions),
                        'correct':len(positions),'top1':1.,'decisions':len(positions)}
            with patch('chesssim.temporal_paired.extract_decisions_v5',return_value=(all_rows,{'games_accepted':80})),\
                 patch('chesssim.temporal_paired._cohort_v5_training',return_value=(all_rows[:280],{
                     'v5_report_sha256':'old_report','v5_test_manifest_sha256':'sealed',
                     'v5_partition_sha256':'old_partition'})),\
                 patch('chesssim.temporal_paired._load_bases',return_value={'karpov.txt':('fake personality',profile)}),\
                 patch('chesssim.temporal_paired._choose_base',side_effect=choose_base),\
                 patch('chesssim.temporal_paired.evaluate_text',side_effect=evaluator):
                result=temporal_compare(root,corpus,registry,p/'result.json',slug='mequinho',
                    match_name='Mecking',v5_report=p/'nonexistent',legacy_v3=p/'n',legacy_pgn=p/'n',
                    allow_provisional=True,include_unknown=True,
                    folds=2,repeats=2,depth=3,positions=32,train_budget=36)
            self.assertEqual(len(result['folds']),4)
            self.assertEqual(result['summary']['mean_delta_pp'],0)
            for i,fold in enumerate(result['folds']):
                self.assertEqual(fold['peak']['positions'],fold['career']['positions'])
                self.assertEqual(fold['train_budget'],36)
                self.assertFalse(groups[2*i]&evaluated[2*i])
                self.assertFalse(groups[2*i+1]&evaluated[2*i+1])
                self.assertEqual(evaluated[2*i],evaluated[2*i+1])
                self.assertTrue(all('1998' not in str(fold) for fold in result['folds']))
            self.assertEqual(result['final_test']['status'],'SEALED_NOT_EVALUATED')
            self.assertEqual(result['selection']['status'],'NOT_PERMITTED')

    def test_fails_before_engine_on_uncertain_peak(self):
        with tempfile.TemporaryDirectory() as dirname:
            p=Path(dirname);root=p/'rodent';root.mkdir();(root/'rodent-iv-plain.exe').write_bytes(b'fake')
            corpus=p/'career.pgn';corpus.write_text('unchanged')
            registry=p/'peak.json';registry.write_text(json.dumps(default_peak_registry()))
            with self.assertRaisesRegex(ValueError,'Provisional'):
                temporal_compare(root,corpus,registry,p/'out',slug='mequinho',match_name='Mecking',
                   v5_report=p/'no',legacy_v3=p/'no',legacy_pgn=p/'no')

class Evidence(unittest.TestCase):
    def test_undocumented_tournament_cannot_annotate(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'NO documented time control'):
                match_tournament_evidence(Path('x'),Path('y'),Path(tmp)/'out',
                    source_id='manila-interzonal-1976',match_name='Mecking')

    def test_exact_source_mainline_and_event_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); corpus=p/'base.pgn';corpus.write_bytes(b'base')
            ext=p/'nice.pgn';ext.write_bytes(b'source')
            g='a'*64; legacy='b'*64
            rows=[dict(game=g,legacy_key=legacy,year=1974,date='1974.06.20',
                       event='Nice olm prel',time_control='?',speed_tag='',
                       fen='p1 w - - 0 12',move='e2e4',fullmove=12,color='white')]
            source={(legacy,1974):[{'event':'Nice Olympiad','site':'Nice'}]}
            with patch('chesssim.time_control_evidence._fingerprints',return_value=(source,1)),\
                 patch('chesssim.time_control_evidence.extract_decisions_v5',return_value=(rows,{})):
                result=match_tournament_evidence(corpus,ext,p/'out.json',
                    source_id='nice-olympiad-1974',match_name='Mecking')
            self.assertEqual(result['matched_games'],1)
            ann=json.loads((p/'out.json').read_text())
            self.assertEqual(ann['games'][g]['mode'],'classical')
            self.assertEqual(ann['games'][g]['evidence'],'DOCUMENTED_EXTERNAL')
            self.assertEqual(ann['v7_evidence']['original_corpus_sha256'],sha256(b'base').hexdigest())
            with patch('chesssim.time_control_evidence._fingerprints',return_value=({(legacy,1974):[{'event':'?','site':'?'}]},1)),\
                 patch('chesssim.time_control_evidence.extract_decisions_v5',return_value=(rows,{})):
                result=match_tournament_evidence(corpus,ext,p/'none.json',source_id='nice-olympiad-1974',match_name='Mecking')
            self.assertEqual(result['matched_games'],0)

class Annex(unittest.TestCase):
    def test_allocations_deterministic(self):
        self.assertEqual(_allocation(2029,'abc'),_allocation(2029,'abc'))

    def test_old_game_and_shared_mainline_quarantined_no_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);base=p/'base.pgn';base.write_text('old')
            ext=p/'extra.pgn';ext.write_text('new')
            old=_rows(2,1)
            new=[dict(x) for x in old[:4]]
            for d in new:d.update(game='new_duplicate',legacy_key=old[0]['legacy_key'],fen='brandnew_'+d['fen'])
            safe=[dict(x) for x in old[4:8]]
            for d in safe:d.update(game='new_safe',legacy_key='new_moves',fen='unique_'+d['fen'])
            collide=[dict(x) for x in old[4:8]]
            for d in collide:d.update(game='new_bad',legacy_key='new_bad_move',fen='position_1_0 w - - 0 20')
            def extractor(file,**kwargs):
                return (old if file==base else new+safe+collide),{'games_accepted':2}
            with patch('chesssim.corpus_annex.extract_decisions_v5',side_effect=extractor),\
                 patch('chesssim.corpus_annex._cohort_v5_training',return_value=(old[:4],{
                     'v5_report_sha256':'r','v5_test_manifest_sha256':'t',
                     'v5_partition_sha256':'p'})):
                doc=plan_annex(base,[ext],p/'annex.json',match_name='Mecking',
                    v5_report=p/'v5',legacy_v3=p/'old',legacy_pgn=p/'old')
            self.assertEqual(doc['status'],'STAGED_ONLY_NO_ENGINES_AND_NO_TRAINING')
            self.assertEqual(doc['eligible_unique_new_games'],1)
            self.assertIn('SAME_MOVES_DIFFERENT_METADATA_REVIEW_REQUIRED',doc['rejected_new_games'])
            self.assertIn('FORBIDDEN_OLD_STATE_OVERLAP',doc['rejected_new_games'])
            self.assertEqual(doc['original_v5_test_manifest_sha256'],'t')

if __name__=='__main__':unittest.main()
