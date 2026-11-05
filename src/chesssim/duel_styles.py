"""Experimental high-depth duels with independently documented openings."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import uuid

from .game_diversity import audit_mainlines

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

    # Series-level container makes cross-pair deduplication persistent. Each
    # pair still retains its own PGNs and raw game summary for replay.
    series_root = (output.expanduser().resolve() /
                   ("style-duels-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    + "-" + uuid.uuid4().hex[:8]))
    series_root.mkdir(parents=True, exist_ok=False)
    series_report = series_root / "series-report.json"
    results = []
    observations = []
    diversity = audit_mainlines([])[1]
    status = "RUNNING"
    error = None

    def checkpoint():
        document = {
            "status": status,
            "players": [white.slug, black.slug],
            "model": "Rodent IV",
            "historically_validated": False,
            "series_root": str(series_root),
            "planned_pairs": rounds,
            "pairs_completed": len(results),
            "seed_base": seed,
            "seed_policy": "base seed plus zero-based pair index; effective only when historical openings are enabled",
            "opening_books": book_provenance,
            "diversity": diversity,
            "pairs": results,
        }
        if error is not None:
            document["error"] = error
        from .matches import _atomic_text
        _atomic_text(series_report, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return document

    checkpoint()
    try:
        for index in range(rounds):
            pair_seed = seed + index
            folder = play_duel(root, series_root, white, black,
                               depth=depth, movetime=movetime, reverse=True,
                               max_plies=max_plies, use_books=False,
                               historical_openings=books or None,
                               opening_seed=pair_seed, opening_min_count=opening_min_count)
            summary_path = folder / 'summary.json'
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            summary['v9'] = {
                'label': 'EXPERIMENTAL_STYLE_DUEL_NOT_HISTORICALLY_VALIDATED',
                'opening_books': book_provenance,
                'opening_min_count': opening_min_count,
                'opening_seed': pair_seed,
                'round_pair': index + 1,
                'analysis': 'use analyze-games (independent Stockfish) and style-audit; not automatic',
            }
            from .matches import _atomic_text
            _atomic_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
            replay_path = folder / 'replay.html'
            # Store the raw-PGN checkpoint BEFORE the optional replay export:
            # a failed HTML renderer must not make an already played pair
            # disappear from the recovered series manifest.
            observations.extend({
                "source": f"pair_{index + 1}/game_{game['round']}",
                "mainline_sha256": game["mainline_sha256"],
                "plies": game["plies"],
                "opening_seed": game.get("opening_seed"),
                "opening_mode": game.get("opening_mode"),
                "color_order": game.get("color_order"),
                "game_status": game["status"],
                "result": game["result"],
            } for game in summary['games'])
            annotated, diversity = audit_mainlines(observations)
            results.append({
                'round_pair': index + 1, 'folder': str(folder),
                'summary': str(summary_path), 'pgn': str(folder / 'duelo.pgn'),
                'replay': None,
                'scheduled_opening_seed': pair_seed,
                'opening_seed_effective': bool(books),
                'results': [game['result'] for game in summary['games']],
                'game_evidence': annotated[-len(summary['games']):] if summary['games'] else [],
                'pair_status': summary['status'],
            })
            checkpoint()
            if replay:
                export_replay_html(folder / 'duelo.pgn', replay_path)
                results[-1]['replay'] = str(replay_path)
                checkpoint()
        status = ("COMPLETED" if all(pair["pair_status"] == "COMPLETED" for pair in results)
                  else "INCOMPLETE")
    except BaseException as exc:
        status = "FAILED"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report = checkpoint()
    return report
