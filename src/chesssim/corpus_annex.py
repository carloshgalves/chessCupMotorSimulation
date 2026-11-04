""" fail-closed, frozen STAGING PLAN for newly discovered historical PGNs.

A plan never grants access to old test groups and does not promote new PGNs to
training automatically. Old cohort is reconstructed BEFORE expansion. Distinct
metadata but repeated moves are conservatively quarantined, not merged.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .calibration import file_hash
from .corpus_collection import extract_decisions_v5
from .historical import position_key
from .research_peak import _cohort_v5_training


def _allocation(seed:int,game_id:str) ->str:
    # Independent of file read order; freeze via saved plan, not chosen by results.
    number=int(sha256(f'annex-v7:{seed}:{game_id}'.encode()).hexdigest(),16)%100
    return 'validation_sealed' if number<20 else 'test_sealed' if number<35 else 'train_proposed'


def plan_annex(pgn:Path,sources:list[Path],out:Path,*,match_name:str,
               v5_report:Path,legacy_v3:Path,legacy_pgn:Path,slug:str='mequinho',
               cohort_ledger:Path|None=None,seed:int=2029)->dict:
    if out.exists(): raise FileExistsError(out)
    if not sources: raise ValueError('Supply at least one independently obtained PGN')
    if slug!='mequinho': raise ValueError('Frozen V5 annex is currently implemented for Mequinho only')
    original,_=extract_decisions_v5(pgn,player_name=match_name,from_year=1600,to_year=2100)
    authorized,chain=_cohort_v5_training(original,report_path=v5_report,legacy_v3=legacy_v3,
        legacy_pgn=legacy_pgn,slug=slug,pgn=pgn,registry_path=cohort_ledger)
    original_ids={d['game'] for d in original}
    old_moves={d['legacy_key'] for d in original}
    # Everything outside train is forbidden, including transpositions.
    authorized_state={position_key(d) for d in authorized}
    forbidden_state={position_key(d) for d in original if position_key(d) not in authorized_state}
    # Note: a position shared with an old reserved game and old training game is
    # also rejected for new training if it appears in the historic corpus.
    new_by_game=defaultdict(list);quarantine=Counter();sources_info=[]
    for source in sources:
        if source.resolve()==pgn.resolve(): raise ValueError('Annex must be external to frozen base corpus')
        rows,stats=extract_decisions_v5(source,player_name=match_name,from_year=1600,to_year=2100)
        sources_info.append({'path':str(source),'sha256':file_hash(source),'accepted_games':stats['games_accepted']})
        for d in rows:
            new_by_game[d['game']].append(d)
    legacy_to_ids=defaultdict(set)
    for gid,rows in new_by_game.items():
        for d in rows:legacy_to_ids[d['legacy_key']].add(gid)
    accepted=[];all_novel=set()
    for gid,rows in sorted(new_by_game.items()):
        if gid in original_ids:
            quarantine['ALREADY_IN_FROZEN_CORPUS']+=1; continue
        if any(d['legacy_key'] in old_moves for d in rows):
            quarantine['SAME_MOVES_DIFFERENT_METADATA_REVIEW_REQUIRED']+=1; continue
        if any(len(legacy_to_ids[d['legacy_key']])>1 for d in rows):
            quarantine['CONFLICTING_IDENTITIES_FOR_SAME_NEW_MOVES']+=1; continue
        if any(position_key(d) in forbidden_state for d in rows):
            quarantine['FORBIDDEN_OLD_STATE_OVERLAP']+=1; continue
        # A game repeated in multiple external PGNs contributes only one set of positions.
        unique={position_key(d):d for d in rows}
        accepted.append((gid,list(unique.values())));all_novel.add(gid)
    new_sealed_states={position_key(d) for gid,rows in accepted
        if _allocation(seed,gid) in ('validation_sealed','test_sealed') for d in rows}
    by_partition=Counter();eligible_train=0;train_candidates=[]
    for gid,rows in accepted:
        assignment=_allocation(seed,gid)
        by_partition[assignment]+=1
        if assignment=='train_proposed':
            if any(position_key(d) in new_sealed_states for d in rows):
                quarantine['CROSS_NEW_SEALED_STATE_OVERLAP']+=1
                by_partition['train_quarantined']+=1
                by_partition['train_proposed']-=1
                continue
            eligible_train+=len(rows)
            train_candidates.append(gid)
    doc={'schema':'annex-staging-v7','status':'STAGED_ONLY_NO_ENGINES_AND_NO_TRAINING',
        'target':slug,'base_corpus_sha256':file_hash(pgn),'v5_report_sha256':chain['v5_report_sha256'],
        'original_v5_test_manifest_sha256':chain['v5_test_manifest_sha256'],
        'original_v5_partition_sha256':chain['v5_partition_sha256'],
        'source_files':sources_info,'allocation_seed':seed,
        'rejected_new_games':dict(quarantine),'new_games_total':len(new_by_game),
        'eligible_unique_new_games':len(accepted),'partitions':dict(by_partition),
        'train_proposed_positions':eligible_train,
        'train_proposed_game_ids':sorted(train_candidates),
        'test_and_validation_game_ids':'NOT EXPOSED; identifiers only represented by aggregate counts',
        'warnings':['This is ONLY an audit and frozen migration proposal.',
                    'Novel holdouts remain inaccessible to any model.',
                    'All proposed games require provenance/duplicate review before TRAIN integration.',
                    'Do not rebuild historic v3/v5 splits on the expanded corpus.']}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return doc
