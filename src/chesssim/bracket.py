"""Sorteio reproduzível; sem pretensão de ordenar historicamente os enxadristas."""
from __future__ import annotations

import random

from .catalog import load_roster


def generate_draw(seed: int) -> dict:
    players = list(load_roster())
    random.Random(seed).shuffle(players)
    pairings = []
    for index in range(0, len(players), 2):
        pairings.append({"match": index // 2 + 1, "player_a": players[index].slug,
                         "player_b": players[index+1].slug, "status": "PENDING"})
    return {
        "edition": "copa-dos-imortais-64",
        "roster_revision": "roster-v1-2026-10-09",
        "kind": "sorteio_nao_cabeceado",
        "seed": seed,
        "rules": "32 confrontos na primeira rodada. O sorteio NÃO implica que os perfis pendentes possam jogar.",
        "round_1": pairings,
    }
