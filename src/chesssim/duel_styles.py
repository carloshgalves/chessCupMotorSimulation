"""Experimental high-depth duels with independently documented openings."""
from __future__ import annotations

from pathlib import Path
import json

from .catalog import Player, find_player
from .opening_books import load_opening_book
from .provisional_profiles import PROVISIONAL_MECKING_ALIAS, PROVISIONAL_MECKING_FILENAME


def _participant(name: str) -> Player:
    if name == 'mequinho-v08':
        return Player(number=0, name='Henrique Mecking (v0.8 provisório)', slug='mequinho-v08',
                      group='experimental', rodent_alias=PROVISIONAL_MECKING_ALIAS,
                      rodent_file=PROVISIONAL_MECKING_FILENAME)
    p = find_player(name)
    if not p.rodent_alias:
        raise ValueError(f'{p.name} ainda não possui perfil Rodent registrado')
    return p


def play_styles(root: Path, output: Path, *, white_name: str, black_name: str,
                white_book: Path | None = None, black_book: Path | None = None,
                depth: int | None = 10, movetime: float | None = None,
                rounds: int = 2, seed: int = 2029, opening_min_count: int = 1,
                max_plies: int = 400, replay: bool = True) -> dict:
    from .matches import play_duel, file_hash
    from .replay_html import export_replay_html

    white, black = _participant(white_name), _participant(black_name)
    if white.slug == black.slug:
        raise ValueError('Choose distinct participants')
    if not 1 <= rounds <= 20:
        raise ValueError('Rounds must be between 1 and 20 pairs')
    if (depth is None) == (movetime is None):
        raise ValueError('Choose exactly one of depth/movetime')
    if depth is not None and depth < 1:
        raise ValueError('Depth must be positive')
    if movetime is not None and movetime <= 0:
        raise ValueError('Movetime must be positive')

    books = {}
    book_provenance = {}
    for player, path in ((white, white_book), (black, black_book)):
        if path is None:
            continue
        book = load_opening_book(path, 'mequinho' if player.slug == 'mequinho-v08' else player.slug)
        books[player.slug] = book
        book_provenance[player.slug] = {
            'path': str(path), 'sha256': file_hash(path),
            'status': book['source']['provenance']['status'],
            'games': book['source']['accepted_games'],
        }

    results = []
    for index in range(rounds):
        # One call per pair, always reverse colors for symmetry.
        folder = play_duel(root, output, white, black,
                           depth=depth, movetime=movetime, reverse=True,
                           max_plies=max_plies, use_books=False,
                           historical_openings=books or None,
                           opening_seed=seed + index, opening_min_count=opening_min_count)
        summary_path = folder / 'summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        summary['v9'] = {
            'label': 'EXPERIMENTAL_STYLE_DUEL_NOT_HISTORICALLY_VALIDATED',
            'opening_books': book_provenance,
            'opening_min_count': opening_min_count,
            'opening_seed': seed + index,
            'round_pair': index + 1,
            'analysis': 'use analyze-games (independent Stockfish) and style-audit; not automatic',
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        replay_path = folder / 'replay.html'
        if replay:
            export_replay_html(folder / 'duelo.pgn', replay_path)
        results.append({'round_pair': index + 1, 'folder': str(folder),
                        'summary': str(summary_path), 'pgn': str(folder / 'duelo.pgn'),
                        'replay': str(replay_path) if replay else None,
                        'results': [g['result'] for g in summary['games']]})
    return {'status': 'COMPLETED', 'players': [white.slug, black.slug],
            'model': 'Rodent IV', 'historically_validated': False, 'pairs': results}
