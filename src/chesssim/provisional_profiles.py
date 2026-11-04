"""Safely register and play exploratory Rodent profiles without changing roster or official profiles."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import uuid

from .catalog import Player, find_player
from .registry import add_to_first_set, registered_aliases

# Fixed experimental namespace: cannot shadow a historical Rodent personality.
PROVISIONAL_MECKING_ALIAS = "MequinhoV08"
PROVISIONAL_MECKING_FILENAME = "mequinho_v08_PROVISIONAL.txt"


def _validate_source(profile: Path) -> tuple[Path, dict]:
    source = profile.expanduser().resolve()
    report_path = source.parent / "provisional_profile_report.json"
    if not source.is_file() or not report_path.is_file():
        raise FileNotFoundError("Perfil v0.8 e relatório provisório precisam estar na mesma pasta")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    if data.get("status") != "PROVISIONAL_PROFILE_NOT_VALIDATED" or data.get("player") != "mequinho":
        raise ValueError("Somente o perfil provisório de Mequinho produzido pela v0.8 é aceito")
    if data.get("profile_file") != PROVISIONAL_MECKING_FILENAME or source.name != PROVISIONAL_MECKING_FILENAME:
        raise ValueError("O arquivo informado não coincide com o relatório de origem")
    raw = source.read_bytes()
    if len(raw) > 256_000 or not raw.startswith(b"; ChessSimulation64 v0.8 provisional profile\n"):
        raise ValueError("Arquivo de personalidade não corresponde ao formato v0.8 esperado")
    if b"setoption name " not in raw:
        raise ValueError("Arquivo não contém opções de personalidade Rodent")
    return source, data


def provisional_install(root: Path, profile: Path, *, apply: bool = False) -> dict:
    """Preview or safely register a separate experimental alias in first active personality set.

    Does not modify any official personality, does not overwrite an installed
    experimental profile and always backs up basic.ini before modifications.
    """
    root = root.expanduser().resolve()
    source, report = _validate_source(profile)
    ini = root / "personalities" / "basic.ini"
    if not (root / "rodent-iv-plain.exe").is_file():
        raise FileNotFoundError("rodent-iv-plain.exe não encontrado")
    contents = ini.read_text(encoding="utf-8-sig")
    match = re.search(r"(?m)^\s*USE_PERSONALITY_SET_NO\s*=\s*(\d+)", contents)
    if match and int(match.group(1)) != 0:
        raise ValueError("Conjunto ativo diferente de 0; registro deve ser feito manualmente")
    aliases = registered_aliases(ini)
    installed = (root / "personalities" / PROVISIONAL_MECKING_FILENAME).resolve()
    if installed == source:
        raise ValueError("Perfil de origem não pode ser o arquivo de instalação")
    current = aliases.get(PROVISIONAL_MECKING_ALIAS)
    if current is not None and current != PROVISIONAL_MECKING_FILENAME:
        raise ValueError("Alias MequinhoV08 já está registrado para outro arquivo")
    if len(aliases) >= 99 and current is None:
        raise ValueError("Rodent atingiu o limite de aliases; selecione outro conjunto")
    src_hash = sha256(source.read_bytes()).hexdigest()
    if installed.exists() and sha256(installed.read_bytes()).hexdigest() != src_hash:
        raise FileExistsError("Existe personalidade provisória divergente; nunca sobrescrever")
    result = {
        "status": "PREVIEW" if not apply else "REGISTERED",
        "alias": PROVISIONAL_MECKING_ALIAS,
        "profile_file": PROVISIONAL_MECKING_FILENAME,
        "profile_sha256": src_hash,
        "source": str(source),
        "installed": str(installed),
        "registry": str(ini),
        "already_registered": current == PROVISIONAL_MECKING_FILENAME and installed.exists(),
        "historically_validated": False,
    }
    if not apply:
        return result
    if result["already_registered"]:
        result["status"] = "ALREADY_REGISTERED_SAME_PROFILE"
        return result
    backup_dir = root / "personalities" / "_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"basic.ini.v08.{stamp}.{uuid.uuid4().hex[:8]}.bak"
    shutil.copy2(ini, backup)
    result["backup"] = str(backup)
    newly_copied = False
    try:
        if not installed.exists():
            with installed.open("xb") as handle:
                handle.write(source.read_bytes())
            newly_copied = True
        if current is None:
            updated = add_to_first_set(contents, {PROVISIONAL_MECKING_ALIAS: PROVISIONAL_MECKING_FILENAME})
            tmp = ini.with_name(f"basic.ini.chesssim-v08-{uuid.uuid4().hex}.tmp")
            try:
                with tmp.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(updated)
                tmp.replace(ini)
            finally:
                tmp.unlink(missing_ok=True)
    except BaseException:
        if newly_copied:
            installed.unlink(missing_ok=True)
        raise
    if registered_aliases(ini).get(PROVISIONAL_MECKING_ALIAS) != PROVISIONAL_MECKING_FILENAME:
        raise RuntimeError("Falha ao conferir registro do alias experimental")
    return result


def play_provisional(root: Path, out: Path, *, opponent: str, reverse: bool = True,
                     depth: int | None = 5, movetime: float | None = None,
                     preset: str | None = None, max_plies: int = 400) -> Path:
    """Run actual Rodent games with provisional Mequinho and an existing official profile."""
    from .matches import play_duel
    from .peak_windows import CLOCK_PRESETS
    if sum(value is not None for value in (depth, movetime, preset)) != 1:
        raise ValueError("Use apenas --depth, --movetime ou --preset")
    if preset not in (None, "official", *CLOCK_PRESETS):
        raise ValueError("Preset inválido")
    if preset and not reverse:
        raise ValueError("Duelo com preset exige --reverse (ambas as cores)")
    black = find_player(opponent)
    if black.rodent_alias is None or black.slug == "mequinho":
        raise ValueError("Adversário deve possuir personalidade oficial Rodent disponível")
    white = Player(number=0, name="Henrique Mecking (v0.8 provisório)", slug="mequinho-v08",
                   group="experimental", rodent_alias=PROVISIONAL_MECKING_ALIAS,
                   rodent_file=PROVISIONAL_MECKING_FILENAME)
    clock = (600, 2) if preset == "official" else CLOCK_PRESETS.get(preset)
    if preset == "official":
        raise ValueError("Perfil provisório não pode participar de partidas sob rótulo oficial; use classical, rapid, blitz ou bullet")
    return play_duel(root, out, white, black, depth=None if clock or movetime is not None else depth,
                     movetime=movetime, reverse=reverse, max_plies=max_plies,
                     clock=clock, use_books=False)
