"""Roster expansion: source readiness, FIT selection, competitive screen and independent TUNE."""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

import chess
import chess.engine

from .adaptive_quality_parity import adaptive_reference, _policy_metric, STOCKFISH_SHA
from .catalog import find_player, load_roster, Player
from .fischer_baseline import phase
from .fischer_deep_calibration import choose, tags
from .fischer_final_gate import features, play_clock, validate_clock, RODENT_HASHES
from .fischer_full_games import atomic_json, digest
from .matches import _open_engine, file_hash, validate
from .product_peak_profiles import render, summarize, split_label
from .product_repertoires import build, audit
from .product_strength_ladder import DepthControl
from .real85_playable import game_arm
from .real_training_cohorts import _player_configuration, _source_bytes, _scan, _iter_verified_games
from .registry import add_to_first_set

FROZEN = frozenset(("fischer", "tal", "karpov", "kasparov"))
WAVE = {"alekhine": (1927, 1934), "botvinnik": (1948, 1958),
        "smyslov": (1953, 1958), "petrosian": (1963, 1969),
        "spassky": (1965, 1972), "lasker": (1894, 1907),
        "rubinstein": (1907, 1914), "anand": (2007, 2013),
        "steinitz": (1866, 1894), "anderssen": (1851, 1870),
        "tarrasch": (1890, 1908), "marshall": (1904, 1915),
        "nimzowitsch": (1925, 1931), "reti": (1919, 1929),
        "larsen": (1964, 1971), "topalov": (2005, 2010),
        "capablanca": (1914, 1927), "euwe": (1934, 1939),
        "kramnik": (2000, 2007), "carlsen": (2013, 2019),
        "ding-liren": (2017, 2019), "judit-polgar": (2000, 2005),
        "hou-yifan": (2010, 2017), "kosteniuk": (2008, 2010),
        "keres": (1937, 1962),
        "bronstein": (1948, 1960),
        "najdorf": (1946, 1956),
        "geller": (1952, 1972),
        "gligoric": (1953, 1970),
        "portisch": (1964, 1980),
        "polugaevsky": (1967, 1981),
        "timman": (1981, 1993),
        "caruana": (2014, 2022),
        "nakamura": (2011, 2019),
        "aronian": (2005, 2017),
        "nepomniachtchi": (2016, 2022),
        "wesley-so": (2015, 2022),
        "vachier-lagrave": (2014, 2021),
        "grischuk": (2009, 2019),
        "mamedyarov": (2013, 2019),
        "mequinho": (1970, 1978),
        "short": (1987, 1995),
        "ivanchuk": (1989, 2013),
        "shirov": (1992, 2002),
        "gelfand": (2004, 2013),
        "leko": (2000, 2009),
        "gaprindashvili": (1962, 1985),
        "chiburdanidze": (1978, 1991),
        "philidor": (1749, 1795),
        "staunton": (1840, 1851),
        "morphy": (1857, 1863),
        "zukertort": (1871, 1887),
        "chigorin": (1889, 1904),
        "pillsbury": (1895, 1904),
        "reshevsky": (1935, 1960),
        "korchnoi": (1974, 1981),
        "taimanov": (1952, 1971),
        "kamsky": (1990, 1996),
        "svidler": (1997, 2017),
        "karjakin": (2009, 2016),
}
WAVE2 = ("spassky", "lasker", "rubinstein", "anand")
WAVE3 = ("steinitz", "anderssen", "tarrasch", "marshall",
         "nimzowitsch", "reti", "larsen", "topalov")
# Explicit modeling hypotheses, not empirical claims of human personality.
# All three selectable recipes retain an individual signature; Simple is a
# same-depth negative control only. Never modify previously frozen recipes.
INDIVIDUAL_SEEDS = {
    "capablanca": {"Material": 105, "OwnAttack": 95, "OwnMobility": 70, "OppMobility": 90,
                   "PawnStructure": 145, "PassedPawns": 145, "Outposts": 75, "PiecePressure": 105},
    "euwe": {"Material": 105, "OwnAttack": 115, "OwnMobility": 85, "OppMobility": 85,
             "PawnStructure": 125, "PassedPawns": 115, "Outposts": 85, "PiecePressure": 125},
    "kramnik": {"Material": 105, "OwnAttack": 95, "OwnMobility": 90, "OppMobility": 110,
                "PawnStructure": 150, "PassedPawns": 125, "Outposts": 115, "PiecePressure": 115},
    "carlsen": {"Material": 105, "OwnAttack": 110, "OwnMobility": 95, "OppMobility": 100,
                "PawnStructure": 135, "PassedPawns": 150, "Outposts": 100, "PiecePressure": 120},
    "ding-liren": {"Material": 105, "OwnAttack": 125, "OwnMobility": 105, "OppMobility": 95,
                   "PawnStructure": 130, "PassedPawns": 130, "Outposts": 95, "PiecePressure": 135},
    "judit-polgar": {"Material": 100, "OwnAttack": 155, "OwnMobility": 115, "OppMobility": 90,
                     "PawnStructure": 110, "PassedPawns": 115, "Outposts": 90, "PiecePressure": 150},
    "hou-yifan": {"Material": 105, "OwnAttack": 120, "OwnMobility": 100, "OppMobility": 100,
                  "PawnStructure": 135, "PassedPawns": 120, "Outposts": 105, "PiecePressure": 130},
    "kosteniuk": {"Material": 100, "OwnAttack": 140, "OwnMobility": 105, "OppMobility": 90,
                  "PawnStructure": 120, "PassedPawns": 120, "Outposts": 90, "PiecePressure": 140},
}
WAVE4 = tuple(INDIVIDUAL_SEEDS)
WAVE5 = ("keres", "bronstein", "najdorf", "geller", "gligoric", "portisch", "polugaevsky", "timman")
WAVE6 = ("caruana", "nakamura", "aronian", "nepomniachtchi", "wesley-so", "vachier-lagrave", "grischuk", "mamedyarov")
WAVE7 = ("mequinho", "short", "ivanchuk", "shirov", "gelfand", "leko", "gaprindashvili", "chiburdanidze")
# Distinct predeclared hypotheses; retain the original wave-four recipes unchanged.
INDIVIDUAL_SEEDS.update({
    "keres": {"Material": 105, "OwnAttack": 125, "OwnMobility": 105, "OppMobility": 95,
             "PawnStructure": 130, "PassedPawns": 120, "Outposts": 100, "PiecePressure": 135},
    "bronstein": {"Material": 100, "OwnAttack": 145, "OwnMobility": 110, "OppMobility": 85,
                 "PawnStructure": 110, "PassedPawns": 115, "Outposts": 85, "PiecePressure": 145},
    "najdorf": {"Material": 100, "OwnAttack": 140, "OwnMobility": 115, "OppMobility": 90,
               "PawnStructure": 115, "PassedPawns": 115, "Outposts": 95, "PiecePressure": 150},
    "geller": {"Material": 105, "OwnAttack": 150, "OwnMobility": 105, "OppMobility": 95,
              "PawnStructure": 120, "PassedPawns": 120, "Outposts": 95, "PiecePressure": 155},
    "gligoric": {"Material": 105, "OwnAttack": 115, "OwnMobility": 100, "OppMobility": 105,
                "PawnStructure": 145, "PassedPawns": 130, "Outposts": 110, "PiecePressure": 120},
    "portisch": {"Material": 110, "OwnAttack": 105, "OwnMobility": 85, "OppMobility": 105,
                "PawnStructure": 145, "PassedPawns": 135, "Outposts": 105, "PiecePressure": 115},
    "polugaevsky": {"Material": 105, "OwnAttack": 135, "OwnMobility": 105, "OppMobility": 100,
                   "PawnStructure": 125, "PassedPawns": 125, "Outposts": 95, "PiecePressure": 145},
    "timman": {"Material": 105, "OwnAttack": 115, "OwnMobility": 95, "OppMobility": 95,
              "PawnStructure": 135, "PassedPawns": 145, "Outposts": 95, "PiecePressure": 130},
    "caruana": {"Material": 110, "OwnAttack": 130, "OwnMobility": 100, "OppMobility": 105,
               "PawnStructure": 140, "PassedPawns": 135, "Outposts": 105, "PiecePressure": 140},
    "nakamura": {"Material": 100, "OwnAttack": 145, "OwnMobility": 120, "OppMobility": 95,
                "PawnStructure": 120, "PassedPawns": 125, "Outposts": 90, "PiecePressure": 145},
    "aronian": {"Material": 100, "OwnAttack": 135, "OwnMobility": 115, "OppMobility": 95,
               "PawnStructure": 130, "PassedPawns": 125, "Outposts": 105, "PiecePressure": 140},
    "nepomniachtchi": {"Material": 100, "OwnAttack": 150, "OwnMobility": 120, "OppMobility": 90,
                      "PawnStructure": 125, "PassedPawns": 120, "Outposts": 95, "PiecePressure": 145},
    "wesley-so": {"Material": 110, "OwnAttack": 105, "OwnMobility": 90, "OppMobility": 110,
                 "PawnStructure": 145, "PassedPawns": 145, "Outposts": 115, "PiecePressure": 115},
    "vachier-lagrave": {"Material": 100, "OwnAttack": 145, "OwnMobility": 105, "OppMobility": 95,
                       "PawnStructure": 120, "PassedPawns": 130, "Outposts": 95, "PiecePressure": 150},
    "grischuk": {"Material": 105, "OwnAttack": 130, "OwnMobility": 100, "OppMobility": 105,
                "PawnStructure": 135, "PassedPawns": 125, "Outposts": 105, "PiecePressure": 140},
    "mamedyarov": {"Material": 100, "OwnAttack": 160, "OwnMobility": 120, "OppMobility": 85,
                  "PawnStructure": 110, "PassedPawns": 120, "Outposts": 90, "PiecePressure": 155},
    "mequinho": {"Material": 105, "OwnAttack": 120, "OwnMobility": 95, "OppMobility": 105,
                "PawnStructure": 145, "PassedPawns": 140, "Outposts": 105, "PiecePressure": 125},
    "short": {"Material": 100, "OwnAttack": 145, "OwnMobility": 115, "OppMobility": 90,
             "PawnStructure": 120, "PassedPawns": 115, "Outposts": 85, "PiecePressure": 140},
    "ivanchuk": {"Material": 105, "OwnAttack": 130, "OwnMobility": 110, "OppMobility": 95,
                "PawnStructure": 135, "PassedPawns": 135, "Outposts": 110, "PiecePressure": 135},
    "shirov": {"Material": 100, "OwnAttack": 165, "OwnMobility": 115, "OppMobility": 85,
              "PawnStructure": 110, "PassedPawns": 115, "Outposts": 90, "PiecePressure": 160},
    "gelfand": {"Material": 110, "OwnAttack": 115, "OwnMobility": 90, "OppMobility": 110,
               "PawnStructure": 145, "PassedPawns": 135, "Outposts": 110, "PiecePressure": 125},
    "leko": {"Material": 110, "OwnAttack": 100, "OwnMobility": 85, "OppMobility": 115,
            "PawnStructure": 150, "PassedPawns": 135, "Outposts": 115, "PiecePressure": 115},
    "gaprindashvili": {"Material": 100, "OwnAttack": 145, "OwnMobility": 110, "OppMobility": 90,
                      "PawnStructure": 120, "PassedPawns": 125, "Outposts": 95, "PiecePressure": 145},
    "chiburdanidze": {"Material": 105, "OwnAttack": 115, "OwnMobility": 90, "OppMobility": 110,
                     "PawnStructure": 140, "PassedPawns": 140, "Outposts": 110, "PiecePressure": 120},
})
WAVE8 = ("philidor", "staunton", "morphy", "zukertort", "chigorin", "pillsbury",
         "reshevsky", "korchnoi", "taimanov", "kamsky", "svidler", "karjakin")
# Final batch: two existing Rodent profiles and ten individual hypotheses.
INDIVIDUAL_SEEDS.update({
    "philidor": {"Material": 105, "OwnAttack": 95, "OwnMobility": 80, "OppMobility": 100,
                "PawnStructure": 160, "PassedPawns": 150, "Outposts": 90, "PiecePressure": 105},
    "staunton": {"Material": 105, "OwnAttack": 105, "OwnMobility": 90, "OppMobility": 110,
                "PawnStructure": 135, "PassedPawns": 125, "Outposts": 100, "PiecePressure": 115},
    "zukertort": {"Material": 100, "OwnAttack": 150, "OwnMobility": 110, "OppMobility": 90,
                 "PawnStructure": 115, "PassedPawns": 120, "Outposts": 100, "PiecePressure": 150},
    "chigorin": {"Material": 100, "OwnAttack": 155, "OwnMobility": 115, "OppMobility": 95,
                "PawnStructure": 115, "PassedPawns": 125, "Outposts": 85, "PiecePressure": 150},
    "pillsbury": {"Material": 105, "OwnAttack": 145, "OwnMobility": 105, "OppMobility": 95,
                 "PawnStructure": 125, "PassedPawns": 135, "Outposts": 110, "PiecePressure": 145},
    "reshevsky": {"Material": 110, "OwnAttack": 105, "OwnMobility": 95, "OppMobility": 115,
                 "PawnStructure": 145, "PassedPawns": 130, "Outposts": 110, "PiecePressure": 120},
    "taimanov": {"Material": 105, "OwnAttack": 120, "OwnMobility": 105, "OppMobility": 105,
                "PawnStructure": 130, "PassedPawns": 125, "Outposts": 110, "PiecePressure": 130},
    "kamsky": {"Material": 110, "OwnAttack": 110, "OwnMobility": 95, "OppMobility": 110,
              "PawnStructure": 150, "PassedPawns": 140, "Outposts": 110, "PiecePressure": 120},
    "svidler": {"Material": 105, "OwnAttack": 135, "OwnMobility": 110, "OppMobility": 100,
               "PawnStructure": 130, "PassedPawns": 130, "Outposts": 105, "PiecePressure": 145},
    "karjakin": {"Material": 110, "OwnAttack": 105, "OwnMobility": 90, "OppMobility": 120,
                "PawnStructure": 150, "PassedPawns": 140, "Outposts": 115, "PiecePressure": 120},
})
TOPALOV_REPAIR = {
    "moderate": {"Material": 100, "OwnAttack": 250, "OwnMobility": 145,
                 "OppAttack": 125, "PiecePressure": 155, "PawnShield": 175,
                 "PawnStorm": 145, "KingTropism": 60, "KeepQueen": 15},
    "balanced": {"Material": 105, "OwnAttack": 190, "OwnMobility": 110,
                 "OppAttack": 140, "PiecePressure": 135, "PawnShield": 150,
                 "PawnStorm": 130, "KingTropism": 45, "KeepQueen": 0, "PassedPawns": 135},
    "safety": {"Material": 100, "OwnAttack": 220, "OwnMobility": 130,
               "OppAttack": 160, "PiecePressure": 150, "PawnShield": 175,
               "PawnStorm": 140, "KingTropism": 55, "KeepQueen": 0, "OppMobility": 115},
    "material": {"Material": 110, "OwnAttack": 220, "OwnMobility": 125,
                 "OppAttack": 130, "PiecePressure": 140, "PawnShield": 160,
                 "PawnStorm": 140, "KingTropism": 50, "KeepQueen": 0,
                 "PawnValueMg": 100, "PawnValueEg": 100, "RookValueMg": 500, "RookValueEg": 510},
}
DEPTHS = (12, 16, 20)
PHASES = ("opening", "middlegame", "endgame")
ROOTS = 12
MIN_PER_PHASE = 12
TARGET_PER_PHASE = 20
PARTITION_VERSION = "product-development-v2"
# Preserve the exact global TEST arm. Of the remaining 85%, assign 20/85 to TUNE.
TUNE_THRESHOLD = 2353
CONTROL = 16
MIN_SCORE = .40
SHARDS = 10


def product_arm(trajectory):
    if game_arm(trajectory) == "test":
        return "excluded_test"
    bucket = int(sha256(f"{PARTITION_VERSION}:{trajectory}".encode()).hexdigest()[:16], 16) % 10000
    return "tune" if bucket < TUNE_THRESHOLD else "fit"


def product_book(slug, data):
    return build(slug, data, period=WAVE[slug], arm_selector=product_arm,
                 partition_version=PARTITION_VERSION)


def remaining():
    return [p.slug for p in load_roster() if p.slug not in FROZEN]


def source(slug):
    cfg = _player_configuration([slug], None, None)[slug]
    _, pgn, origin = _source_bytes(cfg["source"], None)
    # Explicit transliteration alternatives only for this product cohort.
    # Earlier scientific manifests and all other product cohorts are unchanged.
    surname = "Korchnoi|Kortchnoi|Korchnoj|Kortschnoy|Kortschnoi|Kortschnoj" if slug == "korchnoi" else cfg["surname"]
    rows, scan = _scan(pgn, slug, surname)
    return pgn, rows, scan, origin, surname


def in_period(row, period):
    return str(row["date"]).isdigit() and period[0] <= int(row["date"]) <= period[1]


def readiness(slug):
    p = find_player(slug)
    cfg = _player_configuration([slug], None, None)[slug]
    base = {"name": p.name, "base_alias": p.rodent_alias,
            "source_candidate": cfg["source"], "peak": list(WAVE[slug]) if slug in WAVE else None}
    try:
        data = source(slug)
    except HTTPError as e:
        if e.code != 404:
            raise
        return {**base, "status": "SOURCE_MISSING_404"}
    rows = data[1]
    years = Counter(str(r["date"]) for r in rows if r["date"] is not None)
    by_arm = Counter(product_arm(r["mainline_sha256"]) for r in rows)
    peak = [r for r in rows if slug in WAVE and in_period(r, WAVE[slug])]
    peak_arms = Counter(product_arm(r["mainline_sha256"]) for r in peak)
    status = ("EMPTY_OR_UNRESOLVED_SOURCE" if not rows else
              "NEEDS_INDIVIDUAL_BASE" if not p.rodent_alias else
              "NEEDS_PEAK_WINDOW" if slug not in WAVE else
              "INSUFFICIENT_PEAK_GAMES" if min(peak_arms["fit"], peak_arms["tune"]) < MIN_PER_PHASE else
              "WAVE_CANDIDATE")
    return {**base, "status": status, "source": data[3], "sha256": sha256(data[0]).hexdigest(),
            "games": len(rows), "scan": data[2], "years": dict(years),
            "unknown_year": sum(r["date"] is None for r in rows), "arms": dict(by_arm),
            "peak_games": len(peak), "peak_arms": dict(peak_arms),
            "trajectory_ids": sorted(r["mainline_sha256"] for r in rows)}


def inventory(index, out):
    if not 0 <= index < SHARDS:
        raise ValueError("Invalid inventory shard")
    result = {s: readiness(s) for s in remaining()[index::SHARDS]}
    atomic_json(out, {"index": index, "players": result})
    print("ROSTER_INVENTORY", index, json.dumps({k: {a: b for a, b in v.items() if a != "trajectory_ids"}
                                              for k, v in result.items()}, sort_keys=True), flush=True)


def inventory_total(shards, out):
    result, owners = {}, defaultdict(list)
    for index in range(SHARDS):
        doc = json.loads((shards / f"inventory-{index}" / "report.json").read_text())
        if doc["index"] != index or set(doc["players"]) != set(remaining()[index::SHARDS]):
            raise ValueError("Inventory partition mismatch")
        for slug, row in doc["players"].items():
            for gid in row.pop("trajectory_ids", []):
                owners[gid].append(slug)
            result[slug] = row
    if set(result) != set(remaining()) or len(result) != 60:
        raise ValueError("Incomplete roster")
    report = {"players": result, "count": 60, "statuses": dict(Counter(r["status"] for r in result.values())),
              "unique_trajectories": len(owners), "shared_trajectories": sum(len(x) > 1 for x in owners.values()),
              "wave": WAVE, "partition_version": PARTITION_VERSION, "promotions": []}
    atomic_json(out, report)
    print("ROSTER_TOTAL", json.dumps(report["statuses"], sort_keys=True), flush=True)


def sample(slug, data, arm):
    if arm not in ("fit", "tune"):
        raise ValueError("Only development arms allowed")
    period = WAVE[slug]
    pgn, rows, _, _, surname = data
    eligible = [r for r in rows if in_period(r, period) and product_arm(r["mainline_sha256"]) == arm]
    positions = defaultdict(list)
    roots = {}
    for game, row in _iter_verified_games(pgn, eligible, surname):
        board = game.board()
        if board.fen() != chess.STARTING_FEN:
            continue
        prefix, per_phase = [], {}
        gid = row["mainline_sha256"]
        for move in game.mainline_moves():
            if board.turn == (row["color"] == "white") and board.legal_moves.count() > 1:
                stage = phase(board)
                rank = digest([slug, arm, gid, board.ply()])
                if stage not in per_phase or rank < per_phase[stage][0]:
                    per_phase[stage] = (rank, board.copy(stack=True), move, gid)
            board.push(move)
            if len(prefix) < 12:
                prefix.append(move.uci())
                if len(prefix) == 12 and not board.is_game_over(claim_draw=True):
                    roots[tuple(prefix)] = {"id": digest(prefix), "prefix": prefix.copy()}
        for stage, record in per_phase.items():
            positions[stage].append(record)
    probes = []
    for stage in PHASES:
        candidates = sorted(positions[stage], key=lambda x: x[0])
        if len(candidates) < MIN_PER_PHASE:
            raise ValueError(f"Insufficient {slug} {arm} {stage} evidence: {len(candidates)}")
        probes.extend(candidates[:TARGET_PER_PHASE])
    starts = sorted(roots.values(), key=lambda x: x["id"])[:ROOTS]
    if len(starts) != ROOTS:
        raise ValueError("Insufficient distinct roots")
    return probes, starts


def parameters(raw):
    """Symmetric local candidate search around individual original, not stereotypes."""
    def get(key):
        hits = re.findall(rf"(?m)^setoption name {key} value (-?\d+)[ \t]*$", raw)
        if len(hits) != 1:
            raise ValueError("Missing or ambiguous parameter " + key)
        return int(hits[0])
    def bounded(key, delta):
        return max(0, min(1200, get(key) + delta))
    return {"original": {},
            "initiative": {k: bounded(k, d) for k, d in
                           (("OwnAttack", 15), ("PiecePressure", 10), ("OwnMobility", 10))},
            "restriction": {k: bounded(k, d) for k, d in
                            (("OppMobility", 15), ("Outposts", 10), ("PawnStructure", 10))}}


def repair_blank_search(raw, control):
    """Fill only empty operational options with values from pinned Simple."""
    keys = ("Contempt", "SlowMover", "Selectivity", "SearchSkill", "BookFilter")
    for key in keys:
        pattern = rf"(?m)^(setoption name {key} value)[ \t]*$"
        if not re.search(pattern, raw):
            continue
        values = re.findall(rf"(?m)^setoption name {key} value (-?\d+)[ \t]*$", control)
        if len(values) != 1:
            raise ValueError("Missing pinned search control: " + key)
        raw, count = re.subn(pattern, lambda m: m[1] + " " + values[0], raw)
        if count != 1:
            raise ValueError("Ambiguous blank search parameter: " + key)
    return raw


def candidate_parameters(slug, raw):
    if slug in INDIVIDUAL_SEEDS:
        base = dict(INDIVIDUAL_SEEDS[slug])
        initiative = {**base, "OwnAttack": base["OwnAttack"] + 15,
                      "OwnMobility": base["OwnMobility"] + 10, "PiecePressure": base["PiecePressure"] + 10}
        restriction = {**base, "OppMobility": base["OppMobility"] + 15,
                       "Outposts": base["Outposts"] + 10, "PawnStructure": base["PawnStructure"] + 10}
        return {"original": {}, "individual": base, "initiative": initiative, "restriction": restriction}
    result = parameters(raw)
    if slug == "topalov":
        result.update(TOPALOV_REPAIR)
    return result


def identity_comparison(records, label):
    """Compare candidate to Simple on the same positions at the same depth."""
    _, depth = split_label(label)
    control = f"original{depth}"
    stats = summarize(records)
    differences = sum(r["values"][label]["move"] != r["values"][control]["move"] for r in records)
    candidate, baseline = stats[label], stats[control]
    return {"different_moves": differences, "positions": len(records),
            "control": baseline, "candidate": candidate,
            "passed": (differences * 10 >= len(records)
                       and candidate["style_gap"] <= baseline["style_gap"] + .025
                       and candidate["excess_cp"] <= baseline["excess_cp"] + 15)}


def shortlist_for(slug, players, records, stats):
    selectable = [n for n in players if n != "original" or find_player(slug).rodent_alias]
    def rank(k):
        return (not eligible_quality(stats[k]), stats[k]["depth_reached"] == 0,
                slug in INDIVIDUAL_SEEDS and not identity_comparison(records, k)["passed"],
                stats[k]["objective"], k)
    # Retest more than one candidate per strong depth for the failed Topalov.
    if slug == "topalov":
        return [k for d in (16, 20)
                for k in sorted((f"{n}{d}" for n in selectable), key=rank)[:3]]
    return [min((f"{n}{d}" for n in selectable), key=rank) for d in DEPTHS]


def overlay(root, dest, slug):
    if (file_hash(root / "rodent-iv-plain.exe") != RODENT_HASHES["rodent"]
            or file_hash(root / "personalities/simple.txt") != RODENT_HASHES["simple"]):
        raise ValueError("Unpinned Rodent build")
    original = find_player(slug)
    generic_seed = original.rodent_alias is None
    if generic_seed:
        if slug != "smyslov" and slug not in INDIVIDUAL_SEEDS:
            raise ValueError("Individual base not authorized for this wave")
        original = replace(original, rodent_alias="Simple", rodent_file="simple.txt")
    validate(root, original, original)
    shutil.copytree(root, dest)
    directory = dest / "personalities"
    raw = (directory / original.profile_filename).read_text(encoding="utf-8-sig")
    repaired = repair_blank_search(raw, (directory / "simple.txt").read_text(encoding="utf-8-sig"))
    params = candidate_parameters(slug, repaired)
    candidates, additions = {"original": original}, {}
    if repaired != raw:
        alias = original.rodent_alias + "WaveRepaired"
        filename = alias.lower() + ".txt"
        (directory / filename).write_text(repaired, encoding="utf-8")
        candidates["original"] = replace(original, rodent_alias=alias, rodent_file=filename)
        additions[alias] = filename
    raw = repaired
    for name in params:
        if name == "original":
            continue
        prefix = slug.title().replace("-", "") if generic_seed else original.rodent_alias
        alias = prefix + "Wave" + name.capitalize()
        filename = alias.lower() + ".txt"
        (directory / filename).write_text(render(raw, params[name]), encoding="utf-8")
        candidates[name] = replace(original, rodent_alias=alias, rodent_file=filename)
        additions[alias] = filename
    ini = directory / "basic.ini"
    ini.write_text(add_to_first_set(ini.read_text(), additions))
    for p in candidates.values():
        validate(dest, p, p)
    return candidates, params


def evaluate(probes, engines, labels, reference):
    records = []
    for _, board, human, gid in probes:
        choices = {"human": human.uci()}
        telemetry = {}
        for label in labels:
            name, depth = split_label(label)
            move, telemetry[label] = choose(engines[name], board, depth)
            choices[label] = move.uci()
        history = adaptive_reference(reference, board, choices)
        final = history[-1]
        raw = final["evals"]
        best = raw[final["ranked"][0]["move"]]
        stage = phase(board)
        initial = history[0]["ranked"]
        values = {key: {"move": move, "score": _policy_metric(best, raw[move]),
                       "features": features(board, chess.Move.from_uci(move)),
                       "match": move == human.uci()} for key, move in choices.items()}
        records.append({"game": gid, "fen": board.fen(), "phase": stage,
                        "tags": tags(board, stage, initial[0], initial[1]),
                        "values": values, "telemetry": telemetry, "reference": history})
    return records


def eligible_quality(q):
    return q["numeric"] >= 20 and q["excess_cp"] <= 35 and q["severe_excess"] <= .15


def measure(slug, root, stockfish, out):
    data = source(slug)
    try:
        fit, roots = sample(slug, data, "fit")
        tune, _ = sample(slug, data, "tune")
    except ValueError as exc:
        if not str(exc).startswith("Insufficient "):
            raise
        out.mkdir(parents=True, exist_ok=False)
        atomic_json(out / "measure.json", {"slug": slug, "status": "INSUFFICIENT_PEAK_SUPPORT",
                                           "reason": str(exc), "source_sha256": sha256(data[0]).hexdigest()})
        print("WAVE_BLOCKED", slug, str(exc), flush=True)
        return
    if {r[3] for r in fit} & {r[3] for r in tune}:
        raise ValueError("FIT/TUNE trajectory overlap")
    book = product_book(slug, data)
    book_stats = audit(book)
    if file_hash(stockfish) != STOCKFISH_SHA:
        raise ValueError("Unpinned Stockfish")
    out.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        location = Path(tmp) / "rodent"
        players, params = overlay(root, location, slug)
        engines = {k: stack.enter_context(_open_engine(location, location / "rodent-iv-plain.exe", p, book=False))
                   for k, p in players.items()}
        ref = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(stockfish)))
        ref.configure({"Threads": 1, "Hash": 64})
        labels = [f"{n}{d}" for n in players for d in DEPTHS]
        records = evaluate(fit, engines, labels, ref)
        stats = summarize(records)
        # Advance the best FIT candidate at each depth, even if it fails quality;
        # final screening never promotes an ineligible candidate.
        shortlist = shortlist_for(slug, players, records, stats)
        hashes = {n: file_hash(location / "personalities" / p.profile_filename) for n, p in players.items()}
        doc = {"slug": slug, "period": WAVE[slug], "partition_version": PARTITION_VERSION,
               "base_source": "individual" if find_player(slug).rodent_alias else "generic_seed_not_qualified",
               "fit_phase_support": dict(Counter(r["phase"] for r in records)), "source_sha256": sha256(data[0]).hexdigest(),
               "book_sha256": digest(book), "book_audit": book_stats, "params": params,
               "hashes": hashes, "roots": roots, "shortlist": shortlist,
               "fit_stats": stats, "fit_records": records}
        atomic_json(out / "measure.json", doc)
        atomic_json(out / "book.json", book)
    print("WAVE_MEASURE", slug, json.dumps({"shortlist": shortlist, "fit_stats": stats}, sort_keys=True), flush=True)


def bound_measure(slug, selection, data):
    doc = json.loads((selection / "measure.json").read_text())
    probes, roots = sample(slug, data, "fit")
    if len(doc["fit_records"]) != len(probes):
        raise ValueError("Missing FIT positions")
    for record, (_, board, human, gid) in zip(doc["fit_records"], probes):
        if (record["game"] != gid or record["fen"] != board.fen()
                or record["values"]["human"]["move"] != human.uci()):
            raise ValueError("FIT position binding mismatch")
    if (doc["slug"] != slug or doc["period"] != list(WAVE[slug])
            or doc.get("partition_version") != PARTITION_VERSION
            or doc["source_sha256"] != sha256(data[0]).hexdigest()
            or doc["book_sha256"] != digest(product_book(slug, data))
            or doc["roots"] != roots
            or len(doc["shortlist"]) != (6 if slug == "topalov" else 3)
            or len(set(doc["shortlist"])) != len(doc["shortlist"])
            or {split_label(k)[1] for k in doc["shortlist"]} != ({16, 20} if slug == "topalov" else set(DEPTHS))):
        raise ValueError("Measurement binding mismatch")
    if doc["fit_stats"] != summarize(doc["fit_records"]):
        raise ValueError("FIT summary mismatch")
    if slug == "topalov" or slug in INDIVIDUAL_SEEDS:
        if doc["shortlist"] != shortlist_for(slug, doc["params"], doc["fit_records"], doc["fit_stats"]):
            raise ValueError("FIT shortlist mismatch")
    return doc, roots


def screen(slug, root, selection, index, out):
    prior = json.loads((selection / "measure.json").read_text())
    if prior.get("status") == "INSUFFICIENT_PEAK_SUPPORT":
        atomic_json(out, {"slug": slug, "index": index, "status": "INSUFFICIENT_PEAK_SUPPORT"})
        return
    doc, roots = bound_measure(slug, selection, source(slug))
    start = roots[index]
    records = []
    with TemporaryDirectory() as tmp, ExitStack() as stack:
        location = Path(tmp) / "rodent"
        players, params = overlay(root, location, slug)
        if params != doc["params"]:
            raise ValueError("Candidate parameters changed")
        simple = Player(0, "Simple", "control-simple", "control", "Simple", "simple.txt")
        exe = location / "rodent-iv-plain.exe"
        control = stack.enter_context(_open_engine(location, exe, simple, book=False))
        for label in doc["shortlist"]:
            name, depth = split_label(label)
            p = players[name]
            profile_hash = file_hash(location / "personalities" / p.profile_filename)
            if profile_hash != doc["hashes"][name]:
                raise ValueError("Candidate profile changed")
            with _open_engine(location, exe, p, book=False) as engine:
                for color in ("white", "black"):
                    case = {"id": digest([slug, label, start["id"], color]), "root": start["id"],
                            "color": color, "depth": depth, "opponent_depth": CONTROL}
                    raw = play_clock(case, start, DepthControl(engine, depth), DepthControl(control, CONTROL))
                    validate_clock(raw, case, start)
                    records.append({"label": label, "case": case, "game": raw, "profile_sha256": profile_hash})
    atomic_json(out, {"slug": slug, "index": index, "records": records})


def scores(slug, doc, roots, shards):
    result = {label: Counter() for label in doc["shortlist"]}
    for i, start in enumerate(roots):
        raw = json.loads((shards / f"screen-{slug}-{i}" / "report.json").read_text())
        if raw["slug"] != slug or raw["index"] != i:
            raise ValueError("Wrong screen shard")
        seen = set()
        for row in raw["records"]:
            label, case = row["label"], row["case"]
            name, depth = split_label(label)
            color = case["color"]
            if (label not in result or color not in ("white", "black") or (label, color) in seen
                    or case["root"] != start["id"] or case["depth"] != depth
                    or case["opponent_depth"] != CONTROL or row["profile_sha256"] != doc["hashes"][name]
                    or case["id"] != digest([slug, label, start["id"], color])):
                raise ValueError("Wrong screen case")
            seen.add((label, color))
            game = validate_clock(row["game"], case, start)
            res = game["result"]
            key = ("incomplete" if res == "*" else "draws" if res == "1/2-1/2"
                   else "wins" if (res == "1-0") == (color == "white") else "losses")
            result[label][key] += 1
            result[label]["flags"] += int(game["time_forfeit"] is not None)
        if len(seen) != 2 * len(result):
            raise ValueError("Missing screen game")
    for row in result.values():
        for key in ("wins", "draws", "losses", "incomplete", "flags"):
            row.setdefault(key, 0)
        row["score"] = (row["wins"] + row["draws"] / 2) / (2 * ROOTS)
    return result


def finish(slug, root, stockfish, selection, shards, out):
    prior = json.loads((selection / "measure.json").read_text())
    if prior.get("status") == "INSUFFICIENT_PEAK_SUPPORT":
        out.mkdir(parents=True, exist_ok=False)
        atomic_json(out / "report.json", {**prior, "promotion": "DENIED_PENDING_REVIEW"})
        print("WAVE_RESULT", json.dumps(prior, sort_keys=True), flush=True)
        return
    data = source(slug)
    doc, roots = bound_measure(slug, selection, data)
    rows = scores(slug, doc, roots, shards)
    eligible = [k for k, r in rows.items() if not r["incomplete"] and not r["flags"]
                and r["score"] >= MIN_SCORE and eligible_quality(doc["fit_stats"][k])
                and doc["fit_stats"][k]["depth_reached"] > 0
                and (slug not in INDIVIDUAL_SEEDS or identity_comparison(doc["fit_records"], k)["passed"])]
    selected = min(eligible, key=lambda k: (doc["fit_stats"][k]["objective"], -rows[k]["score"], k)) if eligible else None
    report = {"slug": slug, "period": WAVE[slug], "source_sha256": doc["source_sha256"],
              "partition_version": PARTITION_VERSION, "base_source": doc["base_source"],
              "fit_phase_support": doc["fit_phase_support"],
              "book_sha256": doc["book_sha256"], "book_audit": doc["book_audit"],
              "fit_stats": doc["fit_stats"], "strength": rows, "selected": selected,
              "screen_control": CONTROL, "screen_min_score": MIN_SCORE,
              "status": "NO_ELIGIBLE_CANDIDATE", "promotion": "DENIED_PENDING_REVIEW"}
    out.mkdir(parents=True, exist_ok=False)
    if selected:
        name, depth = split_label(selected)
        probes, _ = sample(slug, data, "tune")
        if {p[3] for p in probes} & {r["game"] for r in doc["fit_records"]}:
            raise ValueError("FIT/TUNE overlap")
        if file_hash(stockfish) != STOCKFISH_SHA:
            raise ValueError("Unpinned Stockfish")
        with TemporaryDirectory() as tmp, ExitStack() as stack:
            location = Path(tmp) / "rodent"
            players, params = overlay(root, location, slug)
            candidate = location / "personalities" / players[name].profile_filename
            if params != doc["params"] or file_hash(candidate) != doc["hashes"][name]:
                raise ValueError("Selected candidate mismatch")
            exe = location / "rodent-iv-plain.exe"
            engine = stack.enter_context(_open_engine(location, exe, players[name], book=False))
            ref = stack.enter_context(chess.engine.SimpleEngine.popen_uci(str(stockfish)))
            ref.configure({"Threads": 1, "Hash": 64})
            engines = {name: engine}
            labels = [selected]
            if slug in INDIVIDUAL_SEEDS:
                engines["original"] = stack.enter_context(_open_engine(location, exe, players["original"], book=False))
                labels.append(f"original{depth}")
            tune = evaluate(probes, engines, labels, ref)
            indices = []
            for stage, count in zip(PHASES, (4, 3, 3)):
                group = [i for i, row in enumerate(tune) if row["phase"] == stage]
                indices.extend(group[j * len(group) // count] for j in range(count))
            repeats = sum(choose(engine, probes[i][1], depth)[0].uci() == tune[i]["values"][selected]["move"]
                          for i in indices)
            stats = summarize(tune)[selected]
            comparison = identity_comparison(tune, selected) if slug in INDIVIDUAL_SEEDS else None
            if comparison is not None:
                report["individual_control"] = comparison
                report["fit_individual_control"] = identity_comparison(doc["fit_records"], selected)
            shutil.copyfile(candidate, out / "candidate.txt")
            atomic_json(out / "tune.json", tune)
            report.update({"tune": stats, "tune_phase_support": dict(Counter(r["phase"] for r in tune)),
                           "repeat_matches": repeats, "repeat_total": len(indices),
                           "profile_sha256": doc["hashes"][name], "overrides": params[name], "depth": depth,
                           "status": ("INDIVIDUAL_CANDIDATE_REVIEW_READY" if doc["base_source"] == "generic_seed_not_qualified"
                                      else "MOTOR_BASE_REVIEW_READY") if eligible_quality(stats) and repeats == 10
                           and stats["depth_reached"] > 0
                           and (comparison is None or comparison["passed"])
                           else "TUNE_OR_STABILITY_FAILED"})
    atomic_json(out / "report.json", report)
    print("WAVE_RESULT", json.dumps(report, sort_keys=True), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inventory", "inventory-total", "measure", "screen", "finish"))
    p.add_argument("--player", choices=sorted(WAVE))
    p.add_argument("--rodent", type=Path)
    p.add_argument("--stockfish", type=Path)
    p.add_argument("--index", type=int)
    p.add_argument("--selection", type=Path)
    p.add_argument("--shards", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.action == "inventory":
        inventory(a.index, a.out)
    elif a.action == "inventory-total":
        inventory_total(a.shards, a.out)
    elif a.action == "measure":
        measure(a.player, a.rodent, a.stockfish, a.out)
    elif a.action == "screen":
        screen(a.player, a.rodent, a.selection, a.index, a.out)
    else:
        finish(a.player, a.rodent, a.stockfish, a.selection, a.shards, a.out)


if __name__ == "__main__":
    main()
