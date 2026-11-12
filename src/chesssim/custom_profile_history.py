"""Append-only local display-name history for fictional identities (#63, slice 2).

The v1 identity record is never rewritten. Consecutive hashed revision files
form a local chain. A portable bundle contains the base and its full chain,
but neither checksums nor an operator name establish authentic authorship.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from . import custom_profiles as core

EVENT_SCHEMA = "chesssim-custom-fictional-rename-v1"
BUNDLE_SCHEMA = "chesssim-custom-fictional-portable-v2"
MAX_RENAMES = 64
MAX_PORTABLE_BYTES = 512 * 1024
EVENT_SUFFIX = re.compile(r"\.rename\.(\d{6})\.json\Z")
EVENT_FIELDS = frozenset({
    "id", "revision", "profile_sha256", "previous_sha256",
    "previous_name", "new_name", "recorded_utc",
})


def _event_path(root: Path, identifier: str, revision: int) -> Path:
    return root / f"{identifier}.rename.{revision:06d}.json"


def _validate_time(raw: object) -> None:
    if not isinstance(raw, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", raw
    ):
        raise ValueError("Data de revisão UTC inválida")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Data de revisão UTC impossível") from exc
    if parsed > datetime.now(timezone.utc):
        raise ValueError("Data de revisão futura não aceita")


def _verify_event(row: object, base_doc: dict, *, expected_revision: int,
                  previous_hash: str, previous_name: str) -> tuple[str, str]:
    if not isinstance(row, dict) or set(row) != {
        "schema", "event", "sha256"
    } or row.get("schema") != EVENT_SCHEMA:
        raise ValueError("Formato da revisão não reconhecido")
    event = row.get("event")
    if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        raise ValueError("Campos da revisão não reconhecidos")
    if (event.get("id") != base_doc["profile"]["id"]
            or event.get("profile_sha256") != base_doc["sha256"]
            or type(event.get("revision")) is not int
            or event["revision"] != expected_revision
            or event.get("previous_sha256") != previous_hash):
        raise ValueError("Cadeia de revisão divergente")
    if (event.get("previous_name") != previous_name
            or not isinstance(event.get("new_name"), str)):
        raise ValueError("Nome anterior ou novo inconsistente")
    core._clean_name(event["new_name"])
    if event["new_name"] == previous_name:
        raise ValueError("Revisão não pode ser uma mudança nula")
    _validate_time(event.get("recorded_utc"))
    digest = row.get("sha256")
    if (not isinstance(digest, str) or not core.DIGEST.fullmatch(digest)
            or digest != core._sha(event)):
        raise ValueError("Checksum de revisão incorreto")
    return event["new_name"], digest


def _check_chain(doc: dict, rows: list[dict]) -> tuple[str, str]:
    if len(rows) > MAX_RENAMES:
        raise ValueError("Limite de 64 renomeações excedido")
    name = doc["profile"]["display_name"]
    previous_hash = doc["sha256"]
    for revision, row in enumerate(rows, 1):
        name, previous_hash = _verify_event(
            row, doc, expected_revision=revision,
            previous_hash=previous_hash, previous_name=name)
    return name, previous_hash


def _revisions(root: Path, doc: dict) -> list[dict]:
    identity = doc["profile"]["id"]
    prefix = identity + ".rename."
    paths = []
    for candidate in root.iterdir():
        if not candidate.name.startswith(prefix):
            continue
        match = EVENT_SUFFIX.fullmatch(candidate.name[len(identity):])
        if match is None:
            raise ValueError("Arquivo de revisão com nome inesperado")
        version = int(match.group(1))
        if not 1 <= version <= MAX_RENAMES:
            raise ValueError("Número de revisão fora do limite")
        paths.append((version, candidate))
        if len(paths) > MAX_RENAMES:
            raise ValueError("Número máximo de revisões excedido")
    paths.sort()
    if [version for version, _ in paths] != list(range(1, len(paths) + 1)):
        raise ValueError("Cadeia de revisão interrompida")
    rows = [core._json(core._read_regular(path)) for _, path in paths]
    _check_chain(doc, rows)
    return rows


def current_name(root: Path, doc: dict) -> dict:
    rows = _revisions(root, doc)
    name, head = _check_chain(doc, rows)
    return {"display_name": name, "revision": len(rows),
            "revision_sha256": head}


def rename_fictional(library: Path, identifier: str, new_name: str,
                     expected_head: str) -> dict:
    """Compare-and-append: stale clients fail without creating a revision."""
    new_name = core._clean_name(new_name)
    if (not isinstance(expected_head, str)
            or not core.DIGEST.fullmatch(expected_head)):
        raise ValueError("Informe SHA-256 válido da revisão corrente")
    root = core._library(library)
    if not root.is_dir():
        raise ValueError("Biblioteca inexistente")
    with core._exclusive_library(root):
        doc, _ = core._read_id(root, identifier)
        if core._read_retirement(root, doc):
            raise ValueError("Reative o personagem antes de renomear")
        rows = _revisions(root, doc)
        old_name, head = _check_chain(doc, rows)
        if head != expected_head:
            raise ValueError("Revisão esperada obsoleta; consultar histórico")
        if new_name == old_name:
            return {**core._summary(root, doc), "operation": "ALREADY_NAMED"}
        if len(rows) >= MAX_RENAMES:
            raise ValueError("Número máximo de revisões atingido")
        event = {
            "id": identifier,
            "revision": len(rows) + 1,
            "profile_sha256": doc["sha256"],
            "previous_sha256": head,
            "previous_name": old_name,
            "new_name": new_name,
            "recorded_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        }
        row = {"schema": EVENT_SCHEMA, "event": event,
               "sha256": core._sha(event)}
        core._publish_exclusive(_event_path(root, identifier, len(rows) + 1),
                                core._encode(row))
        return {**core._summary(root, doc), "operation": "RENAMED_METADATA_ONLY"}


def history_fictional(library: Path, identifier: str) -> dict:
    root = core._library(library)
    if not root.is_dir():
        raise ValueError("Biblioteca inexistente")
    with core._exclusive_library(root):
        doc, _ = core._read_id(root, identifier)
        changes = _revisions(root, doc)
        return {
            **core._summary(root, doc),
            "original_display_name": doc["profile"]["display_name"],
            "history": [{
                "revision": row["event"]["revision"],
                "previous_name": row["event"]["previous_name"],
                "new_name": row["event"]["new_name"],
                "recorded_utc": row["event"]["recorded_utc"],
                "previous_sha256": row["event"]["previous_sha256"],
                "revision_sha256": row["sha256"],
            } for row in changes],
            "evidence_limit": "Operator-controlled hashes, not signed identities",
        }


def export_bytes(root: Path, doc: dict, raw_base: bytes) -> bytes:
    rows = _revisions(root, doc)
    if not rows:
        return raw_base  # Preserve the v1 portable format and byte identity.
    body = {"schema": BUNDLE_SCHEMA, "identity": doc, "revisions": rows}
    bundle = {**body, "sha256": core._sha(body)}
    output = core._encode(bundle)
    if len(output) > MAX_PORTABLE_BYTES:
        raise ValueError("Bundle ultrapassa limite de tamanho")
    return output


def _parse_portable(raw: bytes) -> tuple[dict, list[dict]]:
    document = core._json(raw)
    if document.get("schema") == core.SCHEMA:
        identity = core._validate_record(raw)
        return identity, []
    if (set(document) != {"schema", "identity", "revisions", "sha256"}
            or document.get("schema") != BUNDLE_SCHEMA):
        raise ValueError("Bundle portátil desconhecido")
    base = document.get("identity")
    revisions = document.get("revisions")
    if (not isinstance(base, dict) or not isinstance(revisions, list)
            or not revisions or len(revisions) > MAX_RENAMES
            or not isinstance(document.get("sha256"), str)
            or not core.DIGEST.fullmatch(document["sha256"])):
        raise ValueError("Bundle portátil inválido")
    identity = core._validate_record(core._encode(base))
    _check_chain(identity, revisions)
    unsigned = {"schema": BUNDLE_SCHEMA, "identity": identity,
                "revisions": revisions}
    if document["sha256"] != core._sha(unsigned):
        raise ValueError("Checksum do bundle incorreto")
    return identity, revisions


def import_portable(library: Path, raw: bytes) -> dict:
    """Import full chain, appending only verified suffix under library lock.

    A failed import can leave a valid prefix of the chain; reimporting the
    exact bundle can resume. It never rewrites a previous revision.
    """
    doc, incoming = _parse_portable(raw)  # no filesystem writes before preflight
    root = core._library(library, create=True)
    with core._exclusive_library(root):
        identity = doc["profile"]["id"]
        path = core._record_path(root, identity)
        added_identity = False
        if path.exists() or path.is_symlink():
            existing, _ = core._read_id(root, identity)
            if existing != doc:
                raise FileExistsError("Mesmo ID com identidade original divergente")
        else:
            if len(core._inventory(root)) >= core.MAX_LIBRARY_PROFILES:
                raise ValueError("Biblioteca cheia (256 perfis)")
            core._publish_exclusive(path, core._encode(doc))
            added_identity = True
        old = _revisions(root, doc)
        for idx, row in enumerate(old[:len(incoming)]):
            if row != incoming[idx]:
                raise FileExistsError("Mesmo ID com histórico de revisão divergente")
        for idx, row in enumerate(incoming[len(old):], len(old) + 1):
            core._publish_exclusive(_event_path(root, identity, idx),
                                    core._encode(row))
        return {**core._summary(root, doc),
                "operation": (
                    "IMPORTED_METADATA_ONLY" if added_identity
                    else "IMPORTED_NEW_REVISIONS" if len(incoming) > len(old)
                    else "ALREADY_PRESENT")}
