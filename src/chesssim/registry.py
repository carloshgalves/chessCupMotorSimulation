"""Leitura/edição conservadora de personalities/basic.ini do Rodent IV.

Suporta os dois formatos observados:
  - legado: aliases diretos sem PERSONALITY_SET
  - moderno: vários PERSONALITY_SET com índice USE_PERSONALITY_SET_NO
"""
from __future__ import annotations

from pathlib import Path
import re


class RegistryError(ValueError):
    pass


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig")


def _live_lines(text: str):
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", ";", "//", "'")):
            yield s


def registered_aliases(path: Path) -> dict[str, str]:
    """Retorna somente os aliases do conjunto ativo, ou do formato legado.

    No Rodent IV antigo não existe PERSONALITY_SET; o parser aceitava
    'Tal=tal.txt' na raiz e listava Tal como opção UCI. Não devemos exigir
    uma seção que a instalação funcional do usuário não possui.
    """
    if not path.is_file():
        raise FileNotFoundError(f"basic.ini ausente: {path}")
    lines = list(_live_lines(_decode(path.read_bytes())))
    index = 0
    for s in lines:
        if re.match(r"^USE_PERSONALITY_SET_NO\s*=", s):
            try:
                index = int(s.split("=", 1)[1].strip())
            except ValueError as exc:
                raise RegistryError("USE_PERSONALITY_SET_NO inválido no basic.ini") from exc
            if not 0 <= index <= 9:
                raise RegistryError("USE_PERSONALITY_SET_NO fora de 0..9")

    has_sets = any(re.match(r"^PERSONALITY_SET\s*=", s) for s in lines)
    if not has_sets and index != 0:
        raise RegistryError("Formato legado sem conjuntos não aceita índice diferente de 0")

    active = -1
    aliases: dict[str, str] = {}
    for s in lines:
        if re.match(r"^PERSONALITY_SET\s*=", s):
            active += 1
            continue
        if has_sets and active != index:
            continue
        if "=" not in s or re.match(r"^USE_PERSONALITY_SET_NO\s*=", s):
            continue
        key, value = (x.strip() for x in s.split("=", 1))
        # Os caminhos das personalidades apontam para arquivos TXT. Ignora
        # possíveis diretivas não relacionadas com aliases.
        if key and re.fullmatch(r"[A-Za-z0-9_./\\-]+\.txt", value, flags=re.IGNORECASE):
            if key in aliases and aliases[key] != value:
                raise RegistryError(f"Alias duplicado e divergente em basic.ini: {key}")
            aliases[key] = value

    if has_sets and active < index:
        raise RegistryError("Conjunto PERSONALITY_SET selecionado não encontrado")
    if not has_sets and not aliases:
        raise RegistryError("basic.ini legado sem aliases reconhecíveis; instalação exige inspeção manual")
    return aliases


def add_to_first_set(text: str, additions: dict[str, str]) -> str:
    """Registra aliases mantendo o formato original e os conjuntos secundários."""
    if not additions:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    entry = "".join(f"{alias}={filename}{newline}" for alias, filename in sorted(additions.items()))
    lines = text.splitlines(keepends=True)
    target = next((i for i, line in enumerate(lines)
                   if re.match(r"^\s*PERSONALITY_SET\s*=", line)), None)
    if target is None:
        # Formato legado: todos os aliases diretamente no arquivo.
        # Há instalações antigas sem quebra de linha no final (Fischer=...).
        return text + ("" if not text or text.endswith(("\n", "\r")) else newline) + entry
    lines[target + 1:target + 1] = [entry]
    return "".join(lines)
