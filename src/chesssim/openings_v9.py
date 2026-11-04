"""Recorded historical opening choices, with a mandatory Mecking embargo gate.

The repertoire is an optional *early-move policy*. It is not an engine skill
improvement and should be disabled when measuring intrinsic Rodent style.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random

from .calibration import file_hash
from .historical import player_side
from .research_v6 import _cohort_v5_training
from .corpus_v5 import extract_decisions_v5

SCHEMA = 'chesssim-opening-v9'


def _game_id(game) -> str:
    moves = ' '.join(m.uci() for m in game.mainline_moves())
    legacy = sha256(moves.encode()).hexdigest()
    def clean(tag):
        return ' '.join(game.headers.get(tag, '?').casefold().split())
    identity = (legacy, *(clean(tag) for tag in ('Date', 'White', 'Black', 'Event', 'Site', 'Round')))
    return sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def position_token(board) -> str:
    """Use the 4 state-bearing FEN fields, excluding move clocks."""
    return ' '.join(board.fen().split()[:4])


def select_book_move(book: dict, board, *, seed: int = 2029, game_number: int = 1,
                     min_count: int = 1):
    import chess
    if book.get('schema') != SCHEMA:
        raise ValueError('Invalid opening book schema')
    max_plies = book['policy']['max_plies']
    if board.ply() >= max_plies:
        return None
    color = 'white' if board.turn == chess.WHITE else 'black'
    entries = book['positions'][color].get(position_token(board), {})
    choices = []
    for move_text, n in sorted(entries.items()):
        move = chess.Move.from_uci(move_text)
        if n >= min_count and move in board.legal_moves:
            choices.append((move, n))
    if not choices:
        return None
    # Independent of Python's randomized hash; stable with seed and state.
    digest = sha256(f'{seed}:{game_number}:{book["player"]}:{board.fen()}'.encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], 'big'))
    cutoff = rng.randrange(sum(n for _, n in choices))
    for move, n in choices:
        cutoff -= n
        if cutoff < 0:
            return move
    raise AssertionError('weighted selection exhausted')


def build_opening_book(pgn: Path, output: Path, *, slug: str, match_name: str,
                       max_plies: int = 16, from_year: int | None = None,
                       to_year: int | None = None, peak_registry: Path | None = None,
                       v5_report: Path | None = None, legacy_v3: Path | None = None,
                       legacy_pgn: Path | None = None, cohort_ledger: Path | None = None,
                       ack_unpartitioned: bool = False) -> dict:
    if output.exists():
        raise FileExistsError(output)
    if not pgn.is_file():
        raise FileNotFoundError(pgn)
    if not 2 <= max_plies <= 40:
        raise ValueError('max-plies must be 2..40')
    if from_year is not None and to_year is not None and from_year > to_year:
        raise ValueError('Inverted historical window')
    if slug == 'mequinho':
        if not (v5_report and legacy_v3 and legacy_pgn):
            raise ValueError('Mequinho requires original v5 report + v3 embargo + original PGN')
        source_rows, _ = extract_decisions_v5(pgn, player_name=match_name,
                                               from_year=1600, to_year=2100)
        allowed, embargo = _cohort_v5_training(source_rows, report_path=v5_report,
                legacy_v3=legacy_v3, legacy_pgn=legacy_pgn, slug=slug, pgn=pgn,
                registry_path=cohort_ledger)
        permitted_ids = {r['game'] for r in allowed}
        provenance = {'status': 'FROZEN_V5_TRAIN_ONLY', **embargo}
    else:
        if not ack_unpartitioned:
            raise ValueError('Other players require --ack-unpartitioned; not historically validated')
        if v5_report or legacy_v3 or legacy_pgn:
            raise ValueError('Only Mequinho has a supported historic v5 embargo protocol')
        permitted_ids = None
        provenance = {'status': 'PUBLIC_PGN_UNPARTITIONED_EXPLORATORY'}

    import chess.pgn
    position_counts = {'white': defaultdict(Counter), 'black': defaultdict(Counter)}
    accepted, rejected = 0, Counter()
    used_ids = set()
    with pgn.open(encoding='utf-8-sig', errors='replace') as handle:
        while (game := chess.pgn.read_game(handle)) is not None:
            if game.errors:
                rejected['illegal'] += 1
                continue
            player_white = player_side(game, match_name)
            if player_white is None:
                rejected['wrong_player'] += 1
                continue
            gid = _game_id(game)
            if gid in used_ids:
                rejected['duplicate'] += 1
                continue
            used_ids.add(gid)
            if permitted_ids is not None and gid not in permitted_ids:
                rejected['not_in_frozen_training'] += 1
                continue
            date = game.headers.get('Date', '')
            try:
                year = int(date[:4])
            except ValueError:
                rejected['year_unknown'] += 1
                continue
            if (from_year is not None and year < from_year) or (to_year is not None and year > to_year):
                rejected['outside_window'] += 1
                continue
            if game.headers.get('Variant', 'Standard') not in ('Standard', 'Chess'):
                rejected['variant'] += 1
                continue
            board = game.board()
            color = 'white' if player_white else 'black'
            for move in game.mainline_moves():
                if board.ply() >= max_plies:
                    break
                if board.turn == player_white:
                    position_counts[color][position_token(board)][move.uci()] += 1
                board.push(move)
            accepted += 1
    if not accepted:
        raise ValueError('No eligible opening games after embargo and year constraints')
    positions = {c: {fen: dict(sorted(cnt.items())) for fen, cnt in sorted(position_counts[c].items())}
                 for c in ('white', 'black')}
    payload = {'schema': SCHEMA, 'player': slug, 'match_name': match_name,
               'policy': {'max_plies': max_plies, 'never_engine_verified': True},
               'source': {'pgn_sha256': file_hash(pgn), 'from_year': from_year, 'to_year': to_year,
                          'accepted_games': accepted, 'rejected': dict(rejected), 'provenance': provenance},
               'positions': positions}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {'out': str(output), 'player': slug, 'games': accepted,
            'opening_positions': {c: len(positions[c]) for c in positions},
            'provenance': provenance['status'], 'sha256': file_hash(output)}


def load_opening_book(path: Path, slug: str) -> dict:
    doc = json.loads(path.read_text(encoding='utf-8'))
    if doc.get('schema') != SCHEMA or doc.get('player') != slug:
        raise ValueError(f'Opening book player mismatch for {slug}: {path}')
    if not isinstance(doc.get('positions'), dict) or not all(x in doc['positions'] for x in ('white','black')):
        raise ValueError('Incomplete opening book')
    if not 2 <= doc.get('policy', {}).get('max_plies', 0) <= 40:
        raise ValueError('Invalid book ply limit')
    return doc
