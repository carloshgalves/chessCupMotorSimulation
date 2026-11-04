"""Independent postgame Stockfish analysis; no simulated engine is used as judge.

Centipawn loss is reported as *search-depth dependent* and never as an Elo.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .game_diversity import audit_mainlines, mainline_identity, opening_seed_from_headers


def _sample_indices(total: int, limit: int) -> set[int]:
    """Spread a bounded sample across the eligible game decision sequence."""
    if total <= 0 or limit <= 0:
        return set()
    if total <= limit:
        return set(range(total))
    if limit == 1:
        return {total // 2}
    return {round(k * (total - 1) / (limit - 1)) for k in range(limit)}


def _score_cp(info: dict, pov) -> int:
    # Mate values clamped; do not interpret mate CP as positional advantage.
    return info['score'].pov(pov).score(mate_score=1000)


def analyze_games(pgn: Path, engine: Path, out: Path, *, depth: int = 12,
                  max_positions: int = 120, min_fullmove: int = 9) -> dict:
    import chess
    import chess.engine
    import chess.pgn
    if out.exists():
        raise FileExistsError(out)
    if not pgn.is_file() or not engine.is_file():
        raise FileNotFoundError('PGN or Stockfish executable not found')
    if not 1 <= depth <= 35 or not 1 <= max_positions <= 5000 or not 1 <= min_fullmove <= 100:
        raise ValueError('Invalid analysis budget')
    records = []
    observations = []
    seen_mainlines = set()
    with pgn.open(encoding='utf-8-sig', errors='replace') as stream:
        while (g := chess.pgn.read_game(stream)) is not None:
            if g.errors:
                raise ValueError('Malformed PGN; refusing silent quality analysis')
            digest, plies = mainline_identity(g)
            observations.append({
                "source": len(observations) + 1, "mainline_sha256": digest, "plies": plies,
                "opening_seed": opening_seed_from_headers(g.headers),
                "opening_mode": g.headers.get("BookMode"),
                "color_order": g.headers.get("White", "?") + "|" + g.headers.get("Black", "?"),
            })
            if not plies or digest in seen_mainlines:
                continue
            seen_mainlines.add(digest)
            board = g.board()
            for move in g.mainline_moves():
                if board.fullmove_number >= min_fullmove and not board.is_game_over():
                    records.append((g.headers.get('White', '?'), g.headers.get('Black', '?'),
                                    board.fen(), move.uci(), board.turn,
                                    board.fullmove_number))
                board.push(move)
    _, diversity = audit_mainlines(observations)
    if not records:
        raise ValueError('No eligible positions for quality analysis')
    chosen = _sample_indices(len(records), max_positions)
    data = []
    with chess.engine.SimpleEngine.popen_uci(str(engine), timeout=120) as judge:
        settings = {}
        if 'Threads' in judge.options:
            settings['Threads'] = 1
        if 'Hash' in judge.options:
            settings['Hash'] = 64
        if settings:
            judge.configure(settings)
        for idx in sorted(chosen):
            white, black, fen, played_uci, side, fullmove = records[idx]
            board = chess.Board(fen)
            move = chess.Move.from_uci(played_uci)
            if move not in board.legal_moves:
                raise ValueError('Illegal historical move in PGN')
            limit = chess.engine.Limit(depth=depth)
            # Both evaluated from the same ROOT position with the same depth.
            best = judge.analyse(board, limit, info=chess.engine.INFO_SCORE)
            forced = judge.analyse(board, limit, root_moves=[move], info=chess.engine.INFO_SCORE)
            best_cp = _score_cp(best, side)
            played_cp = _score_cp(forced, side)
            loss = max(0, best_cp - played_cp)
            player = white if side == chess.WHITE else black
            data.append({'player': player, 'color': 'white' if side else 'black',
                         'fullmove': fullmove, 'played': played_uci,
                         'best_move': best.get('pv', [None])[0].uci() if best.get('pv') else None,
                         'cp_loss': loss,
                         'mistake': loss >= 100, 'blunder': loss >= 250})
    by_player = defaultdict(list)
    for item in data:
        by_player[item['player']].append(item)
    summary = {}
    for player, rows in sorted(by_player.items()):
        losses = sorted(r['cp_loss'] for r in rows)
        summary[player] = {'decisions_analyzed': len(rows),
                           'mean_centipawn_loss': sum(losses) / len(losses),
                           'median_centipawn_loss': losses[len(losses) // 2],
                           'mistakes_over_100cp': sum(r['mistake'] for r in rows),
                           'blunders_over_250cp': sum(r['blunder'] for r in rows)}
    result = {'protocol': 'independent-stockfish-postgame-v9',
              'status': 'EXPLORATORY_STRENGTH_DIAGNOSTIC_NOT_ELO',
              'inputs': {'pgn_sha256': sha256(pgn.read_bytes()).hexdigest(),
                         'stockfish_sha256': sha256(engine.read_bytes()).hexdigest(),
                         'depth': depth, 'eligible_positions': len(records),
                         'sampled_positions': len(data), 'min_fullmove': min_fullmove},
              'diversity': diversity,
              'summary': summary, 'decisions': data,
              'warning': ('No rating inference; depth-limited root-move analysis with deterministic '
                          'spread sampling. Exact duplicate mainlines were excluded before sampling.')}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
