"""Versioned presets and pure deterministic schedules for Cup #58.

Titles are eligibility facts, NOT validated historical engine personalities.
The 2025 FIDE World Cup tie-break is the documented source; the synthetic
sealed-bid adapter is NOT part of FIDE rules or of the UCI protocol.
"""
from __future__ import annotations

from hashlib import sha256
import json
import random
from typing import Any

from .catalog import load_roster

FIDE_CUP_2025 = "https://calendar.fide.com/event_files/2644/WorldCup2025Regulations.pdf"
FIDE_TITLE_LIST = "https://museum.fide.com/champions"
FIDE_SPLIT_TITLES = "https://worldchesschampionship2023.fide.com/chess-champions"
RULES_VERSION = "fide-world-cup-2025-4.7+chesssim-sealed-bid-v1"
CLASSICAL = (
    "steinitz", "lasker", "capablanca", "alekhine", "euwe", "botvinnik",
    "smyslov", "tal", "petrosian", "spassky", "fischer", "karpov",
    "kasparov", "kramnik", "anand", "carlsen", "ding-liren",
)
SPLIT_FIDE = ("karpov", "anand", "topalov")
SPLIT_CLASSICAL = ("kasparov", "kramnik")
FEMALE_ROSTER = (
    "menchik", "gaprindashvili", "chiburdanidze", "judit-polgar",
    "hou-yifan",
)
# Country filters use separately sourced federation facts, not roster groups.
# Existing roster has only one verified Brazilian candidate; no fake opponent.
COUNTRY_FILTERS = {
    "brazil": {
        "label": "Brasil", "participants": ["mequinho"],
        "source": "https://ratings.fide.com/profile/2100010",
    },
}
PRESETS = ("standard", "world_champions", "women", "custom")
METHODS = ("engine_full", "fast_statistical")
STAGES = (
    {"key": "classical", "label": "2 partidas clássicas", "games": 2,
     "clock": [5400, 30], "extra_after_40": 1800},
    {"key": "rapid15", "label": "Rápido 15+10", "games": 2,
     "clock": [900, 10], "extra_after_40": 0},
    {"key": "rapid10", "label": "Rápido 10+10", "games": 2,
     "clock": [600, 10], "extra_after_40": 0},
    {"key": "blitz5", "label": "Blitz 5+3", "games": 2,
     "clock": [300, 3], "extra_after_40": 0},
    {"key": "blitz3", "label": "Blitz 3+2", "games": 2,
     "clock": [180, 2], "extra_after_40": 0},
    {"key": "armageddon", "label": "Armagedom", "games": 1,
     "clock": None, "extra_after_40": 0},
)
VALID_SLUGS = frozenset(p.slug for p in load_roster())


class CupInput(ValueError):
    """User-controlled values or untrusted stored values are not acceptable."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def checksum(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()


def champion_eligibility() -> dict:
    result = {}
    for slug in CLASSICAL:
        result[slug] = {"category": "classical", "source": FIDE_TITLE_LIST}
    for slug in SPLIT_FIDE:
        if slug not in result:
            result[slug] = {"category": "split_fide_1993_2006",
                            "source": FIDE_SPLIT_TITLES}
        else:
            result[slug]["split_fide"] = True
    for slug in SPLIT_CLASSICAL:
        if slug in result:
            result[slug]["split_classical"] = True
    return result


def presets() -> dict:
    titles = champion_eligibility()
    return {
        "schema": "chesssim-cup-presets-v1",
        "standard": {"participants": [p.slug for p in load_roster()],
                     "format": "knockout", "source": "curated_roster"},
        "world_champions": {"participants": list(titles),
                            "format": "knockout", "eligibility": titles,
                            "excluded_not_in_roster": [
                                "gukesh", "khalifman", "ponomariov", "kasimdzhanov",
                            ],
                            "source": FIDE_TITLE_LIST},
        "women": {"participants": list(FEMALE_ROSTER),
                  "format": "double_round_robin",
                  "byes": "one explicit BYE per round",
                  "source": "curated_women_roster_not_womens_world_title"},
        "custom": {"participants": [], "format": "knockout"},
        "country_filters": COUNTRY_FILTERS,
    }


def _int(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise CupInput(f"{name} deve estar entre {low} e {high}")
    return value


def normalize(raw: Any) -> dict:
    """All controls frozen at creation, never changed after games start."""
    if type(raw) is not dict:
        raise CupInput("Configuração de Copa deve ser um objeto JSON")
    allowed = {
        "name", "edition", "preset", "participants", "seed", "depth",
        "execution_mode", "armageddon_white_seconds",
        "armageddon_increment_seconds", "armageddon_min_black_seconds",
        "armageddon_max_black_seconds", "seed_method",
    }
    if set(raw) - allowed:
        raise CupInput("Campos desconhecidos na configuração de Copa")
    name = raw.get("name")
    edition = raw.get("edition", "Edição 1")
    if (type(name) is not str or not 3 <= len(name.strip()) <= 80
            or any(ord(c) < 32 for c in name)):
        raise CupInput("Nome da Copa deve ter 3–80 caracteres")
    if (type(edition) is not str or not 1 <= len(edition.strip()) <= 40
            or any(ord(c) < 32 for c in edition)):
        raise CupInput("Edição inválida")
    preset = raw.get("preset", "standard")
    if type(preset) is not str or preset not in PRESETS:
        raise CupInput("Preset desconhecido")
    if "participants" in raw:
        participants = raw["participants"]
    else:
        participants = presets()[preset]["participants"]
    if (type(participants) is not list or
            any(type(x) is not str for x in participants) or
            len(set(participants)) != len(participants) or
            not all(x in VALID_SLUGS for x in participants)):
        raise CupInput("Participantes inválidos ou repetidos")
    if preset == "world_champions":
        qualified = set(champion_eligibility())
        if not set(participants) <= qualified:
            raise CupInput("A Copa de Campeões exige título mundial documentado")
    if preset == "women":
        if tuple(participants) != FEMALE_ROSTER:
            raise CupInput("Preset feminino exige os cinco perfis do catálogo, sem substituições")
    elif not 2 <= len(participants) <= 64:
        raise CupInput("Copa eliminatória exige de 2 a 64 jogadores")
    if preset == "standard" and not 2 <= len(participants) <= 64:
        raise CupInput("Copa Standard exige de 2 a 64 jogadores")
    seed = _int(raw.get("seed", 20251107), "Semente", 0, 2**31-1)
    depth = _int(raw.get("depth", 6), "Profundidade", 1, 16)
    method = raw.get("execution_mode", "engine_full")
    if type(method) is not str or method not in METHODS:
        raise CupInput("Método de execução desconhecido")
    seed_method = raw.get("seed_method", "deterministic_draw")
    if seed_method != "deterministic_draw":
        raise CupInput("Apenas sorteio com semente verificável é suportado")
    white = _int(raw.get("armageddon_white_seconds", 240),
                 "Tempo das brancas no Armagedom", 60, 3600)
    inc = _int(raw.get("armageddon_increment_seconds", 2),
               "Incremento do Armagedom", 0, 30)
    minimum = _int(raw.get("armageddon_min_black_seconds", 60),
                   "Lance mínimo das pretas", 30, 3600)
    maximum = _int(raw.get("armageddon_max_black_seconds", white),
                   "Lance máximo das pretas", minimum, white)
    return {
        "name": name.strip(), "edition": edition.strip(), "preset": preset,
        "participants": participants.copy(), "seed": seed, "depth": depth,
        "execution_mode": method, "seed_method": seed_method,
        "format": "double_round_robin" if preset == "women" else "knockout",
        "armageddon": {
            "white_seconds": white, "increment_seconds": inc,
            "min_black_seconds": minimum, "max_black_seconds": maximum,
            "policy": "chesssim-deterministic-sealed-bid-v1",
            "fide_reference": FIDE_CUP_2025,
        },
        "rules_version": RULES_VERSION,
    }


def seed_slots(config: dict) -> list[str | None]:
    """Seeded FIDE-inspired mirrored bracket; a bye is an explicit None."""
    players = sorted(config["participants"])
    random.Random(config["seed"]).shuffle(players)
    size = 1 << (len(players) - 1).bit_length()
    # Standard binary seeded slots: 1vN, 2vN-1, etc; avoid bye-bye pairs.
    order = [1]
    while len(order) < size:
        boundary = len(order) * 2 + 1
        order = [x for previous in order for x in (previous, boundary - previous)]
    return [players[seed-1] if seed <= len(players) else None for seed in order]


def women_schedule(config: dict) -> list[dict]:
    if config["preset"] != "women":
        raise CupInput("Este formato exige o preset feminino")
    # Circular method with one BYE per round; exactly 10 double-leg rounds.
    ring: list[str | None] = sorted(config["participants"]) + [None]
    rounds: list[dict] = []
    first = []
    for day in range(5):
        pairs = []
        bye = None
        for i in range(3):
            left, right = ring[i], ring[5-i]
            if left is None or right is None:
                bye = left or right
                continue
            if (day + i) % 2:
                left, right = right, left
            pairs.append((left, right))
        first.append((pairs, bye))
        ring = [ring[0], ring[-1], *ring[1:-1]]
    for r in range(10):
        pairs, bye = first[r % 5]
        if r >= 5:
            pairs = [(b, a) for a,b in pairs]
        rounds.append({
            "round": r + 1, "bye": bye,
            "games": [{
                "id": f"w{r+1:02d}-g{i+1:02d}",
                "white": a, "black": b, "round": r+1,
                "stage": "women_double_round_robin",
                "clock": [900, 10], "extra_after_40": 0,
            } for i, (a,b) in enumerate(pairs)]
        })
    return rounds


def stage_game(match: dict, config: dict, stage: int, game_index: int) -> dict:
    if type(stage) is not int or type(game_index) is not int or not 0 <= stage < len(STAGES):
        raise CupInput("Etapa de desempate desconhecida")
    phase = STAGES[stage]
    if not 0 <= game_index < phase["games"]:
        raise CupInput("Número de partida inválido")
    base = {
        "id": f"{match['id']}-s{stage}-g{game_index+1}",
        "round": match["round"], "match_id": match["id"],
        "stage": phase["key"], "stage_index": stage, "game_index": game_index,
        "extra_after_40": phase["extra_after_40"],
    }
    a,b = match["players"]
    if stage == len(STAGES)-1:
        base["color_draw"] = None
        bid = sealed_bid(config, match["id"], a, b)
        base.update({
            "white": bid["white"], "black": bid["black"],
            "clock": [config["armageddon"]["white_seconds"],
                      bid["winning_black_seconds"],
                      config["armageddon"]["increment_seconds"]],
            "bid_proof": bid,
        })
    else:
        # FIDE 2025 §4.7 draws the initial color again for each tie-break
        # series. A reproducible independently hashed draw per stage is
        # recorded rather than recycling the classical colors.
        color_sha = checksum(["stage-color-draw-v1", config["seed"],
                              match["id"], stage])
        draw_index = int(color_sha[:16], 16) % 2
        first_white = (a,b)[draw_index]
        first_black = b if first_white == a else a
        base.update({
            "white": first_white if game_index == 0 else first_black,
            "black": first_black if game_index == 0 else first_white,
            "clock": phase["clock"].copy(), "bid_proof": None,
            "color_draw": {
                "policy": "independent-seeded-stage-lottery-v1",
                "sha256": color_sha, "first_white": first_white,
            },
        })
    return base


def sealed_bid(config: dict, match_id: str, a: str, b: str) -> dict:
    """Simulated *secret* bids; UCI engines do not emit time bids.

    Commitments include independent nonces. Reveal + deterministic tie-lottery
    are recorded before the match; never claim the engines made the offers.
    """
    rule = config["armageddon"]
    lower, upper = rule["min_black_seconds"], rule["max_black_seconds"]
    offers = {}
    for slug in (a, b):
        nonce = checksum(["nonce", config["seed"], match_id, slug])[:24]
        offer = lower + int(checksum(["bid", nonce, config["seed"]])[:16], 16) % (
            upper-lower+1)
        commit = checksum({
            "match": match_id, "slug": slug, "seconds": offer, "nonce": nonce,
        })
        offers[slug] = {"commitment": commit, "seconds": offer, "nonce": nonce}
    if offers[a]["seconds"] == offers[b]["seconds"]:
        lottery = int(checksum(["coin", config["seed"], match_id])[:16], 16) % 2
        black = (a,b)[lottery]
        tie = True
    else:
        black = min((a,b), key=lambda slug: offers[slug]["seconds"])
        lottery = None
        tie = False
    return {
        "policy": rule["policy"], "offers": offers, "tie": tie,
        "lottery": lottery, "white": b if black == a else a,
        "black": black, "winning_black_seconds": offers[black]["seconds"],
        "white_seconds": rule["white_seconds"],
        "increment_seconds": rule["increment_seconds"],
    }


def projected_game(config: dict, spec: dict) -> str:
    """Non-engine, non-PGN deterministic toy result, NEVER historical evidence."""
    d = int(checksum(["projection", config["seed"], spec["id"],
                      spec["white"], spec["black"]])[:16], 16) % 100
    return "1/2-1/2" if d < 36 else "1-0" if d < 68 else "0-1"
