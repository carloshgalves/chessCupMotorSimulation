"""Bounded, read-only, paginated archive of local verified chess simulations.

Records are sourced from the same independently verified PGN pipeline used
by History and experimental Elo. Cup projections remain isolated from PGNs.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import date
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
import os
from io import StringIO
import csv
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl

import chess.pgn

from .catalog import load_roster
from .competition_rating import classify_competition_record
from .cups import CupManager
from .history_games import MAX_PGN_BYTES, _game, _read
from .history_profiles import _player
from .history_stats import verified_competition_games, _tournament_game_stream
from .live_board import _states

SCHEMA = "chesssim-player-archive-v1"
MAX_PAGE_SIZE = 50
MAX_CUP_SCAN = 100
MAX_SYNTHETIC_ROWS = 4096
MAX_INDEX_FILES = 50000
_INDEX_LOCK = RLock()
_INDEX: OrderedDict[tuple[str, str], tuple[str, list[dict], dict]] = OrderedDict()
ID_RX = re.compile(r"(?:arena|tournament|cup):[a-z0-9_./:-]{1,450}\Z")
FILTERS = frozenset({"q", "origin", "modality", "result", "color",
                     "opponent", "engine_sha256", "date_from", "date_to",
                     "year_from", "year_to", "page", "page_size"})
ORIGINS = frozenset({"all", "arena", "tournament", "cup", "league", "projection"})
MODALITIES = frozenset({"all", "classical", "rapid", "blitz", "bullet",
                        "unclassified"})
RESULTS = frozenset({"all", "win", "draw", "loss", "incomplete"})
COLORS = frozenset({"all", "white", "black"})


class ArchiveInput(ValueError):
    """Untrusted query or local-file path violates the archive contract."""


def parameters(query: str) -> dict:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=16)
    except ValueError as exc:
        raise ArchiveInput("Consulta muito longa") from exc
    if len(pairs) != len({key for key, _ in pairs}) or any(
            key not in FILTERS for key, _ in pairs):
        raise ArchiveInput("Filtros duplicados ou desconhecidos")
    values = dict(pairs)
    result = {
        "q": values.get("q", "").strip(),
        "origin": values.get("origin", "all"),
        "modality": values.get("modality", "all"),
        "result": values.get("result", "all"),
        "color": values.get("color", "all"),
        "opponent": values.get("opponent", ""),
        "engine_sha256": values.get("engine_sha256", ""),
    }
    if len(result["q"]) > 80 or any(ord(c) < 32 for c in result["q"]):
        raise ArchiveInput("Pesquisa inválida")
    if (result["origin"] not in ORIGINS or
            result["modality"] not in MODALITIES or
            result["result"] not in RESULTS or
            result["color"] not in COLORS):
        raise ArchiveInput("Filtro inválido")
    if result["opponent"] and not re.fullmatch(r"[a-z0-9-]{2,48}", result["opponent"]):
        raise ArchiveInput("Adversário inválido")
    if result["engine_sha256"] and not re.fullmatch(
            r"[0-9a-f]{64}", result["engine_sha256"]):
        raise ArchiveInput("Hash de motor inválido")
    for key in ("date_from", "date_to"):
        raw = values.get(key, "")
        if not raw:
            result[key] = None
        else:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
                raise ArchiveInput("Data deve ser AAAA-MM-DD")
            try:
                date.fromisoformat(raw)
            except ValueError as exc:
                raise ArchiveInput("Data inválida") from exc
            result[key] = raw
    if (result["date_from"] and result["date_to"] and
            result["date_from"] > result["date_to"]):
        raise ArchiveInput("Intervalo de datas invertido")
    for key in ("year_from", "year_to"):
        raw = values.get(key, "")
        if not raw:
            result[key] = None
        elif not raw.isascii() or not raw.isdecimal() or not 1800 <= int(raw) <= 2100:
            raise ArchiveInput("Ano do registro inválido")
        else:
            result[key] = int(raw)
    if (result["year_from"] is not None and result["year_to"] is not None
            and result["year_from"] > result["year_to"]):
        raise ArchiveInput("Intervalo de registro invertido")
    for key, default, upper in (("page", 1, 16384), ("page_size", 20, MAX_PAGE_SIZE)):
        raw = values.get(key)
        if raw is None:
            result[key] = default
        elif not raw.isascii() or not raw.isdecimal():
            raise ArchiveInput("Paginação inválida")
        else:
            result[key] = int(raw)
        if not 1 <= result[key] <= upper:
            raise ArchiveInput("Paginação fora do limite")
    return result


def _from_player(rec: dict, slug: str, names: dict) -> dict:
    white, black = rec["white"], rec["black"]
    opponent = black if white == slug else white
    result = rec.get("result")
    if rec["status"] != "COMPLETED":
        outcome = "incomplete"
    elif result == "1/2-1/2":
        outcome = "draw"
    elif (result == "1-0" and slug == white) or (
            result == "0-1" and slug == black):
        outcome = "win"
    else:
        outcome = "loss"
    mode = rec.get("modality")
    if mode is None:
        mode, _ = classify_competition_record(rec)
    mode = mode if mode in MODALITIES else "unclassified"
    origin = rec["origin"]
    if origin == "cup":
        competition = "Copa · " + rec["id"].split(":")[1]
    elif origin == "league":
        competition = ("Liga · " + rec.get("event_name", rec["id"].split(":")[1]) +
                       " / " + rec.get("season_id", "Temporada"))
    elif origin == "tournament":
        competition = "Torneio local"
    elif origin == "arena":
        competition = "Arena · Duelo"
    else:
        competition = "Projeção"
    proof = rec.get("engine_strength_config") or {}
    timestamp = rec.get("recorded_utc")
    # The created-manifest timestamp is not evidence of when moves were played.
    return {
        "id": rec["id"], "opponent": opponent, "opponent_name": names[opponent],
        "white": white, "black": black, "color": "white" if white == slug else "black",
        "result": result, "outcome": outcome, "status": rec["status"],
        "origin": origin, "modality": mode, "competition": competition,
        "date": timestamp, "recorded_at_utc": timestamp,
        "recorded_timezone": "UTC" if timestamp else None,
        "started_at_utc": rec.get("started_at_utc"),
        "finished_at_utc": rec.get("finished_at_utc"),
        "duration_seconds": rec.get("duration_seconds"),
        "simulated_calendar_date": rec.get("simulated_calendar_date"),
        "virtual_week": rec.get("virtual_week"),
        "virtual_day": rec.get("virtual_day"),
        "event_id": rec.get("event_id"), "season_id": rec.get("season_id"),
        "round": rec.get("round"), "source_kind": rec.get(
            "source_kind", "fast_statistical" if origin == "projection"
            else "engine_full"),
        "engine_sha256": proof.get("engine_sha256"),
        "white_profile_sha256": proof.get("white_profile_sha256"),
        "black_profile_sha256": proof.get("black_profile_sha256"),
        "evidence_pgn_sha256": rec.get("evidence_pgn_sha256"),
        "receipt_sha256": rec.get("receipt_sha256"),
        # Not an assertion of copyright ownership or redistribution rights.
        "license_status": "not_assessed",
        "pgn": origin != "projection" and rec["status"] == "COMPLETED",
        "rating_eligible": bool(rec.get("rating_eligible", origin != "projection")),
    }


def _projections(workspace: Path, slug: str, names: dict) -> tuple[list[dict], int]:
    root = workspace / "cups"
    if not root.exists():
        return [], 0
    if root.is_symlink() or not root.is_dir():
        return [], 1
    manifest = sorted(root.glob("cup-*/cup.json"))
    if len(manifest) > MAX_CUP_SCAN:
        raise ArchiveInput("Quantidade de Copas excede limite")
    viewer = CupManager.readonly(workspace)
    rows = []
    rejected = 0
    for path in manifest:
        try:
            state = viewer._manifest(path.parent.name)
            if state["config"]["execution_mode"] != "fast_statistical":
                continue
            if slug not in state["config"]["participants"]:
                continue
            detail = viewer._state(state, force=True)
            for ident, entry in detail["game_receipts"].items():
                if slug not in (entry["white"], entry["black"]):
                    continue
                if len(rows) >= MAX_SYNTHETIC_ROWS:
                    raise ArchiveInput("Limite de projeções excedido")
                stage = entry["stage"]
                mode = ("classical" if stage == "classical" else
                        "rapid" if stage in {"rapid15","rapid10",
                                             "women_double_round_robin"} else
                        "blitz" if stage in {"blitz5","blitz3"} else
                        "unclassified")
                record = {
                    "id": "projection:" + path.parent.name + ":" + ident,
                    "white": entry["white"], "black": entry["black"],
                    "result": entry["result"], "status": "COMPLETED",
                    "origin": "projection", "modality": mode,
                    "recorded_utc": entry.get("finished_utc"),
                    "rating_eligible": False,
                }
                rows.append(_from_player(record, slug, names))
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            rejected += 1
    return rows, rejected


def _source_fingerprint(workspace: Path) -> str:
    """Metadata-only cache invalidation, including receipt and PGN changes.

    Enumerate competition roots, never scan historical corpora or arbitrary
    user folders recursively. Invalidation also tracks ctime so rewriting a
    PGN while restoring its mtime does not preserve a stale archive index.
    Raw PGN hashes are still revalidated for replay/export.
    """
    roots = []
    for child in workspace.iterdir():
        if child.name in {"cups", "leagues", "tournaments"} or (
                child.name.startswith("session-") or
                child.name.startswith("tournament-") or
                (child.is_dir() and (child / "tournament.json").exists())):
            roots.append(child)
    if (workspace / "tournament.json").exists():
        roots.append(workspace / "tournament.json")
    fingerprint = sha256()
    inspected = 0
    for root in sorted(roots):
        if root.is_symlink():
            raise ArchiveInput("Fonte de partidas redirecionada")
        if root.is_file():
            locations = [root]
        else:
            locations = []
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs.sort()
                files.sort()
                base = Path(directory)
                locations.append(base)
                for name in dirs:
                    location = base / name
                    if location.is_symlink():
                        locations.append(location)
                locations.extend(base / name for name in files
                                 if name.endswith((".json", ".jsonl", ".pgn")))
        for path in locations:
            inspected += 1
            if inspected > MAX_INDEX_FILES:
                raise ArchiveInput("Limite de arquivos no índice excedido")
            stat = path.lstat()
            payload = (path.relative_to(workspace).as_posix(), stat.st_mode,
                       stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            fingerprint.update(repr(payload).encode("utf-8"))
            fingerprint.update(b"\\n")
    return fingerprint.hexdigest()


def _all(workspace: Path, slug: str) -> tuple[list[dict], dict]:
    _player(slug)
    workspace = workspace.expanduser().resolve(strict=True)
    signature = _source_fingerprint(workspace)
    cache_key = (str(workspace), slug)
    with _INDEX_LOCK:
        cached = _INDEX.get(cache_key)
        if cached and cached[0] == signature:
            _INDEX.move_to_end(cache_key)
            return deepcopy(cached[1]), deepcopy(cached[2])
    names = {p.slug: p.name for p in load_roster()}
    records, coverage = verified_competition_games(
        workspace, include_projections=True)
    actual = [_from_player(r, slug, names) for r in records
              if r["origin"] != "projection" and
              slug in (r["white"], r["black"])]
    league_projections = [_from_player(r, slug, names) for r in records
                          if r["origin"] == "projection" and
                          slug in (r["white"], r["black"])]
    cup_projections, invalid_projections = _projections(workspace, slug, names)
    projections = league_projections + cup_projections
    rows = actual + projections
    rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    result_coverage = {
        "verified": len(actual), "projections": len(projections),
        "rejected_records": coverage.get("rejected_records", 0) +
        invalid_projections,
        "rejected_games": coverage.get("rejected_games", 0),
        "partial": coverage.get("partial", False) or invalid_projections > 0,
        "ordering": "record_timestamp_desc_then_id",
        "index_strategy": "in_memory_metadata_versioned",
    }
    # Recompute if source changed while indexing; do not cache a stale result.
    if _source_fingerprint(workspace) == signature:
        with _INDEX_LOCK:
            _INDEX[cache_key] = (signature, deepcopy(rows), deepcopy(result_coverage))
            _INDEX.move_to_end(cache_key)
            while len(_INDEX) > 4:
                _INDEX.popitem(last=False)
    return rows, result_coverage


def _filtered(rows: list[dict], params: dict) -> list[dict]:
    text = params["q"].casefold()
    return [
        row for row in rows
        if (params["origin"] == "all" or row["origin"] == params["origin"])
        and (params["modality"] == "all" or row["modality"] == params["modality"])
        and (params["result"] == "all" or row["outcome"] == params["result"])
        and (params["color"] == "all" or row["color"] == params["color"])
        and (not params["opponent"] or row["opponent"] == params["opponent"])
        and (not params["engine_sha256"] or
             row["engine_sha256"] == params["engine_sha256"])
        and (params["date_from"] is None or bool(row["date"]) and
             row["date"][:10] >= params["date_from"])
        and (params["date_to"] is None or bool(row["date"]) and
             row["date"][:10] <= params["date_to"])
        and (params["year_from"] is None or bool(row["date"]) and
             row["date"][:4].isdigit() and int(row["date"][:4]) >= params["year_from"])
        and (params["year_to"] is None or bool(row["date"]) and
             row["date"][:4].isdigit() and int(row["date"][:4]) <= params["year_to"])
        and (not text or any(text in str(row[key]).casefold() for key in (
            "id", "opponent_name", "opponent", "competition")))
    ]


def archive(workspace: Path, slug: str, query: str = "") -> dict:
    params = parameters(query)
    rows, coverage = _all(workspace, slug)
    filtered = _filtered(rows, params)
    start = (params["page"] - 1) * params["page_size"]
    games = filtered[start:start + params["page_size"]]
    real = [g for g in rows if g["origin"] != "projection"]
    completed = [g for g in real if g["status"] == "COMPLETED"]
    modes = {}
    for mode in ("classical", "rapid", "blitz", "bullet", "unclassified"):
        subset = [g for g in completed if g["modality"] == mode]
        modes[mode] = {
            "games": len(subset),
            "wins": sum(g["outcome"] == "win" for g in subset),
            "draws": sum(g["outcome"] == "draw" for g in subset),
            "losses": sum(g["outcome"] == "loss" for g in subset),
        }
    grouped = {}
    for game in rows:
        key = (game["origin"], game["competition"])
        record = grouped.setdefault(key, {
            "origin": game["origin"], "name": game["competition"],
            "games": 0, "verified": 0, "projections": 0,
        })
        record["games"] += 1
        if game["origin"] == "projection":
            record["projections"] += 1
        else:
            record["verified"] += 1
    return {
        "schema": SCHEMA, "slug": slug, "filters": params,
        "competitions": sorted(grouped.values(),
            key=lambda x:(-x["games"], x["name"])),
        "total": len(filtered), "all_count": len(rows),
        "page": params["page"], "page_size": params["page_size"],
        "pages": max(1, (len(filtered)+params["page_size"]-1)//params["page_size"]),
        "games": games, "overview": {
            "verified": len(real), "completed": len(completed),
            "projections": len(rows)-len(real),
            "wins": sum(g["outcome"] == "win" for g in completed),
            "draws": sum(g["outcome"] == "draw" for g in completed),
            "losses": sum(g["outcome"] == "loss" for g in completed),
            "modalities": modes,
        },
        "coverage": coverage,
        "disclaimer": ("Apenas resultados UCI verificados entram nas estatísticas e Elo. "
                       "Projeções não possuem PGN e não representam jogos históricos."),
    }


def _raw_pgn(workspace: Path, record: dict) -> tuple[bytes, chess.pgn.Game]:
    """Resolve a verified opaque identifier to a bounded, no-follow PGN."""
    id_ = record["id"]
    origin = record["origin"]
    if origin == "league":
        _, league_id, match_id = id_.split(":")
        if (not re.fullmatch(r"league-[0-9a-f]{16}", league_id) or
                not re.fullmatch(r"r[0-9]{3}-g[0-9]{2}", match_id)):
            raise ArchiveInput("ID da Liga inválido")
        rel = Path("leagues") / league_id / "games" / (match_id + ".pgn")
        raw = _read(workspace, rel, MAX_PGN_BYTES)
        game = _game(raw)
    elif origin == "cup":
        _, cup_id, game_id = id_.split(":")
        if not re.fullmatch(r"cup-[a-f0-9]{16}", cup_id):
            raise ArchiveInput("Copa inválida")
        viewer = CupManager.readonly(workspace)
        state = viewer._manifest(cup_id)
        derived = viewer._state(state, force=True)
        entry = derived["game_receipts"].get(game_id)
        if not entry or not entry.get("pgn"):
            raise ArchiveInput("PGN da Copa indisponível")
        raw = _read(workspace, Path("cups")/cup_id/entry["pgn"], MAX_PGN_BYTES)
        game = _game(raw)
    elif origin == "arena":
        _, session, attempt, idx = id_.split(":")
        if (not re.fullmatch(r"session-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", session)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", attempt)
                or idx not in {"1","2"}):
            raise ArchiveInput("ID da Arena inconsistente")
        a, b = (record["white"], record["black"]) if idx == "1" else (
            record["black"], record["white"])
        rel = Path(session)/"duels"/(a+"-vs-"+b)/attempt/("jogo_"+idx+".pgn")
        raw = _read(workspace, rel, MAX_PGN_BYTES)
        game = _game(raw)
    elif origin == "tournament":
        # The tournament path is relative to --out, not a client-supplied path.
        _, run, match, attempt, idx = id_.split(":")
        relbase = Path(run)
        if (relbase.is_absolute() or ".." in relbase.parts or
                not re.fullmatch(r"r[0-9]{2}-m[0-9]{2}", match)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", attempt)
                or idx not in {"1","2"}):
            raise ArchiveInput("ID de torneio incompatível")
        a, b = (record["white"], record["black"]) if idx == "1" else (
            record["black"], record["white"])
        rel = relbase/"matches"/match/"duels"/(a+"-vs-"+b)/attempt/"duelo.pgn"
        raw = _read(workspace, rel, 2*MAX_PGN_BYTES)
        game = _tournament_game_stream(raw)[int(idx)-1]
    else:
        raise ArchiveInput("Projeções não têm PGN")
    if sha256(raw).hexdigest() != record["evidence_pgn_sha256"]:
        raise ArchiveInput("PGN alterado após verificação")
    return raw, game


def replay(workspace: Path, slug: str, opaque_id: str) -> dict:
    _player(slug)
    if not isinstance(opaque_id, str) or len(opaque_id) > 512:
        raise ArchiveInput("Partida inválida")
    records, _ = verified_competition_games(workspace)
    record = next((r for r in records if r["id"] == opaque_id and
                   slug in (r["white"],r["black"]) and
                   r["status"] == "COMPLETED"), None)
    if record is None:
        raise ArchiveInput("Nenhuma partida UCI verificável corresponde ao ID")
    _, game = _raw_pgn(workspace, record)
    return {"schema":"chesssim-archive-replay-v1",
            "game":{"id":opaque_id,"white":record["white"],
                    "black":record["black"],"result":record["result"],
                    "origin":record["origin"]},
            "states":_states(game)}


def export_page(workspace: Path, slug: str, query: str,
                kind: str) -> tuple[bytes,str,str]:
    if kind not in {"json","csv","pgn"}:
        raise ArchiveInput("Formato de exportação desconhecido")
    response = archive(workspace, slug, query)
    rows = response["games"]
    if kind == "json":
        body = json.dumps(response, ensure_ascii=False, allow_nan=False,
                          indent=2).encode("utf-8")
        return body, "application/json; charset=utf-8", "json"
    if kind == "csv":
        out=StringIO()
        writer=csv.writer(out)
        keys=["id","recorded_at_utc","started_at_utc","finished_at_utc",
              "duration_seconds","competition","event_id","season_id","round",
              "opponent_name","color","modality","result","outcome","origin",
              "source_kind","status","pgn","rating_eligible","virtual_week",
              "virtual_day","engine_sha256","white_profile_sha256",
              "black_profile_sha256","evidence_pgn_sha256","receipt_sha256",
              "license_status"]
        writer.writerow(keys)
        for row in rows:
            writer.writerow([row.get(k,"") for k in keys])
        return out.getvalue().encode("utf-8-sig"),"text/csv; charset=utf-8","csv"
    records, _=verified_competition_games(workspace)
    lookup={r["id"]:r for r in records}
    games=[]
    for row in rows:
        if not row["pgn"]:
            continue
        rec=lookup.get(row["id"])
        if rec is None:
            raise ArchiveInput("Registro alterado durante exportação")
        _,game=_raw_pgn(workspace, rec)
        # Add origin/hash tags to the exported copy, never mutate the PGN
        # verified on disk. A hash refers to the original source bytes.
        game.headers["ChessSimOrigin"] = rec["origin"]
        game.headers["ChessSimPGNSHA256"] = rec["evidence_pgn_sha256"]
        game.headers["ChessSimGameID"] = rec["id"]
        game.headers["ChessSimLicenseStatus"] = "NotAssessed"
        games.append(str(game))
    if not games:
        raise ArchiveInput("Página sem PGN de partidas UCI concluídas")
    return ("\n\n".join(games)+"\n").encode("utf-8"),"application/x-chess-pgn","pgn"
