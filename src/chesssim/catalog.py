"""Elenco histórico versionado. A presença no elenco NÃO atesta fidelidade de um perfil."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json

GROUP_LABELS = {
    "mundiais_e_convites": "Campeões mundiais e convites de elite",
    "pioneiros": "Pioneiros",
    "gigantes_sem_coroa": "Gigantes sem a coroa",
    "modernos": "Grandes mestres modernos",
    "rainhas": "Grandes rainhas",
}


@dataclass(frozen=True)
class Player:
    number: int
    name: str
    slug: str
    group: str
    rodent_alias: str | None = None
    rodent_file: str | None = None
    peak_period: str | None = None


    @property
    def profile_filename(self) -> str:
        return self.rodent_file or (self.slug + ".txt")


def load_roster() -> tuple[Player, ...]:
    raw = json.loads(files("chesssim.data").joinpath("roster.json").read_text(encoding="utf-8"))
    result = tuple(Player(number=i, **p) for i, p in enumerate(raw["players"], 1))
    if len(result) != 64 or len({p.slug for p in result}) != 64:
        raise ValueError("Elenco inválido: esperados 64 slugs únicos")
    if len({p.name for p in result}) != 64:
        raise ValueError("Há nomes repetidos no elenco")
    if len([p for p in result if p.rodent_alias]) != 21:
        raise ValueError("Esperados 21 perfis históricos oficiais nesta revisão")
    return result


def find_player(query: str) -> Player:
    roster = load_roster()
    key = query.casefold().strip()
    hits = [p for p in roster if key in (p.slug.casefold(), p.name.casefold()) or
            (p.rodent_alias and key == p.rodent_alias.casefold())]
    if not hits:
        raise ValueError(f"Jogador não encontrado: {query}. Use 'roster' para listar.")
    return hits[0]
