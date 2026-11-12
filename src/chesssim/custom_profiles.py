"""Local, metadata-only fictional player identity registry (#63, slice 1).

Never modifies the canonical 64-player roster, profile files, Rodent UCI
settings, games, competitions, or historical evidence. Portable exports are
checksummed for accidental corruption, NOT signed or identity-verified.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from uuid import uuid4

from .matches import _fsync_directory_tree

SCHEMA = "chesssim-custom-fictional-identity-v1"
RETIRED_SCHEMA = "chesssim-custom-fictional-retirement-v1"
PROFILE_ID = re.compile(r"user-[0-9a-f]{32}\Z")
FILENAME = re.compile(r"user-[0-9a-f]{32}\.json\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_RECORD_BYTES = 4096
MAX_LIBRARY_PROFILES = 256
STATUS = "METADATA_ONLY_NOT_PLAYABLE"
KIND = "fictional-player"
ALLOWED_PUNCTUATION = {" ", "-", "'", "’", ".", "(", ")"}
FIELDS = frozenset({
    "id", "display_name", "kind", "created_utc", "status",
    "identity_basis", "uci_eligible", "historical_fidelity_validated",
})


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("JSON contém chave duplicada")
        obj[key] = value
    return obj


def _sha(obj: dict) -> str:
    encoded = json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _clean_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Nome deve ser string")
    if not 2 <= len(name) <= 80 or name != unicodedata.normalize("NFC", name):
        raise ValueError("Nome deve ser NFC e ter 2..80 caracteres")
    if name != " ".join(name.split()) or name[0] == " " or name[-1] == " ":
        raise ValueError("Nome deve usar espaços simples")
    for char in name:
        category = unicodedata.category(char)
        if char not in ALLOWED_PUNCTUATION and category[0] not in ("L", "M", "N"):
            raise ValueError("Nome contém pontuação ou caractere de controle proibido")
    if not any(unicodedata.category(ch).startswith("L") for ch in name):
        raise ValueError("Nome deve conter letras")
    return name


def _safe_path(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("Caminho com symlink não permitido")
    return absolute


def _library(folder: Path, *, create: bool = False) -> Path:
    root = _safe_path(folder)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.exists() and not root.is_dir():
        raise ValueError("A biblioteca deve ser diretório")
    return root


def _read_regular(path: Path) -> bytes:
    _safe_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Registro ausente ou inacessível") from exc
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
            raise ValueError("Registro não regular ou excessivo")
        raw = stream.read(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES or b"\x00" in raw:
        raise ValueError("Registro excede limite ou é binário")
    return raw


def _json(raw: bytes) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Registro JSON inválido") from exc
    if not isinstance(doc, dict):
        raise ValueError("Registro JSON deve ser objeto")
    return doc


def _validate_record(raw: bytes) -> dict:
    doc = _json(raw)
    if set(doc) != {"schema", "profile", "sha256"} or doc.get("schema") != SCHEMA:
        raise ValueError("Formato do registro não autorizado")
    payload = doc.get("profile")
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError("Campos do perfil não autorizados")
    identity = payload.get("id")
    if not isinstance(identity, str) or not PROFILE_ID.fullmatch(identity):
        raise ValueError("Identificador de perfil inválido")
    _clean_name(payload.get("display_name"))
    if (payload.get("kind") != KIND or payload.get("status") != STATUS
            or payload.get("identity_basis") != "OPERATOR_DECLARED_FICTION"
            or payload.get("uci_eligible") is not False
            or payload.get("historical_fidelity_validated") is not False):
        raise ValueError("Falsificação de status de perfil ou tipo")
    date_text = payload.get("created_utc")
    if not isinstance(date_text, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", date_text
    ):
        raise ValueError("Data UTC de criação inválida")
    try:
        date_parsed = datetime.strptime(date_text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Data UTC de criação impossível") from exc
    if date_parsed > datetime.now(timezone.utc):
        raise ValueError("Data de criação não pode estar no futuro")
    if (not isinstance(doc.get("sha256"), str)
            or not DIGEST.fullmatch(doc["sha256"])
            or doc["sha256"] != _sha(payload)):
        raise ValueError("SHA-256 do perfil não corresponde aos metadados")
    return doc


def _encode(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")


def _publish_exclusive(path: Path, raw: bytes) -> None:
    """Create complete file before exclusive hardlink. No partial final path.

    No retry or silent overwrite on ENOSPC, unavailable hardlinks, or races.
    """
    _safe_path(path)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)
        if os.name == "posix":
            _fsync_directory_tree(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _record_path(root: Path, identifier: str) -> Path:
    if not isinstance(identifier, str) or not PROFILE_ID.fullmatch(identifier):
        raise ValueError("ID canônico de perfil inválido")
    return root / f"{identifier}.json"


def _retirement_path(root: Path, identifier: str) -> Path:
    return root / f"{identifier}.retired.json"


def _read_retirement(root: Path, doc: dict) -> bool:
    key = doc["profile"]["id"]
    marker = _retirement_path(root, key)
    if not marker.exists() and not marker.is_symlink():
        return False
    data = _json(_read_regular(marker))
    if (set(data) != {"schema", "id", "profile_sha256"}
            or data.get("schema") != RETIRED_SCHEMA
            or data.get("id") != key
            or data.get("profile_sha256") != doc["sha256"]):
        raise ValueError("Marcador de desativação inconsistente")
    return True


def _read_id(root: Path, identifier: str) -> tuple[dict, bytes]:
    raw = _read_regular(_record_path(root, identifier))
    doc = _validate_record(raw)
    if doc["profile"]["id"] != identifier:
        raise ValueError("Identidade no caminho e arquivo divergem")
    return doc, raw


def _inventory(root: Path) -> list[dict]:
    if not root.exists():
        return []
    entries = []
    for item in root.iterdir():
        if FILENAME.fullmatch(item.name):
            entries.append(item)
            if len(entries) > MAX_LIBRARY_PROFILES:
                raise ValueError("Biblioteca ultrapassa cota de 256 perfis")
    return sorted(entries)


def _summary(root: Path, doc: dict) -> dict:
    p = doc["profile"]
    return {
        "id": p["id"], "display_name": p["display_name"], "kind": KIND,
        "status": "RETIRED_METADATA_ONLY" if _read_retirement(root, doc) else STATUS,
        "created_utc": p["created_utc"], "profile_sha256": doc["sha256"],
        "uci_eligible": False, "historical_fidelity_validated": False,
    }



@contextmanager
def _exclusive_library(root: Path):
    """Serialize cooperative writers, including capacity and lifecycle state.

    The lock is per local library. It is advisory on POSIX; an operator with
    direct write access can bypass it, so it is not an authentication boundary.
    A crash releases the OS file lock automatically.
    """
    lock = root / ".custom-player.lock"
    if lock.is_symlink():
        raise ValueError("Lock simbólico da biblioteca não permitido")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags, 0o600)
    with os.fdopen(fd, "r+b") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Arquivo de lock da biblioteca inválido")
        if os.name == "posix":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            raise OSError("Plataforma sem mecanismo de lock suportado")


def create_fictional(library: Path, name: str) -> dict:
    name = _clean_name(name)
    root = _library(library, create=True)
    with _exclusive_library(root):
        if len(_inventory(root)) >= MAX_LIBRARY_PROFILES:
            raise ValueError("Biblioteca cheia (256 perfis)")
        identifier = "user-" + uuid4().hex
        payload = {
            "id": identifier, "display_name": name, "kind": KIND,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": STATUS, "identity_basis": "OPERATOR_DECLARED_FICTION",
            "uci_eligible": False, "historical_fidelity_validated": False,
        }
        doc = {"schema": SCHEMA, "profile": payload, "sha256": _sha(payload)}
        _publish_exclusive(_record_path(root, identifier), _encode(doc))
        return _summary(root, doc)


def list_fictional(library: Path) -> list[dict]:
    root = _library(library)
    if not root.exists():
        return []
    with _exclusive_library(root):
        return [_summary(root, _read_id(root, path.stem)[0])
                for path in _inventory(root)]


def export_fictional(library: Path, identifier: str, output: Path) -> dict:
    root = _library(library)
    if not root.exists():
        raise ValueError("Biblioteca local não encontrada")
    with _exclusive_library(root):
        doc, raw = _read_id(root, identifier)
        destination = _safe_path(output)
        if destination.resolve().is_relative_to(root.resolve()):
            raise ValueError("Exportação deve ficar fora da biblioteca")
        if not destination.parent.is_dir():
            raise ValueError("Diretório de exportação inexistente")
        _publish_exclusive(destination, raw)
        return {**_summary(root, doc), "exported_to": str(destination)}


def import_fictional(library: Path, source: Path) -> dict:
    # Import validation happens BEFORE creating the destination library.
    raw = _read_regular(source)
    doc = _validate_record(raw)
    root = _library(library, create=True)
    with _exclusive_library(root):
        identity = doc["profile"]["id"]
        target = _record_path(root, identity)
        if target.exists() or target.is_symlink():
            stored, _ = _read_id(root, identity)
            if stored != doc:
                raise FileExistsError("Mesmo ID com conteúdo divergente; importação recusada")
            return {**_summary(root, stored), "operation": "ALREADY_PRESENT"}
        if len(_inventory(root)) >= MAX_LIBRARY_PROFILES:
            raise ValueError("Biblioteca cheia (256 perfis)")
        root = _library(root, create=True)
        try:
            _publish_exclusive(target, _encode(doc))
        except FileExistsError:
            stored, _ = _read_id(root, identity)
            if stored != doc:
                raise FileExistsError("ID em conflito após corrida de importação")
            return {**_summary(root, stored), "operation": "ALREADY_PRESENT"}
        return {**_summary(root, doc), "operation": "IMPORTED_METADATA_ONLY"}


def retire_fictional(library: Path, identifier: str) -> dict:
    root = _library(library)
    with _exclusive_library(root):
        doc, _ = _read_id(root, identifier)
        if _read_retirement(root, doc):
            return {**_summary(root, doc), "operation": "ALREADY_RETIRED"}
        mark = {
            "schema": RETIRED_SCHEMA, "id": identifier,
            "profile_sha256": doc["sha256"],
        }
        try:
            _publish_exclusive(_retirement_path(root, identifier), _encode(mark))
        except FileExistsError:
            if not _read_retirement(root, doc):
                raise
        return {**_summary(root, doc), "operation": "RETIRED_METADATA_ONLY"}


def restore_fictional(library: Path, identifier: str) -> dict:
    """Reactivate a locally retired identity without changing its UUID/content.

    The previous retirement marker is removed only after a strict receipt
    check; any marker corruption is fail-closed. This is a local user action,
    not a change to historical roster membership or engine eligibility.
    """
    root = _library(library)
    with _exclusive_library(root):
        doc, _ = _read_id(root, identifier)
        if not _read_retirement(root, doc):
            return {**_summary(root, doc), "operation": "ALREADY_ACTIVE"}
        marker = _retirement_path(root, identifier)
        marker.unlink()
        if os.name == "posix":
            _fsync_directory_tree(root)
        return {**_summary(root, doc), "operation": "RESTORED_METADATA_ONLY"}
