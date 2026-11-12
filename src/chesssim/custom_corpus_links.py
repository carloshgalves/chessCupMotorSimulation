"""Metadata-only, operator-attested PGN corpus association for fictional players.

Slice #63/4. Never copies PGN moves, enables UCI, or treats a name match
as proof of historical identity. Every link or detach is an exclusive
append-only local receipt, serialized by the existing library lock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata

from . import custom_profiles as core
from .age_cohort import _cache_snapshot, _full_date, _header_name, _normalize_name

SCHEMA = "chesssim-custom-corpus-event-v1"
MAX_EVENTS = 32
EVENT_NAME = re.compile(r"\.corpus\.(\d{6})\.json\Z")
ACTIONS = frozenset({"LINK", "DETACH"})
FIELDS = frozenset({
    "id", "sequence", "profile_sha256", "previous_sha256", "action",
    "created_utc", "name_revision_sha256", "link",
})
LINK_FIELDS = frozenset({
    "cache_pgn_sha256", "source_catalog_sha256", "source_ids",
    "rights_status", "player_pgn_name_operator_attested", "selection_sha256",
    "games", "white_games", "black_games", "with_valid_full_date",
    "unknown_or_invalid_date", "with_declared_result", "coverage_band",
    "selection_status",
})
COVERAGE = (
    (0, "ZERO_GAMES"), (19, "LOW_1_TO_19"),
    (199, "EXPLORATORY_20_TO_199"),
)


def _clean_name(value: str) -> str:
    normalized = _normalize_name(value)
    if not isinstance(value, str) or len(value) > 150 or not value.strip():
        raise ValueError("Nome completo PGN obrigatório (até 150 caracteres)")
    if (value != unicodedata.normalize("NFC", value)
            or any(unicodedata.category(ch).startswith("C") for ch in value)):
        raise ValueError("Nome PGN deve ser NFC e não conter controles invisíveis")
    return normalized


def _band(count: int) -> str:
    for limit, name in COVERAGE:
        if count <= limit:
            return name
    return "EXPLORATORY_200_PLUS"


def _event_path(root: Path, identifier: str, seq: int) -> Path:
    return root / f"{identifier}.corpus.{seq:06d}.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("Timestamp UTC inválido do evento")
    try:
        day = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Timestamp impossível do evento") from exc
    if day > datetime.now(timezone.utc):
        raise ValueError("Timestamp de evento no futuro")
    return value


def _check_link(obj: object) -> None:
    if not isinstance(obj, dict) or set(obj) != LINK_FIELDS:
        raise ValueError("Snapshot de corpus com campos desconhecidos")
    for name in ("cache_pgn_sha256", "source_catalog_sha256",
                 "selection_sha256"):
        if (not isinstance(obj[name], str) or
                not core.DIGEST.fullmatch(obj[name])):
            raise ValueError("Hash de corpus inválido")
    if obj["rights_status"] != "OPERATOR_ATTESTED_NOT_INDEPENDENTLY_VERIFIED":
        raise ValueError("Status de direitos do corpus inválido")
    _clean_name(obj["player_pgn_name_operator_attested"])
    sources = obj["source_ids"]
    if (not isinstance(sources, list) or not 1 <= len(sources) <= 12
            or any(not isinstance(x, str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,39}", x) for x in sources)
            or sorted(set(sources)) != sources):
        raise ValueError("Fontes do corpus inválidas")
    count = obj["games"]
    for key in ("games", "white_games", "black_games", "with_valid_full_date",
                "unknown_or_invalid_date", "with_declared_result"):
        if type(obj[key]) is not int or not 0 <= obj[key] <= 1000:
            raise ValueError("Cobertura numérica do corpus inválida")
    if (obj["white_games"] + obj["black_games"] != count
            or obj["with_valid_full_date"] + obj["unknown_or_invalid_date"] != count
            or obj["with_declared_result"] > count
            or obj["coverage_band"] != _band(count)
            or obj["selection_status"] != (
                "NO_MATCHING_GAMES" if count == 0 else "SNAPSHOT_NOT_VALIDATED")):
        raise ValueError("Estatísticas do corpus incoerentes")


def _selection(cache: Path, name: str, *,
               snapshot: tuple | None = None) -> dict:
    """Verify a cache, or reuse one already verified immutable in-memory snapshot.

    Downstream training must re-use the exact parsed games for the receipt
    reconciliation: reading the mutable cache a second time creates a TOCTOU
    gap between provenance and training export.
    """
    normalized = _clean_name(name)
    report, games = (snapshot if snapshot is not None else
                     _cache_snapshot(cache.expanduser()))
    source_hash = report.get("source_catalog_sha256")
    if not isinstance(source_hash, str) or not core.DIGEST.fullmatch(source_hash):
        raise ValueError("Hash do catálogo de direitos ausente")
    ids = sorted(s["id"] for s in report["sources"])
    matched = []
    white = black = dated = declared = 0
    for game, digest in games:
        is_white = _header_name(game.headers.get("White")) == normalized
        is_black = _header_name(game.headers.get("Black")) == normalized
        if is_white and is_black:
            # Ambiguous self-play identity is not evidence for the person.
            continue
        if not is_white and not is_black:
            continue
        matched.append(digest)
        white += int(is_white)
        black += int(is_black)
        dated += int(_full_date(game.headers.get("Date")) is not None)
        declared += int(game.headers.get("Result") in ("1-0", "0-1", "1/2-1/2"))
    matched.sort()
    selection = core._sha({
        "cache_pgn_sha256": report["cache_pgn_sha256"],
        "player_pgn_name": normalized,
        "mainline_sha256_sorted": matched,
    })
    count = len(matched)
    output = {
        "cache_pgn_sha256": report["cache_pgn_sha256"],
        "source_catalog_sha256": source_hash,
        "source_ids": ids,
        "rights_status": report["rights_status"],
        "player_pgn_name_operator_attested": name.strip(),
        "selection_sha256": selection,
        "games": count, "white_games": white, "black_games": black,
        "with_valid_full_date": dated,
        "unknown_or_invalid_date": count - dated,
        "with_declared_result": declared, "coverage_band": _band(count),
        "selection_status": (
            "NO_MATCHING_GAMES" if count == 0 else "SNAPSHOT_NOT_VALIDATED"),
    }
    _check_link(output)
    return output


def _events(root: Path, doc: dict) -> list[dict]:
    ident = doc["profile"]["id"]
    prefix = ident + ".corpus."
    found = []
    for item in root.iterdir():
        if not item.name.startswith(prefix):
            continue
        suffix = item.name[len(ident):]
        match = EVENT_NAME.fullmatch(suffix)
        if match is None:
            raise ValueError("Arquivo de evento de corpus fora do padrão")
        number = int(match.group(1))
        if not 1 <= number <= MAX_EVENTS:
            raise ValueError("Sequência de corpus fora da quota")
        found.append((number, item))
        if len(found) > MAX_EVENTS:
            raise ValueError("Limite de 32 eventos de corpus excedido")
    found.sort()
    if [n for n, _ in found] != list(range(1, len(found) + 1)):
        raise ValueError("Cadeia de eventos de corpus interrompida")
    # The stored name revision must exist in this library and must predate
    # the link event. A syntactically valid, arbitrary hash is insufficient.
    from .custom_profile_history import _revisions
    revision_times = {doc["sha256"]: doc["profile"]["created_utc"]}
    for revision in _revisions(root, doc):
        revision_times[revision["sha256"]] = revision["event"]["recorded_utc"]
    head = doc["sha256"]
    last_time = doc["profile"]["created_utc"]
    active = False
    events = []
    for seq, path in found:
        obj = core._json(core._read_regular(path))
        if set(obj) != {"schema", "event", "sha256"} or obj["schema"] != SCHEMA:
            raise ValueError("Evento de corpus desconhecido")
        event = obj["event"]
        if not isinstance(event, dict) or set(event) != FIELDS:
            raise ValueError("Campos do evento de corpus desconhecidos")
        if (event["id"] != ident or type(event["sequence"]) is not int
                or event["sequence"] != seq
                or event["profile_sha256"] != doc["sha256"]
                or event["previous_sha256"] != head
                or not isinstance(event["name_revision_sha256"], str)
                or not core.DIGEST.fullmatch(event["name_revision_sha256"])
                or not isinstance(event["action"], str)
                or event["action"] not in ACTIONS):
            raise ValueError("Cadeia ou identidade do corpus divergente")
        stamp = _utc(event["created_utc"])
        if stamp < last_time:
            raise ValueError("Cronologia de corpus regressiva")
        known_time = revision_times.get(event["name_revision_sha256"])
        if known_time is None or known_time > stamp:
            raise ValueError("Revisão de nome do vínculo desconhecida ou futura")
        last_time = stamp
        if event["action"] == "LINK":
            if active:
                raise ValueError("Vinculação de corpus duplicada sem desvincular")
            _check_link(event["link"])
            active = True
        else:
            if not active or event["link"] is not None:
                raise ValueError("Desvinculação de corpus inconsistente")
            active = False
        digest = obj.get("sha256")
        if (not isinstance(digest, str)
                or not core.DIGEST.fullmatch(digest)
                or digest != core._sha(event)):
            raise ValueError("Checksum de evento de corpus inconsistente")
        head = digest
        events.append(obj)
    return events


def _project(root: Path, doc: dict, events: list[dict]) -> dict:
    active = events[-1]["event"]["action"] == "LINK" if events else False
    linked = events[-1]["event"]["link"] if active else None
    return {
        "schema": "chesssim-custom-corpus-status-v1",
        "id": doc["profile"]["id"],
        "display_name": core._summary(root, doc)["display_name"],
        "profile_sha256": doc["sha256"],
        "sequence": len(events),
        "head_sha256": events[-1]["sha256"] if events else doc["sha256"],
        "linked": active,
        "corpus": linked,
        # The path is deliberately not stored. A link is an audit receipt,
        # never an assertion that its external PGNs remain accessible.
        "source_availability": (
            "NOT_RECHECKED_AFTER_LINK" if active else "NOT_APPLICABLE"),
        "uci_eligible": False,
        "historically_validated": False,
        "limit": "PGN name, source rights and identity are operator-attested; UCI is not enabled",
    }


def corpus_status(library: Path, identifier: str) -> dict:
    root = core._library(library)
    if not root.is_dir():
        raise ValueError("Biblioteca de personagens inexistente")
    with core._exclusive_library(root):
        doc, _ = core._read_id(root, identifier)
        events = _events(root, doc)
        return _project(root, doc, events)


def _append(root: Path, doc: dict, events: list[dict], *, action: str,
            link: dict | None, expected_head: str) -> dict:
    head = events[-1]["sha256"] if events else doc["sha256"]
    if (not isinstance(expected_head, str)
            or not core.DIGEST.fullmatch(expected_head)
            or expected_head != head):
        raise ValueError("Cabeça de eventos de corpus obsoleta ou inválida")
    active = bool(events and events[-1]["event"]["action"] == "LINK")
    if action == "DETACH" and not active:
        return {**_project(root, doc, events), "operation": "ALREADY_DETACHED"}
    if action == "LINK" and active:
        if events[-1]["event"]["link"] == link:
            return {**_project(root, doc, events), "operation": "ALREADY_LINKED"}
        raise ValueError("Desvincule o corpus anterior antes de vincular outro")
    if len(events) >= MAX_EVENTS:
        raise ValueError("Limite de 32 eventos de corpus excedido")
    from .custom_profile_history import current_name
    event = {
        "id": doc["profile"]["id"], "sequence": len(events) + 1,
        "profile_sha256": doc["sha256"],
        "previous_sha256": head, "action": action,
        "created_utc": _now(),
        "name_revision_sha256": current_name(root, doc)["revision_sha256"],
        "link": link,
    }
    row = {"schema": SCHEMA, "event": event, "sha256": core._sha(event)}
    raw = core._encode(row)
    if len(raw) > core.MAX_RECORD_BYTES:
        raise ValueError("Recibo de corpus excede quota de 4 KiB")
    core._publish_exclusive(_event_path(root, doc["profile"]["id"],
                                        len(events) + 1), raw)
    return {**_project(root, doc, [*events, row]), "operation": (
        "LINKED_METADATA_ONLY" if action == "LINK" else "DETACHED_METADATA_ONLY")}


def corpus_link(library: Path, identifier: str, cache: Path, *,
                pgn_name: str, expected_head: str) -> dict:
    # Input is parsed and verified before any mutation in the local library.
    proposal = _selection(cache, pgn_name)
    root = core._library(library)
    if not root.is_dir():
        raise ValueError("Biblioteca de personagens inexistente")
    with core._exclusive_library(root):
        doc, _ = core._read_id(root, identifier)
        if core._read_retirement(root, doc):
            raise ValueError("Personagem desativado não pode vincular corpus")
        return _append(root, doc, _events(root, doc), action="LINK",
                       link=proposal, expected_head=expected_head)


def corpus_detach(library: Path, identifier: str, *, expected_head: str) -> dict:
    root = core._library(library)
    if not root.is_dir():
        raise ValueError("Biblioteca de personagens inexistente")
    with core._exclusive_library(root):
        doc, _ = core._read_id(root, identifier)
        return _append(root, doc, _events(root, doc), action="DETACH",
                       link=None, expected_head=expected_head)
