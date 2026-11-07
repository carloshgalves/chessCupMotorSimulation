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
import tempfile

from .catalog import load_roster
from .history_stats import verified_competition_games

SCHEMA = "chesssim-competition-elo-v1"
LEDGER_SCHEMA = "chesssim-rating-ledger-v1"
VERSION = "elo-logistic-decimal-v1"
UNIVERSE = "local-uci-observed-v1"
BASELINE = Decimal("2500")
DEFAULT_K = Decimal("10")
MAX_EVENTS = 16384
SCORES = {"1-0": Decimal(1), "0-1": Decimal(0), "1/2-1/2": Decimal("0.5")}
GENESIS = sha256(LEDGER_SCHEMA.encode("ascii")).hexdigest()
# More games increase evidence, but cannot prove strength or style authenticity.
PROVISIONAL_UNTIL = 30
MODALITIES = {"official", "classical", "rapid", "blitz", "bullet", "unclassified"}


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
                        roster: tuple[str, ...] | None = None) -> dict:
    """Recalculate deterministically from a set of evidence-validated games.

    IDs, not PGN similarity, identify a game. Two independently played matches
    may have identical mainlines. The same ID on a resumed run does not score
    again. Incompatible payloads with one ID fail closed.
    """
    k = _validated_k(k)
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
        modality = record.get("time_control", "unclassified") or "unclassified"
        if source not in ("arena", "tournament") or modality not in MODALITIES:
            raise ValueError("Origem ou modalidade inválida")
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
        payload = {
            "game_id": game_id, "white": white, "black": black,
            "source": source, "result": result, "status": status,
            "created_manifest_utc": date, "modality": modality,
            "engine_strength_config": proof,
        }
        if game_id in unique:
            if unique[game_id] != payload:
                raise ValueError("Mesmo ID de jogo com provas divergentes")
            duplicate_records += 1
            continue
        unique[game_id] = payload
        if status == "INCOMPLETE":
            ignored_incomplete += 1
    completed = [x for x in unique.values() if x["status"] == "COMPLETED"]
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
                "white": w, "black": b, "result": game["result"],
                "score_white": str(score_w),
                "expected_white": str(exp_w),
                "white_before": str(before_w), "black_before": str(before_b),
                "white_after": str(values[w]), "black_after": str(values[b]),
                "k": str(k), "delta_white": str(change),
                "engine_strength_config": game["engine_strength_config"],
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
        "schema": LEDGER_SCHEMA, "universe": UNIVERSE,
        "model_version": VERSION, "baseline": "2500", "k": str(k),
        "rating_ledger": ledger, "ledger_checksum": previous,
        "ledger_genesis": GENESIS, "ratings": ranking,
        "counts": {
            "scored_games": len(ledger), "ignored_incomplete": ignored_incomplete,
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
    """Detect modified/dropped/duplicated/out-of-order ledger entries."""
    if snapshot.get("schema") != LEDGER_SCHEMA or snapshot.get("ledger_genesis") != GENESIS:
        return False
    ledger = snapshot.get("rating_ledger")
    if not isinstance(ledger, list):
        return False
    previous = GENESIS
    for i, event in enumerate(ledger, 1):
        if not isinstance(event, dict) or event.get("index") != i:
            return False
        payload = {key: val for key, val in event.items() if key != "checksum"}
        if payload.get("previous_checksum") != previous:
            return False
        digest = sha256(previous.encode("ascii") + b"\n" +
                        _canonical(payload)).hexdigest()
        if event.get("checksum") != digest:
            return False
        previous = digest
    return snapshot.get("ledger_checksum") == previous


def rating_report(workspace: Path, *, k: object = DEFAULT_K,
                  include_ledger: bool = False) -> dict:
    """Read-only report. The optional ledger is for explicit CLI export only."""
    records, coverage = verified_competition_games(workspace)
    snapshot = build_rating_ledger(records, k=k)
    summary = {
        "schema": SCHEMA, "universe": snapshot["universe"],
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
                         k: object = DEFAULT_K, replace: bool = False) -> dict:
    """Explicit CLI-only, atomic and idempotent snapshot; GET never writes."""
    report = rating_report(workspace, k=k, include_ledger=True)
    snapshot = {
        **report,
        "snapshot_checksum": sha256(_canonical(report)).hexdigest(),
    }
    data = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2,
                      allow_nan=False).encode("utf-8") + b"\n"
    output = out.expanduser().absolute()
    parent = output.parent
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
