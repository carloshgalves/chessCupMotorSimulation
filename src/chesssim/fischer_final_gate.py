"""Predeclared FINAL Fischer game-asset gate (not a human-equivalence Elo test).

Reuse the already completed 3,616-game benchmark; do NOT retrain Fischer.
New checks: paired 10+2 actual UCI games and fixed historical positions,
with legal replay, fixed population and immutable source/engine hashes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import random
from statistics import mean
import threading
from time import monotonic

import chess
import chess.engine

from .catalog import Player, find_player
from .fischer_baseline import phase
from .fischer_competency_audit import _source
from .fischer_deep_calibration import choose
from .fischer_full_games import atomic_json, digest, initial_board, load_json
from .matches import _open_engine, file_hash
from .real85_neural_stream import neural_arm
from .real_training_cohorts import _iter_verified_games
from .registry import registered_aliases

SCHEMA = "chesssim-fischer-final-product-gate-v1"
SOURCE_RUN = 44970751726
SOURCE_GAME_RUN = 44415124944
SOURCE_RESULT = "ALL_3616_GAMES_TERMINAL_AFTER_EVIDENCE_PRESERVING_RECOVERY"
CLOCK_SECONDS = 600
CLOCK_INCREMENT = 2
FOCAL_DEPTH = 16
NODE_CAP = 5_000_000
START_PLIES = 12
MAX_GAME_PLIES = 600
ROOTS = 12
SHARDS = 8
REPEAT_MODULUS = 7
STYLE_FEATURES = ("forcing","check","pawn_move","kingward")
RODENT_HASHES = {
    "rodent": "c2ccc7a99b442dcaa2aa2dc06e020f0182fe7a198c6f080f45f555c126cdcd95",
    "fischer": "ee2ab4e175f0dbee88f2f12595393d14001915670e086198139d15ad86eae577",
    "simple": "19f83fb05f96039a34e0e9a36bf66fc841693f70d73ee2a243df42e90caf1613",
}
STYLE_SALT = "fischer-final-style-v1"
ROOT_SALT = "fischer-final-roots-v1"


def source_cases(archive_dir=None):
    """All TEST games scanned; exact 12-ply root collisions are deduplicated."""
    pgn,rows,_,origin,surname=_source(archive_dir)
    heldout=[r for r in rows if neural_arm(r["mainline_sha256"])=="excluded_test"]
    if len(heldout)!=121:
        raise ValueError("The preregistered 121 source games have changed")
    roots={}
    style={}
    for game,row in _iter_verified_games(pgn,heldout,surname):
        gid=row["mainline_sha256"]
        board=game.board()
        if board.fen()!=chess.STARTING_FEN:
            raise ValueError("Nonstandard historical root requires separate protocol")
        choices={}
        prefix=[]
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise ValueError("Illegal historical trajectory")
            if board.turn==(row["color"]=="white") and board.legal_moves.count()>1:
                stage=phase(board)
                salt=sha256(f"{STYLE_SALT}:{gid}:{stage}:{board.ply()}".encode()).hexdigest()
                if stage not in choices or salt<choices[stage][0]:
                    choices[stage]=(salt,board.copy(stack=True),move)
            if board.ply()<START_PLIES:
                prefix.append(move.uci())
            board.push(move)
        for stage,(_,historic,move) in choices.items():
            ident=digest([gid,stage,historic.ply(),historic.fen()])
            style[ident]={"id":ident,"game":gid,"phase":stage,
                          "fen_sha256":sha256(historic.fen().encode()).hexdigest(),
                          "ply":historic.ply(),"human":move.uci()}
        if len(prefix)>=START_PLIES:
            board=chess.Board()
            for code in prefix[:START_PLIES]:
                m=chess.Move.from_uci(code)
                if m not in board.legal_moves:
                    raise ValueError("Illegal historical root prefix")
                board.push(m)
            if not board.is_game_over(claim_draw=True):
                key=tuple(prefix[:START_PLIES])
                rank=sha256(f"{ROOT_SALT}:{gid}".encode()).hexdigest()
                candidate={"id":digest(list(key)),"prefix":list(key),
                           "source_game":gid,"rank":rank}
                if key not in roots or rank<roots[key]["rank"]:
                    roots[key]=candidate
    selected=sorted(roots.values(),key=lambda x:(x["rank"],x["id"]))[:ROOTS]
    if len(selected)!=ROOTS or len(style)<100:
        raise ValueError("Insufficient distinct historical TEST positions")
    cases=[]
    for root in selected:
        for color in ("white","black"):
            cases.append({"id":digest([root["id"],color,CLOCK_SECONDS,CLOCK_INCREMENT]),
                          "root":root["id"],"color":color,
                          "opponent":"simple_d16","clock":[CLOCK_SECONDS,CLOCK_INCREMENT]})
    # Every TEST game is considered for style; one deterministic legal
    # position per observed game and phase. This is not a blind holdout.
    return {"schema":SCHEMA,"pgn_sha256":sha256(pgn).hexdigest(),
            "source":origin,"roots":selected,"style":style,"cases":cases,
            "test_games":len(heldout),"style_positions":len(style),
            "limitations":["TEST previously inspected, not blind",
                           "Historical 12-ply starts, not standard-start Elo"]}


def shard(value: str) -> int:
    return int(sha256(value.encode()).hexdigest()[:12],16)%SHARDS


def plan(archive_dir=None):
    source=source_cases(archive_dir)
    games=Counter(shard(c["id"]) for c in source["cases"])
    positions=Counter(shard(x["game"]) for x in source["style"].values())
    if not games or not positions:
        raise ValueError("Empty game or style allocation")
    return {"schema":SCHEMA,"source":source["source"],"pgn_sha256":source["pgn_sha256"],
            "historical_test_games":source["test_games"],
            "roots":len(source["roots"]),"clock_games":len(source["cases"]),
            "style_probes":source["style_positions"],"shards":SHARDS,
            "game_shards":dict(games),"style_shards":dict(positions),
            "time_control":"10+2","focal_depth":FOCAL_DEPTH,
            "stockfish_required":False,"historical_test_is_blind":False,
            "source_game_benchmark_run":SOURCE_RUN,
            "original_profile_modified":False}


def _chess_distance(a,b):
    return max(abs(chess.square_file(a)-chess.square_file(b)),
               abs(chess.square_rank(a)-chess.square_rank(b)))


def features(board,move):
    if move not in board.legal_moves:
        raise ValueError("Illegal move for style measurement")
    piece=board.piece_at(move.from_square)
    king=board.king(not board.turn)
    return {"forcing":board.gives_check(move) or board.is_capture(move),
            "check":board.gives_check(move),
            "pawn_move":piece.piece_type==chess.PAWN,
            "kingward":(piece.piece_type!=chess.PAWN and king is not None and
                        _chess_distance(move.to_square,king)<_chess_distance(move.from_square,king))}


def _clock_choose(engine,board,clocks):
    side=0 if board.turn else 1
    limit=chess.engine.Limit(depth=FOCAL_DEPTH,nodes=NODE_CAP,
                              white_clock=max(.001,clocks[0]),
                              black_clock=max(.001,clocks[1]),
                              white_inc=CLOCK_INCREMENT,black_inc=CLOCK_INCREMENT)
    # python-chess may ignore SimpleEngine.timeout for clock/depth
    # searches. An independent watchdog stops a hung UCI engine.
    stop=threading.Event()
    expired=threading.Event()
    seconds=min(90.0,max(3.0,clocks[side]+2.0))
    def watchdog():
        if not stop.wait(seconds):
            expired.set()
            engine.close()
    worker=threading.Thread(target=watchdog,daemon=True)
    started=monotonic()
    worker.start()
    try:
        response=engine.play(board,limit,game=object(),info=chess.engine.INFO_ALL)
    finally:
        stop.set()
    elapsed=monotonic()-started
    if expired.is_set():
        raise TimeoutError("UCI clock watchdog expired; do not score as a chess loss")
    return response.move,elapsed,response.info


def play_clock(case,root,fischer,simple):
    """Both colors, identical 12-ply root; actual 10+2 time accounting."""
    board=initial_board(root)
    white=case["color"]=="white"
    clocks=[float(CLOCK_SECONDS),float(CLOCK_SECONDS)]
    moves=[]
    move_elapsed=[]
    flag_spent=None
    reached=Counter()
    missed=Counter()
    flag=None
    while not board.is_game_over(claim_draw=True) and board.ply()<MAX_GAME_PLIES:
        is_fischer=board.turn==white
        engine=fischer if is_fischer else simple
        label="fischer" if is_fischer else "simple"
        idx=0 if board.turn else 1
        move,spent,info=_clock_choose(engine,board,clocks)
        clocks[idx]-=spent
        if clocks[idx]<=0:
            # The move is never pushed after time has expired.
            flag_spent=spent
            flag=label
            break
        if move is None or move not in board.legal_moves:
            raise ValueError("UCI returned no legal move")
        clocks[idx]+=CLOCK_INCREMENT
        reached[label]+=int(type(info.get("depth")) is int and info["depth"]>=FOCAL_DEPTH)
        missed[label]+=int(type(info.get("depth")) is not int)
        moves.append(move.uci())
        move_elapsed.append(spent)
        board.push(move)
    outcome=board.outcome(claim_draw=True)
    if flag:
        loser_is_white=(flag=="fischer")==white
        result=("0-1" if loser_is_white else "1-0")
        if board.has_insufficient_material(not loser_is_white):
            result="1/2-1/2"
        term="TIME_FORFEIT"
    elif outcome:
        result=outcome.result()
        term=outcome.termination.name
    else:
        result="*"
        term="PLY_CAP_INCOMPLETE"
    return {"schema":SCHEMA,"case":case,"moves":moves,"move_elapsed":move_elapsed,
            "flag_spent":flag_spent,"result":result,
            "termination":term,"status":"COMPLETE" if term!="PLY_CAP_INCOMPLETE" else "INCOMPLETE",
            "time_forfeit":flag,"depth_reached":dict(reached),"depth_missing":dict(missed),
            "clock_remaining":clocks}


def validate_clock(record,case,root):
    if record.get("schema")!=SCHEMA or record.get("case")!=case:
        raise ValueError("Wrong game case identity")
    board=initial_board(root)
    moves=record.get("moves")
    if not isinstance(moves,list) or len(moves)>MAX_GAME_PLIES-START_PLIES:
        raise ValueError("Invalid move list length")
    turns=Counter()
    observed_clocks=[float(CLOCK_SECONDS),float(CLOCK_SECONDS)]
    spent=record.get("move_elapsed")
    if (not isinstance(spent,list) or len(spent)!=len(moves) or
            any(type(x) not in (float,int) or not 0<=x<=90 for x in spent)):
        raise ValueError("Invalid clock history")
    for encoded,elapsed in zip(moves,spent):
        if board.is_game_over(claim_draw=True):
            raise ValueError("Moves after terminal")
        move=chess.Move.from_uci(encoded)
        if move not in board.legal_moves:
            raise ValueError("Illegal chess move")
        turns["fischer" if board.turn==(case["color"]=="white") else "simple"]+=1
        side=0 if board.turn else 1
        observed_clocks[side]-=elapsed
        if observed_clocks[side]<=0:
            raise ValueError("Time-expired move was illegally accepted")
        observed_clocks[side]+=CLOCK_INCREMENT
        board.push(move)
    outcome=board.outcome(claim_draw=True)
    flag=record.get("time_forfeit")
    if flag not in ("fischer","simple",None):
        raise ValueError("Unknown time forfeit")
    if flag:
        if outcome:
            raise ValueError("Invalid time forfeit position")
        flag_elapsed=record.get("flag_spent")
        if type(flag_elapsed) not in (int,float) or not 0<=flag_elapsed<=90:
            raise ValueError("Missing flag move clock evidence")
        loser_white=(flag=="fischer")== (case["color"]=="white")
        if loser_white!=board.turn:
            raise ValueError("Time forfeit assigned to the side not to move")
        side=0 if loser_white else 1
        observed_clocks[side]-=flag_elapsed
        if observed_clocks[side]>0:
            raise ValueError("Claimed time loss without clock expiration")
        wanted=("0-1" if loser_white else "1-0")
        if board.has_insufficient_material(not loser_white):
            wanted="1/2-1/2"
        expected=(wanted,"TIME_FORFEIT","COMPLETE")
    elif outcome:
        if record.get("flag_spent") is not None:
            raise ValueError("Unexpected flag clock data")
        expected=(outcome.result(),outcome.termination.name,"COMPLETE")
    else:
        if record.get("flag_spent") is not None:
            raise ValueError("Unexpected flag clock data")
        expected=("*","PLY_CAP_INCOMPLETE","INCOMPLETE")
        if board.ply()!=MAX_GAME_PLIES:
            raise ValueError("Unexplained partial game")
    if (record.get("result"),record.get("termination"),record.get("status"))!=expected:
        raise ValueError("Invalid recorded terminal result")
    for key in ("depth_reached","depth_missing"):
        t=record.get(key)
        if not isinstance(t,dict) or any(k not in turns or type(v) is not int or
             v<0 or v>turns[k] for k,v in t.items()):
            raise ValueError("Invalid UCI telemetry")
    clocks=record.get("clock_remaining")
    if (not isinstance(clocks,list) or len(clocks)!=2 or any(
            type(x) not in (float,int) or not abs(x-y)<.001
            for x,y in zip(clocks,observed_clocks))):
        raise ValueError("Unverifiable recorded time usage")
    return record


def _style_positions(pgn,rows,surname):
    for game,row in _iter_verified_games(pgn,rows,surname):
        gid=row["mainline_sha256"]
        board=game.board()
        choice={}
        for move in game.mainline_moves():
            if board.turn==(row["color"]=="white") and board.legal_moves.count()>1:
                stage=phase(board)
                rank=sha256(f"{STYLE_SALT}:{gid}:{stage}:{board.ply()}".encode()).hexdigest()
                if stage not in choice or rank<choice[stage][0]:
                    choice[stage]=(rank,board.copy(stack=True),move)
            board.push(move)
        for stage,(_,board,human) in sorted(choice.items()):
            yield gid,stage,board,human


def _profile(root):
    location=Path(root).resolve(strict=True)
    exe=location/"rodent-iv-plain.exe"
    f=find_player("fischer")
    basic=location/"personalities/basic.ini"
    aliases=registered_aliases(basic)
    if aliases.get(f.rodent_alias)!=f.profile_filename or aliases.get("Simple")!="simple.txt":
        raise ValueError("Unregistered personality; freeze prohibited")
    files={"rodent":exe,"fischer":location/"personalities"/f.profile_filename,
           "simple":location/"personalities/simple.txt"}
    hashes={name:file_hash(path) for name,path in files.items()}
    if hashes!=RODENT_HASHES:
        raise ValueError("Rodent profile or binary changed; frozen baseline invalid")
    simple=Player(number=0,name="Rodent IV Simple",slug="control-simple",
                  group="baseline",rodent_alias="Simple",rodent_file="simple.txt")
    return location,exe,f,simple,hashes


def run_shard(rodent_root,out,index,archive_dir=None):
    if type(index) is not int or not 0<=index<SHARDS:
        raise ValueError("Invalid shard")
    source=source_cases(archive_dir)
    pgn,rows,_,_,surname=_source(archive_dir)
    subset=[r for r in rows if neural_arm(r["mainline_sha256"])=="excluded_test"]
    position_meta=source["style"]
    roots={r["id"]:r for r in source["roots"]}
    cases=[case for case in source["cases"] if shard(case["id"])==index]
    styles=[s for s in position_meta.values() if shard(s["game"])==index]
    path=Path(out)
    if path.is_symlink():
        raise ValueError("Symlink output")
    location,exe,f,simple,hashes=_profile(rodent_root)
    protocol=sha256(Path(__file__).read_bytes()).hexdigest()
    manifest={"schema":SCHEMA,"shard":index,"source":source["pgn_sha256"],
              "rodent":hashes,"protocol_sha256":protocol,
              "python_chess_version":chess.__version__,
              "case_ids":sorted(c["id"] for c in cases),
              "style_ids":sorted(x["id"] for x in styles),
              "clock":[CLOCK_SECONDS,CLOCK_INCREMENT],"depth":FOCAL_DEPTH}
    path.mkdir(exist_ok=True,parents=False)
    if (path/"manifest.json").exists():
        if load_json(path/"manifest.json")!=manifest:
            raise ValueError("Incompatible checkpoint manifest")
    else:
        if any(path.iterdir()):
            raise ValueError("Unrecognized checkpoint directory")
        atomic_json(path/"manifest.json",manifest)
    msha=digest(manifest)
    with ExitStack() as stack:
        fisher=stack.enter_context(_open_engine(location,exe,f,book=False))
        opponent=stack.enter_context(_open_engine(location,exe,simple,book=False))
        for i,case in enumerate(cases):
            file=path/(case["id"]+".json")
            record=load_json(file) if file.exists() else play_clock(case,roots[case["root"]],fisher,opponent)
            if not file.exists():
                record["manifest_sha256"]=msha
                atomic_json(file,record)
            if record["manifest_sha256"]!=msha:
                raise ValueError("Wrong checkpoint manifest")
            validate_clock(record,case,roots[case["root"]])
            print("FISCHER_FINAL_CLOCK",index,i+1,len(cases),record["status"],flush=True)
        matches=0
        for gid,stage,board,human in _style_positions(pgn,subset,surname):
            ident=digest([gid,stage,board.ply(),board.fen()])
            if shard(gid)!=index:
                continue
            expected=position_meta[ident]
            file=path/(ident+".json")
            if file.exists():
                doc=load_json(file)
            else:
                fmove,ftel=choose(fisher,board,FOCAL_DEPTH)
                smove,stel=choose(opponent,board,FOCAL_DEPTH)
                repeat=(int(ident[:12],16)%REPEAT_MODULUS==0)
                repeated=choose(fisher,board,FOCAL_DEPTH)[0].uci() if repeat else None
                doc={"schema":SCHEMA,"id":ident,"game":gid,"phase":stage,
                     "ply":board.ply(),"fen_sha256":expected["fen_sha256"],
                     "human":human.uci(),"fischer":fmove.uci(),"simple":smove.uci(),
                     "features":{name:features(board,move)
                                 for name,move in (("human",human),("fischer",fmove),
                                                   ("simple",smove))},
                     "repeat_fischer":repeated,
                     "depth_met":{"fischer":ftel["reached_requested"],
                                  "simple":stel["reached_requested"]},
                     "manifest_sha256":msha}
                atomic_json(file,doc)
            validate_style(doc,expected,board,msha)
            matches+=1
        if matches!=len(styles):
            raise ValueError("Style sample missing")
    if hashes!=_profile(rodent_root)[-1]:
        raise ValueError("Engine file changed during benchmark")
    return {"schema":SCHEMA,"index":index,"clock_games":len(cases),"style_positions":matches}


def validate_style(doc,expected,board,msha):
    if (doc.get("schema")!=SCHEMA or doc.get("id")!=expected["id"] or
        doc.get("fen_sha256")!=expected["fen_sha256"] or
        doc.get("game")!=expected["game"] or
        doc.get("human")!=expected["human"] or
        doc.get("manifest_sha256")!=msha or
        doc.get("phase")!=expected["phase"] or
        doc.get("ply")!=expected["ply"]):
        raise ValueError("Style checkpoint not bound to original historical position")
    moves={}
    for role in ("human","fischer","simple"):
        move=chess.Move.from_uci(doc[role])
        if move not in board.legal_moves or doc["features"][role]!=features(board,move):
            raise ValueError("Style move or proxy illegal")
        moves[role]=move
    repeated=doc.get("repeat_fischer")
    if (int(doc["id"][:12],16)%REPEAT_MODULUS==0)!=(repeated is not None):
        raise ValueError("Missing preregistered reproducibility check")
    if repeated is not None:
        if chess.Move.from_uci(repeated) not in board.legal_moves:
            raise ValueError("Invalid repeated choice")
    if (set(doc.get("depth_met",{}))!={"fischer","simple"} or
            any(type(v) is not bool for v in doc["depth_met"].values())):
        raise ValueError("Invalid depth telemetry")
    return doc


def source_baseline(doc):
    if (doc.get("schema")!="chesssim-fischer-cap-recovery-v1" or
            doc.get("source_panel_sha256")!=
            "5f186aefaa7728ae44f150ca8f1e6f399cfb7cdaeaf07ea71e78fd5e9b5eb8cb" or
            doc.get("source_run_id")!=SOURCE_GAME_RUN or
            doc.get("status")!=SOURCE_RESULT or doc.get("observed_cases")!=3616 or
            doc.get("original_complete")!=3594 or doc.get("recovered")!=22 or
            doc.get("recovery_termination_counts")!={"FIFTY_MOVES":22}):
        raise ValueError("Historic benchmark is incomplete or inconsistent")
    totals={}
    for arm,depth in (("fit",12),("fit",16),("tune",12),("tune",16)):
        sub={}
        for mode in ("original","book"):
            cells=[doc["scores"][f"{arm}|{depth}|{color}|{mode}"]
                   for color in ("white","black")]
            if any(set(cell)!={"games","wins","draws","losses"} or
                   any(type(v) is not int or v<0 for v in cell.values()) or
                   cell["games"]!=cell["wins"]+cell["draws"]+cell["losses"]
                   for cell in cells):
                raise ValueError("Historically scored WDL denominator inconsistent")
            games=sum(x["games"] for x in cells)
            points=sum(x["wins"]+x["draws"]/2 for x in cells)
            sub[mode]={"games":games,"points":points,"score":points/games}
        totals[f"{arm}|{depth}"]=sub
    if (totals["fit|12"]["original"]["games"]!=858 or
            totals["fit|16"]["original"]["games"]!=858 or
            totals["tune|12"]["original"]["games"]!=46 or
            totals["tune|16"]["original"]["games"]!=46 or
            any(totals[k]["original"]["games"]!=totals[k]["book"]["games"]
                for k in totals)):
        raise ValueError("Unexpected baseline denominators")
    return totals


def _score(result,color):
    if result=="1/2-1/2":
        return .5
    if result not in ("1-0","0-1"):
        raise ValueError("No score for nonterminal game")
    return float((result=="1-0")== (color=="white"))


def root_ci(pairs):
    groups=defaultdict(list)
    for key,score in pairs:
        groups[key].append(score)
    rng=random.Random(202611)
    blocks=list(groups.values())
    if len(blocks)<10:
        return None
    draws=[]
    for _ in range(2000):
        pick=rng.choices(blocks,k=len(blocks))
        draws.append(sum(map(sum,pick))/sum(map(len,pick)))
    draws.sort()
    return [draws[49],draws[1949]]


def aggregate(shards,baseline,archive_dir=None):
    source=source_cases(archive_dir)
    pgn,rows,_,_,surname=_source(archive_dir)
    subset=[x for x in rows if neural_arm(x["mainline_sha256"])=="excluded_test"]
    roots={r["id"]:r for r in source["roots"]}
    expected_cases={x["id"]:x for x in source["cases"]}
    styles=source["style"]
    expected_boards={}
    for gid,stage,board,human in _style_positions(pgn,subset,surname):
        ident=digest([gid,stage,board.ply(),board.fen()])
        expected_boards[ident]=board
    if set(expected_boards)!=set(styles):
        raise ValueError("Original TEST style panel differs")
    seen_cases=set()
    seen_styles=set()
    match=[]
    style_records=[]
    manifests=[]
    for index in range(SHARDS):
        directory=Path(shards)/f"shard-{index}"
        manifest=load_json(directory/"manifest.json")
        expected={"schema":SCHEMA,"shard":index,"source":source["pgn_sha256"],
                  "rodent":RODENT_HASHES,"protocol_sha256":file_hash(Path(__file__)),
                  "python_chess_version":chess.__version__,
                  "case_ids":sorted(c["id"] for c in source["cases"] if shard(c["id"])==index),
                  "style_ids":sorted(s["id"] for s in styles.values() if shard(s["game"])==index),
                  "clock":[CLOCK_SECONDS,CLOCK_INCREMENT],"depth":FOCAL_DEPTH}
        if manifest!=expected:
            raise ValueError("Unrecognized scientific protocol or source shard")
        manifests.append(manifest)
        if {p.name for p in directory.iterdir()}!={"manifest.json",*(
                x+".json" for x in (*expected["case_ids"],*expected["style_ids"]))}:
            raise ValueError("Missing, additional, or truncated records")
        bind=digest(manifest)
        for cid in expected["case_ids"]:
            if cid in seen_cases:
                raise ValueError("Duplicate game")
            seen_cases.add(cid)
            record=load_json(directory/(cid+".json"))
            if record.get("manifest_sha256")!=bind:
                raise ValueError("Unbound clock record")
            validate_clock(record,expected_cases[cid],roots[expected_cases[cid]["root"]])
            match.append(record)
        for sid in expected["style_ids"]:
            if sid in seen_styles:
                raise ValueError("Duplicate style position")
            seen_styles.add(sid)
            record=load_json(directory/(sid+".json"))
            validate_style(record,styles[sid],expected_boards[sid],bind)
            style_records.append(record)
    if seen_cases!=set(expected_cases) or seen_styles!=set(styles):
        raise ValueError("Not every preregistered case was evaluated")
    prev=source_baseline(load_json(Path(baseline)))
    previous_check={
        "fit_d12":prev["fit|12"]["original"]["score"],
        "fit_d16":prev["fit|16"]["original"]["score"],
        "tune_d12":prev["tune|12"]["original"]["score"],
        "tune_d16":prev["tune|16"]["original"]["score"],
    }
    baseline_pass=(prev["fit|12"]["original"]["score"]>=.75 and
                   .45<=prev["fit|16"]["original"]["score"]<=.65 and
                   prev["tune|12"]["original"]["score"]>=.75 and
                   .40<=prev["tune|16"]["original"]["score"]<=.70)
    completed=all(x["status"]=="COMPLETE" for x in match)
    forfeits=Counter(x["time_forfeit"] for x in match if x["time_forfeit"])
    paired=[(x["case"]["root"],_score(x["result"],x["case"]["color"]))
            for x in match if x["status"]=="COMPLETE"]
    score=sum(v for _,v in paired)/len(paired) if paired else None
    results=Counter(x["result"] for x in match)
    # Small clocked panel is a stability/smoke gate, not a formal Elo test.
    clock_pass=(completed and score is not None and score>=.30 and
                forfeits.get("fischer",0)==0)
    rates={}
    for role in ("human","fischer","simple"):
        rates[role]={f:mean(int(x["features"][role][f]) for x in style_records)
                     for f in STYLE_FEATURES}
    closer=[name for name in STYLE_FEATURES
            if abs(rates["fischer"][name]-rates["human"][name]) <=
               abs(rates["simple"][name]-rates["human"][name])+.025]
    agreement={role:sum(x[role]==x["human"] for x in style_records)/len(style_records)
               for role in ("fischer","simple")}
    profile_difference=sum(x["fischer"]!=x["simple"] for x in style_records)/len(style_records)
    fischer_wdl=Counter("draw" if x["result"]=="1/2-1/2" else
                       "win" if _score(x["result"],x["case"]["color"])==1 else "loss"
                       for x in match if x["status"]=="COMPLETE")
    reps=[x for x in style_records if x["repeat_fischer"] is not None]
    exact=sum(x["repeat_fischer"]==x["fischer"] for x in reps)
    stability={"retested":len(reps),"repeat_agreement":exact/len(reps) if reps else None,
               "fischer_depth_met":sum(x["depth_met"]["fischer"] for x in style_records)/
                   len(style_records)}
    style_pass=(len(style_records)>=100 and
                len({x["game"] for x in style_records})>=100 and
                len(closer)>=2 and profile_difference>=.05 and
                agreement["fischer"]>=.40 and
                agreement["fischer"]>=agreement["simple"]-.03)
    stable_pass=(len(reps)>=12 and stability["repeat_agreement"]>=.95 and
                 stability["fischer_depth_met"]>=.90)
    gates={"historical_practical_strength":baseline_pass,
           "clock_play":clock_pass,"style_preferences":style_pass,
           "reproducibility":stable_pass}
    status=("ELIGIBLE_FOR_FISCHER_GAME_ASSET_FREEZE_REVIEW"
            if all(gates.values()) else "DO_NOT_FREEZE_FISCHER")
    # 'Freezing' is conditional on a successful final CI and explicit
    # merge of an immutable manifest: no secret auto-write into main.
    return {"schema":SCHEMA,"status":status,"gates":gates,
            "source_runs":{"historical":SOURCE_RUN},
            "clock":{"control":"10+2","games":len(match),"completed":sum(
                x["status"]=="COMPLETE" for x in match),
                "points_ratio":score,"game_cluster_ci95":root_ci(paired),
                "results":dict(results),"fischer_wdl":dict(fischer_wdl),
                "time_forfeits":dict(forfeits)},
            "historical_fixed_depth":previous_check,
            "historical_recovery_verified":True,
            "style":{"positions":len(style_records),
                     "source_games":len({x["game"] for x in style_records}),
                     "rates":rates,"historical_agreement":agreement,
                     "fischer_vs_simple_move_disagreement":profile_difference,
                     "close_to_human_vs_simple":closer},
            "stability":stability,
            "frozen_profile_hashes":RODENT_HASHES,
            "shard_count":SHARDS,
            "no_fide_elo_claim":True,"blind_historical_test":False,
            "official_profile_modified":False,
            "limitations":["Not a FIDE Elo or equivalent-strength rating",
                           "Clock panel is small and starts after historical 12 plies",
                           "TEST source previously inspected; style signatures are proxies",
                           "A separate human release review is required before freezing"]}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    subs=ap.add_subparsers(dest="command",required=True)
    for command in ("plan","play","aggregate"):
        q=subs.add_parser(command)
        q.add_argument("--archive-dir",type=Path)
        q.add_argument("--fetch",action="store_true")
        if command=="play":
            q.add_argument("--rodent-root",type=Path,required=True)
            q.add_argument("--index",type=int,required=True)
        if command=="aggregate":
            q.add_argument("--shards",type=Path,required=True)
            q.add_argument("--baseline",type=Path,required=True)
        if command!="plan":
            q.add_argument("--out",type=Path,required=True)
    args=ap.parse_args(argv)
    if bool(args.fetch)==bool(args.archive_dir):
        raise ValueError("Select exactly one historical source")
    if args.command=="plan":
        doc=plan(args.archive_dir)
    elif args.command=="play":
        doc=run_shard(args.rodent_root,args.out,args.index,args.archive_dir)
    else:
        doc=aggregate(args.shards,args.baseline,args.archive_dir)
        if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
            raise ValueError("Refuse to overwrite results")
        atomic_json(args.out,doc)
    print(json.dumps(doc,sort_keys=True,allow_nan=False))
    return 0 if args.command!="aggregate" or doc["status"].startswith("ELIGIBLE") else 2


if __name__=="__main__":
    raise SystemExit(main())
