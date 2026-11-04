"""v0.5 regression tests (fake evaluations, no claim of real-engine validation)."""
from __future__ import annotations
import io
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from chesssim.corpus_collection import _Links, roster_sources, fetch_profiles, make_corpus
from chesssim.historical import split_three_ways
from chesssim.research_crossvalidation import calibrate_v5, _legacy_embargo


def rows(n=90):
    return [dict(game=f"g{i:04d}",year=1974+i%3,event=f"Event {i//4}",
        fen=f"fen{i}_{j} w - - 0 19",move="e2e4",fullmove=10+j,
        color="white" if i%2 else "black")
        for i in range(n) for j in range(4)]


def fake_eval(root,contents,positions,depth):
    base=8 if "Botvinnik" in contents else 7 if "Karpov" in contents else 6
    if "OwnAttack value 90" in contents:
        base+=1
    preds=["e2e4" if (int(d['game'][1:])+d['fullmove'])%13 < base else "d2d4" for d in positions]
    good=sum(a==d['move'] for a,d in zip(preds,positions))
    return {'_predicted_moves':preds, 'correct':good,'decisions':len(positions),
        'top1':good/len(positions), 'white':sum(d['color']=='white' for d in positions),
        'black':sum(d['color']=='black' for d in positions)}


class CorpusV5Tests(unittest.TestCase):
    def test_catalog_parser_only_player_zip(self):
        parser=_Links()
        parser.feed('<a href="players/Mecking.zip">Download</a><a href="tournaments/Mecking.zip">X</a>'
                    '<a href="players/Carlsen.zip">C</a>')
        self.assertEqual(parser.filenames, {'Mecking','Carlsen'})
        result=roster_sources({'filenames':['Mecking','Carlsen','PolgarJ','So']})
        self.assertEqual(len(result),64)
        self.assertEqual(next(x for x in result if x['slug']=='wesley-so')['source_file'],'So.zip')

    def test_fetch_mock_zip_no_overwrite_and_no_network_on_preview(self):
        with tempfile.TemporaryDirectory() as t:
            dest=Path(t)
            blob=io.BytesIO()
            with ZipFile(blob,'w',ZIP_DEFLATED) as z:
                z.writestr('Mecking.pgn',b'[Event "Sample"]\n[White "Mecking"]\n[Black "Karpov"]\n\n1. e4 e5 *\n')
            calls=[]
            def download(url):
                calls.append(url)
                return blob.getvalue()
            index={'filenames':['Mecking'], 'index_sha256':'test'}
            dry=fetch_profiles(dest,slugs=['mequinho'],dry_run=True,index=index,get_payload=download)
            self.assertEqual(dry['entries'][0]['status'],'PREVIEW')
            self.assertEqual(calls,[])
            real=fetch_profiles(dest,slugs=['mequinho'],index=index,get_payload=download)
            self.assertEqual(real['entries'][0]['status'],'FETCHED')
            self.assertEqual(len(calls),1)
            again=fetch_profiles(dest,slugs=['mequinho'],index=index,get_payload=download)
            self.assertEqual(again['entries'][0]['status'],'EXISTS_NOT_OVERWRITTEN')
            self.assertEqual(len(calls),1)

    def test_merge_genuine_pgn_if_python_chess_installed(self):
        try: import chess.pgn
        except ImportError: self.skipTest('python-chess only available in user WSL')
        pgntxt = '[Event "Example"]\n[Site "X"]\n[Date "1975.01.01"]\n[Round "1"]\n[White "Mecking"]\n[Black "Botvinnik"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 *\n'
        with tempfile.TemporaryDirectory() as t:
            src=Path(t)
            (src/'one.pgn').write_text(pgntxt,encoding='utf-8')
            (src/'two.pgn').write_text(pgntxt,encoding='utf-8')
            result=make_corpus([src],src/'merged/corpus.pgn',match_name='Mecking')
            self.assertEqual(result['unique_games'],1)
            self.assertEqual(result['excluded']['duplicate'],1)
            self.assertTrue((src/'merged/corpus.manifest.json').is_file())


class ResearchV5Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home=Path(temp.name)
        self.root=self.home/'rodent'
        (self.root/'personalities').mkdir(parents=True)
        (self.root/'rodent-iv-plain.exe').write_bytes(b'engine-v5')
        for name,label in [('karpov.txt','Karpov'),('botvinnik.txt','Botvinnik'),
                           ('petrosian.txt','Petrosian'),('fischer.txt','Fischer')]:
            (self.root/'personalities'/name).write_text(
                f'; {label}\nsetoption name OwnAttack value 110\n'
                'setoption name GuideBookFile value books/k.bin\n',encoding='utf-8')
        self.pgn=self.home/'extended.pgn'
        self.pgn.write_text('stub',encoding='utf-8')
        self.rows=rows()
        self.stats={'pgn_sha256':sha256(b'stub').hexdigest(),
                    'games_accepted':90,'decisions':len(self.rows)}
        self.cohort=self.home/'registry/cohort.json'
        self.validation_ledger=self.home/'registry/used.json'

    def run_cal(self,out,select=False):
        forbidden=split_three_ways(self.rows,seed=2028,train_limit=90,
                                  val_limit=30,test_limit=20)[2]
        test_fens={d['fen'] for d in forbidden}
        seen=[]
        def spy(root,text,positions,depth):
            self.assertFalse(test_fens & {d['fen'] for d in positions}, 'sealed test leaked to UCI')
            seen.append(tuple(d['game'] for d in positions))
            return fake_eval(root,text,positions,depth)
        with patch('chesssim.research_crossvalidation._legacy_embargo',return_value=(set(),{'status':'NONE'})), \
             patch('chesssim.research_crossvalidation.extract_decisions_v5',return_value=(self.rows,self.stats)), \
             patch('chesssim.research_crossvalidation.evaluate_text',side_effect=spy), \
             patch('chesssim.research_search.evaluate_text',side_effect=spy):
            report=calibrate_v5(self.root,self.pgn,self.home/out,target='Other',
                target_slug='other',match_name='Other',train_positions=90,val_positions=30,
                test_positions=20,depth=3,folds=3,repeats=2,
                cv_trials=1,trials=2,shortlist=1,probe_positions=15,
                probe_params=('OwnAttack',),cohort_ledger=self.cohort,
                validation_ledger=self.validation_ledger,select=select)
        return report,seen

    def test_cv_only_never_reads_sealed_validation_or_test(self):
        train,val,test,_=split_three_ways(self.rows,seed=2028,train_limit=90,val_limit=30,test_limit=20)
        validation_fens={d['fen'] for d in val}
        with patch('chesssim.research_crossvalidation._legacy_embargo',return_value=(set(),{'status':'NONE'})), \
             patch('chesssim.research_crossvalidation.extract_decisions_v5',return_value=(self.rows,self.stats)):
            def protected(root,contents,positions,depth):
                self.assertFalse(validation_fens & {d['fen'] for d in positions})
                return fake_eval(root,contents,positions,depth)
            with patch('chesssim.research_crossvalidation.evaluate_text',side_effect=protected), \
                 patch('chesssim.research_search.evaluate_text',side_effect=protected):
                report=calibrate_v5(self.root,self.pgn,self.home/'research',
                    target='Other',target_slug='other',match_name='Other',
                    train_positions=90,val_positions=30,test_positions=20,
                    repeats=2,folds=3,cv_trials=1,trials=2,shortlist=1,
                    probe_positions=15,probe_params=('OwnAttack',),depth=3,
                    cohort_ledger=self.cohort)
        self.assertEqual(report['status'],'VALIDATION_SEALED_NOT_ACCESSED')
        self.assertEqual(report['cv_summary']['repeats'],2)
        self.assertEqual(len(report['cv_folds']),6)
        self.assertFalse(self.validation_ledger.exists())
        self.assertFalse((self.home/'research'/'final_test.json').exists())

    def test_one_time_selection_claim_is_enforced(self):
        report,_=self.run_cal('selected',select=True)
        self.assertTrue(self.validation_ledger.exists())
        self.assertNotEqual(report['selection']['status'],'VALIDATION_SEALED_NOT_ACCESSED')
        with self.assertRaises(FileExistsError):
            self.run_cal('second_try',select=True)
        self.assertFalse((self.home/'second_try').exists())

    def test_preselection_gate_refuses_flat_cv_before_validation_claim(self):
        def constant(root,contents,positions,depth):
            answers=["e2e4" if int(d["game"][1:])%4<2 else "d2d4" for d in positions]
            correct=sum(a==d["move"] for a,d in zip(answers,positions))
            return {"_predicted_moves":answers,"correct":correct,
                    "decisions":len(positions),"top1":correct/len(positions),
                    "white":0,"black":0}
        with patch('chesssim.research_crossvalidation._legacy_embargo',return_value=(set(),{'status':'NONE'})), \
             patch('chesssim.research_crossvalidation.extract_decisions_v5',return_value=(self.rows,self.stats)), \
             patch('chesssim.research_crossvalidation.evaluate_text',side_effect=constant), \
             patch('chesssim.research_search.evaluate_text',side_effect=constant):
            with self.assertRaisesRegex(ValueError,'CV PRESELECTION GATE'):
                calibrate_v5(self.root,self.pgn,self.home/'no-selection',
                    target='Other',target_slug='other',match_name='Other',
                    train_positions=90,val_positions=30,test_positions=20,
                    repeats=2,folds=3,cv_trials=1,trials=2,shortlist=1,
                    probe_positions=15,probe_params=('OwnAttack',),depth=3,
                    cohort_ledger=self.cohort,validation_ledger=self.validation_ledger,
                    select=True)
        self.assertFalse(self.validation_ledger.exists())
        self.assertFalse((self.home/'no-selection').exists())

    def test_mequinho_without_legacy_reference_is_refused(self):
        with self.assertRaisesRegex(ValueError,'requires --legacy-v3'):
            _legacy_embargo(None,None,target_slug='mequinho')

    def test_legacy_test_and_validation_groups_are_both_embargoed(self):
        old_rows=rows(60)
        pgn=self.home/'Mecking.pgn'
        pgn.write_bytes(b'original')
        _,validation,test,part=split_three_ways(old_rows,seed=2027,
                                train_limit=60,val_limit=30,test_limit=30)
        reference=self.home/'v3'
        reference.mkdir()
        (reference/'calibration.json').write_text(json.dumps({
            'protocol':{'version':'rodent-style-calibration-v3'},
            'input':{'pgn_sha256':sha256(b'original').hexdigest(),
                     'from_year':1973,'to_year':1978,'seed':2027,
                     'train_positions':60,'val_positions':30,
                     'test_positions':30,'split_by':'game'},
            'match_name':'Mecking',
            'final_test':{'status':'SEALED_NOT_EVALUATED',
                          'manifest_sha256':part['test_sealed']['manifest_sha256']}}))
        with patch('chesssim.research_crossvalidation.extract_decisions',return_value=(old_rows,{})):
            reserved, info=_legacy_embargo(reference,pgn,target_slug='mequinho')
        self.assertEqual(info['status'],'OLD_VALIDATION_AND_TEST_EMBARGOED')
        self.assertTrue({d['game'] for d in validation}.issubset(reserved))
        self.assertTrue({d['game'] for d in test}.issubset(reserved))
        self.assertGreater(len(reserved),len({d['game'] for d in test}))

    def test_same_cohort_rejects_changes_to_pgn(self):
        report,_=self.run_cal('first')
        self.pgn.write_text('changed',encoding='utf-8')
        stats={**self.stats,'pgn_sha256':sha256(b'changed').hexdigest()}
        with patch('chesssim.research_crossvalidation._legacy_embargo',return_value=(set(),{'status':'NONE'})), \
             patch('chesssim.research_crossvalidation.extract_decisions_v5',return_value=(self.rows,stats)):
            with self.assertRaisesRegex(ValueError,'COHORT REGISTRY CONFLICT'):
                calibrate_v5(self.root,self.pgn,self.home/'changed',
                    target='Other',target_slug='other',match_name='Other',
                    train_positions=90,val_positions=30,test_positions=20,
                    repeats=2,folds=3,cv_trials=1,trials=2,shortlist=1,
                    probe_positions=15,probe_params=('OwnAttack',),depth=3,
                    cohort_ledger=self.cohort)

if __name__=='__main__':unittest.main()
