"""Read-only, personal Human-vs-UCI game archive.

This domain is deliberately not imported by history_stats, history_archive or
competition_rating. A human player is not a 65th historical personality.
Only the local Arena workspace is scanned; no engine, network or recovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
from stat import S_ISREG
from urllib.parse import parse_qs
import json
import math
import os
import re

import chess
import chess.pgn

from .catalog import find_player, load_roster
from .human_game import IDENT_RX, SCHEMA, VALID_STATES

MAX_SESSIONS = 10000
MAX_STATE_BYTES = 32768
MAX_PGN_BYTES = 1024 * 1024
ID_RX = re.compile(r"human:" + IDENT_RX.pattern.removesuffix(r"\Z") + r"\Z")
RESULTS = {"1-0", "0-1", "1/2-1/2"}
STATUS_FILTERS = VALID_STATES | {"all"}
OUTCOMES = {"all", "win", "draw", "loss", "incomplete"}
COLORS = {"all", "white", "black"}
# O_NONBLOCK prevents a malicious FIFO replacing a JSON/PGN from blocking GET
# before fstat can reject the non-regular file.
SAFE_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
              getattr(os, "O_NONBLOCK", 0))
DIR_FLAGS = SAFE_FLAGS | getattr(os, "O_DIRECTORY", 0)


class MyGamesInput(ValueError):
    """Bad filter, unsafe local evidence or missing personal game."""


def _unique_pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise MyGamesInput("JSON com chaves duplicadas")
        data[key] = value
    return data


def _reject_constant(_):
    raise MyGamesInput("Número JSON não finito")


def _utc(value):
    if value is None:
        return None
    if type(value) is not str or len(value) > 40:
        raise MyGamesInput("Timestamp inválido")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MyGamesInput("Timestamp inválido") from exc
    if dt.tzinfo is None:
        raise MyGamesInput("Timestamp sem fuso")
    return dt.astimezone(timezone.utc).isoformat()


def _read_file(parent_fd: int, name: str, limit: int) -> bytes:
    fd = os.open(name, SAFE_FLAGS, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        if not S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise MyGamesInput("Arquivo fora dos limites")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if (not raw or len(raw) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns)):
            raise MyGamesInput("Arquivo modificado durante a leitura")
        return raw
    finally:
        os.close(fd)


def _source(workspace: Path):
    """Open workspace-relative human-games without following redirected dirs."""
    base_fd = os.open(workspace, DIR_FLAGS)
    try:
        try:
            games_fd = os.open("human-games", DIR_FLAGS, dir_fd=base_fd)
        except FileNotFoundError:
            return None
        return games_fd
    finally:
        os.close(base_fd)


def _document(workspace: Path, ident: str):
    if type(ident) is not str or not IDENT_RX.fullmatch(ident):
        raise MyGamesInput("Identificador pessoal inválido")
    root_fd = _source(workspace)
    if root_fd is None:
        raise MyGamesInput("Nenhuma partida pessoal com esse ID")
    try:
        session_fd = os.open(ident, DIR_FLAGS, dir_fd=root_fd)
        try:
            state_raw = _read_file(session_fd, "state.json", MAX_STATE_BYTES)
            pgn_raw = _read_file(session_fd, "game.pgn", MAX_PGN_BYTES)
            # PGN is published before state.json. Reject transitional reads
            # instead of inventing an outcome from mismatched checkpoints.
            if _read_file(session_fd, "state.json", MAX_STATE_BYTES) != state_raw:
                raise MyGamesInput("Checkpoint alterado durante a leitura")
        finally:
            os.close(session_fd)
    finally:
        os.close(root_fd)
    state = json.loads(state_raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                       parse_constant=_reject_constant)
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA or
            state.get("id") != ident or state.get("status") not in VALID_STATES):
        raise MyGamesInput("Estado pessoal incompatível")
    conf = state.get("config")
    hashes = state.get("hashes")
    roster = {p.slug for p in load_roster()}
    if (not isinstance(conf, dict) or
            conf.get("opponent") not in roster or
            conf.get("color") not in {"white", "black"} or
            not isinstance(hashes, dict) or
            any(type(hashes.get(k)) is not str or
                not re.fullmatch(r"[0-9a-f]{64}", hashes[k])
                for k in ("engine", "profile")) or
            type(state.get("revision")) is not int or
            not 0 <= state["revision"] <= 400):
        raise MyGamesInput("Metadados pessoais inválidos")
    clock = conf.get("clock")
    if clock is not None and (not isinstance(clock, list) or len(clock) != 2 or
                               type(clock[0]) is not int or type(clock[1]) is not int or
                               not 60 <= clock[0] <= 14400 or
                               not 0 <= clock[1] <= 120):
        raise MyGamesInput("Relógio configurado inválido")
    clocks = state.get("clocks")
    if ((clock is None) != (clocks is None) or
            clocks is not None and (not isinstance(clocks, list) or len(clocks) != 2 or
                any(type(value) not in (int, float) or
                    not math.isfinite(value) or not 0 <= value <= 86400
                    for value in clocks))):
        raise MyGamesInput("Relógios registrados inválidos")
    created = _utc(state.get("created_utc"))
    updated = _utc(state.get("updated_utc"))
    finished = _utc(state.get("finished_utc"))
    if not created or not updated or (
            datetime.fromisoformat(created) > datetime.fromisoformat(updated)) or (
            finished and not (datetime.fromisoformat(created) <=
                              datetime.fromisoformat(finished) <=
                              datetime.fromisoformat(updated))):
        raise MyGamesInput("Cronologia de sessão incompatível")
    if state["status"] == "RUNNING" and finished is not None:
        raise MyGamesInput("Partida em execução com término declarado")

    try:
        stream = StringIO(pgn_raw.decode("utf-8"))
        game = chess.pgn.read_game(stream)
        if game is None or game.errors or chess.pgn.read_game(stream) is not None:
            raise MyGamesInput("PGN de partida pessoal inválido")
        cursor = game
        while cursor.variations:
            if len(cursor.variations) != 1:
                raise MyGamesInput("Variações estranhas ao PGN canônico")
            cursor = cursor.variations[0]
        last = game.end()
        board = last.board()
    except (ValueError, UnicodeError, AssertionError) as exc:
        raise MyGamesInput("PGN pessoal ilegível ou ilegal") from exc
    human_white = conf["color"] == "white"
    opponent = find_player(conf["opponent"])
    headers = game.headers
    expected_clock = f"{clock[0]}+{clock[1]}" if clock else "-"
    if (headers.get("White") != ("Você" if human_white else opponent.name) or
            headers.get("Black") != (opponent.name if human_white else "Você") or
            headers.get("EngineSlug") != conf["opponent"] or
            headers.get("EngineSHA256") != hashes["engine"] or
            headers.get("EngineProfileSHA256") != hashes["profile"] or
            headers.get("SourceKind") != "human_vs_engine" or
            headers.get("TimeControl") != expected_clock or
            state["revision"] != board.ply()):
        raise MyGamesInput("PGN diverge do checkpoint pessoal")
    result = state.get("result")
    termination = state.get("termination")
    if headers.get("Result") != result or result not in RESULTS | {"*"}:
        raise MyGamesInput("Resultado do PGN e do checkpoint divergem")
    status = state["status"]
    if status == "COMPLETED":
        if result not in RESULTS or not finished or not isinstance(termination, str) or (
                headers.get("Termination") != termination):
            raise MyGamesInput("Conclusão sem evidência de resultado")
        outcome = board.outcome(claim_draw=False)
        if outcome is not None:
            if outcome.result() != result or termination != outcome.termination.name:
                raise MyGamesInput("Resultado ou causa contradiz posição terminal")
        elif termination == "HumanResignation":
            if result != ("0-1" if human_white else "1-0"):
                raise MyGamesInput("Desistência com vencedor inválido")
        elif termination == "TimeForfeit":
            losing_index = 0 if board.turn == chess.WHITE else 1
            if clocks is None or clocks[losing_index] != 0:
                raise MyGamesInput("Queda de tempo do lado a jogar não comprovada")
            winner_has_material = not board.has_insufficient_material(
                not board.turn)
            expected = (("0-1" if losing_index == 0 else "1-0")
                        if winner_has_material else "1/2-1/2")
            if result != expected:
                raise MyGamesInput("Resultado contradiz a queda de tempo")
        elif termination == "HumanDrawClaim":
            if result != "1/2-1/2" or not board.can_claim_draw():
                raise MyGamesInput("Empate reivindicado sem direito")
        else:
            raise MyGamesInput("Resultado final não comprovado")
    elif result != "*" or board.is_game_over(claim_draw=False):
        raise MyGamesInput("Partida não concluída com resultado ou posição terminal")
    if status == "RUNNING" and termination is not None:
        raise MyGamesInput("Partida ativa com término declarado")

    win = (result == "1-0" if human_white else result == "0-1")
    loss = (result == "0-1" if human_white else result == "1-0")
    score = ("win" if win else "loss" if loss else "draw") if status == "COMPLETED" else "incomplete"
    record = {
        "id": ident, "opponent": opponent.slug, "opponent_name": opponent.name,
        "color": conf["color"], "white": headers["White"], "black": headers["Black"],
        "result": result, "outcome": score, "status": status,
        "termination": termination, "ply_count": board.ply(),
        "created_at_utc": created, "updated_at_utc": updated,
        "finished_at_utc": finished, "started_at_utc": None,
        "duration_seconds": None, "clock": clock,
        "engine_sha256": hashes["engine"],
        "profile_sha256": hashes["profile"],
        "pgn_sha256": sha256(pgn_raw).hexdigest(),
        "provenance": "locally_consistent", "rating_eligible": False,
        "origin": "human_vs_engine",
    }
    return record, game, pgn_raw


def _scan(workspace: Path):
    root_fd = _source(workspace)
    if root_fd is None:
        return [], {"scanned": 0, "rejected": 0, "partial": False}
    try:
        entries = sorted(name for name in os.listdir(root_fd)
                         if name.startswith("human-"))
        if len(entries) > MAX_SESSIONS:
            raise MyGamesInput("Limite de partidas pessoais excedido")
    finally:
        os.close(root_fd)
    rows, rejected = [], 0
    for ident in entries:
        try:
            row, _, _ = _document(workspace, ident)
            rows.append(row)
        except (OSError, ValueError, UnicodeError, TypeError, KeyError,
                AssertionError, json.JSONDecodeError):
            rejected += 1
    rows.sort(key=lambda row: (row["created_at_utc"], row["id"]), reverse=True)
    return rows, {"scanned": len(entries), "rejected": rejected,
                  "partial": bool(rejected)}


def _filters(query: str):
    if type(query) is not str or len(query) > 2048:
        raise MyGamesInput("Consulta excessiva")
    parsed = parse_qs(query, keep_blank_values=True, strict_parsing=True) if query else {}
    if set(parsed) - {"page", "page_size", "status", "outcome", "color", "opponent"} or (
            any(len(values) != 1 for values in parsed.values())):
        raise MyGamesInput("Filtros pessoais inválidos")
    try:
        page = int(parsed.get("page", ["1"])[0])
        size = int(parsed.get("page_size", ["20"])[0])
    except ValueError as exc:
        raise MyGamesInput("Paginação inválida") from exc
    if not 1 <= page <= 10000 or not 1 <= size <= 50:
        raise MyGamesInput("Paginação fora dos limites")
    status = parsed.get("status", ["all"])[0]
    outcome = parsed.get("outcome", ["all"])[0]
    color = parsed.get("color", ["all"])[0]
    opponent = parsed.get("opponent", [""])[0]
    if (status not in STATUS_FILTERS or outcome not in OUTCOMES or
            color not in COLORS or
            opponent and opponent not in {p.slug for p in load_roster()}):
        raise MyGamesInput("Filtro pessoal desconhecido")
    return {"page": page, "page_size": size, "status": status,
            "outcome": outcome, "color": color, "opponent": opponent}


def listing(workspace: Path, query: str = ""):
    filters = _filters(query)
    rows, coverage = _scan(workspace)
    completed = [r for r in rows if r["status"] == "COMPLETED"]
    grouped = {}
    for row in rows:
        op = grouped.setdefault(row["opponent"],
            {"slug": row["opponent"], "name": row["opponent_name"],
             "games": 0, "wins": 0, "draws": 0, "losses": 0})
        op["games"] += 1
        if row["status"] == "COMPLETED":
            op[{"win": "wins", "draw": "draws", "loss": "losses"}[row["outcome"]]] += 1
    filtered = [r for r in rows
                if (filters["status"] == "all" or r["status"] == filters["status"])
                and (filters["outcome"] == "all" or r["outcome"] == filters["outcome"])
                and (filters["color"] == "all" or r["color"] == filters["color"])
                and (not filters["opponent"] or r["opponent"] == filters["opponent"])]
    start = (filters["page"] - 1) * filters["page_size"]
    return {
        "schema": "chesssim-my-games-v1", "filters": filters,
        "page": filters["page"], "page_size": filters["page_size"],
        "pages": max(1, (len(filtered)+filters["page_size"]-1)//filters["page_size"]),
        "total": len(filtered), "all_count": len(rows),
        "games": filtered[start:start+filters["page_size"]],
        "stats": {"games": len(rows), "completed": len(completed),
                  "wins": sum(r["outcome"] == "win" for r in completed),
                  "draws": sum(r["outcome"] == "draw" for r in completed),
                  "losses": sum(r["outcome"] == "loss" for r in completed),
                  "unfinished": len(rows)-len(completed),
                  "by_opponent": sorted(grouped.values(), key=lambda r: (-r["games"], r["name"]))},
        "coverage": coverage, "rating_eligible": False,
        "disclaimer": "Partidas pessoais locais. Sem Elo e fora do histórico dos motores."
    }


def replay(workspace: Path, ident: str):
    record, game, _ = _document(workspace, ident)
    board = game.board()
    states = [{"fen": board.fen(), "last": None, "san": None}]
    for node in game.mainline():
        move = node.move
        san = board.san(move)
        board.push(move)
        uci = move.uci()
        states.append({"fen": board.fen(), "last": [uci[:2], uci[2:4]],
                       "san": san, "uci": uci})
    return {"schema": "chesssim-my-replay-v1", "game": record, "states": states}


def pgn(workspace: Path, ident: str):
    _, _, raw = _document(workspace, ident)
    return raw
