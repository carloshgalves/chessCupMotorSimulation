"""Read-only statistics from verified local Arena sessions and tournament ledgers.

Historical PGNs are not simulations. Every counted game must have a legal,
single-game PGN and a consistent persistent summary. A completed tournament
attempt must additionally be registered with matching immutable hashes.
No engines, network access, timestamps inferred from file mtimes or writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import json
import re
import math

import chess.pgn

from .catalog import load_roster
from .history_games import (
    MAX_MANIFEST_BYTES, MAX_PGN_BYTES, MAX_PLIES, InvalidCatalogue, _game, _json,
    _read, _validate_game,
)
from .matches import mainline_identity
from .tournament import SCHEMA as TOURNAMENT_SCHEMA, _check_bracket

SCHEMA = "chesssim-history-stats-v1"
MAX_RUNS = 300
MAX_ATTEMPTS = 800
_RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ARENA_NAME = re.compile(r"session-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}\Z")
_ATTEMPT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_MATCH_NAME = re.compile(r"r[0-9]{2}-m[0-9]{2}\Z")
_RESULTS = {"1-0", "0-1", "1/2-1/2"}
_STATUSES = {"RUNNING", "COMPLETED", "INCOMPLETE", "FAILED", "INTERRUPTED"}


class InvalidStats(ValueError):
    """Unsupported or unsafe local ledger data."""


def _iso_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _paths(workspace: Path) -> tuple[list[Path], list[Path]]:
    """Only discover bounded, named workspace children; never arbitrary paths."""
    sessions = sorted(
        p.relative_to(workspace).parent for p in workspace.glob("session-*/session.json")
    )
    tournaments = sorted({
        p.relative_to(workspace).parent for pattern in (
            "tournament.json", "*/tournament.json", "tournaments/*/tournament.json"
        ) for p in workspace.glob(pattern)
    })
    if len(sessions) + len(tournaments) > MAX_RUNS:
        raise InvalidStats("Número de execuções excede limite de leitura")
    return sessions, tournaments


def _duel_summaries(workspace: Path, session: Path) -> list[Path]:
    root = workspace / session / "duels"
    paths = sorted(
        p.relative_to(workspace) for p in root.glob("*/*/summary.json")
    )
    if len(paths) > MAX_ATTEMPTS:
        raise InvalidStats("Sessão excede limite de tentativas")
    return paths


def _games(workspace: Path, folder: Path, summary: dict, names: dict[str, str],
           *, tournament: bool, combined_raw: bytes | None = None) -> list[tuple[str, str, str, str]]:
    """Validate an entire attempt atomically; a corrupt game rejects the attempt.

    Returns (white_slug, black_slug, status, result) for each verified game.
    """
    players = summary.get("players")
    records = summary.get("games")
    if (not isinstance(players, list) or len(players) != 2 or
            any(type(p) is not str or p not in names for p in players) or
            players[0] == players[1] or not isinstance(records, list) or
            len(records) > 2 or summary.get("status") not in _STATUSES):
        raise InvalidStats("Resumo do confronto inválido")
    if summary["status"] == "COMPLETED" and (
            not records or any(not isinstance(x, dict) or
                               x.get("status") != "COMPLETED" for x in records)):
        raise InvalidStats("Confronto final com jogos não concluídos")
    if tournament and (summary["status"] != "COMPLETED" or len(records) != 2):
        raise InvalidStats("Tentativa de torneio não concluída")
    verified = []
    combined_games = _read_combined(combined_raw) if tournament else None
    for i, entry in enumerate(records, 1):
        if not isinstance(entry, dict):
            raise InvalidStats("Registro de partida inválido")
        status = entry.get("status")
        if status not in ("COMPLETED", "INCOMPLETE"):
            # FAILED/INTERRUPTED without a verifiable PGN are not games.
            if status in ("FAILED", "INTERRUPTED"):
                continue
            raise InvalidStats("Status de jogo desconhecido")
        if tournament:
            game = combined_games[i - 1]
        else:
            # A final incomplete game is often saved as jogo_N.pgn. Partial
            # checkpoints exist only when no final file is available.
            final = folder / f"jogo_{i}.pgn"
            partial = folder / f"jogo_{i}.partial.pgn"
            if status == "COMPLETED":
                target = final
            else:
                target = (final if (workspace / final).exists() or
                          (workspace / final).is_symlink() else partial)
            game = _game(_read(workspace, target, MAX_PGN_BYTES))
        if game is None or game.errors:
            raise InvalidStats("PGN inválido")
        white, black = (entry.get("white"), entry.get("black"))
        if (not isinstance(white, str) or not isinstance(black, str)):
            if tournament:
                white, black = (players if i == 1 else players[::-1])
            else:
                white = next((p for p in players if names[p] == game.headers.get("White")), None)
                black = next((p for p in players if names[p] == game.headers.get("Black")), None)
        if (white not in players or black not in players or white == black or
                tournament and [white, black] !=
                (players if i == 1 else players[::-1])):
            raise InvalidStats("Cores ou jogadores inconsistentes")
        outcome = entry.get("result") if status == "COMPLETED" else None
        if status == "INCOMPLETE" and game.headers.get("Result") != "*":
            raise InvalidStats("Jogo incompleto com resultado final no PGN")
        _validate_game(game, white, black, names, status, outcome)
        board = game.end().board()
        proven = board.outcome(claim_draw=True)
        if status == "COMPLETED" and proven is None:
            if game.headers.get("Termination") != "TimeForfeit":
                raise InvalidStats("Vitória/empate sem condição terminal ou queda de tempo")
            remaining = entry.get("clock_left")
            loser = 0 if board.turn == chess.WHITE else 1
            if (not isinstance(remaining, list) or len(remaining) != 2 or
                    any(type(x) not in (int, float) or not math.isfinite(x)
                        for x in remaining) or remaining[loser] > 0 or
                    outcome != ("1/2-1/2" if board.has_insufficient_material(
                        not board.turn) else "0-1" if board.turn == chess.WHITE
                        else "1-0")):
                raise InvalidStats("Queda de tempo não comprovada")
        elif status == "INCOMPLETE" and proven is not None:
            raise InvalidStats("Jogo terminal classificado como incompleto")
        digest, plies = mainline_identity(game)
        if ((entry.get("mainline_sha256") is not None and
             entry["mainline_sha256"] != digest) or
                (entry.get("plies") is not None and entry["plies"] != plies) or
                (entry.get("termination") is not None and
                 entry["termination"] != game.headers.get("Termination"))):
            raise InvalidStats("Lances ou término divergentes do resumo")
        verified.append((white, black, status, outcome if status == "COMPLETED" else "*"))
    return verified


def _tournament_attempt(workspace: Path, path: Path, state: dict, match: dict,
                        entry: dict, names: dict[str, str]) -> list[tuple[str, str, str, str]]:
    if not isinstance(entry, dict) or not isinstance(entry.get("folder"), str):
        raise InvalidStats("Identificador de tentativa inválido")
    a, b = match["players"]
    prefix = Path("matches") / match["id"] / "duels" / f"{a}-vs-{b}"
    folder = Path(entry["folder"])
    if (folder.parts[:4] != prefix.parts or len(folder.parts) != 5 or
            not _ATTEMPT_NAME.fullmatch(folder.parts[4])):
        raise InvalidStats("Tentativa fora do confronto canônico")
    rel = path / folder
    summary_bytes = _read(workspace, rel / "summary.json", MAX_MANIFEST_BYTES)
    pgn_bytes = _read(workspace, rel / "duelo.pgn", 2 * MAX_PGN_BYTES)
    if (entry.get("summary_sha256") != sha256(summary_bytes).hexdigest() or
            entry.get("pgn_sha256") != sha256(pgn_bytes).hexdigest()):
        raise InvalidStats("Confronto alterado após checkpoint")
    summary = _strict_json(summary_bytes)
    if summary.get("players") != [a, b]:
        raise InvalidStats("Confronto do torneio com participantes errados")
    resources = state.get("resources")
    if (not isinstance(resources, dict) or
            not isinstance(resources.get("profile_sha256"), dict) or
            not isinstance(resources.get("books_by_player"), dict) or
            not isinstance(resources.get("book_sha256"), dict)):
        raise InvalidStats("Identidade dos motores do torneio ausente")
    expected_hashes = {"exe": resources.get("engine_sha256"),
                       a: resources["profile_sha256"].get(a),
                       b: resources["profile_sha256"].get(b)}
    expected_books = {}
    for player in (a, b):
        for key in resources["books_by_player"].get(player, []):
            if key not in resources["book_sha256"]:
                raise InvalidStats("Livro de abertura não registrado")
            expected_books[key] = resources["book_sha256"][key]
    if (not all(type(value) is str and value for value in expected_hashes.values()) or
            summary.get("file_hashes") != expected_hashes or
            summary.get("book_hashes") != expected_books):
        raise InvalidStats("Recursos do confronto divergentes")
    clock = state.get("clock")
    if (not isinstance(clock, list) or len(clock) != 2 or
            any(type(x) is not int for x in clock)):
        raise InvalidStats("Ritmo do torneio inválido")
    from .protocol import OFFICIAL
    protocol = summary.get("protocol")
    if state.get("preset") == "official":
        if not isinstance(protocol, dict) or protocol.get("sha256") != OFFICIAL.json()["sha256"]:
            raise InvalidStats("Protocolo oficial não comprovado")
    elif (not isinstance(protocol, dict) or
          protocol.get("time_control") != f"{clock[0]}+{clock[1]}"):
        raise InvalidStats("Protocolo de tempo divergente")
    games = _read_combined(pgn_bytes)
    for i, game in enumerate(games):
        white, black = (a, b) if i == 0 else (b, a)
        if (game.headers.get("EngineSHA256") != resources["engine_sha256"] or
                game.headers.get("WhiteProfileSHA256") != resources["profile_sha256"][white] or
                game.headers.get("BlackProfileSHA256") != resources["profile_sha256"][black] or
                game.headers.get("TimeControl") != f"{clock[0]}+{clock[1]}"):
            raise InvalidStats("Proveniência do PGN diverge da configuração congelada")
    verified = _games(workspace, rel, summary, names, tournament=True,
                      combined_raw=pgn_bytes)
    if (len(verified) != 2 or entry.get("results") != [g[3] for g in verified]):
        raise InvalidStats("Resultados diferentes do manifesto")
    if (entry.get("mainline_sha256") is not None and
            entry["mainline_sha256"] != [
                mainline_identity(g)[0] for g in _read_combined(pgn_bytes)
            ]):
        raise InvalidStats("Lances diferentes do manifesto")
    # A registered attempt has two games and one duel, regardless of colors.
    return verified


def _strict_json(raw: bytes) -> dict:
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise InvalidStats("JSON contém chaves duplicadas")
            data[key] = value
        return data

    def reject_constant(value):
        raise InvalidStats("JSON contém constante não finita: " + value)

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidStats("JSON de partida inválido") from exc
    if not isinstance(result, dict):
        raise InvalidStats("Resumo não é um objeto")
    return result


def _read_combined(raw: bytes | None) -> list[chess.pgn.Game]:
    if raw is None:
        raise InvalidStats("PGN consolidado ausente")
    try:
        stream = StringIO(raw.decode("utf-8-sig"))
        games = [chess.pgn.read_game(stream), chess.pgn.read_game(stream)]
        if (any(g is None or g.errors or
                g.headers.get("Variant", "Standard") not in ("Standard", "") or
                not g.board().is_valid() or
                sum(1 for _ in g.mainline_moves()) > MAX_PLIES for g in games) or
                chess.pgn.read_game(stream) is not None):
            raise InvalidStats("PGN consolidado inválido")
        return games
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise InvalidStats("PGN de confronto inválido") from exc


def _counter() -> dict[str, int]:
    return {"duels_count": 0, "completed_games": 0, "wins": 0,
            "draws": 0, "losses": 0, "incomplete_games": 0}


def list_stats(workspace: Path, slug: str) -> dict:
    names = {p.slug: p.name for p in load_roster()}
    if slug not in names:
        raise LookupError("Jogador não pertence ao elenco")
    workspace = workspace.expanduser().resolve(strict=True)
    sessions, tournaments = _paths(workspace)
    counts = _counter()
    opponents: dict[str, dict[str, int]] = defaultdict(_counter)
    runs: set[str] = set()
    tourneys: set[str] = set()
    duels: set[str] = set()
    duel_opponents: set[tuple[str, str]] = set()
    seen_attempts: set[str] = set()
    covered = {"runs_scanned": 0, "attempts_scanned": 0,
               "games_accepted": 0, "games_excluded": 0,
               "sources_rejected": 0, "reasons": defaultdict(int)}
    latest = None

    def reject(reason: str, records: int = 0) -> None:
        covered["sources_rejected"] += 1
        covered["games_excluded"] += records
        covered["reasons"][reason] += 1

    def time_marker(value: object) -> None:
        nonlocal latest
        normalized = _iso_time(value)
        if normalized and (latest is None or normalized > latest):
            latest = normalized

    def include(run_id: str, duel_id: str,
                records: list[tuple[str, str, str, str]]) -> None:
        if not records:
            return
        # Check IDs before counting either game in a pair.
        if duel_id in seen_attempts:
            reject("DUPLICATE_ATTEMPT", len(records))
            return
        seen_attempts.add(duel_id)
        involved = [g for g in records if slug in g[:2]]
        if not involved:
            return
        runs.add(run_id)
        if duel_id not in duels:
            duels.add(duel_id)
            counts["duels_count"] += 1
        for white, black, status, result in involved:
            opponent = black if white == slug else white
            key = (duel_id, opponent)
            if key not in duel_opponents:
                duel_opponents.add(key)
                opponents[opponent]["duels_count"] += 1
            target = opponents[opponent]
            covered["games_accepted"] += 1
            if status == "INCOMPLETE":
                counts["incomplete_games"] += 1
                target["incomplete_games"] += 1
                continue
            counts["completed_games"] += 1
            target["completed_games"] += 1
            delta = ("draws" if result == "1/2-1/2" else
                     "wins" if (result == "1-0") == (white == slug) else "losses")
            counts[delta] += 1
            target[delta] += 1

    def counted_attempt() -> None:
        covered["attempts_scanned"] += 1
        if covered["attempts_scanned"] > MAX_ATTEMPTS:
            raise InvalidStats("Tentativas excedem limite de leitura")

    for session in sessions:
        covered["runs_scanned"] += 1
        run_id = f"arena:{session.as_posix()}"
        try:
            meta = _json(workspace, session / "session.json", MAX_MANIFEST_BYTES)
            if (not _ARENA_NAME.fullmatch(session.name) or
                    meta.get("schema") != "chesssim-arena-session-v1" or
                    meta.get("id") != session.name or
                    not isinstance(meta.get("config"), dict) or
                    type(meta["config"].get("white")) is not str or
                    type(meta["config"].get("black")) is not str or
                    meta["config"]["white"] == meta["config"]["black"] or
                    any(meta["config"][k] not in names for k in ("white", "black"))):
                raise InvalidStats("Sessão da Arena inválida")
            if slug in (meta["config"]["white"], meta["config"]["black"]):
                runs.add(run_id)
                time_marker(meta.get("created_utc"))
            paths = _duel_summaries(workspace, session)
        except (InvalidCatalogue, InvalidStats, OSError, KeyError, TypeError, ValueError):
            reject("INVALID_ARENA_SESSION")
            continue
        for summary_path in paths:
            counted_attempt()
            path = summary_path.parent
            if (len(summary_path.parts) != 5 or
                    not _ATTEMPT_NAME.fullmatch(path.name) or
                    path.parts[-3] != "duels" or
                    path.parts[-2] !=
                    f"{meta['config']['white']}-vs-{meta['config']['black']}"):
                reject("INVALID_ARENA_PATH")
                continue
            try:
                summary = _json(workspace, summary_path, MAX_MANIFEST_BYTES)
                if summary.get("players") != [
                    meta["config"]["white"], meta["config"]["black"]
                ]:
                    raise InvalidStats("Participantes divergentes")
                records = _games(workspace, path, summary, names, tournament=False)
            except (InvalidCatalogue, InvalidStats, OSError, ValueError,
                    KeyError, TypeError, RecursionError):
                reject("INVALID_ARENA_ATTEMPT", 1)
                continue
            include(run_id, f"{run_id}:{path.as_posix()}", records)

    for root in tournaments:
        covered["runs_scanned"] += 1
        run_id = f"tournament:{root.as_posix()}"
        try:
            state = _json(workspace, root / "tournament.json", MAX_MANIFEST_BYTES)
            if (state.get("schema") != TOURNAMENT_SCHEMA or
                    not isinstance(state.get("participants"), list) or
                    any(type(p) is not str or p not in names
                        for p in state["participants"])):
                raise InvalidStats("Torneio desconhecido")
            _check_bracket(state)
            if slug in state["participants"]:
                runs.add(run_id)
                tourneys.add(run_id)
                time_marker(state.get("created_utc"))
        except (InvalidCatalogue, InvalidStats, OSError, ValueError, KeyError, TypeError,
                IndexError, RecursionError):
            reject("INVALID_TOURNAMENT")
            continue
        for rd in state["rounds"]:
            for match in rd["matches"]:
                entries = match["attempts"]
                for entry in entries:
                    counted_attempt()
                    try:
                        verified = _tournament_attempt(
                            workspace, root, state, match, entry, names
                        )
                    except (InvalidCatalogue, InvalidStats, OSError, ValueError,
                            KeyError, TypeError, IndexError, RecursionError):
                        reject("INVALID_TOURNAMENT_ATTEMPT", 2)
                        continue
                    include(run_id, f"{run_id}:{entry['folder']}", verified)
                # Unregistered/failed attempts and byes must not be counted.
                # No filesystem discovery here: only confirmed ledger entries.
    return {
        "schema": SCHEMA,
        "slug": slug,
        "source_scope": {
            "kind": "configured_arena_workspace",
            "included": ["session-*/session.json", "*/tournament.json",
                         "tournaments/*/tournament.json"],
            "historical_pgns_included": False,
        },
        "run_count": len(runs),
        "tournaments_participated": len(tourneys),
        **counts,
        "head_to_head": [
            {"opponent_slug": key, **value} for key, value
            in sorted(opponents.items())
        ],
        "coverage": {**covered, "reasons": dict(sorted(covered["reasons"].items())),
                     "complete": covered["sources_rejected"] == 0},
        "updated_at": latest,
    }
