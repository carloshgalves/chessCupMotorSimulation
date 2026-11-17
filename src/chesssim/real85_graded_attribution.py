"""Pre-registered, authentic UCI graded-strength and move-source diagnostics.

Four historical pilots; two distinct FIT-derived 12-ply roots per actual
historical player color; opponent Rodent Simple at depths 2/4/6. SHADOW and
GUIDED face the identical opening FEN with the same search budgets.
Scores are exploratory. Independently re-evaluated decisions are *not*
causal estimates of complete-game strength or human Elo.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from .catalog import load_roster
from .game_diversity import mainline_identity
from .matches import _open_engine, file_hash
from .real85_neural_guarded_uci import play, SCHEMA as GAME_SCHEMA
from .real85_neural_stream import neural_arm, verify as verify_inputs
from .real85_playable import _material_endgame
from .registry import registered_aliases

SCHEMA = "chesssim-real85-graded-historic-root-crossover-v1"
AUDIT_SCHEMA = "chesssim-real85-graded-source-attribution-v1"
PLAYERS = ("fischer", "tal", "karpov", "kasparov")
DEPTHS = (2, 4, 6)
OPENING_PLIES = 12
ROOTS_PER_COLOR = 2
ANALYSIS_DEPTH = 12
MAX_PER_PLAYER_SOURCE_PHASE_DEPTH = 1


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 48*1024*1024:
        raise ValueError("Missing or unsafe UCI evidence")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate evidence JSON key")
            out[key] = value
        return out
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("Evidence must be an object")
    return value


def choose_roots(inputs: Path) -> dict:
    """Select popular but *different* real FIT opening families, separately by color.

    Only whole legal FIT historical game trajectories are consulted. TUNE,
    TEST, and the independently sealed old-year holdout never supply a root.
    """
    inputs = Path(inputs).resolve(strict=True)
    if verify_inputs(inputs)["status"] != "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES":
        raise ValueError("Graded roots require complete, verified pilot FIT/TUNE input")
    manifest = _read(inputs/"manifest.json")
    if not set(PLAYERS) <= set(manifest["players"]):
        raise ValueError("Missing original four historical pilot corpora")
    chosen = {}
    for player in PLAYERS:
        row = manifest["players"][player]
        if row.get("status") != "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING":
            raise ValueError("Pilot missing complete FIT opening material")
        groups = {"white": {}, "black": {}}
        path = inputs / row["shards"]["fit"]["file"]
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                entry = json.loads(line)
                if (entry["player"] != player or entry["arm"] != "fit" or
                        neural_arm(entry["game_sha256"]) != "fit" or
                        entry["source_pgn_sha256"] != row["source_pgn_sha256"] or
                        entry["initial_fen"] != chess.STARTING_FEN or
                        len(entry["uci_moves"]) < OPENING_PLIES):
                    continue
                board = chess.Board()
                prefix = entry["uci_moves"][:OPENING_PLIES]
                for notation in prefix:
                    move = chess.Move.from_uci(notation)
                    if move not in board.legal_moves:
                        raise ValueError("Corrupted historical opening in verified FIT shard")
                    board.push(move)
                fen = board.fen()
                color = entry["focal_color"]
                if color not in groups:
                    raise ValueError("Invalid historic player color")
                current = groups[color].setdefault(fen, {
                    "fen": fen, "prefix_uci": prefix, "first_four": prefix[:4],
                    "historical_fit_game_sha256": entry["game_sha256"],
                    "historical_source_pgn_sha256": row["source_pgn_sha256"],
                    "count": 0,
                })
                current["count"] += 1
                if entry["game_sha256"] < current["historical_fit_game_sha256"]:
                    current["historical_fit_game_sha256"] = entry["game_sha256"]
        roots = {}
        for color in ("white", "black"):
            options = sorted(groups[color].values(), key=lambda x: (
                -x["count"], x["prefix_uci"], x["historical_fit_game_sha256"]))
            if len(options) < ROOTS_PER_COLOR:
                raise ValueError("Not enough distinct actual FIT opening roots per color")
            selected = [options[0]]
            for option in options[1:]:
                if option["first_four"] != selected[0]["first_four"]:
                    selected.append(option)
                    break
            if len(selected) != ROOTS_PER_COLOR:
                raise ValueError("Historical FIT lacks two different 4-ply opening families")
            roots[color] = [{k: v for k, v in option.items() if k != "first_four"}
                            for option in selected]
        chosen[player] = roots
    result = {
        "schema": SCHEMA,
        "status": "FIXED_FIT_ONLY_DIVERSE_START_SCHEDULE",
        "training_manifest_sha256": file_hash(inputs/"manifest.json"),
        "players": chosen, "depths": list(DEPTHS),
        "focal_rodent_depth": 4, "source_prefix_plies": OPENING_PLIES,
        "roots_per_player_color": ROOTS_PER_COLOR,
        "training_game_partition": "real85-global-SHA FIT only",
        "selection": "most-frequent distinct four-ply FIT opening families per player color",
        "test_or_holdout_used": False, "strength_results_inspected_for_selection": False,
    }
    result["schedule_sha256"] = _sha(json.dumps(result, sort_keys=True,
                                     separators=(",", ":")).encode())
    return result


def _trial_id(slug: str, color: str, root_index: int, opponent_depth: int) -> str:
    return f"{slug}-{color}-root{root_index}-control-d{opponent_depth}"


def _opening_seed(color: str, root_index: int, opponent_depth: int) -> int:
    return 5301 + 100*root_index + (30 if color == "black" else 0) + opponent_depth


def _score(result: str, color: str) -> float:
    if result == "1/2-1/2":
        return .5
    if result not in ("1-0", "0-1"):
        raise ValueError("Unfinished/invalid actual UCI game result")
    return float((result == "1-0") == (color == "white"))


def _phase(board: chess.Board) -> str:
    if _material_endgame(board):
        return "material_endgame"
    if board.ply() < 24:
        return "opening_position"
    return "middlegame_position"


def inspect(out: Path) -> tuple[dict, list[dict]]:
    """Replay *all* PGNs, source decisions, actors and graded-depth commitments."""
    out = Path(out)
    schedule = _read(out/"schedule.json")
    if (schedule.get("schema") != SCHEMA or
            schedule.get("status") != "FIXED_FIT_ONLY_DIVERSE_START_SCHEDULE" or
            schedule.get("depths") != list(DEPTHS) or
            schedule.get("focal_rodent_depth") != 4 or
            schedule.get("source_prefix_plies") != OPENING_PLIES or
            schedule.get("roots_per_player_color") != ROOTS_PER_COLOR or
            schedule.get("test_or_holdout_used") is not False or
            schedule.get("strength_results_inspected_for_selection") is not False):
        raise ValueError("Unrecognized real FIT opening schedule")
    commitment = dict(schedule)
    claimed = commitment.pop("schedule_sha256", None)
    if claimed != _sha(json.dumps(commitment, sort_keys=True,
                                   separators=(",", ":")).encode()):
        raise ValueError("Forged historical FIT opening schedule")
    if set(schedule["players"]) != set(PLAYERS):
        raise ValueError("Expected all four original historic profiles")
    names = {p.slug: p.name for p in load_roster()}
    records, candidates = [], []
    observed_identity = {}
    model_hashes, profile_hashes = {}, {}
    blocks = Counter()
    unique_openings = defaultdict(set)
    full_games = defaultdict(set)
    source_counts = Counter()
    overrides = Counter()
    books = Counter()
    for slug in PLAYERS:
        for color in ("white", "black"):
            roots = schedule["players"][slug][color]
            if len(roots) != ROOTS_PER_COLOR:
                raise ValueError("Incomplete paired historical FIT opening roots")
            if len({r["fen"] for r in roots}) != ROOTS_PER_COLOR:
                raise ValueError("Duplicate historical opening roots")
            if len({tuple(r["prefix_uci"][:4]) for r in roots}) != ROOTS_PER_COLOR:
                raise ValueError("Missing distinct historical 4-ply opening families")
            for index, root in enumerate(roots):
                board = chess.Board()
                for notation in root["prefix_uci"]:
                    move = chess.Move.from_uci(notation)
                    if move not in board.legal_moves:
                        raise ValueError("Illegal historical opening provenance")
                    board.push(move)
                if (len(root["prefix_uci"]) != OPENING_PLIES or
                        root["fen"] != board.fen() or
                        not isinstance(root["count"], int) or root["count"] < 1):
                    raise ValueError("Corrupted historic FIT opening root")
                for opponent_depth in DEPTHS:
                    name = _trial_id(slug,color,index,opponent_depth)
                    folder = out/name
                    doc = _read(folder/"results.json")
                    if (doc.get("schema") != GAME_SCHEMA or
                            doc.get("status") != "ALL_REAL_UCI_GAMES_COMPLETE" or
                            doc.get("players") != [slug] or
                            doc.get("colors") != [color] or
                            doc.get("rodent_depth") != 4 or
                            doc.get("opponent_depth") != opponent_depth or
                            doc.get("historical_start_fen") != root["fen"] or
                            doc.get("historical_start_source_game_sha256") !=
                            root["historical_fit_game_sha256"] or
                            doc.get("opening_seed") !=
                            _opening_seed(color,index,opponent_depth) or
                            doc.get("historical_focal_book_enabled") is not True or
                            doc.get("confirmation_depth") != 12 or
                            doc.get("stockfish_depth") != 8 or
                            doc.get("cp_budget") != 25 or
                            doc.get("official_personality_files_changed") is not False or
                            len(doc.get("games",[])) != 2):
                        raise ValueError("Wrong/incomplete graded real UCI trial provenance")
                    engine_provenance = (doc["rodent_original_sha256"],
                                         doc["stockfish_sha256"])
                    if observed_identity and next(iter(observed_identity.values())) != engine_provenance:
                        raise ValueError("Mixed original Rodent or independent Stockfish binaries")
                    observed_identity[name] = engine_provenance
                    with (folder/"games.pgn").open(encoding="utf-8") as stream:
                        for record in doc["games"]:
                            game = chess.pgn.read_game(stream)
                            if game is None or game.errors:
                                raise ValueError("Missing or malformed original UCI PGN")
                            arm = record.get("arm")
                            if (arm not in ("shadow","guided") or
                                    record.get("color") != color or
                                    record.get("player") != slug or
                                    record.get("opponent_depth") != opponent_depth):
                                raise ValueError("Detached UCI scorer identity")
                            blocks[(slug,color,index,opponent_depth,arm)] += 1
                            if blocks[(slug,color,index,opponent_depth,arm)] != 1:
                                raise ValueError("Duplicated graded trial")
                            expected_white = names[slug] if color=="white" else "Rodent IV Simple"
                            expected_black = names[slug] if color=="black" else "Rodent IV Simple"
                            if (game.headers.get("White") != expected_white or
                                    game.headers.get("Black") != expected_black or
                                    game.headers.get("NeuralMode") != arm or
                                    game.headers.get("HistoricalStartMode") !=
                                    "PINNED_REAL_FIT_OPENING_FEN" or
                                    game.headers.get("HistoricalStartSourceGameSHA256") !=
                                    root["historical_fit_game_sha256"] or
                                    game.headers.get("OpeningSeed") != str(
                                        _opening_seed(color,index,opponent_depth)) or
                                    game.headers.get("BookMode") != "historical-v09-exploratory" or
                                    game.headers.get("EngineSHA256") != engine_provenance[0] or
                                    game.headers.get("IndependentStockfishSHA256") != engine_provenance[1] or
                                    game.headers.get("ExperimentalDepthWhite") !=
                                    str(4 if color=="white" else opponent_depth) or
                                    game.headers.get("ExperimentalDepthBlack") !=
                                    str(4 if color=="black" else opponent_depth) or
                                    game.board().fen() != root["fen"] or
                                    game.headers.get("Result") != record.get("result") or
                                    game.headers.get("Termination") != record.get("termination") or
                                    record.get("status") != "COMPLETED"):
                                raise ValueError("Incorrect original engine/position/result PGN evidence")
                            sha_model = game.headers.get("ExperimentalNeuralSourceSHA256")
                            if (not isinstance(sha_model,str) or len(sha_model) != 64 or
                                    game.headers.get("PolicyModelSHA256") != sha_model):
                                raise ValueError("Missing original neural provenance")
                            if slug in model_hashes and model_hashes[slug] != sha_model:
                                raise ValueError("Neural model switched within graded trial")
                            model_hashes[slug] = sha_model
                            for key, value in ((slug,game.headers.get(
                                    "WhiteProfileSHA256" if color=="white" else
                                    "BlackProfileSHA256")),
                                               ("control-simple",game.headers.get(
                                    "BlackProfileSHA256" if color=="white" else
                                    "WhiteProfileSHA256"))):
                                if (not isinstance(value,str) or len(value) != 64 or
                                        (key in profile_hashes and profile_hashes[key] != value)):
                                    raise ValueError("Original Rodent profile changed across trial")
                                profile_hashes[key] = value
                            played = list(game.mainline_moves())
                            traces = record.get("policy_trace")
                            opening_traces = record.get("opening_trace")
                            if (not isinstance(traces,list) or len(traces) != len(played) or
                                    not isinstance(opening_traces,list) or
                                    record.get("plies") != game.end().board().ply()):
                                raise ValueError("UCI moves and decision source traces disagree")
                            for book_event in opening_traces:
                                if (not isinstance(book_event,dict) or
                                        type(book_event.get("ply")) is not int or
                                        book_event.get("player") != slug or
                                        book_event["ply"] <= OPENING_PLIES):
                                    raise ValueError("Wrong historical book turn in recorded opening trace")
                            by_book_ply = {event["ply"]: event for event in opening_traces}
                            if len(by_book_ply) != len(opening_traces):
                                raise ValueError("Duplicate book turn attribution")
                            actual_overrides = 0
                            board = game.board()
                            for move,event in zip(played,traces):
                                if (move not in board.legal_moves or
                                        not isinstance(event,dict) or
                                        event.get("ply") != board.ply()+1 or
                                        event.get("played_move") != move.uci()):
                                    raise ValueError("Detached/illegal played move trace")
                                owner = slug if board.turn == (color=="white") else "control-simple"
                                if event.get("player") != owner:
                                    raise ValueError("UCI move attributed to wrong player")
                                book = by_book_ply.get(board.ply()+1)
                                if book is not None and (book["move"] != move.uci() or
                                                         owner != slug):
                                    raise ValueError("Wrong historical book move attributed")
                                kind = ("historical_book" if book is not None else
                                        "neural_override" if
                                        event.get("outcome") == "EXTERNAL_POLICY_OVERRIDE"
                                        else "baseline_rodent")
                                if event.get("outcome")=="EXTERNAL_POLICY_OVERRIDE":
                                    if arm != "guided" or book is not None or owner != slug or (
                                            event.get("rodent_move")==move.uci()) or (
                                            event.get("confirmation_depth")!=12):
                                        raise ValueError("Fake neural override in authentic UCI trace")
                                    actual_overrides += 1
                                if kind == "historical_book" and owner == slug:
                                    books[slug] += 1
                                if kind=="neural_override":
                                    overrides[slug] += 1
                                if owner==slug and (kind!="baseline_rodent" or arm=="shadow"):
                                    candidates.append({
                                        "slug":slug,"arm":arm,"color":color,"opponent_depth":opponent_depth,
                                        "trial":name,"source":kind,"phase":_phase(board),
                                        "fen":board.fen(),"move":move.uci(),
                                        "rodent_move":event.get("rodent_move")
                                        if kind=="neural_override" else None,
                                    })
                                board.push(move)
                            if actual_overrides != record.get("actual_overrides"):
                                raise ValueError("Real guided style intervention count is forged")
                            if board.outcome(claim_draw=True) is None or (
                                    board.outcome(claim_draw=True).result()!=record["result"]):
                                raise ValueError("UCI game did not legally complete from historical root")
                            identity, _ = mainline_identity(game)
                            full_games[slug].add(identity)
                            unique_openings[slug].add(root["fen"])
                            source_counts[(slug,opponent_depth,arm)] += _score(record["result"], color)
                            records.append({
                                "slug":slug,"color":color,"root":index,"opponent_depth":opponent_depth,
                                "arm":arm,"points":_score(record["result"],color),
                                "completed":True,"plies_after_historic_start":len(played),
                                "neural_overrides":actual_overrides,
                                "historical_book_moves":len(opening_traces),
                                "trial":name,
                            })
                        if chess.pgn.read_game(stream) is not None:
                            raise ValueError("Unexpected extra original UCI game")
    expected = len(PLAYERS)*2*ROOTS_PER_COLOR*len(DEPTHS)*2
    if len(blocks) != expected or len(records) != expected:
        raise ValueError("Missing graded color/root/arm paired experiment")
    summary = {
        "schema":SCHEMA,"status":"ALL_AUTHENTIC_GRADED_UCI_GAMES_COMPLETE_EXPLORATORY",
        "schedule_sha256":schedule["schedule_sha256"],
        "source_training_manifest_sha256":schedule["training_manifest_sha256"],
        "complete_games":len(records),"paired_conditions":len(records)//2,
        "historical_start_roots":len(PLAYERS)*2*ROOTS_PER_COLOR,
        "opponent_depths":list(DEPTHS),
        "depth_by_player":{"focal":4,"control":list(DEPTHS)},
        "distinct_game_trajectories":len(set().union(*full_games.values())),
        "frozen_original_rodent_and_stockfish_sha256":next(iter(observed_identity.values())),
        "frozen_player_model_sha256":model_hashes,
        "frozen_personality_sha256":profile_hashes,
        "results":{
            slug:{
                str(depth):{
                    "guided_points":source_counts[(slug,depth,"guided")],
                    "shadow_points":source_counts[(slug,depth,"shadow")],
                    "games_per_arm":2*ROOTS_PER_COLOR,
                } for depth in DEPTHS
            } for slug in PLAYERS
        },
        "focal_neural_overrides":dict(overrides),
        "historical_book_plies":dict(books),
        "source_files_verified":True,
        "original_personality_files_modified":False,
        "human_elo_or_original_player_strength_estimated":False,
        "holdout_or_test_used_for_training":False,
        "intervals_or_causal_strength_claims":None,
        "method_note":"Same historical opening FEN/color/depth paired, but diverging continuations are not same-FEN causal match effects.",
    }
    return summary,candidates


def run(inputs: Path, rodent: Path, stockfish: Path, weights: Path,
        profiles: Path, out: Path) -> dict:
    out = Path(out)
    if out.is_symlink() or out.exists() or not out.parent.is_dir():
        raise FileExistsError("Graded UCI output must be a new directory")
    schedule = choose_roots(inputs)
    out.mkdir()
    (out/"schedule.json").write_text(json.dumps(schedule, sort_keys=True, indent=2)+"\n")
    for slug in PLAYERS:
        for color in ("white","black"):
            for index,root in enumerate(schedule["players"][slug][color]):
                for depth in DEPTHS:
                    name = _trial_id(slug,color,index,depth)
                    doc=play(rodent,stockfish,weights,out/name,players=[slug],
                             colors=(color,),rodent_depth=4,opponent_depth=depth,
                             stockfish_depth=8,confirmation_depth=12,
                             cp_budget=25,min_ply=41,profiles=profiles,
                             start_fen=root["fen"],
                             start_source_game_sha256=root["historical_fit_game_sha256"],
                             opening_seed=_opening_seed(color,index,depth),
                             max_plies=450)
                    if doc["status"]!="ALL_REAL_UCI_GAMES_COMPLETE":
                        raise RuntimeError(f"Incomplete authentic UCI games in {name}")
    summary,_=inspect(out)
    (out/"summary.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
    return summary


def _root_score(engine, board: chess.Board, move: chess.Move, depth: int) -> dict:
    if move not in board.legal_moves:
        raise ValueError("Counterfactual candidate is illegal at the original root")
    info = engine.analyse(board,chess.engine.Limit(depth=depth),
                          root_moves=[move],
                          info=chess.engine.INFO_SCORE|chess.engine.INFO_PV,
                          game=object())
    if info.get("pv",[])[0:1] != [move] or not info.get("score"):
        raise ValueError("Independent Stockfish did not confirm the forced root")
    if info.get("depth",0)<depth:
        raise ValueError("Independent Stockfish did not finish requested depth")
    pov=info["score"].pov(board.turn)
    return {"cp":pov.score(),"mate":pov.mate()}


def attribution(out: Path, rodent_root: Path, stockfish: Path, path: Path, *,
                depth: int = ANALYSIS_DEPTH) -> dict:
    if depth != ANALYSIS_DEPTH:
        raise ValueError("Pre-registered reference Stockfish depth cannot be tuned")
    summary, candidates = inspect(out)
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise FileExistsError("Refuse to overwrite source-attribution evidence")
    if file_hash(stockfish) != summary["frozen_original_rodent_and_stockfish_sha256"][1]:
        raise ValueError("Stockfish binary differs from one that guarded played games")
    root = Path(rodent_root).resolve(strict=True)
    original = root/"rodent-iv-plain.exe"
    if file_hash(original) != summary["frozen_original_rodent_and_stockfish_sha256"][0]:
        raise ValueError("Original Rodent binary differs from played games")
    alias=registered_aliases(root/"personalities/basic.ini")
    roster={p.slug:p for p in load_roster()}
    for slug in PLAYERS:
        if (alias.get(roster[slug].rodent_alias)!=roster[slug].profile_filename or
                file_hash(root/"personalities"/roster[slug].profile_filename) !=
                summary["frozen_personality_sha256"][slug]):
            raise ValueError("Focal original Rodent personality changed after playing")
    selected=defaultdict(list)
    for item in candidates:
        key=(item["slug"],item["source"],item["phase"],item["opponent_depth"])
        selected[key].append(item)
    pending=[]
    for key,items in sorted(selected.items()):
        # Deterministic sort is committed in code before examining scores.
        seen=set()
        for candidate in sorted(items,key=lambda item: _sha(
                (item["fen"]+":"+item["trial"]+":"+item["move"]).encode())):
            identity=(candidate["fen"],candidate["move"])
            if identity in seen:
                continue
            seen.add(identity)
            pending.append(candidate)
            if len(seen)>=MAX_PER_PLAYER_SOURCE_PHASE_DEPTH:
                break
    observations=[]
    sf=chess.engine.SimpleEngine.popen_uci(str(stockfish),timeout=120.0)
    originals={}
    try:
        for item in pending:
            board=chess.Board(item["fen"])
            player=item["slug"]
            actual=chess.Move.from_uci(item["move"])
            best=sf.analyse(board,chess.engine.Limit(depth=depth),
                            info=chess.engine.INFO_SCORE|chess.engine.INFO_PV,game=object())
            if (not best.get("pv") or best["pv"][0] not in board.legal_moves or
                    best.get("depth",0)<depth):
                raise ValueError("Independent reference search incomplete")
            best_move=best["pv"][0]
            ref=_root_score(sf,board,best_move,depth)
            played=_root_score(sf,board,actual,depth)
            original_move=None
            original_quality=None
            if item["source"]=="neural_override":
                original_move=chess.Move.from_uci(item["rodent_move"])
            elif item["source"]=="historical_book":
                if player not in originals:
                    originals[player]=_open_engine(root, original, roster[player],book=False)
                original_move=originals[player].play(
                    board,chess.engine.Limit(depth=4),game=object()).move
            if original_move is not None:
                original_quality=_root_score(sf,board,original_move,depth)
            def cp_delta(left, right):
                if left["mate"] is not None or right["mate"] is not None:
                    return None
                return left["cp"]-right["cp"]
            observations.append({
                "slug":player,"trial":item["trial"],"source":item["source"],
                "phase":item["phase"],"opponent_depth":item["opponent_depth"],
                "fen_sha256":_sha(item["fen"].encode()),
                "played_move":actual.uci(),"reference_move":best_move.uci(),
                "rodent_counterfactual_move":(
                    original_move.uci() if original_move is not None else None),
                "played_score":played,"reference_score":ref,
                "rodent_counterfactual_score":original_quality,
                "played_minus_stockfish_best_cp":cp_delta(played,ref),
                "played_minus_same_fen_rodent_cp":(
                    cp_delta(played,original_quality)
                    if original_quality is not None else None),
            })
    finally:
        for engine in originals.values():
            try:
                engine.quit()
            finally:
                engine.close()
        try:
            sf.quit()
        finally:
            sf.close()
    counters={}
    for key in ("baseline_rodent","historical_book","neural_override"):
        rows=[row for row in observations if row["source"]==key]
        comparable=[r["played_minus_stockfish_best_cp"] for r in rows
                    if r["played_minus_stockfish_best_cp"] is not None]
        comparisons=[r["played_minus_same_fen_rodent_cp"] for r in rows
                     if r["played_minus_same_fen_rodent_cp"] is not None]
        counters[key]={
            "reference_positions":len(rows),"finite_cp_best_comparisons":len(comparable),
            "played_worse_than_stockfish_best_over_100cp":sum(v < -100 for v in comparable),
            "finite_cp_same_fen_rodent_comparisons":len(comparisons),
            "style_or_book_worse_than_rodent_over_100cp":sum(v < -100 for v in comparisons),
            "by_material_endgame_position":sum(
                r["phase"]=="material_endgame" for r in rows),
        }
    report={
        "schema":AUDIT_SCHEMA,
        "status":"OBSERVATIONAL_SAME_ROOT_SOURCE_DIAGNOSTIC_NOT_HUMAN_ELO",
        "source_crossover_schedule_sha256":summary["schedule_sha256"],
        "played_complete_games":summary["complete_games"],
        "independent_reference_stockfish_sha256":file_hash(stockfish),
        "reference_depth":depth,
        "sampling":"deterministic max one per player/source/material phase/opponent depth",
        "sampled_positions":len(observations),
        "source_counts":counters,"observations":observations,
        "holdout_or_test_used":False,"new_training_applied":False,
        "limitations":[
            "Stockfish depth12 is an imperfect search reference, not a human-strength oracle.",
            "Book comparisons recompute authentic Rodent depth4 bestmove from same initial FEN.",
            "Neural comparisons use the recorded Rodent move at the same initial FEN.",
            "Baseline uses SHADOW Rodent moves only; different complete game paths are observational.",
            "Mate scores are kept separate; centipawns are not substituted for mating sequences.",
            "Position-phase material proxy is not proof of real endgame technical quality.",
        ],
    }
    path.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    return report


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Authentic historical graded UCI and same-FEN attribution")
    sub=p.add_subparsers(dest="cmd",required=True)
    play_cmd=sub.add_parser("run")
    for flag in ("inputs","rodent-root","stockfish","weights","profiles","out"):
        play_cmd.add_argument("--"+flag,required=True,type=Path)
    audit_cmd=sub.add_parser("audit")
    for flag in ("dir","rodent-root","stockfish","out"):
        audit_cmd.add_argument("--"+flag,required=True,type=Path)
    args=p.parse_args(argv)
    if args.cmd=="run":
        result=run(args.inputs,args.rodent_root,args.stockfish,args.weights,
                   args.profiles,args.out)
        print(json.dumps({"status":result["status"],"complete_games":result["complete_games"],
                          "historical_start_roots":result["historical_start_roots"],
                          "distinct_game_trajectories":result["distinct_game_trajectories"],
                          "results":result["results"]},sort_keys=True),flush=True)
    else:
        result=attribution(args.dir,args.rodent_root,args.stockfish,args.out)
        print(json.dumps({"status":result["status"],"sampled_positions":
                          result["sampled_positions"],"source_counts":
                          result["source_counts"]},sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
