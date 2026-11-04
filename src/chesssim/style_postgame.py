"""Descriptive, color- and phase-aware stylistic fingerprints of played games.

A feature gap is not an identity classifier. Multiple games, matched openings
and a separate strength benchmark are required for plausible reconstruction.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

FEATURES = ('capture', 'check', 'pawn_move', 'queen_move', 'king_move',
            'knight_move', 'bishop_move', 'rook_move', 'castle', 'promotion')


def fingerprint(pgn: Path, output: Path, *, opening_plies: int = 16) -> dict:
    import chess
    import chess.pgn
    if output.exists():
        raise FileExistsError(output)
    if not pgn.is_file():
        raise FileNotFoundError(pgn)
    if not 0 <= opening_plies <= 40:
        raise ValueError('Invalid opening_plies')
    per_player = defaultdict(lambda: {'games': set(), 'mid': Counter(), 'opening': Counter(),
                                      'by_game': defaultdict(Counter), 'first_moves': Counter()})
    games_count = 0
    duplicate_first_8 = Counter()
    with pgn.open(encoding='utf-8-sig', errors='replace') as stream:
        while (g := chess.pgn.read_game(stream)) is not None:
            if g.errors:
                raise ValueError('Invalid PGN')
            games_count += 1
            label = (g.headers.get('White', 'White'), g.headers.get('Black', 'Black'))
            gid = games_count
            board = g.board()
            opening_uci = []
            for move in g.mainline_moves():
                current = label[0] if board.turn else label[1]
                slot = per_player[current]
                slot['games'].add(gid)
                data = slot['opening' if board.ply() < opening_plies else 'mid']
                data['moves'] += 1
                if board.is_capture(move):
                    data['capture'] += 1
                if board.gives_check(move):
                    data['check'] += 1
                if board.is_castling(move):
                    data['castle'] += 1
                if move.promotion:
                    data['promotion'] += 1
                piece = board.piece_at(move.from_square)
                if piece:
                    feature = {
                        chess.PAWN: 'pawn_move', chess.QUEEN: 'queen_move',
                        chess.KING: 'king_move', chess.KNIGHT: 'knight_move',
                        chess.BISHOP: 'bishop_move', chess.ROOK: 'rook_move',
                    }.get(piece.piece_type)
                    if feature:
                        data[feature] += 1
                if board.ply() < 8:
                    opening_uci.append(move.uci())
                    if board.ply() < 2:
                        slot['first_moves'][move.uci()] += 1
                if board.ply() >= opening_plies:
                    slot['by_game'][gid]['moves'] += 1
                    for feature in FEATURES:
                        if feature == 'capture' and board.is_capture(move):
                            slot['by_game'][gid][feature] += 1
                        elif feature == 'check' and board.gives_check(move):
                            slot['by_game'][gid][feature] += 1
                        elif feature == 'castle' and board.is_castling(move):
                            slot['by_game'][gid][feature] += 1
                        elif feature == 'promotion' and move.promotion:
                            slot['by_game'][gid][feature] += 1
                        elif piece and feature == {chess.PAWN: 'pawn_move', chess.QUEEN: 'queen_move',
                            chess.KING: 'king_move', chess.KNIGHT: 'knight_move',
                            chess.BISHOP: 'bishop_move', chess.ROOK: 'rook_move'}.get(piece.piece_type):
                            slot['by_game'][gid][feature] += 1
                board.push(move)
            duplicate_first_8[' '.join(opening_uci)] += 1
    if not games_count:
        raise ValueError('No games found')

    result_players = {}
    for player, stat in sorted(per_player.items()):
        stage = {}
        for name, data in [('opening', stat['opening']), ('middlegame_plus', stat['mid'])]:
            total = data['moves']
            stage[name] = {'moves': total, 'rates': {x: round(data[x] / total, 5) if total else None for x in FEATURES}}
        per_game = list(stat['by_game'].values())
        macro = {x: round(sum(g[x]/g['moves'] for g in per_game if g['moves']) / len(per_game), 5)
                 if per_game else None for x in FEATURES}
        result_players[player] = {'games': len(stat['games']), 'stages': stage,
                                  'per_game_macro_middlegame': macro,
                                  'first_move_counts': dict(stat['first_moves'])}
    sorted_names = sorted(result_players)
    pairwise = []
    for i, a in enumerate(sorted_names):
        for b in sorted_names[i+1:]:
            one = result_players[a]['per_game_macro_middlegame']
            two = result_players[b]['per_game_macro_middlegame']
            gap = {f: round(one[f]-two[f], 5) for f in FEATURES if one[f] is not None and two[f] is not None}
            pairwise.append({'a': a, 'b': b, 'descriptive_absolute_rate_gap_mean':
                             round(sum(abs(v) for v in gap.values()) / len(gap), 5) if gap else None,
                             'feature_rate_differences': gap})
    report = {'protocol': 'descriptive-simulated-style-v9',
              'pgn_sha256': sha256(pgn.read_bytes()).hexdigest(),
              'games': games_count, 'opening_plies': opening_plies,
              'distinct_first_eight_sequences': len(duplicate_first_8),
              'most_common_first_eight': [{'uci': k, 'games': v} for k, v in duplicate_first_8.most_common(10)],
              'players': result_players, 'pairwise': pairwise,
              'warning': 'Descriptive statistics only; no claim of blind identifiability or historical fidelity.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report
