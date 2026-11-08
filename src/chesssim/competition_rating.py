"""Experimental competition Elo, exclusively from verified local game outcomes.

The number 2500 is a *display baseline*, never an engine-strength setting.
This module does not import, launch, configure, or write to any UCI engine.
No live match, imported historical PGN, incomplete game, or manual adjudication
is inferred from an Elo value.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .catalog import load_roster
from .history_stats import verified_competition_games

SCHEMA = "chesssim-competition-elo-v2"
LEDGER_SCHEMA = "chesssim-rating-ledger-v2"
VERSION = "elo-logistic-decimal-pools-v2"
UNIVERSE = "local-uci-observed-v2"
BASELINE = Decimal("2500")
DEFAULT_K = Decimal("10")
MAX_EVENTS = 16384
SCORES = {"1-0": Decimal(1), "0-1": Decimal(0), "1/2-1/2": Decimal("0.5")}
GENESIS = sha256(LEDGER_SCHEMA.encode("ascii")).hexdigest()
# More games increase evidence, but cannot prove strength or style authenticity.
PROVISIONAL_UNTIL = 30
# Distinct, zero-sum universes. Unclocked games never enter a timed rating.
RATING_POOLS = ("classical", "rapid", "blitz", "bullet", "unclassified")
MODALITIES = set(RATING_POOLS)
CLOCK_LABELS = MODALITIES | {"official", "custom"}

def classify_clock(initial_seconds: int, increment_seconds: int) -> str:
    """Classify using the FIDE 60-move estimate; bullet is our local convention.

    Not a claim of FIDE rating eligibility. Clock units are seconds per player.
    """
    if (type(initial_seconds) is not int or type(increment_seconds) is not int
            or not 1 <= initial_seconds <= 86400
            or not 0 <= increment_seconds <= 3600):
        raise ValueError("Relógio deve conter segundos inteiros válidos")
    effective = initial_seconds + 60 * increment_seconds
    if effective < 180:
        return "bullet"
    if effective <= 600:
        return "blitz"
    if effective < 3600:
        return "rapid"
    return "classical"


def classify_competition_record(record: dict) -> tuple[str, list[int] | None]:
    """Persist exact clock evidence; never classify custom by its textual label."""
    label = record.get("time_control", "unclassified") or "unclassified"
    if label not in CLOCK_LABELS:
        raise ValueError("Ritmo desconhecido")
    clock = record.get("clock")
    if clock is not None:
        if not isinstance(clock, (list, tuple)) or len(clock) != 2:
            raise ValueError("Relógio deve ter início e incremento")
        normalized = list(clock)
        return classify_clock(*normalized), normalized
    if label == "custom":
        raise ValueError("Relógio personalizado sem valores registrados")
    if label == "official":
        # Legacy named official preset is 600+2 = rapid, never a classical pool.
        return "rapid", [600, 2]
    # Legacy verified records without a clock remain explicitly unclocked.
    return label, None


def _canonical(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validated_k(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ValueError("K deve ser decimal ou inteiro finito")
    try:
        k = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError("K inválido") from exc
    if not k.is_finite() or not Decimal("0") < k <= Decimal("100"):
        raise ValueError("K deve ser maior que zero e não exceder 100")
    return k


def expected_score(own: Decimal, other: Decimal) -> Decimal:
    """Logistic expected score (400-point scale), full Decimal precision."""
    return Decimal(1) / (Decimal(1) + Decimal(10) ** ((other - own) / Decimal(400)))


def _rating(row: dict, n: int) -> dict:
    games = row["wins"] + row["draws"] + row["losses"]
    assert games == n
    score = (row["wins"] + Decimal("0.5") * row["draws"]) / n if n else None
    # Hoeffding bounds for bounded per-game scores; descriptive only.
    # The independence assumption is generally violated by repeated opponents,
    # so this is never labeled an Elo confidence interval or a strength proof.
    margin = (math.log(40) / (2 * n)) ** 0.5 if n else 1.0
    return {
        "slug": row["slug"],
        "rating": str(row["rating"]),
        "display_rating": str(row["rating"].quantize(Decimal("0.1"),
                                                    rounding=ROUND_HALF_UP)),
        "initial_rating": "2500",
        "games": n,
        "wins": row["wins"], "draws": row["draws"], "losses": row["losses"],
        "points_3_1_0": 3 * row["wins"] + row["draws"],
        "sample_score": (float(score) if score is not None else None),
        "score_range_exploratory": (
            [round(max(0, float(score) - margin), 4),
             round(min(1, float(score) + margin), 4)] if n else None
        ),
        "status": ("sem_amostra" if n == 0 else
                   "provisional" if n < PROVISIONAL_UNTIL else "observed_experimental"),
        "strength_evaluation": None,
        "move_accuracy": None,
    }


def build_rating_ledger(records: list[dict], *, k: object = DEFAULT_K,
                        roster: tuple[str, ...] | None = None,
                        modality: str = "rapid") -> dict:
    """Recalculate deterministically from a set of evidence-validated games.

    IDs, not PGN similarity, identify a game. Two independently played matches
    may have identical mainlines. The same ID on a resumed run does not score
    again. Incompatible payloads with one ID fail closed.
    """
    k = _validated_k(k)
    if modality not in MODALITIES:
        raise ValueError("Modalidade de Elo inválida")
    participants = tuple(p.slug for p in load_roster()) if roster is None else roster
    if (len(participants) != len(set(participants)) or not participants
            or any(not isinstance(p, str) or not p for p in participants)):
        raise ValueError("Universo deve conter slugs únicos")
    if len(records) > MAX_EVENTS * 2:
        raise ValueError("Limite de registros excedido")
    names = set(participants)
    unique: dict[str, dict] = {}
    duplicate_records = 0
    ignored_incomplete = 0
    ignored_ineligible = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Registro de competição inválido")
        game_id = record.get("id")
        white, black = record.get("white"), record.get("black")
        status, result = record.get("status"), record.get("result")
        if (not isinstance(game_id, str) or not 0 < len(game_id) <= 512
                or white not in names or black not in names or white == black
                or status not in ("COMPLETED", "INCOMPLETE")):
            raise ValueError("Identidade de resultado inconsistente")
        if status == "COMPLETED" and result not in SCORES:
            raise ValueError("Resultado concluído inválido")
        if status == "INCOMPLETE" and result not in (None, "*"):
            raise ValueError("Interrupção declara resultado final")
        source = record.get("origin")
        record_modality, clock = classify_competition_record(record)
        if source not in ("arena", "tournament", "cup"):
            raise ValueError("Origem de jogo inválida")
        rating_eligible = record.get("rating_eligible", True)
        if type(rating_eligible) is not bool:
            raise ValueError("Elegibilidade de Elo deve ser booleana")
        date = record.get("recorded_utc")
        if date is not None and (not isinstance(date, str) or
                                 len(date) > 50 or not date.endswith("+00:00")):
            raise ValueError("Timestamp não UTC ou não normalizado")
        proof = record.get("engine_strength_config")
        if proof is not None and (not isinstance(proof, dict) or
                                  set(proof) != {"engine_sha256",
                                                "white_profile_sha256",
                                                "black_profile_sha256"} or
                                  any(not isinstance(v, str) or not 0 < len(v) <= 128
                                      for v in proof.values())):
            raise ValueError("Configuração de motor não verificável")
        evidence = record.get("evidence_pgn_sha256")
        if evidence is not None and (not isinstance(evidence, str) or
                                     not re.fullmatch(r"[0-9a-f]{64}", evidence)):
            raise ValueError("Hash da evidência PGN inválido")
        payload = {
            "game_id": game_id, "white": white, "black": black,
            "source": source, "result": result, "status": status,
            "created_manifest_utc": date, "modality": record_modality,
            "clock": clock, "engine_strength_config": proof,
            "evidence_pgn_sha256": evidence,
            "rating_eligible": rating_eligible,
        }
        if game_id in unique:
            if unique[game_id] != payload:
                raise ValueError("Mesmo ID de jogo com provas divergentes")
            duplicate_records += 1
            continue
        unique[game_id] = payload
        if status == "INCOMPLETE":
            ignored_incomplete += 1
        elif not rating_eligible:
            ignored_ineligible += 1
    completed = [x for x in unique.values()
                 if x["status"] == "COMPLETED" and x["rating_eligible"] and
                 x["modality"] == modality]
    if len(completed) > MAX_EVENTS:
        raise ValueError("Limite de partidas para Elo excedido")
    # Manifest timestamps are CREATION dates, not trusted play times. Missing
    # dates sort last. Within one manifest, stable IDs determine order.
    completed.sort(key=lambda x: (x["created_manifest_utc"] is None,
                                  x["created_manifest_utc"] or "", x["game_id"]))
    with localcontext() as ctx:
        ctx.prec = 38
        values = {p: BASELINE for p in participants}
        outcomes = {p: {"slug": p, "rating": BASELINE, "wins": 0, "draws": 0,
                        "losses": 0} for p in participants}
        games = Counter()
        ledger: list[dict] = []
        previous = GENESIS
        modalities = set()
        for index, game in enumerate(completed, 1):
            w, b = game["white"], game["black"]
            before_w, before_b = values[w], values[b]
            score_w = SCORES[game["result"]]
            exp_w = expected_score(before_w, before_b)
            change = k * (score_w - exp_w)
            values[w] += change
            values[b] -= change
            outcomes[w]["rating"] = values[w]
            outcomes[b]["rating"] = values[b]
            games[w] += 1
            games[b] += 1
            modalities.add(game["modality"])
            if score_w == 1:
                outcomes[w]["wins"] += 1
                outcomes[b]["losses"] += 1
            elif score_w == 0:
                outcomes[w]["losses"] += 1
                outcomes[b]["wins"] += 1
            else:
                outcomes[w]["draws"] += 1
                outcomes[b]["draws"] += 1
            event = {
                "index": index, "id": game["game_id"],
                "created_manifest_utc": game["created_manifest_utc"],
                "source": game["source"], "modality": game["modality"],
                "clock": game["clock"], "white": w, "black": b, "result": game["result"],
                "score_white": str(score_w),
                "expected_white": str(exp_w),
                "white_before": str(before_w), "black_before": str(before_b),
                "white_after": str(values[w]), "black_after": str(values[b]),
                "k": str(k), "delta_white": str(change),
                "engine_strength_config": game["engine_strength_config"],
                "evidence_pgn_sha256": game["evidence_pgn_sha256"],
                "model_version": VERSION, "previous_checksum": previous,
            }
            checksum = sha256(previous.encode("ascii") + b"\n" +
                              _canonical(event)).hexdigest()
            event["checksum"] = checksum
            previous = checksum
            ledger.append(event)
        ranking = [_rating(outcomes[p], games[p]) for p in participants]
        ranked = sorted((r for r in ranking if r["games"]),
                        key=lambda r: (-Decimal(r["rating"]), r["slug"]))
        for pos, r in enumerate(ranked, 1):
            r["rank"] = pos
        for r in ranking:
            if not r["games"]:
                r["rank"] = None
        ranking.sort(key=lambda r: (
            r["rank"] is None, r["rank"] if r["rank"] is not None else 9999,
            r["slug"],
        ))
    return {
        "schema": LEDGER_SCHEMA, "universe": UNIVERSE, "modality": modality,
        "model_version": VERSION, "baseline": "2500", "k": str(k),
        "rating_ledger": ledger, "ledger_checksum": previous,
        "ledger_genesis": GENESIS, "ratings": ranking,
        "counts": {
            "scored_games": len(ledger), "ignored_incomplete": ignored_incomplete,
            "ignored_ineligible": ignored_ineligible,
            "duplicate_records": duplicate_records, "participants": len(participants),
        },
        "modalities": sorted(modalities),
        "mixed_modalities_warning": len(modalities) > 1,
        "ordering_rule": "manifest_created_utc_then_game_id_not_played_at",
        "confidence_note": (
            "Faixa exploratória do score, baseada em Hoeffding sob hipótese de "
            "independência não garantida; não é IC de Elo, força ou precisão."
        ),
    }


def verify_rating_ledger(snapshot: dict) -> bool:
    """Check the chain AND recompute every rating transition from its events.

    For a full exported report, also verify all report fields against the
    snapshot checksum. This is integrity, never external authenticity.
    """
    if not isinstance(snapshot, dict):
        return False
    report = snapshot.get("schema") == SCHEMA
    if report:
        if snapshot.get("ledger_schema") != LEDGER_SCHEMA:
            return False
        expected_snapshot_checksum = snapshot.get("snapshot_checksum")
        # A full exported report without its integrity checksum is invalid.
        if not isinstance(expected_snapshot_checksum, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected_snapshot_checksum):
            return False
        body = {k: v for k, v in snapshot.items() if k != "snapshot_checksum"}
        try:
            if sha256(_canonical(body)).hexdigest() != expected_snapshot_checksum:
                return False
        except (TypeError, ValueError):
            return False
    elif snapshot.get("schema") != LEDGER_SCHEMA:
        return False
    if snapshot.get("ledger_genesis") != GENESIS:
        return False
    ledger = snapshot.get("rating_ledger")
    if not isinstance(ledger, list) or len(ledger) > MAX_EVENTS:
        return False
    previous = GENESIS
    game_ids = set()
    records = []
    try:
        for i, event in enumerate(ledger, 1):
            if not isinstance(event, dict) or event.get("index") != i:
                return False
            payload = {key: val for key, val in event.items() if key != "checksum"}
            if payload.get("previous_checksum") != previous:
                return False
            digest = sha256(previous.encode("ascii") + b"\n" +
                            _canonical(payload)).hexdigest()
            if event.get("checksum") != digest or event.get("id") in game_ids:
                return False
            game_ids.add(event["id"])
            previous = digest
            records.append({
                "id": event["id"], "white": event["white"],
                "black": event["black"], "status": "COMPLETED",
                "result": event["result"], "origin": event["source"],
                "time_control": event["modality"], "clock": event["clock"],
                "recorded_utc": event["created_manifest_utc"],
                "engine_strength_config": event["engine_strength_config"],
                "evidence_pgn_sha256": event["evidence_pgn_sha256"],
            })
        if snapshot.get("ledger_checksum") != previous:
            return False
        k = snapshot.get("k", ledger[0]["k"] if ledger else str(DEFAULT_K))
        recalculated = build_rating_ledger(
            records, k=k, modality=snapshot.get("modality"))
        if recalculated["rating_ledger"] != ledger:
            return False
        if "ratings" in snapshot and snapshot["ratings"] != recalculated["ratings"]:
            return False
        if "counts" in snapshot and snapshot["counts"].get(
                "scored_games") != len(ledger):
            return False
        return True
    except (ValueError, TypeError, KeyError, ArithmeticError, OverflowError):
        return False


def rating_report(workspace: Path, *, k: object = DEFAULT_K,
                  include_ledger: bool = False,
                  modality: str = "rapid") -> dict:
    """Read-only independent pool, never a blended global Elo."""
    records, coverage = verified_competition_games(workspace)
    snapshot = build_rating_ledger(records, k=k, modality=modality)
    available_modalities = dict.fromkeys(RATING_POOLS, 0)
    for record in records:
        if record["status"] == "COMPLETED" and record.get("rating_eligible", True):
            category, _ = classify_competition_record(record)
            available_modalities[category] += 1
    summary = {
        "schema": SCHEMA, "universe": snapshot["universe"],
        "modality": modality, "available_modalities": available_modalities,
        "model_version": snapshot["model_version"], "baseline": snapshot["baseline"],
        "k": snapshot["k"], "ratings": snapshot["ratings"],
        "counts": snapshot["counts"], "coverage": coverage,
        "modalities": snapshot["modalities"],
        "mixed_modalities_warning": snapshot["mixed_modalities_warning"],
        "ordering_rule": snapshot["ordering_rule"],
        "confidence_note": snapshot["confidence_note"],
        "ledger_checksum": snapshot["ledger_checksum"],
        "sample_requirement": PROVISIONAL_UNTIL,
        "precision_status": "not_measured",
        "strength_status": "not_evaluated",
        "scale_note": (
            "Elo ChessSimulation experimental; início em 2500 não define "
            "força UCI, habilidade ou rating FIDE."
        ),
    }
    if include_ledger:
        summary["ledger_schema"] = LEDGER_SCHEMA
        summary["rating_ledger"] = snapshot["rating_ledger"]
        summary["ledger_genesis"] = snapshot["ledger_genesis"]
    return summary


def export_rating_ledger(workspace: Path, out: Path, *,
                         k: object = DEFAULT_K, replace: bool = False,
                         modality: str = "rapid") -> dict:
    """Explicit CLI-only, atomic and idempotent snapshot; GET never writes."""
    report = rating_report(workspace, k=k, include_ledger=True, modality=modality)
    snapshot = {
        **report,
        "snapshot_checksum": sha256(_canonical(report)).hexdigest(),
    }
    data = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2,
                      allow_nan=False).encode("utf-8") + b"\n"
    output = out.expanduser().absolute()
    parent = output.parent
    if output.name in {"session.json", "summary.json", "tournament.json",
                        "duelo.pgn", "jogo_1.pgn", "jogo_2.pgn"}:
        raise ValueError("Não é permitido substituir arquivos de partida")
    if not parent.is_dir() or parent.is_symlink() or output.is_symlink():
        raise ValueError("Destino deve ter diretório existente e não ser symlink")
    if output.exists():
        existing = output.read_bytes()
        if existing == data:
            return {"path": str(output), "changed": False,
                    "scored_games": report["counts"]["scored_games"]}
        if not replace:
            raise FileExistsError("Ledger mudou; use --replace explicitamente")
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=parent, prefix=".rating-", suffix=".tmp", delete=False,
    ) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(output), "changed": True,
            "scored_games": report["counts"]["scored_games"]}
