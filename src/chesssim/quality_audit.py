"""ChessSimulation64 auditable, stratified independent UCI quality analysis.

Averages measure depth-sensitive agreement with Stockfish, NOT historical style,
Elo, nor reproducible chess strength without more control over positions.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median

from .game_diversity import audit_mainlines, mainline_identity

PROTOCOL = "independent-stockfish-postgame-v9.1"


def spread_indices(total: int, limit: int) -> list[int]:
    """Deterministic, approximately evenly spaced local integer indices."""
    if total <= 0 or limit <= 0:
        return []
    if total <= limit:
        return list(range(total))
    if limit == 1:
        return [total // 2]
    return [round(i * (total - 1) / (limit - 1)) for i in range(limit)]


def select_decisions(records: list[dict], *, limit: int,
                     mode: str = 'balanced') -> tuple[list[dict], dict]:
    """Spread selection globally or equally across (game, side) independent cells.

    Strict balancing may select fewer than requested: never quietly pad with
    extra positions from one game and then claim balanced comparisons.
    """
    if limit < 1:
        raise ValueError('max_positions must be >= 1')
    if mode not in ('balanced', 'spread'):
        raise ValueError('sample_mode must be balanced or spread')
    if not records:
        raise ValueError('No eligible positions')
    if mode == 'spread':
        chosen = [records[i] for i in spread_indices(len(records), limit)]
        return chosen, {'method': mode, 'requested_positions': limit,
                        'actual_positions': len(chosen), 'cell_quota': None,
                        'warning': 'Not balanced across games or colors.'}
    by_cell = defaultdict(list)
    for row in records:
        by_cell[(row['game_index'], row['color'])].append(row)
    cells = sorted(by_cell)
    quota = min(limit // len(cells), *(len(by_cell[cell]) for cell in cells))
    if quota <= 0:
        raise ValueError(f'max_positions={limit} cannot give one decision to each of {len(cells)} game/color cells')
    chosen = []
    for cell in cells:
        chosen.extend(by_cell[cell][i] for i in spread_indices(len(by_cell[cell]), quota))
    chosen.sort(key=lambda r: r['global_index'])
    return chosen, {
        'method': mode, 'requested_positions': limit, 'actual_positions': len(chosen),
        'cell_quota': quota, 'cells': [{'game_index': g, 'color': color,
                                      'eligible_positions': len(by_cell[(g, color)]),
                                      'selected_positions': quota} for g, color in cells],
        'warning': 'Strict game/color balance: may use fewer than the requested max_positions.'
    }


def summarize_decisions(rows: list[dict], *, label: str = 'player') -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row[label]].append(row)
    output = {}
    for player, decisions in sorted(groups.items()):
        losses = [r['cp_loss'] for r in decisions]
        output[player] = {
            'decisions_analyzed': len(decisions),
            'mean_centipawn_loss': mean(losses),
            'median_centipawn_loss': median(losses),
            'mistakes_over_100cp': sum(v >= 100 for v in losses),
            'blunders_over_250cp': sum(v >= 250 for v in losses),
            'mate_affected_decisions': sum(bool(r.get('mate_affected')) for r in decisions),
            'negative_unclipped_gaps': sum(r.get('raw_gap_cp', 0) < 0 for r in decisions),
        }
    return output


def _pv_san(board, pv: list, cap: int) -> list[str]:
    copy = board.copy(stack=False)
    result = []
    for move in pv[:cap]:
        if move not in copy.legal_moves:
            break
        result.append(copy.san(move))
        copy.push(move)
    return result


def _eval_pov(info: dict, pov) -> dict:
    value = info['score'].pov(pov)
    return {'cp_equivalent': value.score(mate_score=1000), 'mate_in': value.mate()}


def _evaluate_choice(judge, board, played, *, depth: int, pv_plies: int) -> dict:
    import chess.engine
    limit = chess.engine.Limit(depth=depth)
    # Both searches start from the same board and declare the same depth.
    # This does not remove all search instability or the root_moves effect.
    best = judge.analyse(board, limit, info=chess.engine.INFO_ALL)
    forced = judge.analyse(board, limit, root_moves=[played], info=chess.engine.INFO_ALL)
    a = _eval_pov(best, board.turn)
    b = _eval_pov(forced, board.turn)
    raw = a['cp_equivalent'] - b['cp_equivalent']
    pv = best.get('pv', [])
    forced_pv = forced.get('pv', [])
    return {
        'depth': depth,
        'best_move': pv[0].uci() if pv else None,
        'best_line_san': _pv_san(board, pv, pv_plies),
        'played_line_san': _pv_san(board, forced_pv, pv_plies),
        'best_eval_cp': a['cp_equivalent'],
        'played_eval_cp': b['cp_equivalent'],
        'best_mate_in': a['mate_in'],
        'played_mate_in': b['mate_in'],
        'mate_affected': a['mate_in'] is not None or b['mate_in'] is not None,
        'raw_gap_cp': raw,
        'cp_loss': max(0, raw),
        'search_disagreement': raw < 0,
    }


def _load_positions(pgn: Path, *, min_fullmove: int, dedupe_games: bool) -> tuple[list[dict], list[dict]]:
    import chess
    import chess.pgn
    records = []
    games = []
    seen = set()
    original_game_index = -1
    with pgn.open('r', encoding='utf-8-sig', errors='replace') as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            original_game_index += 1
            if game.errors:
                raise ValueError(f'Malformed PGN in game {original_game_index + 1}')
            moves = list(game.mainline_moves())
            signature, plies = mainline_identity(game)
            duplicate = bool(plies) and signature in seen
            if plies:
                seen.add(signature)
            meta = {
                'source_game_index': original_game_index,
                'game_index': original_game_index,
                'white': game.headers.get('White', '?'),
                'black': game.headers.get('Black', '?'),
                'result': game.headers.get('Result', '*'),
                'mainline_sha256': signature,
                'plies': plies,
                'source': original_game_index + 1,
                'opening_mode': game.headers.get('BookMode'),
                'color_order': (game.headers.get('White', '?') + '|' +
                                game.headers.get('Black', '?')),
                'duplicate_mainline': duplicate,
                'excluded_from_analysis': duplicate and dedupe_games,
            }
            games.append(meta)
            if duplicate and dedupe_games:
                continue
            board = game.board()
            for ply, move in enumerate(moves, 1):
                if board.fullmove_number >= min_fullmove and not board.is_game_over():
                    color = 'white' if board.turn == chess.WHITE else 'black'
                    records.append({
                        'global_index': len(records),
                        'source_game_index': original_game_index,
                        'game_index': original_game_index,
                        'player': meta[color], 'color': color,
                        'ply': ply, 'fullmove': board.fullmove_number,
                        'fen': board.fen(), 'played': move.uci(),
                        'played_san': board.san(move),
                    })
                board.push(move)
    annotated, _ = audit_mainlines(games)
    return records, annotated


def analyze_games_v91(pgn: Path, engine: Path, out: Path, *, depth: int = 18,
                      max_positions: int = 120, min_fullmove: int = 9,
                      sample_mode: str = 'balanced', dedupe_games: bool = True,
                      pv_plies: int = 10, refine_depth: int | None = None,
                      refine_threshold: int = 100, refine_max_positions: int = 20) -> dict:
    import chess
    import chess.engine
    if out.exists():
        raise FileExistsError(out)
    if not pgn.is_file() or not engine.is_file():
        raise FileNotFoundError('PGN or Stockfish executable not found')
    if not (1 <= depth <= 35 and 1 <= max_positions <= 5000 and 1 <= min_fullmove <= 100):
        raise ValueError('Invalid analysis budget')
    if not (1 <= pv_plies <= 30 and 0 <= refine_threshold <= 1000 and 0 <= refine_max_positions <= 5000):
        raise ValueError('Invalid PV or refinement budget')
    if refine_depth is not None and (refine_depth <= depth or refine_depth > 35):
        raise ValueError('refine_depth must be above depth and <= 35')
    records, games = _load_positions(pgn, min_fullmove=min_fullmove, dedupe_games=dedupe_games)
    _, diversity = audit_mainlines(games)
    chosen, sampling = select_decisions(records, limit=max_positions, mode=sample_mode)
    output = []
    with chess.engine.SimpleEngine.popen_uci(str(engine), timeout=120) as judge:
        settings = {}
        if 'Threads' in judge.options:
            settings['Threads'] = 1
        if 'Hash' in judge.options:
            settings['Hash'] = 64
        if settings:
            judge.configure(settings)
        for row in chosen:
            board = chess.Board(row['fen'])
            move = chess.Move.from_uci(row['played'])
            if move not in board.legal_moves:
                raise ValueError('Illegal move in PGN')
            score = _evaluate_choice(judge, board, move, depth=depth, pv_plies=pv_plies)
            output.append({k: v for k, v in row.items() if k != 'fen'} |
                          {'fen': row['fen'], **score,
                           'mistake': score['cp_loss'] >= 100,
                           'blunder': score['cp_loss'] >= 250})
        # Optional targeted search refinement: preserve original metrics for
        # an apples-to-apples comparison; never mix depths into main summary.
        ranked = sorted((r for r in output if r['cp_loss'] >= refine_threshold),
                        key=lambda x: (-x['cp_loss'], x['global_index']))
        chosen_refine = ranked[:refine_max_positions] if refine_depth is not None else []
        for row in chosen_refine:
            board = chess.Board(row['fen'])
            refined = _evaluate_choice(judge, board, chess.Move.from_uci(row['played']),
                                       depth=refine_depth, pv_plies=pv_plies)
            row['refined'] = refined
    by_game = defaultdict(list)
    for item in output:
        by_game[item['game_index']].append(item)
    game_report = []
    for game in games:
        if game['excluded_from_analysis']:
            continue
        rows = by_game.get(game['game_index'], [])
        game_report.append({**game, 'positions_analyzed': len(rows),
                            'by_player': summarize_decisions(rows)})
    # Diagnostic analysis may inspect duplicates without counting them in
    # aggregate independent-evidence estimates.
    duplicate_indices = {g['game_index'] for g in games if g['duplicate_mainline']}
    independent_decisions = [r for r in output if r['game_index'] not in duplicate_indices]
    result = {
        'protocol': PROTOCOL,
        'status': 'EXPLORATORY_STRENGTH_DIAGNOSTIC_NOT_ELO',
        'inputs': {
            'pgn_sha256': sha256(pgn.read_bytes()).hexdigest(),
            'stockfish_sha256': sha256(engine.read_bytes()).hexdigest(),
            'depth': depth, 'eligible_positions': len(records),
            'sampled_positions': len(output), 'min_fullmove': min_fullmove,
            'sample_mode': sample_mode, 'dedupe_games': dedupe_games,
            'pv_plies': pv_plies, 'refine_depth': refine_depth,
            'refine_threshold': refine_threshold,
            'refine_max_positions': refine_max_positions,
        },
        'sampling': sampling,
        'diversity': diversity,
        'independent_decisions_sampled': len(independent_decisions),
        'duplicate_decisions_excluded_from_summary': len(output) - len(independent_decisions),
        'games': game_report,
        'excluded_duplicate_games': [g for g in games if g['excluded_from_analysis']],
        'summary': summarize_decisions(independent_decisions),
        'refinement': {'selected': len(chosen_refine),
                       'warning': 'Refinements are reported separately and never mixed into base-depth averages.'},
        'decisions': output,
        'warning': ('All conclusions are depth-dependent. Mate evaluations use a 1000-cp mapping; '
                    'root-move forcing and depth variation introduce search instability. '
                    'Duplicate trajectories are excluded from the aggregate summary, '
                    'even when diagnostic inspection retains duplicate games. '
                    'Not a rating or historical identity evaluation.'),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return result


def compare_quality_reports(paths: list[Path], out: Path) -> dict:
    """Legacy-compatible comparison of identical PGN decisions at distinct depths."""
    if out.exists():
        raise FileExistsError(out)
    if len(paths) < 2:
        raise ValueError('At least two quality reports required')
    docs = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    hashes = [d['inputs']['pgn_sha256'] for d in docs]
    if len(set(hashes)) != 1:
        raise ValueError('Cannot compare reports from different PGNs')
    if len(set(d['inputs']['stockfish_sha256'] for d in docs)) != 1:
        raise ValueError('Cannot compare reports from different Stockfish executables')
    def key(row):
        return (row.get('source_game_index'), row['player'], row['color'],
                row['fullmove'], row['played'])
    # Legacy v9 lacks source_game_index: it is possible to have repeated move
    # keys in repeated games. Use lists and retain only unambiguous exact keys.
    indices = []
    collisions = []
    for doc in docs:
        idx = defaultdict(list)
        for r in doc['decisions']:
            idx[key(r)].append(r)
        indices.append(idx)
        collisions.append(sum(len(v) - 1 for v in idx.values() if len(v) > 1))
    common = sorted(set.intersection(*(set(idx) for idx in indices)), key=str)
    matched = []
    for k in common:
        if any(len(idx[k]) != 1 for idx in indices):
            continue
        moves = [idx[k][0] for idx in indices]
        matched.append({'key': {'source_game_index': k[0], 'player': k[1],
                                'color': k[2], 'fullmove': k[3], 'played': k[4]},
                        'loss_by_depth': {str(doc['inputs']['depth']): row['cp_loss'] for doc, row in zip(docs, moves)},
                        'difference_last_minus_first': moves[-1]['cp_loss'] - moves[0]['cp_loss'],
                        'classification_changed': (moves[0]['cp_loss'] >= 100) != (moves[-1]['cp_loss'] >= 100) or (moves[0]['cp_loss'] >= 250) != (moves[-1]['cp_loss'] >= 250)})
    ranking = sorted(matched, key=lambda m: (-abs(m['difference_last_minus_first']), str(m['key'])))
    result = {'protocol': 'depth-quality-comparison-v9.1',
              'status': 'EXPLORATORY',
              'inputs': [{'path': str(p), 'depth': doc['inputs']['depth'],
                          'sampled_positions': len(doc['decisions'])} for p, doc in zip(paths, docs)],
              'pgn_sha256': hashes[0],
              'aligned_decisions': len(matched),
              'unmatched_or_ambiguous_by_report': [len(d['decisions']) - len(matched) for d in docs],
              'legacy_collision_counts': collisions,
              'classification_changes': sum(m['classification_changed'] for m in matched),
              'largest_changes': ranking[:20],
              'note': 'Only exact aligned decisions used. Analysis depths do not independently validate move quality.'}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return result
