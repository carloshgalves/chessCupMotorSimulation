"""Integração segura com a instalação existente e download opcional de arquivos oficiais."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
import urllib.request
import urllib.error
import shutil

from .catalog import load_roster
from .registry import RegistryError, registered_aliases, add_to_first_set

OFFICIAL_COMMIT = "e8d84c8c8c189a1cf4eb27c578fc573af3b916d2"
OFFICIAL_BASE = f"https://raw.githubusercontent.com/nescitus/rodent-iv/{OFFICIAL_COMMIT}/"

# Erro verificado no repositório oficial: o perfil de Spassky aponta para
# 'ph_spassky2.bin', mas o arquivo publicado chama-se 'ph-spassky2.bin'.
# Baixamos a origem correta e salvamos no nome esperado pelo perfil, mantendo
# seu arquivo de personalidade original intacto.
BOOK_SOURCE_ALIASES = {
    "players/ph_spassky2.bin": "players/ph-spassky2.bin",
}


def profile_status(root: Path) -> list[tuple[str, str, str]]:
    """(nome, alias ou -, estado) para cada participante."""
    ini = root / "personalities" / "basic.ini"
    aliases = registered_aliases(ini)
    results = []
    for p in load_roster():
        if not p.rodent_alias:
            results.append((p.name, "-", "PENDENTE_CALIBRACAO"))
            continue
        expected = p.profile_filename
        actual = aliases.get(p.rodent_alias)
        disk_path = root / "personalities" / expected
        if not disk_path.is_file():
            status = "ARQUIVO_AUSENTE"
        elif actual != expected:
            status = "NAO_REGISTRADO" if actual is None else f"ALIAS_DIVERGENTE:{actual}"
        else:
            status = "PRONTO_PARA_TESTE"
        results.append((p.name, p.rodent_alias, status))
    return results


def _fetch(relative: str) -> bytes:
    url = OFFICIAL_BASE + relative
    request = urllib.request.Request(url, headers={"User-Agent": "ChessSimulation64-local"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Falha no download: HTTP {exc.code}; arquivo: {relative}; URL: {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha na conexão ao baixar {relative}: {exc.reason}; URL: {url}") from exc


def _save_new(path: Path, content: bytes) -> None:
    """Cria arquivo apenas se não existir; não sobrescreve personalidades já ajustadas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)


def setup_official(root: Path, *, apply: bool = False, books: bool = False) -> dict:
    """Instala somente perfis ausentes, preserva arquivos atuais e faz backup do ini.

    Pré-baixa e valida os novos arquivos antes da primeira mutação.
    """
    root = root.expanduser().resolve()
    ini = root / "personalities" / "basic.ini"
    aliases = registered_aliases(ini)
    contents = ini.read_text(encoding="utf-8-sig")
    if re.search(r"(?m)^\s*USE_PERSONALITY_SET_NO\s*=\s*([1-9])", contents):
        raise RegistryError("Conjunto ativo não é o primeiro; ajuste manualmente para 0 antes do setup")

    profiles: dict[str, Path] = {}
    additional: dict[str, str] = {}
    missing: list[str] = []
    for p in load_roster():
        if p.rodent_alias is None:
            continue
        name = p.profile_filename
        path = root / "personalities" / name
        profiles[p.rodent_alias] = path
        current = aliases.get(p.rodent_alias)
        if current is not None and current != name:
            raise RegistryError(f"Alias {p.rodent_alias} aponta para {current}; intervenção manual necessária")
        if current is None:
            additional[p.rodent_alias] = name
        if not path.is_file():
            missing.append("personalities/" + name)

    # A implementação do Rodent reserva um alias para '---' e suporta no máximo 100.
    if len(aliases) + len(additional) + 1 > 100:
        raise RegistryError("Limite de aliases UCI excedido; use conjuntos separados")

    report = {"source_commit": OFFICIAL_COMMIT, "missing_profiles": missing, "new_aliases": additional,
              "downloads": [], "books_missing": [], "book_source_aliases": {},
              "books_preview_complete": apply or not missing}
    # Inspeção de livros dos perfis existentes e dos futuros perfis novos.
    books_re = re.compile(r"(?m)^setoption name (?:GuideBookFile|MainBookFile) value ((?:players|hist)/[\w.-]+\.bin)\s*$")
    download_payload: dict[str, bytes] = {}
    if apply:
        for relative in missing:
            data = _fetch(relative)
            if b"setoption name" not in data or len(data) > 256_000:
                raise RuntimeError(f"Conteúdo inesperado no perfil {relative}")
            download_payload[relative] = data

    for alias, path in profiles.items():
        if path.is_file():
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        elif apply:
            text = download_payload["personalities/" + path.name].decode("utf-8-sig")
        else:
            continue
        for relative in books_re.findall(text):
            destination = root / "books" / relative
            if not destination.is_file() and relative not in report["books_missing"]:
                report["books_missing"].append(relative)

    if apply and books:
        for rel in report["books_missing"]:
            upstream_rel = BOOK_SOURCE_ALIASES.get(rel, rel)
            if upstream_rel != rel:
                report["book_source_aliases"][rel] = upstream_rel
            data = _fetch("books/" + upstream_rel)
            # Polyglot: registros de 16 bytes. Não salvar HTML/404 mascarados.
            if not data or len(data) % 16 != 0 or len(data) > 50_000_000:
                raise RuntimeError(f"Livro Polyglot inválido: books/{upstream_rel} ({len(data)} bytes)")
            download_payload["books/" + rel] = data

    if apply and not download_payload and not additional:
        return report

    if apply:
        # Criamos o backup ANTES de modificar qualquer coisa.
        backup_dir = root / "personalities" / "_backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_dir / f"basic.ini.{stamp}.{sha256(ini.read_bytes()).hexdigest()[:8]}.bak"
        shutil.copy2(ini, backup)
        report["backup"] = str(backup)
        for relative, data in download_payload.items():
            path = root / relative
            if not path.exists():
                _save_new(path, data)
                report["downloads"].append(relative)
        if additional:
            new_text = add_to_first_set(contents, additional)
            tmp = ini.with_name("basic.ini.chesssim.tmp")
            try:
                with tmp.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(new_text)
                tmp.replace(ini)
            finally:
                tmp.unlink(missing_ok=True)
    return report
