"""Pre-registered, non-executing experiment design for seed/repertoire diversity.

The generated file is a *plan*, not evidence of unique real UCI games or
historic identity. External repertoire seed effectiveness is never presumed.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re

from .catalog import load_roster

SLUG = re.compile(r"[a-z0-9-]{2,48}\Z")
SCHEMA = "chesssim-diversity-controlled-design-v1"


def design(players: list[str], *, seed: int, trials: int,
           declared_external_repertoire: bool) -> dict:
    if (not isinstance(players, list) or len(players) != 2 or
            any(not isinstance(s, str) or not SLUG.fullmatch(s) for s in players) or
            players[0] == players[1]):
        raise ValueError("Informe dois slugs distintos de jogadores conhecidos")
    if not {p.slug for p in load_roster()}.issuperset(players):
        raise ValueError("Jogador ausente do elenco canônico")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Seed deve ser uint32")
    if type(trials) is not int or not 2 <= trials <= 40:
        raise ValueError("Amostra deve ter 2..40 blocos")
    if seed + 2 * trials > 2**32:
        raise ValueError("Seeds do experimento excedem uint32")
    if type(declared_external_repertoire) is not bool:
        raise ValueError("Flag de repertório inválida")
    blocks = []
    for i in range(trials):
        baseline_seed = seed + i
        different_seed = seed + trials + i
        blocks.append({
            "block": i + 1,
            "fixed_variables": ["profile_hash", "engine_hash", "clock",
                                "depth", "book_hash", "color_order", "repertoire_policy",
                                "game_number", "run_mode"],
            "runs": [
                {"condition": "A1_RESEARCH_CONTROL", "seed": baseline_seed},
                {"condition": "A2_EXACT_REPLICATE", "seed": baseline_seed},
                {"condition": "B_ALTERNATE_SEED", "seed": different_seed},
            ],
        })
    payload = {
        "schema": SCHEMA,
        "status": "PLAN_ONLY_NOT_RUN_NOT_CALIBRATED",
        "players": players,
        "seed_effectiveness": ("OPERATOR_DECLARED_EXTERNAL_REPERTOIRE_UNVERIFIED"
                               if declared_external_repertoire else
                               "NO_EFFECT_EXPECTED_WITHOUT_EXTERNAL_REPERTOIRE"),
        "trials": trials,
        "blocks": blocks,
        "evaluation": {
            "before_claiming_diversity": [
                "Execute every run in a new folder and preserve raw UCI PGN/summary",
                "Compare complete SHA-256 mainlines and opening position transpositions",
                "Control identical engine/profile/book hash, clock, depth and colors",
                "Keep game_number/round_number equal across A1/A2/B: the\n                 external opening selector hashes seed, game number and FEN",
                "Quantify exact repeat rate for A1 vs A2 (negative control) and A vs B",
                "Measure quality/style drift independently; no tuning on sealed holdout",
            ],
            "forbidden_claims": [
                "Seed changes Rodent's internal search when external repertoire is disabled",
                "Every trial is an independent sample of historical identity",
                "Variation alone proves quality or fidelity improved",
            ],
            "planned_only": True,
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["design_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def export_plan(players: list[str], path: Path, *, seed: int, trials: int,
                declared_external_repertoire: bool = False) -> dict:
    doc = design(players, seed=seed, trials=trials,
                 declared_external_repertoire=declared_external_repertoire)
    if path.is_symlink() or path.exists():
        raise FileExistsError("Relatório já existe; não sobrescrever")
    if any(ancestor.is_symlink() for ancestor in path.parents):
        raise ValueError("Diretório de saída simbólico não permitido")
    # No engine, PGN, dataset, network or outside paths are opened.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(doc, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return doc
