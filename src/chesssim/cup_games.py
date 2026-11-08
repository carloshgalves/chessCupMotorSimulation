"""One provenance-bearing UCI chess game with FIDE Cup-specific clock rules."""
from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Callable

import chess
import chess.engine
import chess.pgn

from .catalog import find_player
from .matches import _open_engine


def play_cup_game(root, spec: dict, config: dict, resources: dict,
                  stop_check: Callable[[], bool], *, factory=None,
                  max_plies: int = 400) -> tuple[chess.pgn.Game, dict]:
    """Play actual Rodent IV engines, never sample a synthetic PGN.

    In classical 2025 rules each player gets +30m after their 40th move.
    Armageddon is asymmetric, including the bid-determined Black clock.
    UCI search depth is independent of both real clock budgets.
    """
    factory = factory or _open_engine
    white = find_player(spec["white"])
    black = find_player(spec["black"])
    exe = root / "rodent-iv-plain.exe"
    clock = spec["clock"]
    if len(clock) == 3:
        base = [float(clock[0]), float(clock[1])]
        inc = clock[2]
    else:
        base = [float(clock[0]), float(clock[0])]
        inc = clock[1]
    white_engine = factory(root, exe, white, book=False)
    black_engine = None
    try:
        black_engine = factory(root, exe, black, book=False)
        board = chess.Board()
        game = chess.pgn.Game()
        game.headers.update({
            "Event": "ChessCup Motor Simulation - Copa virtual",
            "Site": "Local", "Date": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
            "Round": str(spec["round"]), "White": white.name,
            "Black": black.name, "Result": "*",
            "TimeControl": (f"40/{clock[0]}:{spec['extra_after_40']}+{inc}"
                            if spec["extra_after_40"] else
                            f"{clock[0]}+{inc}" if len(clock)==2 else
                            f"{clock[0]}/{clock[1]}+{inc}"),
            "EngineSHA256": resources["engine_sha256"],
            "WhiteProfileSHA256": resources["profile_sha256"][white.slug],
            "BlackProfileSHA256": resources["profile_sha256"][black.slug],
            "SourceKind": "cup_uci_verified",
            "CupStage": spec["stage"],
            "CupMatch": spec["match_id"],
            "CupGame": spec["id"],
            "RulesVersion": config["rules_version"],
            "SearchLimit": f"depth={config['depth']}",
        })
        if spec["bid_proof"]:
            game.headers["ArmageddonBlackBid"] = str(
                spec["bid_proof"]["winning_black_seconds"])
            game.headers["ArmageddonBlackDrawOdds"] = "true"
            game.headers["BidPolicy"] = "synthetic-deterministic-no-UCI-bid"
        node = game
        engines = (white_engine, black_engine)
        started = monotonic()
        clocks = base.copy()
        elapsed = [0.0, 0.0]
        termination = None
        for _ply in range(max_plies):
            result = board.outcome(claim_draw=True)
            if result is not None:
                break
            if stop_check():
                termination = "OperatorStop"
                break
            if monotonic() - started >= min(
                86400, sum(base) + 2 * 1800 + max_plies * inc + 600
            ):
                termination = "WallTimeLimit"
                break
            idx = 0 if board.turn else 1
            limit = chess.engine.Limit(
                depth=config["depth"],
                white_clock=max(0.001, clocks[0]),
                black_clock=max(0.001, clocks[1]),
                white_inc=inc, black_inc=inc,
            )
            before = monotonic()
            move = engines[idx].play(
                board, limit, game=game, info=chess.engine.INFO_NONE).move
            spent = monotonic() - before
            elapsed[idx] += spent
            clocks[idx] -= spent
            if clocks[idx] <= 0:
                # The flag falls before the proposed move can be applied.
                winner_has_material = not board.has_insufficient_material(
                    not board.turn)
                score = ("0-1" if idx == 0 else "1-0"
                         ) if winner_has_material else "1/2-1/2"
                game.headers["Result"] = score
                game.headers["Termination"] = "TimeForfeit"
                return game, {
                    "status": "COMPLETED", "result": score,
                    "termination": "TimeForfeit",
                    "clock_left": clocks.copy(),
                    "plies": board.ply(), "elapsed_seconds": elapsed,
                }
            if move is None or move not in board.legal_moves:
                raise RuntimeError("Motor UCI retornou lance ausente ou ilegal")
            node = node.add_variation(move)
            board.push(move)
            clocks[idx] += inc
            if spec["extra_after_40"] and board.ply() == 79 + idx:
                # 40th move = ply 79 for white; 80 for black.
                clocks[idx] += spec["extra_after_40"]
            node.set_clock(clocks[idx])
        outcome = board.outcome(claim_draw=True)
        if termination or outcome is None:
            score, status = "*", "INCOMPLETE"
            term = termination or "PlyLimit"
        else:
            score, status = outcome.result(), "COMPLETED"
            term = outcome.termination.name
        game.headers["Result"] = score
        game.headers["Termination"] = term
        return game, {
            "status": status, "result": score, "termination": term,
            "clock_left": clocks.copy(), "plies": board.ply(),
            "elapsed_seconds": elapsed,
        }
    finally:
        for engine in (black_engine, white_engine):
            if engine is not None:
                try:
                    engine.quit()
                except BaseException:
                    pass
                finally:
                    engine.close()
