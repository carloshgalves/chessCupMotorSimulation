"""v0.7: paired peak-era vs full-career study, evaluated on identical peak folds.

Only the FROZEN v5 TRAIN groups are ever presented to a chess engine.  Each
fold's peak held-out game is excluded from BOTH learning arms and all matching
board states are purged.  Repeats are not independent outside samples.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
from statistics import mean, pstdev

from .calibration import file_hash, tune_text
from .corpus_v5 import extract_decisions_v5
from .historical import _balanced_sample, position_key
from .peak_v6 import annotate_decisions, load_annotations, load_peak_window
from .research_v3 import evaluate_text
from .research_v4 import _directed_search, _folds_by_game, _load_bases, DEFAULT_PROBES
from .research_v5 import _choose_base
from .research_v6 import _cohort_v5_training, _distinct_positions

PROTOCOL = {
    'version': 'paired-temporal-comparison-v7',
    'evaluation': 'SAME peak-era heldout moves for both arms in each fold, grouped by game',
    'train': 'SAME position budget/depth/trial count; arms differ by eligible years only',
    'source': 'FROZEN V5 TRAIN ONLY; V3 and V5 validation and final test excluded',
    'promotion': 'DISABLED: exploratory CV, never historical identity or Elo validation',
}


def _mode_rows(rows: list[dict], annotations: dict, include_unknown: bool) -> list[dict]:
    checked = annotate_decisions(rows, annotations)
    return _distinct_positions([
        d for d in checked if
        (d['modality'] == 'classical' and d['modality_evidence'] in ('TIMECONTROL_TAG','DOCUMENTED_EXTERNAL')) or
        (include_unknown and d['modality'] == 'unknown')
    ])


def _evaluate_arm(root:Path, bases:dict, development:list[dict], held:list[dict],
                  *, depth:int, trials:int, probes:int, penalty:float, seed:int) -> dict:
    origin, _scores = _choose_base(bases, development, root, depth)
    baseline_text = bases[origin][0]
    params:dict = {}
    if trials:
        _, best, _, _ = _directed_search(root, baseline_text, development,
            trials=trials, depth=depth, delta=20, penalty=penalty, seed=seed,
            probe_positions=min(probes,len(development)), probe_params=DEFAULT_PROBES)
        params = dict(best['params'])
    prediction = evaluate_text(root, tune_text(baseline_text, params) if params else baseline_text, held, depth)
    if len(prediction['_predicted_moves']) != len(held):
        raise ValueError('Engine returned unaligned predictions')
    by_game={}
    for row, move in zip(held,prediction['_predicted_moves']):
        stat=by_game.setdefault(row['game'],{'total':0,'correct':0})
        stat['total']+=1; stat['correct']+=int(move==row['move'])
    return {'base':origin,'params':params,'correct':prediction['correct'],
            'positions':len(held), 'by_game':by_game}


def temporal_compare(root:Path,pgn:Path,peak_registry:Path,output:Path,*,
                     slug:str,match_name:str,v5_report:Path,legacy_v3:Path,
                     legacy_pgn:Path,allow_provisional:bool=False,
                     include_unknown:bool=False,annotations:Path|None=None,
                     cohort_ledger:Path|None=None,positions:int=60,
                     train_budget:int=90,depth:int=3,folds:int=2,repeats:int=2,
                     trials:int=0,probe_positions:int=15,seed:int=31415,
                     penalty:float=.08,base_files:tuple[str,...]=
                     ('karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt')) ->dict:
    if output.exists(): raise FileExistsError(output)
    if not (2<=folds<=8 and 1<=repeats<=10 and 20<=positions<=3000 and
            20<=train_budget<=10000 and 1<=depth<=20 and 0<=trials<=20 and
            5<=probe_positions<=300 and 0<=penalty<=1):
        raise ValueError('Invalid paired temporal comparison arguments')
    if not (root/'rodent-iv-plain.exe').is_file(): raise FileNotFoundError('Rodent not found')
    if slug!='mequinho':
        raise ValueError('v0.7 paired comparator currently requires frozen Mequinho V5 cohort')
    peak=load_peak_window(peak_registry,slug,allow_provisional=allow_provisional)
    all_rows,source=extract_decisions_v5(pgn,player_name=match_name,from_year=1600,to_year=2100)
    frozen,embargo=_cohort_v5_training(all_rows,report_path=v5_report,legacy_v3=legacy_v3,
        legacy_pgn=legacy_pgn,slug=slug,pgn=pgn,registry_path=cohort_ledger)
    modes=load_annotations(annotations)
    eligible=_mode_rows(frozen,modes,include_unknown)
    # Dedupe the peak arm independently: otherwise an old-career occurrence
    # of the same board state could silently erase an eligible peak decision.
    peak_rows=_mode_rows([d for d in frozen if peak['from_year']<=d['year']<=peak['to_year']],
                         modes,include_unknown)
    if len({d['game'] for d in peak_rows})<folds*4:
        raise ValueError('Not enough permitted independent peak games')
    held_pool=_balanced_sample(peak_rows,min(positions,len(peak_rows)),seed=seed)
    if len({d['game'] for d in held_pool})<folds*4:
        raise ValueError('Too few distinct sampled peak games')
    bases=_load_bases(root,base_files)
    results=[]
    for r in range(repeats):
        for f,(_unused,held) in enumerate(_folds_by_game(held_pool,folds,seed+1009*r)):
            prohibited_games={d['game'] for d in held}
            forbidden_positions={position_key(d) for d in held}
            # IMPORTANT: remove ALL moves from held-out games, not just sampled moves.
            career_dev=[d for d in eligible if d['game'] not in prohibited_games
                        and position_key(d) not in forbidden_positions]
            peak_dev=[d for d in peak_rows if d['game'] not in prohibited_games
                      and position_key(d) not in forbidden_positions]
            budget=min(train_budget,len(career_dev),len(peak_dev))
            if budget<20: raise ValueError('Insufficient peak training positions for paired budget')
            # Identical sample depth and budget, distinct era eligibility.
            arm_rows={
                'peak':_balanced_sample(peak_dev,budget,seed=seed+113*r+791*f+11),
                'career':_balanced_sample(career_dev,budget,seed=seed+113*r+791*f+11),
            }
            outcome={}
            for arm,dev in arm_rows.items():
                if len(dev)!=budget:raise ValueError('Cannot match position budgets')
                if ({d['game'] for d in dev}&prohibited_games or
                    {position_key(d) for d in dev}&forbidden_positions):
                    raise AssertionError('Temporal fold training leak')
                outcome[arm]=_evaluate_arm(root,bases,dev,held,depth=depth,trials=trials,
                    probes=probe_positions,penalty=penalty,seed=seed+991*r+19*f)
                outcome[arm]['train_games']=len({d['game'] for d in dev})
                outcome[arm]['train_era_counts']=dict(sorted(Counter(
                    'peak' if peak['from_year']<=d['year']<=peak['to_year'] else 'outside_peak'
                    for d in dev).items()))
            difference=outcome['peak']['correct']-outcome['career']['correct']
            game_deltas=[
                outcome['peak']['by_game'][gid]['correct']/outcome['peak']['by_game'][gid]['total']-
                outcome['career']['by_game'][gid]['correct']/outcome['career']['by_game'][gid]['total']
                for gid in sorted(outcome['peak']['by_game'])]
            results.append({'repeat':r,'fold':f,'evaluation_positions':len(held),
                'evaluation_games':len(prohibited_games),'train_budget':budget,
                'career':{k:v for k,v in outcome['career'].items() if k!='by_game'},
                'peak':{k:v for k,v in outcome['peak'].items() if k!='by_game'},
                'delta_correct_peak_minus_career':difference,
                'game_macro_delta_pp':100*mean(game_deltas),
                'heldout_game_ids_sha256':__import__('hashlib').sha256(
                    json.dumps(sorted(prohibited_games)).encode()).hexdigest()})
            print(f'Temporal repeat {r+1}/{repeats} fold {f+1}/{folds}: '
                  f'peak {outcome["peak"]["correct"]} / career {outcome["career"]["correct"]}'
                  f' on SAME {len(held)} positions',flush=True)
    per_repeat=[]
    for r in range(repeats):
        rows=[x for x in results if x['repeat']==r]
        total=sum(x['evaluation_positions'] for x in rows)
        win=sum(x['peak']['correct'] for x in rows); ctrl=sum(x['career']['correct'] for x in rows)
        per_repeat.append({'repeat':r,'positions':total,'peak_correct':win,
            'career_correct':ctrl,'delta_pp':100*(win-ctrl)/total})
    gains=[p['delta_pp'] for p in per_repeat]
    report={'protocol':PROTOCOL,'status':'PAIRED_EXPLORATORY_NO_SELECTION_OR_TEST',
        'player':slug,'peak':peak,'source':source,'embargo':embargo,
        'inputs':{'pgn_sha256':file_hash(pgn),'engine_sha256':file_hash(root/'rodent-iv-plain.exe'),
                  'v5_report_sha256':embargo['v5_report_sha256'],
                  'annotations_sha256':file_hash(annotations) if annotations else None,
                  'depth':depth,'trials':trials,'train_budget':train_budget,'positions':positions,
                  'seed':seed,'base_profiles':{k:file_hash(p) for k,(_t,p) in bases.items()}},
        'available_training':{'career_games':len({d['game'] for d in eligible}),
            'peak_games':len({d['game'] for d in peak_rows}),
            'career_positions':len(eligible),'peak_positions':len(peak_rows),
            'include_unknown_opt_in':include_unknown},
        'summary':{'per_repeat':per_repeat,'mean_delta_pp':mean(gains),
            'std_delta_pp':pstdev(gains) if repeats>1 else 0.,
            'peak_positive_repeats':sum(x>0 for x in gains),
            'warning':'Same games recur in multiple repeats. Differences are paired but not independent.'},
        'folds':results,'selection':{'status':'NOT_PERMITTED'},
        'final_test':{'status':'SEALED_NOT_EVALUATED'}}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
