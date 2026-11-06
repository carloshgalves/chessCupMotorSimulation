"""Read-only, bounded local competition statistics for the history profile.

Only canonical local Arena sessions and tournament checkpoint ledgers are
eligible. Every counted result is reconciled with a legal PGN; historical
curated games are explicitly excluded. Corrupt records fail independently
and are reported as excluded coverage rather than fabricated outcomes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re

import chess
import chess.pgn

from .catalog import load_roster
from .game_diversity import mainline_identity
from .history_games import (
    InvalidCatalogue, MAX_MANIFEST_BYTES, MAX_PGN_BYTES, _game, _json, _read,
    _validate_game,
)
from .history_profiles import _player
from .tournament import SCHEMA as CUP_SCHEMA, _check_bracket

SCHEMA = "chesssim-player-stats-v1"
MAX_SESSIONS = 1000
MAX_TOURNAMENTS = 100
MAX_SUMMARIES = 2000
MAX_GAMES = 4000
SESSION_ID = re.compile(r"session-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}\Z")
ATTEMPT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}\Z")
RESULTS = frozenset({"1-0", "0-1", "1/2-1/2"})
COUNTERS = ("games", "completed", "wins", "draws", "losses", "incomplete")


def _timestamp(value: object) -> str | None:
    """Only timestamps explicitly recorded with UTC offset are exposed."""
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.isoformat()
    except (ValueError, OverflowError):
        return None


def _mark(coverage: Counter, reason: str) -> None:
    coverage["rejected_records"] += 1
    coverage["rejected_" + reason] += 1


def _validated_result(game: chess.pgn.Game, white: str, black: str,
                      names: dict[str, str], status: str, result: str | None,
                      meta: dict) -> None:
    _validate_game(game, white, black, names, status, result)
    if status == "INCOMPLETE":
        if game.headers.get("Result", "*") != "*":
            raise InvalidCatalogue("PGN incompleto declara resultado final")
    else:
        if result not in RESULTS:
            raise InvalidCatalogue("Resultado inválido")
    if "plies" in meta and meta["plies"] != game.end().board().ply():
        raise InvalidCatalogue("Número de lances contraditório")
    if "mainline_sha256" in meta:
        identity, _ = mainline_identity(game)
        if identity != meta["mainline_sha256"]:
            raise InvalidCatalogue("Assinatura da partida divergente")
    # A recorded completion may be a resignation or timeout. When there is
    # a terminal board state, its winner must still agree with the summary.
    outcome = game.end().board().outcome(claim_draw=True)
    if outcome is not None and status == "COMPLETED" and outcome.result() != result:
        raise InvalidCatalogue("Resultado contradiz o tabuleiro")


def _arena_games(root: Path, names: dict[str, str], slug: str,
                 coverage: Counter, seen: set[str], records: list[dict],
                 dates: list[str]) -> None:
    manifests = sorted(root.glob("session-*/session.json"))
    if len(manifests) > MAX_SESSIONS:
        raise InvalidCatalogue("Limite de sessões excedido")
    for manifest in manifests:
        relative = manifest.relative_to(root)
        sid = relative.parts[0]
        coverage["session_manifests"] += 1
        try:
            if not SESSION_ID.fullmatch(sid):
                raise InvalidCatalogue("ID de sessão inválido")
            session = _json(root, relative, MAX_MANIFEST_BYTES)
            config = session.get("config")
            if (session.get("schema") != "chesssim-arena-session-v1"
                    or session.get("id") != sid or not isinstance(config, dict)
                    or config.get("white") not in names
                    or config.get("black") not in names
                    or config["white"] == config["black"]):
                raise InvalidCatalogue("Sessão inconsistente")
            participants = [config["white"], config["black"]]
            if slug not in participants:
                continue
            when = _timestamp(session.get("created_utc"))
            if when:
                dates.append(when)
            summaries = sorted((root / sid / "duels").glob("*/*/summary.json"))
            if len(summaries) > 1:
                # A session launches exactly one duel. Multiple summaries are
                # ambiguous, even if one happens to be a valid older record.
                _mark(coverage, "ambiguous_session")
                continue
            for item in summaries:
                rel = item.relative_to(root)
                if (len(rel.parts) != 5 or rel.parts[1] != "duels"
                        or rel.parts[2] != "-vs-".join(participants)
                        or not ATTEMPT.fullmatch(rel.parts[3])):
                    _mark(coverage, "invalid_session_path")
                    continue
                try:
                    summary = _json(root, rel, MAX_MANIFEST_BYTES)
                    games = summary.get("games")
                    if (summary.get("players") != participants
                            or not isinstance(games, list) or len(games) > 2
                            or summary.get("status") not in
                            {"RUNNING", "COMPLETED", "INCOMPLETE", "FAILED"}):
                        raise InvalidCatalogue("Resumo inválido")
                    if summary["status"] == "FAILED":
                        coverage["failed_attempts"] += 1
                        continue
                    if summary["status"] == "COMPLETED" and (
                            len(games) != (2 if config.get("reverse") else 1)
                            or any(not isinstance(g, dict) or
                                   g.get("status") != "COMPLETED" for g in games)):
                        raise InvalidCatalogue("Sessão final sem partidas completas")
                    verified = []
                    for idx, meta in enumerate(games, 1):
                        coverage["candidate_games"] += 1
                        try:
                            if not isinstance(meta, dict) or meta.get("status") not in {
                                "COMPLETED", "INCOMPLETE",
                            }:
                                raise InvalidCatalogue("Estado de jogo inconsistente")
                            status = meta["status"]
                            final = rel.parent / f"jogo_{idx}.pgn"
                            partial = rel.parent / f"jogo_{idx}.partial.pgn"
                            # A stopped game can have a final file with status
                            # INCOMPLETE, or an earlier partial checkpoint.
                            target = final if status == "COMPLETED" or (
                                (root / final).exists() or (root / final).is_symlink()
                            ) else partial
                            game = _game(_read(root, target, MAX_PGN_BYTES))
                            white, black = (participants if idx == 1
                                            else participants[::-1])
                            result = meta.get("result") if status == "COMPLETED" else None
                            _validated_result(game, white, black, names, status, result, meta)
                            if status == "INCOMPLETE" and meta.get("result") not in (None, "*"):
                                raise InvalidCatalogue("Resultado de interrupção indevido")
                            verified.append({
                                "id": "arena:" + sid + ":" + rel.parts[3] + ":" + str(idx),
                                "duel_id": "arena:" + sid, "white": white, "black": black,
                                "status": status, "result": result,
                                "origin": "arena", "recorded_utc": when,
                            })
                        except (InvalidCatalogue, OSError, ValueError, KeyError, TypeError):
                            coverage["rejected_games"] += 1
                    for game_record in verified:
                        _record(game_record, seen, records, coverage)
                except (InvalidCatalogue, ValueError, KeyError, TypeError, OSError):
                    _mark(coverage, "arena_summary")
        except (InvalidCatalogue, ValueError, KeyError, TypeError, OSError):
            _mark(coverage, "arena_manifest")


def _tournament_game_stream(raw: bytes) -> list[chess.pgn.Game]:
    try:
        stream = StringIO(raw.decode("utf-8-sig"))
        games = []
        for _ in range(2):
            game = chess.pgn.read_game(stream)
            if (game is None or game.errors or not game.board().is_valid()
                    or game.headers.get("Variant", "Standard") not in ("Standard", "")
                    or sum(1 for _ in game.mainline_moves()) > 2000):
                raise InvalidCatalogue("PGN de torneio inválido")
            games.append(game)
        if chess.pgn.read_game(stream) is not None:
            raise InvalidCatalogue("PGN de torneio excede dois jogos")
        return games
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise InvalidCatalogue("PGN de torneio ilegível") from exc


def _tournament_attempt(root: Path, run: Path, state: dict,
                        match: dict, attempt: dict, names: dict[str, str],
                        date: str | None) -> list[dict]:
    a, b = match["players"]
    rel = attempt.get("folder")
    prefix = Path("matches") / match["id"] / "duels" / (a + "-vs-" + b)
    if (not isinstance(rel, str) or "\\" in rel or
            not rel.startswith(prefix.as_posix() + "/")):
        raise InvalidCatalogue("Diretório de tentativa inválido")
    parts = Path(rel).parts
    if (len(parts) != 5 or Path(*parts[:4]) != prefix or
            not ATTEMPT.fullmatch(parts[4])):
        raise InvalidCatalogue("Caminho da tentativa contraditório")
    relative = run / Path(rel)
    summary_raw = _read(root, relative / "summary.json", MAX_MANIFEST_BYTES)
    pgn_raw = _read(root, relative / "duelo.pgn", 2 * MAX_PGN_BYTES)
    if (sha256(summary_raw).hexdigest() != attempt.get("summary_sha256")
            or sha256(pgn_raw).hexdigest() != attempt.get("pgn_sha256")):
        raise InvalidCatalogue("Hashes do ledger e dos arquivos divergem")
    summary = _json(root, relative / "summary.json", MAX_MANIFEST_BYTES)
    meta_games = summary.get("games")
    if (summary.get("status") != "COMPLETED" or summary.get("players") != [a, b]
            or not isinstance(meta_games, list) or len(meta_games) != 2
            or not isinstance(attempt.get("results"), list)
            or len(attempt["results"]) != 2):
        raise InvalidCatalogue("Resumo de torneio incompatível")
    resources = state["resources"]
    hashes = resources["profile_sha256"]
    if summary.get("file_hashes") != {
        "exe": resources["engine_sha256"], a: hashes[a], b: hashes[b],
    }:
        raise InvalidCatalogue("Motor ou perfis divergentes")
    expected_books = {
        name: resources["book_sha256"][name]
        for name in set(resources["books_by_player"][a]) |
                    set(resources["books_by_player"][b])
    }
    if summary.get("book_hashes") != expected_books:
        raise InvalidCatalogue("Livro de aberturas incompatível")
    if state["preset"] == "official":
        from .protocol import OFFICIAL
        if summary.get("protocol", {}).get("sha256") != OFFICIAL.json()["sha256"]:
            raise InvalidCatalogue("Protocolo do torneio divergente")
    elif summary.get("protocol", {}).get("time_control") != (
            f"{state['clock'][0]}+{state['clock'][1]}"):
        raise InvalidCatalogue("Tempo do torneio divergente")
    games = _tournament_game_stream(pgn_raw)
    records = []
    for idx, (meta, game) in enumerate(zip(meta_games, games), 1):
        white, black = ((a, b) if idx == 1 else (b, a))
        result = attempt["results"][idx - 1]
        if (not isinstance(meta, dict) or meta.get("status") != "COMPLETED"
                or meta.get("result") != result
                or meta.get("termination") != game.headers.get("Termination")
                or game.headers.get("EngineSHA256") != resources["engine_sha256"]
                or game.headers.get("WhiteProfileSHA256") != hashes[white]
                or game.headers.get("BlackProfileSHA256") != hashes[black]
                or game.headers.get("TimeControl") !=
                f"{state['clock'][0]}+{state['clock'][1]}"):
            raise InvalidCatalogue("Proveniência do torneio inconsistente")
        _validated_result(game, white, black, names, "COMPLETED", result, meta)
        if meta["termination"] != "TimeForfeit":
            outcome = game.end().board().outcome(claim_draw=True)
            if outcome is None or outcome.result() != result:
                raise InvalidCatalogue("Resultado não terminado no tabuleiro")
        else:
            clocks = meta.get("clock_left")
            loser_index = 0 if game.end().board().turn else 1
            if (not isinstance(clocks, list) or len(clocks) != 2 or
                    not isinstance(clocks[loser_index], (int, float))
                    or clocks[loser_index] > 0):
                raise InvalidCatalogue("Tempo excedido sem evidência")
        identity, plies = mainline_identity(game)
        if (meta.get("mainline_sha256") != identity or
                meta.get("plies") != plies or
                attempt.get("mainline_sha256", [None, None])[idx - 1] != identity):
            raise InvalidCatalogue("Trajetória do ledger divergente")
        records.append({
            "id": "tournament:" + run.as_posix() + ":" + match["id"] +
                  ":" + parts[-1] + ":" + str(idx),
            "duel_id": "tournament:" + run.as_posix() + ":" +
                       match["id"] + ":" + parts[-1],
            "white": white, "black": black, "status": "COMPLETED",
            "result": result, "origin": "tournament", "recorded_utc": date,
        })
    return records


def _tournament_games(root: Path, names: dict[str, str], slug: str,
                      coverage: Counter, seen: set[str], records: list[dict],
                      dates: list[str], tournaments: set[str]) -> None:
    # User-owned tournament directories can be placed directly below --out
    # or under --out/tournaments. Do not recursively crawl arbitrary folders.
    paths = sorted(set(
        root.glob("*/tournament.json")
    ) | set(root.glob("tournaments/*/tournament.json")) |
        ({root / "tournament.json"} if (root / "tournament.json").exists() else set()))
    if len(paths) > MAX_TOURNAMENTS:
        raise InvalidCatalogue("Limite de torneios excedido")
    for manifest in paths:
        relative = manifest.relative_to(root)
        run = relative.parent
        coverage["tournament_manifests"] += 1
        try:
            state = _json(root, relative, MAX_MANIFEST_BYTES)
            if state.get("schema") != CUP_SCHEMA:
                raise InvalidCatalogue("Versão de torneio incompatível")
            _check_bracket(state)
            participants = state["participants"]
            if (not isinstance(participants, list) or
                    any(x not in names for x in participants)):
                raise InvalidCatalogue("Participantes desconhecidos")
            if slug not in participants:
                continue
            run_id = run.as_posix()
            tournaments.add(run_id)
            when = _timestamp(state.get("created_utc"))
            if when:
                dates.append(when)
            for rd in state["rounds"]:
                for match in rd["matches"]:
                    if slug not in match["players"]:
                        continue
                    grouped = {}
                    conflicts = set()
                    for attempt in match.get("attempts", []):
                        if not isinstance(attempt, dict) or not isinstance(
                                attempt.get("folder"), str):
                            _mark(coverage, "tournament_attempt")
                            continue
                        folder = attempt["folder"]
                        if folder in grouped and grouped[folder] != attempt:
                            conflicts.add(folder)
                        else:
                            grouped[folder] = attempt
                    coverage["excluded_attempts"] += len(match.get("excluded_attempts", []))
                    for folder, attempt in grouped.items():
                        coverage["candidate_games"] += 2
                        if folder in conflicts:
                            coverage["rejected_games"] += 2
                            _mark(coverage, "duplicate_attempt_conflict")
                            continue
                        try:
                            game_records = _tournament_attempt(
                                root, run, state, match, attempt, names, when,
                            )
                            for record in game_records:
                                _record(record, seen, records, coverage)
                        except (InvalidCatalogue, OSError, ValueError, TypeError,
                                KeyError, IndexError, AttributeError):
                            coverage["rejected_games"] += 2
                            _mark(coverage, "tournament_attempt")
        except (InvalidCatalogue, OSError, ValueError, TypeError, KeyError,
                IndexError, AttributeError):
            _mark(coverage, "tournament_manifest")


def _record(game: dict, seen: set[str], records: list[dict],
            coverage: Counter) -> None:
    if game["id"] in seen:
        coverage["duplicate_records"] += 1
        return
    if len(seen) >= MAX_GAMES:
        raise InvalidCatalogue("Limite de partidas excedido")
    seen.add(game["id"])
    records.append(game)
    coverage["verified_games"] += 1


def _tally(player: str, records: list[dict], tournaments: set[str]) -> tuple[dict, list[dict]]:
    totals = dict.fromkeys(COUNTERS, 0)
    totals.update({"tournaments": len(tournaments), "duels": 0})
    rivals = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))
    duel_ids = set()
    for record in records:
        if player not in (record["white"], record["black"]):
            continue
        rival = record["black"] if record["white"] == player else record["white"]
        if rival == player:
            continue
        duel_ids.add(record["duel_id"])
        for collection in (totals, rivals[rival]):
            collection["games"] += 1
            if record["status"] != "COMPLETED":
                collection["incomplete"] += 1
                continue
            collection["completed"] += 1
            if record["result"] == "1/2-1/2":
                collection["draws"] += 1
            elif (record["result"] == "1-0") == (record["white"] == player):
                collection["wins"] += 1
            else:
                collection["losses"] += 1
    totals["duels"] = len(duel_ids)
    opponents = [{"slug": opponent, **values}
                 for opponent, values in sorted(
                     rivals.items(), key=lambda item: (-item[1]["games"], item[0]))]
    return totals, opponents


def player_stats(workspace: Path, slug: str) -> dict:
    _player(slug)
    root = workspace.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise InvalidCatalogue("Diretório de estatísticas inválido")
    names = {p.slug: p.name for p in load_roster()}
    coverage = Counter()
    records = []
    seen = set()
    dates = []
    tournaments = set()
    _arena_games(root, names, slug, coverage, seen, records, dates)
    _tournament_games(root, names, slug, coverage, seen, records, dates, tournaments)
    totals, opponents = _tally(slug, records, tournaments)
    return {
        "schema": SCHEMA, "slug": slug,
        "scope": {
            "directory": root.name,
            "sources": ["arena_local", "tournament_local"],
            "from_utc": min(dates) if dates else None,
            "to_utc": max(dates) if dates else None,
            "date_basis": "created_utc from persisted manifest, not game duration",
        },
        "totals": totals, "opponents": opponents,
        "coverage": {
            "sessions_seen": coverage["session_manifests"],
            "tournaments_seen": coverage["tournament_manifests"],
            "candidate_games": coverage["candidate_games"],
            "verified_games": coverage["verified_games"],
            "rejected_games": coverage["rejected_games"],
            "rejected_records": coverage["rejected_records"],
            "excluded_attempts": coverage["excluded_attempts"],
            "failed_attempts": coverage["failed_attempts"],
            "duplicate_records": coverage["duplicate_records"],
            "partial": bool(coverage["rejected_records"] or coverage["rejected_games"]),
        },
    }
