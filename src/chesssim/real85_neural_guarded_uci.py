"""Opt-in real Rodent IV games with learned style and independent Stockfish guard.

The authentic Rodent Personality always supplies bestmove first. The small
frozen historical residual may change it only among Stockfish depth-limited
non-mate candidates within a bounded loss, and only when Rodent's choice
itself belongs to the independently analyzed safe set. Never official play.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

import chess
import chess.engine

from .catalog import load_roster, Player
from .experimental_policy_adapter import PolicyDecision
from .matches import _play_one, file_hash
from .opening_books import load_opening_book
from .real85_playable import SCHEMA as HISTORICAL_BOOK_SCHEMA
from .real85_online_residual import (
    REPORT_SCHEMA, SCHEMA as TRAIN_SCHEMA, _action, _context, _read_checkpoint, _score,
)
from .registry import registered_aliases

SCHEMA = "chesssim-real85-actual-guarded-neural-uci-v1"


class NeuralGuardedPolicy:
    """Satisfies the existing development-only policy hook, with no clock/book."""
    def __init__(self, player: str, checkpoint: dict, stockfish, *,
                 depth: int = 8, cp_budget: int = 25,
                 min_ply: int = 21, override_percent: int = 100,
                 confirmation_depth: int | None = None):
        if (type(depth) is not int or not 8 <= depth <= 20 or
                type(cp_budget) is not int or not 0 <= cp_budget <= 100 or
                type(min_ply) is not int or not 1 <= min_ply <= 500 or
                type(override_percent) is not int or
                override_percent not in (0,100) or
                (confirmation_depth is not None and (
                    type(confirmation_depth) is not int or
                    not 10 <= confirmation_depth <= 20 or
                    confirmation_depth <= depth))):
            raise ValueError("Invalid development-only neural safety parameters")
        if (checkpoint.get("schema") != TRAIN_SCHEMA or
                checkpoint.get("player") != player or
                checkpoint.get("gradient_steps",0) <= 0):
            raise ValueError("Expected genuinely trained frozen historical residual")
        self.player=player
        self.checkpoint=checkpoint
        self.stockfish=stockfish
        self.depth=depth
        self.cp_budget=cp_budget
        self.min_ply=min_ply
        self.override_percent=override_percent
        self.confirmation_depth=confirmation_depth
        self.gate=None
        self.bindings={player:"overall","control-simple":"overall"}
        self.model={"model_sha256":sha256(
            json.dumps(checkpoint["model"],sort_keys=True).encode()).hexdigest()}

    def _forced_root_cp(self, board: chess.Board, move: chess.Move) -> tuple[int | None, int | None]:
        """Independently score a forced root candidate on the same original FEN."""
        if move not in board.legal_moves or self.confirmation_depth is None:
            raise ValueError("Deep safety confirmation requested for invalid candidate")
        try:
            info=self.stockfish.analyse(
                board,chess.engine.Limit(depth=self.confirmation_depth),
                root_moves=[move],info=chess.engine.INFO_SCORE | chess.engine.INFO_PV,
                game=object())
        except Exception as exc:
            raise RuntimeError("Independent deep Stockfish confirmation failed") from exc
        pv=info.get("pv")
        value=info.get("score")
        if not pv or pv[0]!=move or value is None:
            raise RuntimeError("Independent deep Stockfish returned incorrect forced PV")
        pov=value.pov(board.turn)
        mate=pov.mate()
        if mate is not None:
            return None,mate
        cp=pov.score()
        if (type(cp) is not int or type(info.get("depth")) is not int or
                info["depth"]<self.confirmation_depth):
            raise RuntimeError("Stockfish did not complete required deep root confirmation")
        return cp,None

    def select(self, board: chess.Board, *, slug: str, engine_move: chess.Move,
               round_number: int, ply: int) -> PolicyDecision:
        if (slug not in self.bindings or
                type(round_number) is not int or round_number < 1 or
                type(ply) is not int or ply != board.ply()+1 or
                engine_move not in board.legal_moves):
            raise ValueError("Neural policy received invalid authentic UCI turn")
        eligible=slug==self.player and ply>=self.min_ply
        event={
            "ply":ply,"player":slug,"fen_sha256":sha256(board.fen().encode()).hexdigest(),
            "rodent_move":engine_move.uci(),"policy_move":None,
            "played_move":engine_move.uci(),"eligible":eligible,
            "sampled":False,"outcome":"RODENT_UNMODIFIED",
        }
        if not eligible:
            event["outcome"]=("CONTROL_RODENT_UNMODIFIED" if slug!=self.player
                              else "WARMUP_RODENT_UNMODIFIED")
            return PolicyDecision(engine_move,event)
        available=board.legal_moves.count()
        if available<=1:
            event["outcome"]="FORCED_NO_STYLE_CHOICE"
            return PolicyDecision(engine_move,event)
        try:
            infos=self.stockfish.analyse(
                board,chess.engine.Limit(depth=self.depth),
                multipv=min(4,available),
                info=chess.engine.INFO_SCORE|chess.engine.INFO_PV)
        except Exception as error:
            raise RuntimeError("Independent Stockfish safety engine failed") from error
        if isinstance(infos,dict):
            infos=[infos]
        analyses={}
        for row in infos:
            pv=row.get("pv")
            score=row.get("score")
            if not pv or score is None or pv[0] not in board.legal_moves:
                raise RuntimeError("Independent Stockfish returned invalid legal PV")
            pov=score.pov(board.turn)
            if pov.mate() is not None:
                event["outcome"]="MATE_SENSITIVE_RODENT_RETAINED"
                return PolicyDecision(engine_move,event)
            cp=pov.score()
            if cp is None:
                raise RuntimeError("Independent Stockfish returned empty score")
            analyses[pv[0]]=int(cp)
        if not analyses:
            raise RuntimeError("Stockfish failed to analyze any legal move")
        best=max(analyses.values())
        safe={m:cp for m,cp in analyses.items() if best-cp<=self.cp_budget}
        event.update({"stockfish_depth":self.depth,"stockfish_best_cp":best,
                      "stockfish_cp_budget":self.cp_budget,
                      "safe_candidate_count":len(safe),
                      "rodent_in_analyzed_set":engine_move in analyses})
        if engine_move not in safe:
            event["outcome"]="RODENT_NOT_IN_INDEPENDENT_SAFE_SET"
            return PolicyDecision(engine_move,event)
        if len(safe)<2:
            event["outcome"]="NO_SAFE_ALTERNATIVE"
            return PolicyDecision(engine_move,event)
        context=_context(board)
        scores={m:_score(self.checkpoint["model"],context,_action(board,m))
                for m in safe}
        proposed=max(safe,key=lambda m:(scores[m],int(m==engine_move),m.uci()))
        event["policy_move"]=proposed.uci()
        event["policy_margin_over_rodent"]=round(scores[proposed]-scores[engine_move],8)
        event["proposed_cp_gap_from_best"]=best-safe[proposed]
        if proposed==engine_move:
            event["outcome"]="POLICY_RODENT_AGREEMENT"
            return PolicyDecision(engine_move,event)
        if self.confirmation_depth is not None:
            original_cp,original_mate=self._forced_root_cp(board,engine_move)
            proposed_cp,proposed_mate=self._forced_root_cp(board,proposed)
            event["confirmation_depth"]=self.confirmation_depth
            event["confirmation_original_cp"]=original_cp
            event["confirmation_proposed_cp"]=proposed_cp
            event["confirmation_original_mate"]=original_mate
            event["confirmation_proposed_mate"]=proposed_mate
            if original_mate is not None or proposed_mate is not None:
                event["outcome"]="DEEP_MATE_GUARD_RETAINED_RODENT"
                return PolicyDecision(engine_move,event)
            delta=proposed_cp-original_cp
            event["confirmation_neural_minus_rodent_cp"]=delta
            if delta < -self.cp_budget:
                event["outcome"]="DEEP_CP_GUARD_RETAINED_RODENT"
                return PolicyDecision(engine_move,event)
        if self.override_percent==0:
            event["outcome"]="SHADOW_ONLY"
            return PolicyDecision(engine_move,event)
        event["sampled"]=True
        event["outcome"]="EXTERNAL_POLICY_OVERRIDE"
        event["played_move"]=proposed.uci()
        return PolicyDecision(proposed,event)


def _load_player(weights: Path, slug: str) -> dict:
    manifest=json.loads((weights/"report.json").read_text())
    row=manifest.get("players",{}).get(slug)
    if (manifest.get("schema")!=REPORT_SCHEMA or
            manifest.get("status")!="ALL_REQUESTED_PLAYER_RESIDUALS_FITTED" or
            not row or row["status"]!="FITTED_ALL_REAL_FOCAL_MOVES_CPU" or
            row["fit_games_processed"]!=row["fit_games_available"] or
            row["focal_decisions_processed"]!=row["fit_focal_decisions_available"]):
        raise ValueError("Only fully fitted historical model may enter real UCI")
    path=weights/row["checkpoint_file"]
    if path.parent!=weights or path.is_symlink() or file_hash(path)!=row["checkpoint_sha256"]:
        raise ValueError("Trained neural checkpoint integrity mismatch")
    state=_read_checkpoint(path)
    if (state["player"]!=slug or
            state["input_manifest_sha256"]!=manifest["input_manifest_sha256"] or
            state["gradient_steps"]!=row["gradient_steps"]):
        raise ValueError("Neural UCI checkpoint provenance mismatch")
    return state


def play(root: Path, stockfish: Path, weights: Path, out: Path, *,
         players: list[str] | None = None, rodent_depth: int = 4,
         stockfish_depth: int = 8, cp_budget: int = 25,
         min_ply: int = 21, max_plies: int = 450,
         profiles: Path | None = None,
         confirmation_depth: int | None = None,
         opening_seed: int = 2033,
         opponent_depth: int | None = None,
         start_fen: str | None = None,
         start_source_game_sha256: str | None = None,
         colors: tuple[str, ...] = ("white", "black")) -> dict:
    participants=list(players or ("fischer","tal","karpov","kasparov"))
    if (opponent_depth is not None and (type(opponent_depth) is not int or
            not 1 <= opponent_depth <= 12)):
        raise ValueError("Graded control requires bounded original Rodent UCI depth")
    if (not isinstance(colors, (tuple,list)) or not colors or
            len(colors) != len(set(colors)) or
            not set(colors) <= {"white","black"}):
        raise ValueError("Invalid color slice")
    if start_fen is not None and (
            not isinstance(start_fen, str) or
            not isinstance(start_source_game_sha256, str) or
            len(start_source_game_sha256) != 64 or
            any(ch not in "0123456789abcdef" for ch in start_source_game_sha256)):
        raise ValueError("Historical start needs an authenticated FIT game reference")
    if type(opening_seed) is not int or not 0 <= opening_seed <= 2**31-1:
        raise ValueError("Historical book seed must be a bounded integer")
    if (not 1<=len(participants)<=8 or len(set(participants))!=len(participants) or
            type(rodent_depth) is not int or not 1<=rodent_depth<=12 or
            type(max_plies) is not int or not 100<=max_plies<=800):
        raise ValueError("Invalid experimental UCI roster/search budget")
    root=Path(root).resolve(strict=True)
    stockfish=Path(stockfish).resolve(strict=True)
    weights=Path(weights).resolve(strict=True)
    out=Path(out)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise FileExistsError("Cannot overwrite real UCI result directory")
    executable=root/"rodent-iv-plain.exe"
    if not executable.is_file() or not stockfish.is_file():
        raise FileNotFoundError("Original Rodent/independent Stockfish binaries required")
    aliases=registered_aliases(root/"personalities/basic.ini")
    if "Simple" not in aliases:
        raise ValueError("Original Rodent IV Simple is missing")
    simple=Player(number=0,name="Rodent IV Simple",slug="control-simple",
                  group="baseline",rodent_alias="Simple",rodent_file=aliases["Simple"])
    roster={p.slug:p for p in load_roster()}
    checkpoints={}
    for slug in participants:
        profile=roster.get(slug)
        if (profile is None or profile.rodent_alias is None or
                aliases.get(profile.rodent_alias)!=profile.profile_filename):
            raise ValueError(f"No authentic Rodent personality for {slug}")
        checkpoints[slug]=_load_player(weights,slug)
    original_books={}
    if profiles is not None:
        profiles=Path(profiles).resolve(strict=True)
        manifest=json.loads((profiles/"report.json").read_text())
        if manifest.get("schema")!=HISTORICAL_BOOK_SCHEMA or min_ply<=40:
            raise ValueError("Historical neural books require genuine real85 FIT and min_ply>=41")
        for slug in participants:
            row=manifest.get("players",{}).get(slug, {})
            book_file=profiles/f"{slug}.json"
            if (row.get("status")!="REAL_GAMES_TRAINED_PLAYABLE_REPERTOIRE" or
                    row.get("train_fraction",0)<0.8 or
                    file_hash(book_file)!=row.get("book_sha256")):
                raise ValueError(f"Historical real85 opening corpus/book not verified: {slug}")
            original_books[slug]=load_opening_book(book_file,slug)
            if original_books[slug].get("policy",{}).get("max_plies")!=40:
                raise ValueError("Expected trained 40-ply historical repertoire")
    engine_hash=file_hash(executable)
    sf_hash=file_hash(stockfish)
    records,pgns=[],[]
    sf=chess.engine.SimpleEngine.popen_uci(str(stockfish),timeout=120.0)
    try:
        for slug in participants:
            focal=roster[slug]
            hashes={"exe":engine_hash,
                    focal.slug:file_hash(root/"personalities"/focal.profile_filename),
                    simple.slug:file_hash(root/"personalities"/simple.profile_filename)}
            for arm,percent in (("shadow",0),("guided",100)):
                policy=NeuralGuardedPolicy(
                    slug,checkpoints[slug],sf,depth=stockfish_depth,
                    cp_budget=cp_budget,min_ply=min_ply,override_percent=percent,
                    confirmation_depth=confirmation_depth)
                for round_number,(white,black) in enumerate(
                        ((focal,simple),(simple,focal)),1):
                    color = "white" if white.slug == slug else "black"
                    if color not in colors:
                        continue
                    pgn,meta=_play_one(
                        root,executable,white,black,
                        depth=rodent_depth,movetime=None,max_plies=max_plies,
                        round_number=round_number,hashes=hashes,use_books=False,
                        historical_openings=({slug:original_books[slug]}
                                             if original_books else None),
                        opening_seed=opening_seed,opening_min_count=2,
                        experimental_policy=policy,
                        experimental_depths=({slug:rodent_depth,
                                             simple.slug:opponent_depth}
                                            if opponent_depth is not None else None),
                        experimental_start_fen=start_fen)
                    pgn.headers["NeuralMode"]=arm
                    if start_source_game_sha256 is not None:
                        pgn.headers["HistoricalStartSourceGameSHA256"]=start_source_game_sha256
                    pgn.headers["IndependentStockfishSHA256"]=sf_hash
                    pgn.headers["ExperimentalNeuralSourceSHA256"]=policy.model["model_sha256"]
                    pgns.append(str(pgn))
                    records.append({
                        "player":slug,"arm":arm,"opening_seed":opening_seed,
                        "color":color,
                        "historical_start_fen":start_fen,
                        "historical_start_source_game_sha256":start_source_game_sha256,
                        "opponent_depth":opponent_depth,
                        "status":meta["status"],"result":meta["result"],
                        "termination":meta["termination"],"plies":meta["plies"],
                        "policy_trace":meta["policy_trace"],
                        "opening_trace":meta["opening_trace"],
                        "focal_historical_book_plies":meta["opening_moves"][
                            "white" if white.slug==slug else "black"],
                        "simple_book_plies":meta["opening_moves"][
                            "black" if white.slug==slug else "white"],
                        "actual_overrides":sum(
                            e["outcome"]=="EXTERNAL_POLICY_OVERRIDE"
                            for e in meta["policy_trace"]),
                        "safe_compared":sum(
                            "safe_candidate_count" in e and e["safe_candidate_count"]>=2
                            for e in meta["policy_trace"]),
                        "safety_retained":sum(
                            e["outcome"] in ("MATE_SENSITIVE_RODENT_RETAINED",
                                             "RODENT_NOT_IN_INDEPENDENT_SAFE_SET")
                            for e in meta["policy_trace"]),
                        "deep_confirmed_candidate_pairs":sum(
                            "confirmation_depth" in e for e in meta["policy_trace"]),
                        "deep_rejected":sum(
                            e["outcome"] in ("DEEP_MATE_GUARD_RETAINED_RODENT",
                                             "DEEP_CP_GUARD_RETAINED_RODENT")
                            for e in meta["policy_trace"]),
                    })
    finally:
        try:
            sf.quit()
        finally:
            sf.close()
    report={
        "schema":SCHEMA,
        "status":("ALL_REAL_UCI_GAMES_COMPLETE" if all(
            g["status"]=="COMPLETED" for g in records) else
            "SOME_GAMES_INCOMPLETE_DO_NOT_SCORE"),
        "rodent_original_sha256":engine_hash,
        "stockfish_sha256":sf_hash,
        "rodent_depth":rodent_depth,"stockfish_depth":stockfish_depth,
        "cp_budget":cp_budget,"min_ply":min_ply,
        "opening_seed":opening_seed,
        "opponent_depth":opponent_depth,
        "historical_start_fen":start_fen,
        "historical_start_source_game_sha256":start_source_game_sha256,
        "colors":list(colors),
        "confirmation_depth":confirmation_depth,
        "deep_guard_enabled":confirmation_depth is not None,
        "historical_focal_book_enabled":bool(original_books),
        "historical_book_training":"real85_game_disjoint_85pct_train" if original_books else None,
        "official_personality_files_changed":False,
        "rated_official_games":False,"historical_elo_estimated":False,
        "maia_dsc_weights_changed":False,
        "players":participants,"games":records,
    }
    out.mkdir()
    (out/"results.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    (out/"games.pgn").write_text("\n\n".join(pgns)+"\n")
    return report


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Development-only Rodent with neural and Stockfish guard")
    p.add_argument("--rodent-root",type=Path,required=True)
    p.add_argument("--stockfish",type=Path,required=True)
    p.add_argument("--weights",type=Path,required=True)
    p.add_argument("--profiles",type=Path,help="Genuine real85 85pct FIT opening books; requires --min-ply >=41")
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--players",nargs="+",default=["fischer","tal","karpov","kasparov"])
    p.add_argument("--rodent-depth",type=int,default=4)
    p.add_argument("--stockfish-depth",type=int,default=8)
    p.add_argument("--confirmation-depth",type=int,
                   help="Opt-in deeper forced-root comparison; must exceed --stockfish-depth")
    p.add_argument("--cp-budget",type=int,default=25)
    p.add_argument("--min-ply",type=int,default=21)
    p.add_argument("--max-plies",type=int,default=450)
    p.add_argument("--opening-seed",type=int,default=2033,
                   help="Fixed book choice seed; legacy default 2033")
    a=p.parse_args(argv)
    result=play(a.rodent_root,a.stockfish,a.weights,a.out,players=a.players,
                rodent_depth=a.rodent_depth,stockfish_depth=a.stockfish_depth,
                cp_budget=a.cp_budget,min_ply=a.min_ply,max_plies=a.max_plies,
                profiles=a.profiles,confirmation_depth=a.confirmation_depth,
                 opening_seed=a.opening_seed)
    print(json.dumps({"status":result["status"],"games":len(result["games"]),
                      "overrides":sum(g["actual_overrides"] for g in result["games"])}))
    return 0 if result["status"]=="ALL_REAL_UCI_GAMES_COMPLETE" else 2


if __name__=="__main__":
    # ``python -m`` constructs a second __main__ class object. Delegate to
    # the canonical import so the exact-class book safety gate in matches
    # cannot mistake this legitimate adapter for a different policy class.
    from chesssim import real85_neural_guarded_uci as canonical
    raise SystemExit(canonical.main())
