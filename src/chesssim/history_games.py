"""Closed, read-only catalogue of sourced local historical and simulated games.

Historical PGNs are user-curated in workspace/history-games; no third-party games
are shipped. Simulated PGNs are loaded only from the Arena's own session shape.
An untrusted HTTP request can select an opaque catalogue ID, never a filename.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import json
import os
import stat
from pathlib import Path
import re
from urllib.parse import urlsplit

import chess.pgn

from .catalog import load_roster
from .live_board import _states

SCHEMA = "chesssim-history-games-v1"
REPLAY_SCHEMA = "chesssim-history-replay-v1"
MANIFEST_SCHEMA = "chesssim-curated-games-v1"
MAX_PGN_BYTES = 2 * 1024 * 1024
MAX_PLIES = 2000
MAX_MANIFEST_BYTES = 256 * 1024
MAX_CATALOGUE_GAMES = 1000
MAX_SESSION_SUMMARIES = 2000
MAX_VISIBLE = 5
_ID = re.compile(r"(?:h-[a-z0-9-]{2,64}|s-[a-f0-9]{24})\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_SESSION = re.compile(r"session-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}\Z")


class InvalidCatalogue(ValueError):
    """A local source or metadata cannot be trusted or validated."""


class UnknownGame(ValueError):
    """The requested ID is not part of the exposed, closed catalogue."""


@dataclass(frozen=True)
class _Entry:
    game_id: str
    path: Path
    origin: str
    white_slug: str
    black_slug: str
    sha256: str
    signature: str
    status: str
    result: str | None
    event: str
    date: str | None
    source: dict
    priority: int = 100

    def public(self) -> dict:
        return {
            "game_id": self.game_id, "origin": self.origin,
            "white_slug": self.white_slug, "black_slug": self.black_slug,
            "status": self.status, "result": self.result,
            "event": self.event, "date": self.date,
            "source": self.source, "pgn_sha256": self.sha256,
        }


def _read(root: Path, relative: Path, limit: int) -> bytes:
    """Open all components by directory FD with O_NOFOLLOW; never trust a path check.

    An attacker controlling local files cannot replace a checked symlink before
    opening it. A bounded read protects against growth after fstat.
    """
    if (relative.is_absolute() or not relative.parts or
            any(part in (".", "..") for part in relative.parts)):
        raise InvalidCatalogue("Referência de arquivo inválida")
    try:
        fd = os.open(root.resolve(strict=True),
                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate(relative.parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW
                if index < len(relative.parts) - 1:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                raise InvalidCatalogue("Artefato inválido ou excede limite")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise InvalidCatalogue("Arquivo excede limite")
            return raw
        finally:
            os.close(fd)
    except (OSError, RuntimeError) as exc:
        raise InvalidCatalogue("Artefato local indisponível") from exc


def _json(root: Path, relative: Path, limit: int) -> dict:
    def unique_pairs(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise InvalidCatalogue("Chave duplicada em JSON")
            data[key] = value
        return data

    def reject_nonfinite(value):
        raise InvalidCatalogue("Constante numérica inválida: " + value)

    try:
        result = json.loads(_read(root, relative, limit).decode("utf-8"),
                            object_pairs_hook=unique_pairs,
                            parse_constant=reject_nonfinite)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidCatalogue("Metadados inválidos") from exc
    if not isinstance(result, dict):
        raise InvalidCatalogue("Metadados não são objeto")
    return result


def _game(raw: bytes) -> chess.pgn.Game:
    try:
        text = raw.decode("utf-8-sig")
        stream = StringIO(text)
        game = chess.pgn.read_game(stream)
        if game is None or game.errors or chess.pgn.read_game(stream) is not None:
            raise InvalidCatalogue("PGN vazio, ilegal ou múltiplo")
        if game.headers.get("Variant", "Standard") not in ("Standard", ""):
            raise InvalidCatalogue("Variante de xadrez não suportada")
        if not game.board().is_valid():
            raise InvalidCatalogue("FEN inicial inválida")
        if sum(1 for _ in game.mainline_moves()) > MAX_PLIES:
            raise InvalidCatalogue("PGN excede limite de lances")
        return game
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise InvalidCatalogue("PGN ou FEN inválido") from exc


def _signature(game: chess.pgn.Game, white: str, black: str) -> str:
    """Semantic duplicate key without treating distinct dated games as identical."""
    payload = [white, black, game.headers.get("Event", "?"),
               game.headers.get("Date", "?"), game.headers.get("Round", "?"),
               game.board().fen(), *[move.uci() for move in game.mainline_moves()]]
    return sha256(json.dumps(payload, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_game(game: chess.pgn.Game, white: str, black: str,
                   names: dict[str, str], status: str, result: str | None) -> None:
    if (white not in names or black not in names or
            game.headers.get("White") != names[white] or
            game.headers.get("Black") != names[black]):
        raise InvalidCatalogue("Jogadores do PGN não coincidem com o catálogo")
    declared = game.headers.get("Result", "*")
    if status == "COMPLETED":
        if declared not in ("1-0", "0-1", "1/2-1/2") or result != declared:
            raise InvalidCatalogue("Resultado final contraditório")
        board_result = game.end().board().outcome(claim_draw=True)
        if board_result is not None and board_result.result() != declared:
            raise InvalidCatalogue("Resultado contradiz posição final")
    elif status == "INCOMPLETE":
        if result is not None:
            raise InvalidCatalogue("Jogo incompleto não pode pontuar")
    else:
        raise InvalidCatalogue("Status de partida desconhecido")


def _historical(workspace: Path, names: dict[str, str]) -> list[_Entry]:
    root = workspace / "history-games"
    manifest_path = root / "manifest.json"
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return []
    data = _json(workspace, Path("history-games/manifest.json"), MAX_MANIFEST_BYTES)
    records = data.get("games")
    if (data.get("schema") != MANIFEST_SCHEMA or not isinstance(records, list)
            or len(records) > MAX_CATALOGUE_GAMES):
        raise InvalidCatalogue("Manifesto curado incompatível")
    entries = []
    for record in records:
        if not isinstance(record, dict):
            raise InvalidCatalogue("Entrada curada inválida")
        ident = record.get("game_id")
        digest = record.get("sha256")
        white, black = record.get("white_slug"), record.get("black_slug")
        source = record.get("source_url")
        label = record.get("source_label")
        priority = record.get("priority", 100)
        if (not isinstance(ident, str) or not ident.startswith("h-") or
                not _ID.fullmatch(ident) or not isinstance(digest, str) or
                not _DIGEST.fullmatch(digest) or
                not isinstance(source, str) or len(source) > 1000 or
                not isinstance(label, str) or not 1 <= len(label) <= 200 or
                type(priority) is not int or not 0 <= priority <= 10000):
            raise InvalidCatalogue("Identidade, hash, fonte ou prioridade inválida")
        parsed = urlsplit(source)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.fragment):
            raise InvalidCatalogue("Fonte histórica precisa ter URL HTTPS")
        # A catalogue entry cannot point to a different filename or directory.
        if record.get("pgn") != f"games/{ident}.pgn":
            raise InvalidCatalogue("PGN histórico não corresponde ao ID")
        rel = Path("history-games") / "games" / (ident + ".pgn")
        raw = _read(workspace, rel, MAX_PGN_BYTES)
        if sha256(raw).hexdigest() != digest:
            raise InvalidCatalogue("Hash do PGN histórico divergente")
        game = _game(raw)
        status = "INCOMPLETE" if game.headers.get("Result", "*") == "*" else "COMPLETED"
        result = game.headers.get("Result") if status == "COMPLETED" else None
        _validate_game(game, white, black, names, status, result)
        entries.append(_Entry(
            ident, rel, "historical", white, black, digest,
            _signature(game, white, black), status, result, game.headers.get("Event", "?"),
            game.headers.get("Date"), {"label": label, "url": source}, priority,
        ))
    return entries


def _simulated(workspace: Path, names: dict[str, str]) -> list[_Entry]:
    summaries = sorted(workspace.glob("session-*/duels/*/*/summary.json"))
    if len(summaries) > MAX_SESSION_SUMMARIES:
        raise InvalidCatalogue("Número de sessões excede limite de leitura")
    entries = []
    for summary_path in summaries:
        relative = summary_path.relative_to(workspace)
        parts = relative.parts
        if (len(parts) != 5 or not _SESSION.fullmatch(parts[0]) or
                parts[1] != "duels" or parts[4] != "summary.json"):
            raise InvalidCatalogue("Caminho de sessão incompatível")
        summary = _json(workspace, relative, MAX_MANIFEST_BYTES)
        players = summary.get("players")
        games = summary.get("games")
        if (not isinstance(players, list) or len(players) != 2 or
                any(p not in names for p in players if isinstance(p, str)) or
                any(not isinstance(p, str) for p in players) or
                parts[2] != "-vs-".join(players) or
                not isinstance(games, list) or len(games) > 2):
            raise InvalidCatalogue("Resumo de duelo contraditório")
        for index, item in enumerate(games, 1):
            if not isinstance(item, dict):
                raise InvalidCatalogue("Resultado de jogo inválido")
            status = item.get("status")
            if status not in ("COMPLETED", "INCOMPLETE", "FAILED", "INTERRUPTED"):
                raise InvalidCatalogue("Status não reconhecido")
            if status in ("FAILED", "INTERRUPTED"):
                continue
            final = relative.parent / f"jogo_{index}.pgn"
            partial = relative.parent / f"jogo_{index}.partial.pgn"
            # Matches persist even an INCOMPLETE terminated game as final PGN;
            # a currently interrupted run may only have a partial checkpoint.
            final_path = workspace / final
            relative_pgn = (final if status == "COMPLETED" or
                            final_path.exists() or final_path.is_symlink()
                            else partial)
            raw = _read(workspace, relative_pgn, MAX_PGN_BYTES)
            game = _game(raw)
            white = next((p for p in players if game.headers.get("White") == names[p]), None)
            black = next((p for p in players if game.headers.get("Black") == names[p]), None)
            if white is None or black is None or white == black and players[0] != players[1]:
                raise InvalidCatalogue("Jogadores da simulação inconsistentes")
            result = item.get("result") if status == "COMPLETED" else None
            _validate_game(game, white, black, names, status, result)
            digest = sha256(raw).hexdigest()
            ident = "s-" + sha256(relative_pgn.as_posix().encode("utf-8")).hexdigest()[:24]
            entries.append(_Entry(
                ident, relative_pgn, "simulation", white, black,
                digest, _signature(game, white, black), status, result, game.headers.get("Event", "?"),
                game.headers.get("Date"),
                {"label": "Partida local do ChessSimulation", "session_id": parts[0]},
            ))
    return entries


def _selection(workspace: Path) -> dict[str, list[_Entry]]:
    names = {p.slug: p.name for p in load_roster()}
    historic = _historical(workspace, names)
    simulated = _simulated(workspace, names)
    # Curated originals take precedence; newer simulated sessions precede old.
    historic.sort(key=lambda e: (e.priority, e.game_id))
    simulated.sort(key=lambda e: e.path.as_posix(), reverse=True)
    selected: dict[str, list[_Entry]] = {slug: [] for slug in names}
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    seen_signatures: dict[str, _Entry] = {}
    for entry in historic + simulated:
        if entry.game_id in seen_ids:
            raise InvalidCatalogue("IDs duplicados no catálogo")
        seen_ids.add(entry.game_id)
        previous = seen_signatures.get(entry.signature)
        if previous is not None:
            if (previous.status, previous.result) != (entry.status, entry.result):
                raise InvalidCatalogue("Resultados conflitantes para mesma partida")
            continue
        if entry.sha256 in seen_hashes:
            continue
        seen_hashes.add(entry.sha256)
        seen_signatures[entry.signature] = entry
        for slug in {entry.white_slug, entry.black_slug}:
            if len(selected[slug]) < MAX_VISIBLE:
                selected[slug].append(entry)
    return selected


def list_games(workspace: Path, slug: str) -> dict:
    from .history_profiles import _player
    _player(slug)
    selected = _selection(workspace)[slug]
    return {"schema": SCHEMA, "slug": slug,
            "count": len(selected), "limit": MAX_VISIBLE,
            "games": [entry.public() for entry in selected]}


def replay(workspace: Path, game_id: str) -> dict:
    if not isinstance(game_id, str) or not _ID.fullmatch(game_id):
        raise UnknownGame("ID inválido")
    index = _selection(workspace)
    found = next((e for group in index.values() for e in group
                  if e.game_id == game_id), None)
    if found is None:
        raise UnknownGame("Partida não está no catálogo público")
    raw = _read(workspace, found.path, MAX_PGN_BYTES)
    if sha256(raw).hexdigest() != found.sha256:
        raise InvalidCatalogue("PGN mudou após catalogação")
    game = _game(raw)
    names = {p.slug: p.name for p in load_roster()}
    _validate_game(game, found.white_slug, found.black_slug, names,
                   found.status, found.result)
    return {"schema": REPLAY_SCHEMA, "game": found.public(),
            "states": _states(game)}
