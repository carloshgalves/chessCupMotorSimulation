"""Actual UCI continuations from distinct historical FIT openings against graded Rodent.

Three authentic 12-ply FIT positions per player; two genuinely different
search budgets for the Simple opponent, reversed colors, mirrored SHADOW/
GUIDED policies. No original personality file is edited. Games begin from a
documented FEN, never masquerading as a standard-start full game.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from .catalog import Player, load_roster
from .game_diversity import mainline_identity
from .matches import _play_one, file_hash
from .opening_books import load_opening_book
from .real85_neural_guarded_uci import NeuralGuardedPolicy, _load_player
from .real85_neural_stream import GAME_SCHEMA, neural_arm, verify as verify_stream
from .real85_playable import SCHEMA as BOOK_SCHEMA
from .registry import registered_aliases

SCHEMA = "chesssim-real85-graded-fen-uci-crossover-v1"
PANEL_SCHEMA = "chesssim-real85-fit-diverse-three-starts-v1"
PILOTS = ("fischer", "tal", "karpov", "kasparov")
DEPTHS = (2, 6)
START_COUNT = 3
FEN_PLIES = 12
FOCAL_DEPTH = 4


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value)


def select_fit_openings(inputs: Path) -> dict:
    """Choose *before the games* deterministic distinct REAL FIT openings.

    Prefer distinct first four plies, then first eight, then distinct FENs.
    The global FIT/TUNE/TEST split is inherited, not reallocated.
    """
    inputs = Path(inputs).resolve(strict=True)
    if verify_stream(inputs)["status"] != "VERIFIED_ALL_REAL_NEURAL_INPUT_TRAJECTORIES":
        raise ValueError("Only entirely verified source trajectories can seed opening panel")
    source = json.loads((inputs / "manifest.json").read_text())
    if set(source["players"]) != set(PILOTS):
        raise ValueError("Opening panel requires the four original real85 pilot sources")
    players = {}
    for slug in PILOTS:
        row = source["players"][slug]
        if row["status"] != "READY_ALL_REAL_MOVES_FOR_NEURAL_TRAINING":
            raise ValueError("Historical player has insufficient verified FIT input")
        shard = inputs / row["shards"]["fit"]["file"]
        if (shard.parent != inputs or shard.is_symlink() or
                file_hash(shard) != row["shards"]["fit"]["sha256"]):
            raise ValueError("Historical FIT game shard changed")
        candidates = {}
        with shard.open(encoding="utf-8") as handle:
            for line in handle:
                game = json.loads(line)
                if (game["schema"] != GAME_SCHEMA or game["player"] != slug or
                        game["arm"] != "fit" or
                        neural_arm(game["game_sha256"]) != "fit" or
                        game["source_pgn_sha256"] != row["source_pgn_sha256"] or
                        game["initial_fen"] != chess.STARTING_FEN or
                        len(game["uci_moves"]) < FEN_PLIES):
                    continue
                board = chess.Board()
                moves = game["uci_moves"][:FEN_PLIES]
                for encoded in moves:
                    move = chess.Move.from_uci(encoded)
                    if move not in board.legal_moves:
                        raise ValueError("Historical source opening contains illegal move")
                    board.push(move)
                if (not board.is_valid() or board.is_game_over(claim_draw=True) or
                        board.ply() != FEN_PLIES):
                    continue
                fen = board.fen()
                identity = game["game_sha256"]
                score = sha256(("chesssim-graded-panel-v1:" + identity).encode()).hexdigest()
                candidate = {
                    "fen": fen, "game_sha256": identity, "source_arm": "fit",
                    "source_pgn_sha256": row["source_pgn_sha256"],
                    "opening_uci_12": moves, "sort_hash": score,
                }
                if fen not in candidates or score < candidates[fen]["sort_hash"]:
                    candidates[fen] = candidate
        ordered = sorted(candidates.values(), key=lambda r:r["sort_hash"])
        if len(ordered) < START_COUNT:
            raise ValueError(f"Not enough independently recorded FIT starting positions: {slug}")
        chosen = []
        for length in (4, 8, 12):
            previous = {tuple(row["opening_uci_12"][:length]) for row in chosen}
            for row in ordered:
                if len(chosen) == START_COUNT:
                    break
                if (row not in chosen and
                        tuple(row["opening_uci_12"][:length]) not in previous):
                    chosen.append(row)
                    previous.add(tuple(row["opening_uci_12"][:length]))
            if len(chosen) == START_COUNT:
                break
        if len(chosen) != START_COUNT or len({p["fen"] for p in chosen}) != START_COUNT:
            raise ValueError(f"Unable to select diverse FIT openings: {slug}")
        players[slug] = [{
            "fen":p["fen"], "game_sha256":p["game_sha256"],
            "source_arm":p["source_arm"],
            "source_pgn_sha256":p["source_pgn_sha256"],
            "opening_uci_12":p["opening_uci_12"],
        } for p in chosen]
    return {
        "schema": PANEL_SCHEMA, "source_manifest_sha256": file_hash(inputs/"manifest.json"),
        "focal_players": list(PILOTS), "historical_test_used": False,
        "historical_tune_used": False, "source_arm": "fit", "plies_per_opening": FEN_PLIES,
        "starts_per_player": START_COUNT, "players": players,
    }


def _points(result: str, focal_white: bool) -> float:
    if result == "1/2-1/2":
        return 0.5
    if result not in ("1-0", "0-1"):
        raise ValueError("Cannot score incomplete UCI game")
    return float((result == "1-0") == focal_white)


def validate(root: Path) -> dict:
    """Replay all real chess PGNs and trace their exact UCI decisions and sources."""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Missing actual UCI evidence directory")
    panel = json.loads((root/"starts.json").read_text())
    doc = json.loads((root/"results.json").read_text())
    if (panel.get("schema") != PANEL_SCHEMA or doc.get("schema") != SCHEMA or
            doc.get("opening_panel_sha256") != file_hash(root/"starts.json") or
            doc.get("players") != list(PILOTS) or
            doc.get("opponent_depths") != list(DEPTHS) or
            doc.get("focal_depth") != FOCAL_DEPTH or
            doc.get("status") != "ALL_ACTUAL_FEN_UCI_GAMES_COMPLETE" or
            not _sha(doc.get("rodent_original_sha256")) or
            not _sha(doc.get("stockfish_sha256")) or
            set(panel.get("players", {})) != set(PILOTS)):
        raise ValueError("Wrong/unfinished or detached real85 depth-graded experiment")
    for slug in PILOTS:
        openings = panel["players"][slug]
        if len(openings) != START_COUNT or len({p["fen"] for p in openings}) != START_COUNT:
            raise ValueError("Historical starting-position diversity not committed")
        for p in openings:
            if (p["source_arm"] != "fit" or neural_arm(p["game_sha256"]) != "fit" or
                    not _sha(p["source_pgn_sha256"]) or
                    len(p["opening_uci_12"]) != FEN_PLIES):
                raise ValueError("Unverified historical FIT opening source")
            board = chess.Board()
            for encoded in p["opening_uci_12"]:
                move = chess.Move.from_uci(encoded)
                if move not in board.legal_moves:
                    raise ValueError("Historical opening prefix is illegal")
                board.push(move)
            if board.fen() != p["fen"] or board.ply() != FEN_PLIES:
                raise ValueError("Historical opening FEN detached from real FIT prefix")
    expected = {(slug,idx,depth,color,arm)
                for slug in PILOTS for idx in range(START_COUNT)
                for depth in DEPTHS for color in ("white","black")
                for arm in ("shadow","guided")}
    actual = set()
    trajectories = defaultdict(lambda: defaultdict(set))
    counts = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    policies = defaultdict(set)
    personalities = defaultdict(set)
    rows = doc["games"]
    if len(rows) != len(expected):
        raise ValueError("Missing graded opponent / opening / arm / color block")
    actors = {p.slug:p.name for p in load_roster()}
    with (root/"games.pgn").open(encoding="utf-8") as handle:
        for record in rows:
            game = chess.pgn.read_game(handle)
            if game is None or game.errors:
                raise ValueError("Missing or malformed genuine graded UCI PGN")
            slug,idx,depth,arm,color = (record["player"],record["opening_index"],
                                        record["opponent_depth"],record["arm"],
                                        record["color"])
            key = (slug,idx,depth,color,arm)
            if key not in expected or key in actual:
                raise ValueError("Unmatched or duplicate graded UCI pairing")
            actual.add(key)
            panel_item = panel["players"][slug][idx]
            if (record["source_game_sha256"] != panel_item["game_sha256"] or
                    record["source_opening_fen"] != panel_item["fen"] or
                    game.board().fen() != panel_item["fen"] or
                    game.headers.get("ExperimentalOpeningSource") != "REAL85_FIT_START_12_PLIES" or
                    game.headers.get("StartSourceGameSHA256") != panel_item["game_sha256"] or
                    game.headers.get("EngineSHA256") != doc["rodent_original_sha256"] or
                    game.headers.get("IndependentStockfishSHA256") != doc["stockfish_sha256"] or
                    game.headers.get("NeuralMode") != arm or
                    game.headers.get("ExperimentalDepthWhite") != str(
                        FOCAL_DEPTH if color == "white" else depth) or
                    game.headers.get("ExperimentalDepthBlack") != str(
                        FOCAL_DEPTH if color == "black" else depth) or
                    game.headers.get("White") != (
                        actors[slug] if color == "white" else "Rodent IV Simple") or
                    game.headers.get("Black") != (
                        actors[slug] if color == "black" else "Rodent IV Simple") or
                    game.headers.get("PolicyOverridePercent") != (
                        "100" if arm == "guided" else "0") or
                    game.headers.get("OpeningSeed") != str(35021 + idx)):
                raise ValueError("Actual UCI source/color/depth/model evidence diverges")
            focal_sha = game.headers.get(
                "WhiteProfileSHA256" if color == "white" else "BlackProfileSHA256")
            simple_sha = game.headers.get(
                "BlackProfileSHA256" if color == "white" else "WhiteProfileSHA256")
            model_sha = game.headers.get("ExperimentalNeuralSourceSHA256")
            if not all(map(_sha,(focal_sha,simple_sha,model_sha))) or (
                    game.headers.get("PolicyModelSHA256") != model_sha):
                raise ValueError("Original personality/model provenance missing")
            policies[slug].add(model_sha)
            personalities[slug].add(focal_sha)
            personalities["control-simple"].add(simple_sha)
            if (record["status"] != "COMPLETED" or
                    game.headers.get("Result") != record["result"] or
                    game.headers.get("Termination") != record["termination"] or
                    not isinstance(record["policy_trace"],list) or
                    not isinstance(record["opening_trace"],list)):
                raise ValueError("Unfinished or untraced true UCI result")
            moves = list(game.mainline_moves())
            events = record["policy_trace"]
            opening = record["opening_trace"]
            if (len(moves) != len(events) or
                    record["played_plies"] != len(moves) or
                    record["total_board_ply"] != FEN_PLIES+len(moves)):
                raise ValueError("Missing real continuations or policy observations")
            opening_by_ply = {}
            for observed in opening:
                ply = observed["ply"]
                if (type(ply) is not int or not FEN_PLIES < ply <= 40 or
                        observed["player"] != slug or ply in opening_by_ply or
                        observed["seed"] != 35021 + idx or
                        observed["book_source_sha256"] != panel_item["source_pgn_sha256"]):
                    raise ValueError("Forged historical repertoire opening event")
                opening_by_ply[ply] = observed["move"]
            if record["focal_historical_book_plies"] != len(opening):
                raise ValueError("Opening book moves not reconciled")
            board = game.board()
            overrides = 0
            for move,event in zip(moves,events):
                ply = board.ply()+1
                focal_turn = board.turn == (color == "white")
                actor = slug if focal_turn else "control-simple"
                if (event["ply"] != ply or event["player"] != actor or
                        event["fen_sha256"] != sha256(board.fen().encode()).hexdigest() or
                        event["played_move"] != move.uci() or
                        chess.Move.from_uci(event["rodent_move"]) not in board.legal_moves or
                        move not in board.legal_moves or
                        board.is_game_over(claim_draw=True)):
                    raise ValueError("Rodent/guard original-FEN evidence cannot replay")
                if ply in opening_by_ply:
                    if not focal_turn or opening_by_ply[ply] != move.uci():
                        raise ValueError("Historical repertoire played by wrong actor")
                if event["outcome"] == "EXTERNAL_POLICY_OVERRIDE":
                    overrides += 1
                    if (arm != "guided" or not focal_turn or ply < 41 or
                            ply in opening_by_ply or
                            event.get("policy_move") != move.uci() or
                            event["rodent_move"] == move.uci() or
                            event.get("confirmation_depth") != 12 or
                            event.get("confirmation_original_mate") is not None or
                            event.get("confirmation_proposed_mate") is not None or
                            type(event.get("confirmation_neural_minus_rodent_cp")) is not int or
                            event["confirmation_neural_minus_rodent_cp"] < -25):
                        raise ValueError("Unverified external neural replacement")
                elif (move.uci() != event["rodent_move"] or
                      event.get("sampled") is True or
                      (not focal_turn and
                       event["outcome"] != "CONTROL_RODENT_UNMODIFIED")):
                    raise ValueError("Unreported neural replacement or modified simple control")
                board.push(move)
            outcome = board.outcome(claim_draw=True)
            if (outcome is None or outcome.result() != record["result"] or
                    overrides != record["actual_overrides"] or
                    (arm == "shadow" and overrides) or
                    record["simple_book_plies"] != 0):
                raise ValueError("Incomplete result, shadow leak or corrupted move counts")
            digest,_ = mainline_identity(game)
            trajectories[(slug,depth,arm)]["game"].add(digest)
            trajectories[(slug,depth,arm)]["source"].add(panel_item["fen"])
            counts[(slug,depth)][arm]["points"] += _points(record["result"], color == "white")
            counts[(slug,depth)][arm]["games"] += 1
            counts[(slug,depth)][arm]["book_plies"] += len(opening)
            counts[(slug,depth)][arm]["neural_overrides"] += overrides
        if chess.pgn.read_game(handle) is not None:
            raise ValueError("Unmatched extra experimental PGN")
    if actual != expected or any(len(v) != 1 for v in (*policies.values(),*personalities.values())):
        raise ValueError("Incomplete pairings or mutable engine personalities")
    result = {}
    for slug in PILOTS:
        result[slug] = {}
        for depth in DEPTHS:
            arms = counts[(slug,depth)]
            if any(arms[arm]["games"] != START_COUNT*2 for arm in ("shadow","guided")):
                raise ValueError("Unbalanced depth/arm real UCI scores")
            result[slug][str(depth)] = {
                arm:{
                    "games": int(arms[arm]["games"]),
                    "points":arms[arm]["points"],
                    "neural_overrides":int(arms[arm]["neural_overrides"]),
                    "historical_book_plies":int(arms[arm]["book_plies"]),
                    "unique_played_trajectories":len(trajectories[(slug,depth,arm)]["game"]),
                    "unique_fit_start_positions":len(trajectories[(slug,depth,arm)]["source"]),
                } for arm in ("shadow","guided")
            }
            result[slug][str(depth)]["paired_score_difference"] = (
                arms["guided"]["points"]-arms["shadow"]["points"])
    return {
        "status":"GRADED_AUTHENTIC_UCI_DESCRIPTIVE_NO_HUMAN_ELO",
        "total_complete_games":len(actual),
        "distinct_source_fit_positions":sum(len(panel["players"][slug]) for slug in PILOTS),
        "players":result,
    }


def play(rodent: Path, stockfish: Path, weights: Path, profiles: Path,
         inputs: Path, out: Path) -> dict:
    """Run the full preselected four-player / two-depth / three-opening matrix."""
    out = Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Graded real UCI output must be a new directory")
    root = Path(rodent).resolve(strict=True)
    stockfish = Path(stockfish).resolve(strict=True)
    weights = Path(weights).resolve(strict=True)
    profiles = Path(profiles).resolve(strict=True)
    executable = root/"rodent-iv-plain.exe"
    if not executable.is_file() or not stockfish.is_file():
        raise FileNotFoundError("Actual original Rodent and independent Stockfish required")
    panel = select_fit_openings(inputs)
    aliases = registered_aliases(root/"personalities/basic.ini")
    if "Simple" not in aliases:
        raise ValueError("Original Rodent Simple UCI baseline absent")
    roster = {p.slug:p for p in load_roster()}
    simple = Player(number=0,name="Rodent IV Simple",slug="control-simple",
                    group="baseline",rodent_alias="Simple",rodent_file=aliases["Simple"])
    manifest = json.loads((profiles/"report.json").read_text())
    if manifest.get("schema") != BOOK_SCHEMA:
        raise ValueError("No genuine fitted 85-percent historical books")
    books, checkpoints = {},{}
    for slug in PILOTS:
        player = roster[slug]
        if (player.rodent_alias is None or
                aliases.get(player.rodent_alias) != player.profile_filename):
            raise ValueError("Original Rodent player profile not registered")
        row = manifest["players"][slug]
        bookpath = profiles/f"{slug}.json"
        if (row["status"] != "REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE" or
                row["train_fraction"] < .80 or
                row["source_pgn_sha256"] != panel["players"][slug][0]["source_pgn_sha256"] or
                file_hash(bookpath) != row["book_sha256"]):
            raise ValueError("Historical FIT source and book must match original snapshot")
        books[slug] = load_opening_book(bookpath,slug)
        if books[slug]["policy"]["max_plies"] != 40:
            raise ValueError("Expected complete real85 historical opening repertoire")
        checkpoints[slug] = _load_player(weights,slug)
    out.mkdir()
    (out/"starts.json").write_text(json.dumps(panel,sort_keys=True,indent=2)+"\n")
    records,pgns=[],[]
    engine_hash = file_hash(executable)
    stockfish_hash = file_hash(stockfish)
    sf = chess.engine.SimpleEngine.popen_uci(str(stockfish),timeout=120.0)
    try:
        for slug in PILOTS:
            focal=roster[slug]
            hashes = {"exe":engine_hash, slug:file_hash(root/"personalities"/focal.profile_filename),
                      simple.slug:file_hash(root/"personalities"/simple.profile_filename)}
            for idx,opening in enumerate(panel["players"][slug]):
                for depth in DEPTHS:
                    for arm,percent in (("shadow",0),("guided",100)):
                        policy = NeuralGuardedPolicy(
                            slug,checkpoints[slug],sf,depth=8,cp_budget=25,
                            min_ply=41,override_percent=percent,confirmation_depth=12)
                        for color in ("white","black"):
                            white,black = ((focal,simple) if color=="white"
                                           else (simple,focal))
                            game,meta = _play_one(
                                root,executable,white,black,depth=FOCAL_DEPTH,
                                movetime=None,max_plies=450,
                                round_number=1 if color=="white" else 2,
                                hashes=hashes,use_books=False,
                                historical_openings={slug:books[slug]},
                                opening_seed=35021+idx,opening_min_count=2,
                                experimental_policy=policy,
                                experimental_depths={slug:FOCAL_DEPTH,
                                                     "control-simple":depth},
                                experimental_start_fen=opening["fen"])
                            game.headers["NeuralMode"] = arm
                            game.headers["StartSourceGameSHA256"] = opening["game_sha256"]
                            game.headers["IndependentStockfishSHA256"] = stockfish_hash
                            game.headers["ExperimentalNeuralSourceSHA256"] = policy.model["model_sha256"]
                            pgns.append(str(game))
                            records.append({
                                "player":slug,"opening_index":idx,"opponent_depth":depth,
                                "arm":arm,"color":color,
                                "source_opening_fen":opening["fen"],
                                "source_game_sha256":opening["game_sha256"],
                                "status":meta["status"],"result":meta["result"],
                                "termination":meta["termination"],
                                "played_plies":meta["plies"]-FEN_PLIES,
                                "total_board_ply":meta["plies"],
                                "focal_historical_book_plies":meta["opening_moves"][color],
                                "simple_book_plies":meta["opening_moves"][
                                    "black" if color=="white" else "white"],
                                "actual_overrides":sum(
                                    e["outcome"]=="EXTERNAL_POLICY_OVERRIDE"
                                    for e in meta["policy_trace"]),
                                "opening_trace":meta["opening_trace"],
                                "policy_trace":meta["policy_trace"],
                            })
    finally:
        try:
            sf.quit()
        finally:
            sf.close()
    report = {
        "schema": SCHEMA,
        "status":("ALL_ACTUAL_FEN_UCI_GAMES_COMPLETE" if all(
            row["status"]=="COMPLETED" for row in records)
            else "INCOMPLETE_UNSCORABLE_UCI_GAMES"),
        "players":list(PILOTS),"opponent_depths":list(DEPTHS),
        "focal_depth":FOCAL_DEPTH,"source_start_plies":FEN_PLIES,
        "opening_panel_sha256":file_hash(out/"starts.json"),
        "original_fit_manifest_sha256":panel["source_manifest_sha256"],
        "rodent_original_sha256":engine_hash,"stockfish_sha256":stockfish_hash,
        "book_source_training":"real85_85pct_source_verified_fit",
        "model_fitted_on_original_80pct_plus_fit":True,
        "old_year_holdout_used":False,"original_test_used":False,
        "official_personalities_untouched":True,"human_elo_claim":False,
        "games":records,
    }
    (out/"results.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    (out/"games.pgn").write_text("\n\n".join(pgns)+"\n")
    if report["status"] != "ALL_ACTUAL_FEN_UCI_GAMES_COMPLETE":
        raise RuntimeError("A real chess game failed to reach a terminal position")
    summary=validate(out)
    (out/"summary.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
    return summary


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Authentic real85 diverse FIT opening UCI graded opponent crossover")
    for key in ("rodent-root","stockfish","weights","profiles","inputs","out"):
        parser.add_argument("--"+key,type=Path,required=True)
    args=parser.parse_args(argv)
    result=play(args.rodent_root,args.stockfish,args.weights,args.profiles,args.inputs,args.out)
    print(json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
