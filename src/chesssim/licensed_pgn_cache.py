"""Opt-in, offline PGN cache with operator-attested source rights (#62).

A source catalog is an *operator claim*; it is not a license-verification
service. No downloader, network client, engine, or calibration consumer runs.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import mkdtemp
from urllib.parse import urlsplit

import chess.pgn

from .game_diversity import mainline_identity


SCHEMA = "chesssim-pgn-rights-v1"
CACHE_SCHEMA = "chesssim-local-pgn-cache-v1"
ALLOWED_LICENSES = frozenset({
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CUSTOM_PERMISSION",
})
MAX_CATALOG_BYTES = 128 * 1024
MAX_PGN_BYTES = 8 * 1024 * 1024
MAX_SOURCES = 12
MAX_GAMES = 1000
SAFE_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
HEX_SHA = re.compile(r"[a-f0-9]{64}\Z")
HEADER_FIELDS = (
    "Event", "Site", "Date", "Round", "White", "Black", "Result",
)


def _regular(path: Path, limit: int, *, root: Path | None = None) -> bytes:
    """Reject symlinks even when embedded in the input path."""
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Arquivos ou pastas simbólicos não são permitidos")
    if root is not None and not path.resolve().is_relative_to(root):
        raise ValueError("Origem PGN fora da pasta autorizada")
    if not path.is_file():
        raise ValueError("Arquivo local não encontrado")
    if path.stat().st_size > limit:
        raise ValueError("Arquivo excede o tamanho máximo permitido")
    data = path.read_bytes()
    if len(data) > limit or b"\0" in data:
        raise ValueError("Arquivo binário ou acima da quota")
    return data


def _source_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw.endswith(".pgn"):
        raise ValueError("A fonte deve usar nome .pgn relativo")
    posix = PurePosixPath(raw)
    if (posix.is_absolute() or not posix.parts or ".." in posix.parts
            or any(part in {".", ""} for part in posix.parts)
            or "\\" in raw):
        raise ValueError("Caminho PGN inválido ou atravessando diretórios")
    return Path(*posix.parts)


def _https(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        return False
    if any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and bool(parts.hostname) and
                "." in parts.hostname and not parts.username and
                not parts.password and not parts.query and not parts.fragment)
    except ValueError:
        return False


def _catalog(path: Path) -> tuple[list[dict], str]:
    raw = _regular(path, MAX_CATALOG_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Catálogo de direitos JSON inválido") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("Schema de direitos inválido")
    sources = data.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("Quantidade de fontes fora do limite")
    seen = set()
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError("Fonte sem metadados de direitos")
        if set(item) != {
            "id", "file", "source_url", "license_id", "license_evidence_url",
            "rights_reviewed_on", "rights_attestation", "expected_sha256",
            "allowed_uses", "attribution",
        }:
            raise ValueError("Contrato de direitos incompleto ou desconhecido")
        source_id = item["id"]
        if not isinstance(source_id, str) or not SAFE_ID.fullmatch(source_id) or source_id in seen:
            raise ValueError("ID de fonte inválido ou duplicado")
        seen.add(source_id)
        _source_path(item["file"])
        if not (_https(item["source_url"]) and _https(item["license_evidence_url"])):
            raise ValueError("Origem e evidência precisam de URLs HTTPS")
        if (not isinstance(item["license_id"], str) or
                item["license_id"] not in ALLOWED_LICENSES):
            raise ValueError("Licença não reconhecida: aprovação manual necessária")
        if (item["rights_attestation"] != "operator_reviewed" or
                not isinstance(item["attribution"], str) or
                not 1 <= len(item["attribution"].strip()) <= 500):
            raise ValueError("Atestação do operador e atribuição são obrigatórias")
        raw_date = item["rights_reviewed_on"]
        try:
            day = date.fromisoformat(raw_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Data de revisão de direitos inválida") from exc
        if day > date.today():
            raise ValueError("Revisão de direitos não pode ter data futura")
        uses = item["allowed_uses"]
        if (not isinstance(uses, list) or
                not all(isinstance(use, str) for use in uses) or
                len(uses) != len(set(uses)) or
                not (set(uses) <= {"local_cache", "derived_openings"} and
                     "local_cache" in uses)):
            raise ValueError("Uso local não foi autorizado pelo operador")
        if not isinstance(item["expected_sha256"], str) or not HEX_SHA.fullmatch(
                item["expected_sha256"]):
            raise ValueError("Checksum SHA-256 obrigatório")
    return sources, sha256(raw).hexdigest()


_GAP = re.compile(r"(?:\s+|[0-9]+\.(?:\.\.)?|\.\.)*\Z")
_HEADER = re.compile(r'\[[A-Za-z0-9][A-Za-z0-9_+#=:-]*\s+"[^"\r]*"\]\s*\Z')


class _ResultConsistentGameBuilder(chess.pgn.GameBuilder):
    """Reject conflicting PGN [Result] headers and movetext termination.

    python-chess normally preserves an explicit non-'*' Result header even if
    the final result token says something different. That is not safe for a
    provenance-preserving cache.
    """

    def visit_result(self, result: str) -> None:
        declared = self.game.headers.get("Result", "*")
        if declared != "*" and declared != result:
            self.game.errors.append(
                ValueError("Cabeçalho Result contradiz resultado dos lances"))
        super().visit_result(result)


def _strict_movetext(text: str) -> None:
    """Reject ignored PGN garbage and truncated SAN, unlike a lenient parser.

    python-chess searches for SAN-like regex substrings and ignores unknown
    movetext, which can silently truncate a game without setting game.errors.
    Validate complete lexical coverage, then retain python-chess legal-move
    validation as the semantic authority.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if not _HEADER.fullmatch(stripped):
                raise ValueError("Cabeçalho PGN inválido")
            continue
        lines.append(line)
    body = "\n".join(lines)
    body = re.sub(r"(?m)^\s*%[^\n]*", " ", body)
    body = re.sub(r";[^\n]*", " ", body)
    body = re.sub(r"\{[^{}]*\}", " ", body, flags=re.S)
    if "{" in body or "}" in body:
        raise ValueError("Comentário PGN inválido")
    cursor = 0
    variations = 0
    for match in chess.pgn.MOVETEXT_REGEX.finditer(body):
        if not _GAP.fullmatch(body[cursor:match.start()]):
            raise ValueError("Token de lance PGN não reconhecido")
        token = match.group()
        if token in {"--", "Z0", "0000", "@@@@"}:
            raise ValueError("Lance nulo não permitido no PGN padrão")
        if token == "(":
            variations += 1
        elif token == ")":
            variations -= 1
            if variations < 0:
                raise ValueError("Variação PGN sem abertura")
        cursor = match.end()
        # The python-chess lexer does not include check/mate suffixes in its
        # SAN regex, so accept one immediately following + or #.
        if token not in {"(", ")", "1-0", "0-1", "1/2-1/2", "*"} and (
                token.startswith(("O-O", "0-0")) or
                (token and token[0] in "abcdefghNBKRQ")):
            if body[cursor:cursor + 1] in ("+", "#"):
                cursor += 1
    if variations != 0 or not _GAP.fullmatch(body[cursor:]):
        raise ValueError("PGN incompleto ou com texto ignorado pelo parser")


def _scan(catalog_path: Path, *, max_games: int, max_bytes: int) -> tuple[dict, bytes]:
    sources, catalog_hash = _catalog(catalog_path)
    root = catalog_path.parent.resolve()
    observed = {}
    source_report = []
    rejected = Counter()
    total = 0
    for src in sources:
        path = catalog_path.parent / _source_path(src["file"])
        raw = _regular(path, MAX_PGN_BYTES, root=root)
        digest = sha256(raw).hexdigest()
        if digest != src["expected_sha256"]:
            raise ValueError("Checksum PGN diverge do catálogo; revalidar direitos e arquivo")
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("PGN não está em UTF-8") from exc
        _strict_movetext(decoded)
        stream = StringIO(decoded)
        source_games, source_unique, source_dupes = 0, 0, 0
        names = Counter()
        while True:
            game = chess.pgn.read_game(stream, Visitor=_ResultConsistentGameBuilder)
            if game is None:
                break
            total += 1
            source_games += 1
            if total > max_games:
                raise ValueError("Número de partidas excede a quota")
            if game.errors:
                raise ValueError("PGN contém movimento ilegal ou erro sintático")
            if game.headers.get("Variant", "Standard") not in {"Standard", "Chess"}:
                raise ValueError("Variante não padrão exige fluxo separado")
            digest_game, plies = mainline_identity(game)
            if plies == 0:
                rejected["zero_ply"] += 1
                continue
            # The derived cache contains only headers plus the mainline.
            clean = chess.pgn.Game()
            for key in HEADER_FIELDS:
                if key in game.headers:
                    clean.headers[key] = game.headers[key]
            board = game.board()
            # Custom SetUp/FEN games require retaining their initial position.
            if game.headers.get("SetUp") == "1":
                clean.setup(board)
            node = clean
            for move in game.mainline_moves():
                if move not in board.legal_moves:
                    raise ValueError("PGN com lance inválido")
                node = node.add_variation(move)
                board.push(move)
            clean.headers["Result"] = game.headers.get("Result", "*")
            text = clean.accept(chess.pgn.StringExporter(
                headers=True, variations=False, comments=False)).rstrip() + "\n\n"
            if digest_game in observed:
                if observed[digest_game]["result"] != clean.headers["Result"]:
                    raise ValueError("Resultados contraditórios para a mesma trajetória")
                observed[digest_game]["sources"].add(src["id"])
                source_dupes += 1
                continue
            observed[digest_game] = {
                "pgn": text, "plies": plies, "result": clean.headers["Result"],
                "sources": {src["id"]},
            }
            source_unique += 1
            for side in ("White", "Black"):
                player = game.headers.get(side, "").strip()
                if player and player != "?":
                    names[player] += 1
        source_report.append({
            "id": src["id"], "source_url": src["source_url"],
            "license_id": src["license_id"],
            "license_evidence_url": src["license_evidence_url"],
            "rights_reviewed_on": src["rights_reviewed_on"],
            "attribution": src["attribution"],
            "allowed_uses": src["allowed_uses"],
            "pgn_sha256": digest, "input_bytes": len(raw),
            "games_total": source_games, "unique_new": source_unique,
            "exact_duplicates": source_dupes,
            "players_seen_in_new_games": dict(sorted(names.items())),
        })
    if not observed:
        raise ValueError("Nenhuma partida com lances para armazenar")
    ordered = sorted(observed.items())
    pgn_output = "".join(entry["pgn"] for _, entry in ordered).encode("utf-8")
    if len(pgn_output) > max_bytes:
        raise ValueError("Cache excede o orçamento de disco")
    report = {
        "schema": CACHE_SCHEMA,
        "status": "READY_FOR_LOCAL_CACHE",
        "rights_status": "OPERATOR_ATTESTED_NOT_INDEPENDENTLY_VERIFIED",
        "source_catalog_sha256": catalog_hash,
        "sources": source_report,
        "games_examined": total, "games_distinct": len(ordered),
        "games_duplicates": sum(x["exact_duplicates"] for x in source_report),
        "games_excluded": dict(rejected),
        "complete_results": sum(x["result"] in {"1-0", "0-1", "1/2-1/2"}
                                for _, x in ordered),
        "incomplete_results": sum(x["result"] == "*" for _, x in ordered),
        "cache_pgn_sha256": sha256(pgn_output).hexdigest(),
        "estimated_pgn_bytes": len(pgn_output),
        "max_disk_bytes": max_bytes,
        "games": [
            {"mainline_sha256": key, "plies": entry["plies"],
             "result": entry["result"], "sources": sorted(entry["sources"])}
            for key, entry in ordered
        ],
        "limitations": [
            "Operator attestation is not independent legal verification.",
            "No network, engine, historical calibration, validation or sealed holdout is accessed.",
            "PGN metadata does not prove player identity, age, completeness or event provenance.",
            "This cache is not automatically eligible for style calibration or public redistribution.",
        ],
    }
    return report, pgn_output


def prepare_cache(catalog: Path, *, out: Path, apply: bool = False,
                  max_games: int = 500, max_cache_mb: int = 32) -> dict:
    """Validate every input in preflight; write only with explicit --apply."""
    if (type(max_games) is not int or not 1 <= max_games <= MAX_GAMES or
            type(max_cache_mb) is not int or not 1 <= max_cache_mb <= 512):
        raise ValueError("Limites de jogos/disco inválidos")
    report, content = _scan(catalog, max_games=max_games,
                            max_bytes=max_cache_mb * 1024 * 1024)
    out = out.expanduser()
    report["operation"] = "APPLIED" if apply else "DRY_RUN"
    # Include the audit manifest in the real disk budget. Its own length
    # fields are determined by a stable fixed point (usually two iterations).
    report["estimated_manifest_bytes"] = 0
    report["estimated_cache_bytes"] = 0
    while True:
        encoded = (json.dumps(report, ensure_ascii=False, sort_keys=True,
                              indent=2) + "\n").encode("utf-8")
        size = len(encoded)
        total = len(content) + size
        if (report["estimated_manifest_bytes"] == size and
                report["estimated_cache_bytes"] == total):
            break
        report["estimated_manifest_bytes"] = size
        report["estimated_cache_bytes"] = total
    if total > max_cache_mb * 1024 * 1024:
        raise ValueError("Cache mais manifesto excedem o orçamento de disco")
    if not apply:
        return report
    if out.exists() or out.is_symlink():
        raise FileExistsError("Diretório de cache já existe")
    parent = out.parent
    parent.mkdir(parents=True, exist_ok=True)
    # A temporary sibling avoids exposing a half-written final cache.
    stage = Path(mkdtemp(prefix=".chesssim-cache-", dir=parent))
    try:
        pgn = stage / "games.pgn"
        pgn.write_bytes(content)
        (stage / "manifest.json").write_bytes(encoded)
        # Exclusive mkdir is atomic and cannot replace an empty directory
        # created by another process in between validation and commit.
        out.mkdir(mode=0o700, exist_ok=False)
        try:
            (stage / "games.pgn").replace(out / "games.pgn")
            # The manifest is the final commit marker: consumers must only
            # read caches that contain both files with matching hashes.
            (stage / "manifest.json").replace(out / "manifest.json")
        except BaseException:
            shutil.rmtree(out)
            raise
    finally:
        shutil.rmtree(stage)
    return report
