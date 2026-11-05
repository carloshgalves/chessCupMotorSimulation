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

from .game_diversity import audit_mainlines, mainline_identity, opening_seed_from_headers

PROTOCOL = "independent-stockfish-postgame-v9.1"


def _sha256_file(path: Path) -> str:
    """Stream a source fingerprint rather than loading the complete PGN/engine."""
    digest = sha256()
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


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


def _reconcile_stats(rows: list[dict], published: dict, *, label: str) -> None:
    """Independent conservation check of values, not only sample counts.

    The primary summary uses statistics.mean/median; this gate independently
    checks raw sums, sorted middle values, classifications and mate flags.
    """
    from math import isclose

    grouped = defaultdict(list)
    for row in rows:
        grouped[row[label]].append(row)
    if set(grouped) != set(published):
        raise RuntimeError(f'Quality audit reconciliation failed: {label} groups')
    for name, decisions in grouped.items():
        stats = published[name]
        losses = sorted(row['cp_loss'] for row in decisions)
        count = len(losses)
        midpoint = count // 2
        expected_median = (losses[midpoint] if count % 2 else
                           (losses[midpoint - 1] + losses[midpoint]) / 2)
        expected = {
            'decisions_analyzed': count,
            'median_centipawn_loss': expected_median,
            'mistakes_over_100cp': sum(loss >= 100 for loss in losses),
            'blunders_over_250cp': sum(loss >= 250 for loss in losses),
            'mate_affected_decisions': sum(bool(d.get('mate_affected')) for d in decisions),
            'negative_unclipped_gaps': sum(d.get('raw_gap_cp', 0) < 0 for d in decisions),
        }
        if any(stats.get(field) != value for field, value in expected.items()):
            raise RuntimeError(f'Quality audit reconciliation failed: {label} metrics')
        if not isclose(stats['mean_centipawn_loss'] * count, sum(losses),
                       rel_tol=1e-12, abs_tol=1e-9):
            raise RuntimeError(f'Quality audit reconciliation failed: {label} mean')


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
    """Always retain mate distance separately from the 1000-cp display mapping."""
    value = info['score'].pov(pov)
    cp = value.score(mate_score=1000)
    if cp is None:
        raise ValueError('Stockfish evaluation has no numeric or mate score')
    return {'cp_equivalent': cp, 'mate_in': value.mate()}


def _validated_pv(board, sequence: list, *, expected_first=None) -> list:
    """Reject foreign/illegal principal variations instead of reporting false lines."""
    import chess
    if not isinstance(sequence, (list, tuple)):
        raise ValueError('Engine PV must be a sequence')
    if expected_first is not None and sequence and sequence[0] != expected_first:
        raise ValueError('Forced search returned a PV for the wrong root move')
    state = board.copy(stack=False)
    for move in sequence:
        if not isinstance(move, chess.Move) or move not in state.legal_moves:
            raise ValueError('Engine returned an illegal or foreign principal variation')
        state.push(move)
    return list(sequence)


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
    pv = _validated_pv(board, best.get('pv') or [])
    forced_pv = _validated_pv(board, forced.get('pv') or [], expected_first=played)
    return {
        'depth': depth,
        'best_move': pv[0].uci() if pv else None,
        'best_move_san': board.san(pv[0]) if pv else None,
        'played_move_san': board.san(played),
        'best_line_uci': [move.uci() for move in pv[:pv_plies]],
        'played_line_uci': [move.uci() for move in forced_pv[:pv_plies]],
        'best_line_san': _pv_san(board, pv, pv_plies),
        'played_line_san': _pv_san(board, forced_pv, pv_plies),
        'pv_availability': {
            'best': 'PRESENT' if pv else 'UNAVAILABLE',
            'forced': 'PRESENT' if forced_pv else 'UNAVAILABLE',
        },
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
                'opening_seed': opening_seed_from_headers(game.headers),
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
    pgn_sha256 = _sha256_file(pgn)
    engine_sha256 = _sha256_file(engine)
    records, games = _load_positions(pgn, min_fullmove=min_fullmove, dedupe_games=dedupe_games)
    if _sha256_file(pgn) != pgn_sha256:
        raise ValueError('PGN changed while selecting quality-analysis positions')
    _, diversity = audit_mainlines(games)
    duplicate_indices = {g['game_index'] for g in games if g['duplicate_mainline']}
    # Audit-critical: independent estimates must be invariant to the
    # --keep-duplicate-games option. Reserve the same unique-game sampling
    # budget first; duplicate diagnostics may use ONLY spare capacity.
    unique_records = [r for r in records if r['game_index'] not in duplicate_indices]
    chosen, sampling = select_decisions(unique_records, limit=max_positions, mode=sample_mode)
    independent_sample_size = len(chosen)
    diagnostic_count = 0
    if not dedupe_games and independent_sample_size < max_positions:
        repeated = [r for r in records if r['game_index'] in duplicate_indices]
        diagnostic_indices = spread_indices(len(repeated), max_positions - independent_sample_size)
        chosen.extend(repeated[index] for index in diagnostic_indices)
        diagnostic_count = len(diagnostic_indices)
        chosen.sort(key=lambda row: row['global_index'])
    sampling['independent_positions_selected'] = independent_sample_size
    sampling['diagnostic_duplicate_positions_selected'] = diagnostic_count
    sampling['actual_positions'] = len(chosen)
    if not dedupe_games:
        sampling['warning'] += (
            ' Duplicates may be inspected within spare max_positions budget, '
            'but never replace independent decisions in aggregate estimates.'
        )
    output = []
    engine_identity = {}
    configured_options = {}
    with chess.engine.SimpleEngine.popen_uci(str(engine), timeout=120) as judge:
        # UCI identity comes from the executable actually launched for analysis.
        # An executable hash alone does not record the runtime engine settings.
        engine_identity = dict(getattr(judge, 'id', {}) or {})
        settings = {}
        if 'Threads' in judge.options:
            settings['Threads'] = 1
        if 'Hash' in judge.options:
            settings['Hash'] = 64
        if settings:
            judge.configure(settings)
        configured_options = settings.copy()
        for row in chosen:
            board = chess.Board(row['fen'])
            move = chess.Move.from_uci(row['played'])
            if move not in board.legal_moves:
                raise ValueError('Illegal move in PGN')
            score = _evaluate_choice(judge, board, move, depth=depth, pv_plies=pv_plies)
            output.append({k: v for k, v in row.items() if k != 'fen'} |
                          {'fen': row['fen'], 'player_color': f"{row['player']}|{row['color']}",
                           **score, 'mistake': score['cp_loss'] >= 100,
                           'blunder': score['cp_loss'] >= 250})
        # Optional targeted search refinement: preserve original metrics for
        # an apples-to-apples comparison; never mix depths into main summary.
        ranked = sorted((r for r in output if r['cp_loss'] >= refine_threshold and
                         r['game_index'] not in duplicate_indices),
                        key=lambda x: (-x['cp_loss'], x['global_index']))
        # Duplicate trajectories may only consume spare refinement capacity;
        # they cannot displace any independent critical decision.
        diagnostic_ranked = sorted(
            (r for r in output if r['cp_loss'] >= refine_threshold and
             r['game_index'] in duplicate_indices),
            key=lambda x: (-x['cp_loss'], x['global_index']))
        chosen_refine = (
            ranked[:refine_max_positions] +
            diagnostic_ranked[:max(0, refine_max_positions - len(ranked))]
            if refine_depth is not None else []
        )
        if chosen_refine:
            from .quality_stability import compare_depth_evaluations
        for row in chosen_refine:
            board = chess.Board(row['fen'])
            refined = _evaluate_choice(judge, board, chess.Move.from_uci(row['played']),
                                       depth=refine_depth, pv_plies=pv_plies)
            row['refined'] = refined
            row['stability'] = compare_depth_evaluations(row, refined)
    # Fail closed if the game evidence or judge executable was replaced during
    # the time-consuming UCI search; never publish mismatched provenance.
    if _sha256_file(pgn) != pgn_sha256 or _sha256_file(engine) != engine_sha256:
        raise ValueError('PGN or Stockfish executable changed during quality analysis')
    by_game = defaultdict(list)
    for item in output:
        by_game[item['game_index']].append(item)
    game_report = []
    for game in games:
        if game['excluded_from_analysis']:
            continue
        rows = by_game.get(game['game_index'], [])
        game_report.append({**game, 'positions_analyzed': len(rows),
                            'diagnostic_only': game['duplicate_mainline'],
                            'by_player': summarize_decisions(rows)})
    # Diagnostic analysis may inspect duplicates without counting them in
    # aggregate independent-evidence estimates.
    independent_decisions = [r for r in output if r['game_index'] not in duplicate_indices]
    global_summary = summarize_decisions(independent_decisions)
    per_player_color = summarize_decisions(independent_decisions, label='player_color')
    # Explicit reconciliation makes sample-level/pairwise summaries auditable:
    # grouping by game or color must not silently include duplicate evidence.
    if sum(row['positions_analyzed'] for row in game_report) != len(output):
        raise RuntimeError('Quality audit reconciliation failed: game-level decisions')
    if sum(stats['decisions_analyzed'] for stats in global_summary.values()) != len(independent_decisions):
        raise RuntimeError('Quality audit reconciliation failed: global player totals')
    if sum(stats['decisions_analyzed'] for stats in per_player_color.values()) != len(independent_decisions):
        raise RuntimeError('Quality audit reconciliation failed: player/color totals')
    for group in game_report:
        if sum(stats['decisions_analyzed'] for stats in group['by_player'].values()) != group['positions_analyzed']:
            raise RuntimeError('Quality audit reconciliation failed: per-game player totals')
        _reconcile_stats(by_game.get(group['game_index'], []),
                         group['by_player'], label='player')
    _reconcile_stats(independent_decisions, global_summary, label='player')
    _reconcile_stats(independent_decisions, per_player_color, label='player_color')
    result = {
        'protocol': PROTOCOL,
        'status': 'EXPLORATORY_STRENGTH_DIAGNOSTIC_NOT_ELO',
        'inputs': {
            'pgn_sha256': pgn_sha256,
            'stockfish_sha256': engine_sha256,
            'depth': depth, 'eligible_positions': len(records),
            'sampled_positions': len(output), 'min_fullmove': min_fullmove,
            'sample_mode': sample_mode, 'dedupe_games': dedupe_games,
            'pv_plies': pv_plies, 'refine_depth': refine_depth,
            'refine_threshold': refine_threshold,
            'refine_max_positions': refine_max_positions,
            'engine_uci_id': engine_identity,
            'engine_configured_options': configured_options,
            'analysis_limit': {'kind': 'depth', 'value': depth},
            'mate_mapping_cp': 1000,
        },
        'sampling': sampling,
        'diversity': diversity,
        'independent_decisions_sampled': len(independent_decisions),
        'duplicate_decisions_excluded_from_summary': len(output) - len(independent_decisions),
        'games': game_report,
        'excluded_duplicate_games': [g for g in games if g['excluded_from_analysis']],
        'summary': global_summary,
        'summary_by_player_color': per_player_color,
        'reconciliation': {
            'game_level_decisions': sum(g['positions_analyzed'] for g in game_report),
            'all_decisions': len(output),
            'independent_decisions': len(independent_decisions),
            'player_summary_decisions': sum(g['decisions_analyzed'] for g in global_summary.values()),
            'color_summary_decisions': sum(g['decisions_analyzed'] for g in per_player_color.values()),
            'diagnostic_duplicate_decisions': len(output) - len(independent_decisions),
            'status': 'CONSISTENT',
        },
        'refinement': {
            'selected': len(chosen_refine),
            'independent_selected': sum(r['game_index'] not in duplicate_indices
                                        for r in chosen_refine),
            'diagnostic_duplicates_selected': sum(r['game_index'] in duplicate_indices
                                                  for r in chosen_refine),
            'classification_changes_independent': sum(
                r['stability']['classification_changed'] for r in chosen_refine
                if r['game_index'] not in duplicate_indices),
            'warning': ('Every refined result remains nested under the original decision; '
                        'base-depth summaries are never replaced with refinement values. '
                        'Duplicated games use only spare refinement budget; '
                        'neither depth establishes an Elo or identity verdict.'),
        },
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
    """Backward-compatible entry point for strict depth-stability reporting."""
    from .quality_stability import compare_quality_reports as compare
    return compare(paths, out)
