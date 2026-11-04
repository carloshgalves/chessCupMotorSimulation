"""v0.6: peak-window and modality-separated exploratory Rodent calibration.

V6 NEVER evaluates existing v3/v5 reserved game labels. For Mequinho it
reconstructs and verifies the prior v5 cohort, then trains ONLY on its old
TRAIN groups. This is not a final-selection or tournament-strength procedure.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random

from .calibration import _remove_inherited_opening_books, file_hash, tune_text
from .corpus_v5 import extract_decisions_v5
from .historical import _balanced_sample, position_key, split_three_ways
from .peak_v6 import (MODES, annotate_decisions, load_annotations, load_peak_window,
                      modality_inventory, modality_plan)
from .research_v3 import _safe_filename
from .research_v4 import DEFAULT_PROBES, _directed_search, _folds_by_game, _load_bases
from .research_v5 import _agg_cv, _choose_base, _legacy_embargo, _sticky_registry
from .research_v3 import evaluate_text

PROTOCOL_V6 = {
    "version":"rodent-style-calibration-v6",
    "historical":"predeclared provisional/approved peak windows, never chosen by model agreement",
    "modality":"verified TimeControl or annotated evidence; event-label-only and unknown separately audited",
    "training":"one modality only; opt-in unknown records may supplement classical baseline as uncertain",
    "legacy_isolation":"Mecking v3 and v5 ALL validation+test games excluded, verified by original hashes",
    "validation":"repeated grouped CV inside previously permitted training groups",
    "test":"no reserved v3/v5/novel test positions sent to any engine",
    "promotion":"disabled; no verified identity/strength claim",
}


def _cohort_v5_training(rows: list[dict], *, report_path: Path,
                        legacy_v3: Path, legacy_pgn: Path, slug: str, pgn: Path,
                        registry_path: Path | None = None) -> tuple[list[dict], dict]:
    """Recover all full v5 TRAIN-group positions, not merely its sampled 240.

    Rejects changed source/hash/old embargo/partition. Existing V5 validation
    and test groups remain completely inaccessible to the optimizer.
    """
    path = report_path / 'calibration.json' if report_path.is_dir() else report_path
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('protocol',{}).get('version') != 'rodent-style-calibration-v5':
        raise ValueError('Expected original v5 calibration report')
    if (report.get('selection',{}).get('requested') or
        report.get('final_test',{}).get('status')!='SEALED_NOT_EVALUATED'):
        raise ValueError('V5 selection/test already opened or status uncertain; refuse exploratory reuse')
    if report['inputs']['pgn_sha256'] != file_hash(pgn):
        raise ValueError('V5 source PGN hash mismatch')
    original = report['cohort_registry']
    if original['target_slug'] != slug or original['pgn_sha256'] != file_hash(pgn):
        raise ValueError('V5 cohort registry mismatch')
    registry = registry_path or Path(report['cohort_registry_path'])
    if not registry.is_file():
        raise FileNotFoundError(f'Frozen v5 registry is missing: {registry}')
    if json.loads(registry.read_text(encoding='utf-8')) != original:
        raise ValueError('Original V5 registry file disagrees with calibration')
    embargo, legacy = _legacy_embargo(legacy_v3,legacy_pgn,target_slug=slug)
    if original.get('legacy_reserved_sha256') != legacy.get('reserved_game_ids_sha256'):
        raise ValueError('Legacy v3 embargo does not match frozen v5 registry')
    remaining = [d for d in rows if d.get('legacy_key',d['game']) not in embargo]
    excluded = len({d['game'] for d in rows if d.get('legacy_key',d['game']) in embargo})
    if excluded != report['legacy_embargo']['excluded_games_present_in_new_corpus']:
        raise ValueError('Legacy v3 exclusion count changed')
    p = report['partition']; inp=report['inputs']
    _,_,_, verified = split_three_ways(remaining, seed=p['seed'],
        train_limit=inp['train_positions'], val_limit=inp['validation_positions'],
        test_limit=p['test_sealed']['positions'], group_by=p['group_by'])
    if (verified['partition_sha256']!=original['partition_sha256'] or
        verified['test_sealed']['manifest_sha256']!=original['test_manifest_sha256'] or
        verified['test_sealed']['manifest_sha256']!=report['final_test']['manifest_sha256']):
        raise ValueError('V5 partition/test seal cannot be reconstructed; no training allowed')
    groups=defaultdict(list)
    for d in remaining:
        groups[str(d['game'])].append(d)
    gids=sorted(groups)
    random.Random(p['seed']).shuffle(gids)
    nt=max(3,round(len(gids)*p['test_fraction']))
    nv=max(3,round(len(gids)*p['validation_fraction']))
    reserved = [d for gid in gids[:nt+nv] for d in groups[gid]]
    train = [d for gid in gids[nt+nv:] for d in groups[gid]]
    # Cross-group transpositions cannot be counted as new positions.
    reserved_keys = {position_key(d) for d in reserved}
    train = [d for d in train if position_key(d) not in reserved_keys]
    assert not ({d['game'] for d in train} & {d['game'] for d in reserved})
    assert not ({position_key(d) for d in train} & reserved_keys)
    return train, {"origin":"V5_FROZEN_TRAIN_GROUPS_ONLY",
       "v5_report_sha256":file_hash(path),"v5_partition_sha256":verified['partition_sha256'],
       "v5_test_manifest_sha256":verified['test_sealed']['manifest_sha256'],
       "legacy_v3_reserved_games":len(embargo),"v5_reserved_groups":nt+nv,
       "v5_reserved_positions_not_evaluated":True,
       "training_games_before_peak":len({d['game'] for d in train})}


def _fresh_training(rows: list[dict], *, seed: int, cohort_ledger: Path,
                    slug: str, pgn: Path, peak: dict) -> tuple[list[dict], dict]:
    """For never-calibrated players only. Freeze train/validation/test before use."""
    if len({d['game'] for d in rows}) < 15:
        raise ValueError('Too few independent peak games to create a new sealed cohort')
    train, _validation, _test, manifest = split_three_ways(rows, seed=seed,
        train_limit=30, val_limit=10, test_limit=10, group_by='game')
    reg = {"version":6,"target_slug":slug,"pgn_sha256":file_hash(pgn),
           "peak_from":peak['from_year'],"peak_to":peak['to_year'],
           "peak_window_sha256":peak['window_sha256'],"partition_seed":seed,
           "partition_sha256":manifest['partition_sha256'],
           "test_manifest_sha256":manifest['test_sealed']['manifest_sha256']}
    _sticky_registry(cohort_ledger,reg)
    # Reconstruct entire train groups, never touching reserved positions.
    groups=defaultdict(list)
    for d in rows:
        groups[str(d['game'])].append(d)
    gids=sorted(groups)
    random.Random(seed).shuffle(gids)
    nt=max(3,round(len(gids)*.15)); nv=max(3,round(len(gids)*.20))
    reserved=[d for g in gids[:nt+nv] for d in groups[g]]
    forbidden={position_key(d) for d in reserved}
    full_train=[d for g in gids[nt+nv:] for d in groups[g] if position_key(d) not in forbidden]
    return full_train, {"origin":"NEW_V6_SEALED_COHORT", "registry":reg,
                        "cohort_ledger":str(cohort_ledger),
                        "reserved_groups":nt+nv,"reserved_positions_not_evaluated":True}


def _distinct_positions(rows: list[dict]) -> list[dict]:
    # Duplicate transpositions across games should not dominate sample or folds.
    seen=set(); clean=[]
    for d in sorted(rows,key=lambda r:(r['game'],r['fullmove'],r['fen'])):
        key=position_key(d)
        if key not in seen:
            seen.add(key);clean.append(d)
    return clean


def prepare_peak_training(pgn:Path, registry:Path, *, slug:str, match_name:str,
                          allow_provisional:bool, annotations:Path|None,
                          mode:str, include_unknown:bool=False,
                          v5_report:Path|None=None, legacy_v3:Path|None=None,
                          legacy_pgn:Path|None=None, cohort_ledger:Path|None=None,
                          fresh_cohort_ack:bool=False, partition_seed:int=2028):
    """All transformations are evidence-only until the engine-ready training list.

    Peak filtering happens AFTER frozen full-career embargo for Mequinho.
    """
    if mode not in MODES:
        raise ValueError(f'Unknown modality: {mode}')
    if include_unknown and mode!='classical':
        raise ValueError('--include-unknown is permitted only for uncertain classical baseline')
    peak=load_peak_window(registry,slug,allow_provisional=allow_provisional)
    # Never filter years before v5 full-career partition reconstruction.
    from_year,to_year=(1600,2100) if v5_report else (peak['from_year'],peak['to_year'])
    rows, source = extract_decisions_v5(pgn,player_name=match_name,from_year=from_year,to_year=to_year)
    if v5_report is not None:
        if legacy_v3 is None or legacy_pgn is None:
            raise ValueError('V5 reuse requires --legacy-v3 and --legacy-pgn')
        training, chain = _cohort_v5_training(rows,report_path=v5_report,
              legacy_v3=legacy_v3,legacy_pgn=legacy_pgn,slug=slug,pgn=pgn,
              registry_path=cohort_ledger)
        training=[d for d in training if peak['from_year'] <= d['year'] <= peak['to_year']]
    else:
        if slug == 'mequinho':
            raise ValueError('Mequinho MUST reuse --v5-report and original legacy v3 seal')
        if not fresh_cohort_ack:
            raise ValueError('New player requires --fresh-cohort-ack (confirm no earlier reserved cohort)')
        ledger = cohort_ledger or Path('calibracoes/registry')/(slug+'-cohort-v06.json')
        training,chain=_fresh_training(rows,seed=partition_seed,cohort_ledger=ledger,
                                       slug=slug,pgn=pgn,peak=peak)
    annotations_map=load_annotations(annotations)
    annotated=annotate_decisions(training,annotations_map)
    inventory=modality_inventory(annotated)
    plan=modality_plan(annotated)
    selected=[d for d in annotated if d['modality']==mode and
        d['modality_evidence'] in ('TIMECONTROL_TAG','DOCUMENTED_EXTERNAL')]
    label_only=sum(d['modality']==mode and d['modality_evidence']=='EVENT_LABEL_ONLY' for d in annotated)
    # Event hints excluded in all cases; UNKNOWN optionally allowed for classical exploratory base.
    if include_unknown:
        selected.extend(d for d in annotated if d['modality']=='unknown')
    selected=_distinct_positions(selected)
    policy={"selected_mode":mode,"unknown_opt_in":include_unknown,
            "verified_games":len({d['game'] for d in selected if d['modality']!='unknown'}),
            "unverified_unknown_games":len({d['game'] for d in selected if d['modality']=='unknown'}),
            "event_hint_positions_excluded":label_only,
            "training_evidence_tier":"B_UNVERIFIED_CLASSICAL_ASSUMPTION" if include_unknown else
                                     plan['modes'][mode]['tier'],
            "no_force_or_identity_claim":True}
    return selected, {"peak":peak,"source":source,"embargo":chain,
                      "annotations_sha256":file_hash(annotations) if annotations else None,
                      "training_inventory":inventory,"mode_plan":plan,"training_policy":policy}


def peak_audit(pgn:Path, registry:Path, *, slug:str,match_name:str,
               allow_provisional:bool=False,annotations:Path|None=None) -> dict:
    peak=load_peak_window(registry,slug,allow_provisional=allow_provisional)
    # Audit uses metadata throughout the peak, not hidden-position engine predictions.
    rows,stats=extract_decisions_v5(pgn,player_name=match_name,
          from_year=peak['from_year'],to_year=peak['to_year'])
    enriched=annotate_decisions(rows,load_annotations(annotations))
    return {"schema":"peak-audit-v6","target":slug,"peak":peak,"source":stats,
            "inventory":modality_inventory(enriched),"modality_plan":modality_plan(enriched),
            "warning":"Audit covers supplied PGNs, not all historic games. Unknown is NOT classical."}


def calibrate_v6(root:Path,pgn:Path,output:Path,registry:Path,*,slug:str,
                 match_name:str,mode:str='classical',allow_provisional:bool=False,
                 include_unknown:bool=False,annotations:Path|None=None,
                 v5_report:Path|None=None,legacy_v3:Path|None=None,legacy_pgn:Path|None=None,
                 cohort_ledger:Path|None=None,fresh_cohort_ack:bool=False,
                 partition_seed:int=2028,search_seed:int=31415,depth:int=3,
                 positions:int=90,folds:int=3,repeats:int=2,cv_trials:int=1,
                 trials:int=2,probe_positions:int=20,delta:int=20,
                 base_files:tuple[str,...]=('karpov.txt','botvinnik.txt','petrosian.txt','fischer.txt'),
                 probe_params:tuple[str,...]=DEFAULT_PROBES,divergence_penalty:float=.08) -> dict:
    root,pgn,output=root.resolve(),pgn.resolve(),output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if not (1<=depth<=20 and 2<=folds<=8 and 1<=repeats<=10 and
            1<=cv_trials<=20 and 1<=trials<=100 and positions>=folds*8 and
            probe_positions>=5 and 0<=divergence_penalty<=1):
        raise ValueError('Invalid v6 calibration arguments')
    if not (root/'rodent-iv-plain.exe').is_file():
        raise FileNotFoundError('Rodent engine not found')
    bases=_load_bases(root,base_files)
    available,info=prepare_peak_training(pgn,registry,slug=slug,match_name=match_name,
        allow_provisional=allow_provisional,annotations=annotations,mode=mode,
        include_unknown=include_unknown,v5_report=v5_report,legacy_v3=legacy_v3,
        legacy_pgn=legacy_pgn,cohort_ledger=cohort_ledger,
        fresh_cohort_ack=fresh_cohort_ack,partition_seed=partition_seed)
    if len({d['game'] for d in available}) < max(12,folds*3):
        raise ValueError('Not enough peak games with acceptable modality evidence. '
                         'Use peak-audit; UNKNOWN may be opted into classical with --include-unknown.')
    sample=_balanced_sample(available,min(positions,len(available)),seed=search_seed)
    if len({d['game'] for d in sample}) < folds*3:
        raise ValueError('Too few distinct sampled games for folds; increase --positions')
    records=[]
    for r in range(repeats):
        for i,(dev,held) in enumerate(_folds_by_game(sample,folds,search_seed+1009*r)):
            # Cross-game transpositions inside a fold must not be trained on.
            held_keys={position_key(d) for d in held}
            dev=[d for d in dev if position_key(d) not in held_keys]
            if not dev: raise ValueError('Empty CV training fold after transposition exclusion')
            base,_=_choose_base(bases,dev,root,depth)
            _,candidate,_,_=_directed_search(root,bases[base][0],dev,
                trials=cv_trials,depth=depth,delta=delta,penalty=divergence_penalty,
                seed=search_seed+999*r+37*i,probe_positions=probe_positions,
                probe_params=probe_params)
            before=evaluate_text(root,bases[base][0],held,depth)
            after=(evaluate_text(root,tune_text(bases[base][0],candidate['params']),held,depth)
                   if candidate['params'] else before)
            records.append({"repeat":r,"fold":i,"base":base,"positions":len(held),
                "development_games":len({d['game'] for d in dev}),
                "heldout_games":len({d['game'] for d in held}),
                "before_correct":before['correct'],"after_correct":after['correct'],
                "params":candidate['params'],
                "moves_changed_vs_local_base":sum(a!=b for a,b in zip(before['_predicted_moves'],after['_predicted_moves']))})
            print(f'Peak CV repeat {r+1}/{repeats} fold {i+1}/{folds}: '
                  f'{base} {before["correct"]} -> {after["correct"]}/{len(held)}',flush=True)
    cv=_agg_cv(records)
    origin,base_scores=_choose_base(bases,sample,root,depth)
    candidates,best,probes,_=_directed_search(root,bases[origin][0],sample,
        trials=trials,depth=depth,delta=delta,penalty=divergence_penalty,
        seed=search_seed+100000,probe_positions=probe_positions,
        probe_params=probe_params)
    # CV results do not authorize validation/test or production profile promotion.
    output.mkdir(parents=True,exist_ok=False)
    experimental=output/(slug+'-'+mode+'_BEST_TRAIN_NOT_VALIDATED.txt')
    experimental.write_text('; V6 TRAIN EXPERIMENT ONLY; not approved; no inherited books\n'+
        _remove_inherited_opening_books(tune_text(bases[origin][0],best['params'])),encoding='utf-8')
    reference=output/(slug+'-'+mode+'_BASELINE_NOT_PERSONALIZED.txt')
    reference.write_text('; OFFICIAL REFERENCE COPY; not personalized; research use\n'+
        _remove_inherited_opening_books(bases[origin][0]),encoding='utf-8')
    report={"protocol":PROTOCOL_V6,"status":"EXPLORATORY_CV_ONLY_NOT_HOMOLOGATED",
            "player":slug,"modality":mode, **info,
            "sampling":{"positions":len(sample),"games":len({d['game'] for d in sample}),
                        "eligible_positions":len(available),"depth":depth,
                        "folds":folds,"repeats":repeats,"trials":trials,"cv_trials":cv_trials,
                        "search_seed":search_seed},
            "inputs":{"pgn_sha256":file_hash(pgn),"engine_sha256":file_hash(root/'rodent-iv-plain.exe'),
                      "base_profile_sha256":{key:file_hash(path) for key,(_,path) in bases.items()}},
            "cv_summary":cv,"cv_folds":records,
            "train_baselines":[{"profile":name,"correct":scores['metric']['correct'],
                                "macro":scores['macro']} for name,scores in base_scores.items()],
            "origin":origin,"sensitivity":probes,
            "train_candidates":[{"trial":x['trial'],"params":x['params'],
                                 "correct":x['raw']['correct'],"objective":x['objective']} for x in candidates],
            "experimental_profile":str(experimental),"experimental_sha256":file_hash(experimental),
            "reference_profile":str(reference),"reference_sha256":file_hash(reference),
            "selection":{"status":"NOT_PERMITTED_V6"},
            "final_test":{"status":"SEALED_NOT_EVALUATED"},
            "warnings":["CV folds share the original train corpus; not independent external evidence",
                        "Unknown mode opted in only with explicit flag; not verified classical",
                        "Mode profile and Elo not historically authenticated"]}
    (output/'calibration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def write_annotation_template(pgn:Path,registry:Path,output:Path,*,slug:str,
                              match_name:str,allow_provisional:bool=False):
    from .peak_v6 import annotation_template
    peak=load_peak_window(registry,slug,allow_provisional=allow_provisional)
    if output.exists(): raise FileExistsError(output)
    rows,_=extract_decisions_v5(pgn,player_name=match_name,
        from_year=peak['from_year'],to_year=peak['to_year'])
    result=annotation_template(rows)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
        f.write('\n')
    return {'out':str(output),'games_pending':len(result['games']),
            'source_pgn_sha256':file_hash(pgn)}
