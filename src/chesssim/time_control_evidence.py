""" verifiable tournament-time-control evidence matched by full game moves.

No annotation can be inferred from an event title, a date, or general period.
Requires a separately obtained tournament-specific PGN AND a documentary
source explicitly stating its clock.  Existing user files never overwritten.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from .calibration import file_hash
from .corpus_collection import extract_decisions_v5
from .peak_windows import annotation_template, load_annotations, classify_mode

SOURCES = {
 'nice-olympiad-1974': {
   'name':'21st Chess Olympiad — Nice, 1974', 'year':1974,
   'document_url':'https://www.olimpbase.org/~dendo/1974/1974in.html',
   'pgn_page_url':'https://www.olimpbase.org/~dendo/1974/1974in.html',
   'mode':'classical',
   'time_control_text':'40 moves in 2 hours 30 minutes, then 1 hour for each next 16 moves',
   'tournament_identifiers':('nice','olympiad','olymp','olm'),
   'status':'DOCUMENTED_TOURNAMENT_CLOCK_REQUIRES_EXACT_GAME_MATCH',
 },
 'petropolis-interzonal-1973': {
   'name':'Petrópolis Interzonal 1973', 'year':1973,
   'document_url':'https://www.olimpbase.org/ind-wcc/wc1975-izp.html',
   'pgn_page_url':'https://www.olimpbase.org/ind-wcc/wc1975-izp.html',
   'mode':None,'time_control_text':None,'status':'TIME_CONTROL_NOT_DOCUMENTED_IN_SOURCE',
 },
 'manila-interzonal-1976': {
   'name':'Manila Interzonal 1976', 'year':1976,
   'document_url':'https://www.olimpbase.org/ind-wcc/wc1978-izm.html',
   'pgn_page_url':'https://www.olimpbase.org/ind-wcc/wc1978-izm.html',
   'mode':None,'time_control_text':None,'status':'TIME_CONTROL_NOT_DOCUMENTED_IN_SOURCE',
 },
}


def _fingerprints(path: Path, match_name: str) -> tuple[dict, int]:
    import chess.pgn
    from .historical import player_side
    found=defaultdict(list);count=0
    with path.open(encoding='utf-8-sig',errors='replace') as handle:
        while (game:=chess.pgn.read_game(handle)) is not None:
            if game.errors or player_side(game,match_name) is None:
                continue
            moves=' '.join(m.uci() for m in game.mainline_moves())
            if len(moves.split())<16:continue
            year=re.match(r'^\d{4}',game.headers.get('Date',''))
            if not year:continue
            key=(sha256(moves.encode()).hexdigest(),int(year.group(0)))
            found[key].append({'year':key[1],'event':game.headers.get('Event','?'),
                'site':game.headers.get('Site','?')})
            count+=1
    return found,count


def match_tournament_evidence(pgn:Path,source_pgn:Path,output:Path,*,
                              source_id:str,match_name:str,annotations:Path|None=None)->dict:
    if output.exists(): raise FileExistsError(output)
    rule=SOURCES.get(source_id)
    if rule is None: raise ValueError('Unknown historically curated evidence source')
    if rule['mode'] is None:
        raise ValueError('This tournament has NO documented time control in our reference. Refusing to label games.')
    # The source must be a tournament-specific PGN obtained independently.
    comparison,_count=_fingerprints(source_pgn,match_name)
    if not comparison: raise ValueError('Tournament PGN has no eligible target games')
    rows,_stats=extract_decisions_v5(pgn,player_name=match_name,from_year=1600,to_year=2100)
    tpl=annotation_template(rows)
    original=json.loads(annotations.read_text(encoding='utf-8')) if annotations else tpl
    if original.get('schema')!='modalities-annotations-v6': raise ValueError('Not a v6 annotation template')
    if set(original.get('games',{}))!=set(tpl['games']):
        raise ValueError('Annotations do not correspond to this exact historical corpus')
    # First validate all prior entries to fail closed on malformed evidence.
    load_annotations(annotations) if annotations else None
    updated=json.loads(json.dumps(original));confirmed=[];quarantined=[]
    already=set()
    for row in rows:
        gid=row['game']
        if gid in already:continue
        already.add(gid)
        if row['year']!=rule['year']:continue
        # Original record also must identify the same tournament; identical
        # mainline in another event does not imply the same time control.
        if not any(term in row.get('event','').casefold() for term in rule['tournament_identifiers']):
            continue
        key=(row['legacy_key'],row['year'])
        matching=comparison.get(key,[])
        if not matching:continue
        # Tournament PGN Event/Site must explicitly identify this competition.
        verified=[g for g in matching if any(term in (g['event']+' '+g['site']).casefold()
                 for term in rule['tournament_identifiers'])]
        if len(verified)!=1:
            quarantined.append({'game_id':gid,'reason':'ambiguous or unverifiable tournament origin'})
            continue
        old=updated['games'][gid]
        if old.get('mode') not in (None,rule['mode']):
            raise ValueError('Existing historical annotation conflicts with documentary source')
        pg_mode=classify_mode(row.get('time_control','?'),row.get('event',''))
        if pg_mode['evidence']=='TIMECONTROL_TAG' and pg_mode['mode']!=rule['mode']:
            raise ValueError('PGN explicit TimeControl conflicts with tournament documentation')
        reference=f"{rule['document_url']} | {rule['time_control_text']} | tournament PGN SHA256={file_hash(source_pgn)}"
        old.update({'mode':rule['mode'],'evidence':'DOCUMENTED_EXTERNAL','reference':reference})
        confirmed.append(gid)
    updated['v7_evidence']={'source_id':source_id,'document_url':rule['document_url'],
        'source_pgn_sha256':file_hash(source_pgn),'original_corpus_sha256':file_hash(pgn),
        'matched_games':len(confirmed),'ambiguous_games':len(quarantined),
        'note':'Matching exact full mainline and date/year. PGN source authenticity must be checked by operator; no inference for other events.'}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {'status':'DOCUMENTED_MATCHES_ONLY','out':str(output),'matched_games':len(confirmed),
        'unmatched_or_unverified_games':len(tpl['games'])-len(confirmed),
        'quarantined':quarantined,'source_pgn_sha256':file_hash(source_pgn),
        'manual_verification_required':True}
