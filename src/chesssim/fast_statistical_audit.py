"""Read-only prospective audit of fast-mode nominal probabilities against UCI proof.

This never calibrates a model, enables competitive Elo, or manufactures games.
The independent source of truth is leagues.verified_league_games(), which
checks season manifests, ledger receipts, and the actual PGN evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile

from .leagues import verified_league_games


SCHEMA = "chesssim-fast-probability-audit-v1"
OUTCOMES = ("1-0", "1/2-1/2", "0-1")
# From leagues.projected_result: score <24 draw, <62 white, else black.
NOMINAL_FAST_WDL = (0.38, 0.24, 0.38)
MODALITIES = {"classical", "rapid", "blitz", "bullet", "unclassified"}
LEAGUE_ID = re.compile(r"league-[0-9a-f]{16}\Z")
RECEIPT = re.compile(r"[0-9a-f]{64}\Z")
MAX_UCI_GAMES = 50000
MIN_SEASONS = 4
MIN_GAMES_EACH_SPLIT = 20
BOOTSTRAP_ITERATIONS = 300


def _canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate_verified_games(games: list[dict]) -> tuple[list[dict], list[str]]:
    """Fail closed on evidence fields rather than treating projections as games."""
    if not isinstance(games, list) or len(games) > MAX_UCI_GAMES:
        raise ValueError("Número de jogos UCI verificados fora do limite")
    seen_ids: set[str] = set()
    seasons: dict[str, str] = {}
    verified = []
    for game in games:
        if not isinstance(game, dict):
            raise ValueError("Registro de partida não é objeto")
        ident = game.get("id")
        event = game.get("event_id")
        result = game.get("result")
        source = game.get("source_kind")
        if (not isinstance(ident, str) or ident in seen_ids or
                not ident.startswith("league:") or
                not isinstance(event, str) or not LEAGUE_ID.fullmatch(event) or
                result not in OUTCOMES or source != "engine_full" or
                game.get("origin") != "league" or
                game.get("rating_eligible") is not True or
                game.get("status") != "COMPLETED"):
            raise ValueError("Registro sem comprovação UCI ou duplicado")
        seen_ids.add(ident)
        signature = game.get("receipt_sha256")
        pgn = game.get("evidence_pgn_sha256")
        if (not isinstance(signature, str) or not RECEIPT.fullmatch(signature)
                or not isinstance(pgn, str) or not RECEIPT.fullmatch(pgn)):
            raise ValueError("Recibo ou hash PGN ausente/inválido")
        provenance = game.get("engine_strength_config")
        keys = ("engine_sha256", "white_profile_sha256", "black_profile_sha256")
        if (not isinstance(provenance, dict) or
                any(not isinstance(provenance.get(key), str) or
                    not RECEIPT.fullmatch(provenance[key]) for key in keys)):
            raise ValueError("Versão do motor/perfis sem checksum verificável")
        modality = game.get("modality")
        if not isinstance(modality, str) or modality not in MODALITIES:
            raise ValueError("Modalidade UCI inválida")
        white, black = game.get("white"), game.get("black")
        if (not isinstance(white, str) or not white or
                not isinstance(black, str) or not black or white == black):
            raise ValueError("Identidade de jogador inválida")
        when = game.get("recorded_utc")
        if not isinstance(when, str):
            raise ValueError("Data de competição ausente")
        try:
            timestamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Data de competição inválida") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Data de competição sem fuso")
        normalized = timestamp.astimezone(timezone.utc).isoformat()
        if event in seasons and seasons[event] != normalized:
            raise ValueError("Identidade temporal da temporada inconsistente")
        seasons[event] = normalized
        verified.append({
            "id": ident, "event": event, "result": result,
            "modality": modality, "white": white, "black": black,
            "pgn_sha256": pgn, "receipt_sha256": signature,
            "engine_sha256": provenance["engine_sha256"],
            "white_profile_sha256": provenance["white_profile_sha256"],
            "black_profile_sha256": provenance["black_profile_sha256"],
            "recorded_utc": normalized,
        })
    verified.sort(key=lambda g: (g["recorded_utc"], g["event"], g["id"]))
    ordered_seasons = sorted(seasons, key=lambda k: (seasons[k], k))
    return verified, ordered_seasons


def _counts(games: list[dict]) -> list[int]:
    count = Counter(g["result"] for g in games)
    return [count[outcome] for outcome in OUTCOMES]


def _probabilities(counts: list[int], smoothing: float = 1.0) -> tuple[float, ...]:
    total = sum(counts) + 3 * smoothing
    return tuple((value + smoothing) / total for value in counts)


def _metrics(games: list[dict], probabilities: tuple[float, ...]) -> dict:
    if not games:
        raise ValueError("Métricas exigem partidas UCI reais")
    if (len(probabilities) != 3 or
            not all(math.isfinite(p) and 0 < p < 1 for p in probabilities) or
            not math.isclose(sum(probabilities), 1.0, abs_tol=1e-12)):
        raise ValueError("Probabilidades WDL inválidas")
    counts = _counts(games)
    brier = 0.0
    log_loss = 0.0
    for g in games:
        outcome = OUTCOMES.index(g["result"])
        brier += sum((p - (1.0 if i == outcome else 0.0)) ** 2
                     for i, p in enumerate(probabilities))
        log_loss -= math.log(probabilities[outcome])
    n = len(games)
    return {
        "games": n,
        "observed_wdl_counts": dict(zip(("white_win", "draw", "black_win"), counts)),
        "observed_wdl_fraction": [round(c / n, 8) for c in counts],
        "white_minus_black_win_fraction": round((counts[0] - counts[2]) / n, 8),
        "draw_fraction": round(counts[1] / n, 8),
        "multiclass_brier": round(brier / n, 10),
        "log_loss": round(log_loss / n, 10),
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower) if lower != upper else ordered[lower]


def _cluster_intervals(holdout: list[dict], baseline: tuple[float, ...],
                       event_ids: list[str]) -> dict | None:
    # A pair of seasons does not support a credible season-cluster interval;
    # report null instead of deceptive narrow game-level precision.
    if len(event_ids) < 5:
        return None
    grouped: dict[str, list[dict]] = defaultdict(list)
    for game in holdout:
        grouped[game["event"]].append(game)
    rng = random.Random(61027)
    brier_diffs = []
    log_diffs = []
    draws = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        sampled = [
            game for _ in event_ids
            for event_game in grouped[rng.choice(event_ids)]
            for game in [event_game]
        ]
        a = _metrics(sampled, NOMINAL_FAST_WDL)
        b = _metrics(sampled, baseline)
        brier_diffs.append(a["multiclass_brier"] - b["multiclass_brier"])
        log_diffs.append(a["log_loss"] - b["log_loss"])
        draws.append(a["draw_fraction"])
    return {
        "method": "season_cluster_bootstrap",
        "iterations": BOOTSTRAP_ITERATIONS,
        "seed": 61027,
        "fast_minus_empirical_brier_ci95": [
            round(_percentile(brier_diffs, q), 10) for q in (0.025, 0.975)],
        "fast_minus_empirical_log_loss_ci95": [
            round(_percentile(log_diffs, q), 10) for q in (0.025, 0.975)],
        "observed_draw_fraction_ci95": [
            round(_percentile(draws, q), 8) for q in (0.025, 0.975)],
    }


def evaluate_verified_games(games: list[dict], *,
                            rejected_seasons: int = 0,
                            projections_seen: int = 0) -> dict:
    """Compare *nominal* fast probabilities to held-out UCI game outcomes.

    Distinct seasons, not individual games, are the split unit. No fast
    projections enter training or evaluation. No parameters are optimized.
    """
    if (type(rejected_seasons) is not int or rejected_seasons < 0 or
            type(projections_seen) is not int or projections_seen < 0):
        raise ValueError("Contagens de evidência inválidas")
    data, season_ids = _validate_verified_games(games)
    fingerprint = sha256(_canonical(data)).hexdigest()
    document = {
        "schema": SCHEMA,
        "model": "fast_statistical",
        "model_status": "DEMONSTRATION_NOT_CALIBRATED",
        "predicted_wdl_order": ["white_win", "draw", "black_win"],
        "nominal_probabilities": list(NOMINAL_FAST_WDL),
        "verified_uci_games": len(data),
        "verified_uci_seasons": len(season_ids),
        "rejected_seasons": rejected_seasons,
        "excluded_synthetic_projections": projections_seen,
        "input_verified_records_sha256": fingerprint,
        "status": "INSUFFICIENT_EVIDENCE",
        "split": None,
        "evaluation": None,
        "warnings": [
            "Current fast mode uses a fixed seed-only categorical distribution, not measured player strength.",
            "The Elo display starts from an arbitrary prior and is not used as engine strength.",
            "A season holdout is observational and does not establish historical stylistic fidelity.",
            "Repeated or strategically correlated chess games may reduce the effective sample size.",
            "No test/validation/holdout historical style corpus is accessed or unsealed.",
        ],
    }
    if rejected_seasons:
        document["status"] = "BLOCKED_UNVERIFIED_SEASONS"
        return document
    if len(season_ids) < MIN_SEASONS:
        return document
    holdout_size = max(2, math.ceil(len(season_ids) / 4))
    train_ids = season_ids[:-holdout_size]
    holdout_ids = season_ids[-holdout_size:]
    train_set, holdout_set = set(train_ids), set(holdout_ids)
    train = [r for r in data if r["event"] in train_set]
    holdout = [r for r in data if r["event"] in holdout_set]
    document["split"] = {
        "type": "chronological_by_competition",
        "train_seasons": train_ids, "holdout_seasons": holdout_ids,
        "train_games": len(train), "holdout_games": len(holdout),
        "minimum_games_each_split": MIN_GAMES_EACH_SPLIT,
        "no_overlap": not bool(train_set & holdout_set),
        "train_modalities": sorted({g["modality"] for g in train}),
        "holdout_modalities": sorted({g["modality"] for g in holdout}),
        "train_engine_sha256": sorted({g["engine_sha256"] for g in train}),
        "holdout_engine_sha256": sorted({g["engine_sha256"] for g in holdout}),
    }
    unseen_modalities = sorted(
        set(document["split"]["holdout_modalities"]) -
        set(document["split"]["train_modalities"]))
    unseen_engines = sorted(
        set(document["split"]["holdout_engine_sha256"]) -
        set(document["split"]["train_engine_sha256"]))
    if unseen_modalities or unseen_engines:
        document["warnings"].append(
            "Holdout includes UCI modalities or engine revisions absent in training; comparison cannot establish generalization.")
    if len(train) < MIN_GAMES_EACH_SPLIT or len(holdout) < MIN_GAMES_EACH_SPLIT:
        return document
    baseline = _probabilities(_counts(train))
    fast = _metrics(holdout, NOMINAL_FAST_WDL)
    empirical = _metrics(holdout, baseline)
    by_modality = {}
    for modality in sorted({g["modality"] for g in holdout}):
        cohort = [r for r in holdout if r["modality"] == modality]
        by_modality[modality] = _metrics(cohort, NOMINAL_FAST_WDL)
    document["status"] = (
        "DISTRIBUTION_SHIFT_DESCRIPTIVE_ONLY"
        if unseen_modalities or unseen_engines else
        "DESCRIPTIVE_HOLDOUT_NOT_CALIBRATED")
    document["evaluation"] = {
        "empirical_baseline": {
            "type": "training_only_laplace_multinomial",
            "alpha": 1,
            "training_outcome_counts": _counts(train),
            "probabilities": [round(p, 10) for p in baseline],
            "holdout": empirical,
        },
        "fast_nominal": fast,
        "fast_minus_empirical_multiclass_brier": round(
            fast["multiclass_brier"] - empirical["multiclass_brier"], 10),
        "fast_minus_empirical_log_loss": round(
            fast["log_loss"] - empirical["log_loss"], 10),
        "by_modality_fast_nominal": by_modality,
        "cluster_ci95": _cluster_intervals(holdout, baseline, holdout_ids),
    }
    if len(holdout_ids) < 5:
        document["warnings"].append(
            "Fewer than five held-out seasons: cluster confidence intervals are deliberately unavailable.")
    return document


def audit_workspace(workspace: Path) -> dict:
    actual, synthetic, rejected = verified_league_games(workspace)
    return evaluate_verified_games(
        actual, rejected_seasons=rejected, projections_seen=len(synthetic))


def export_audit(workspace: Path, out: Path) -> dict:
    """Explicitly write a new JSON report; never alter competition evidence."""
    report = audit_workspace(workspace)
    output = out.expanduser().absolute()
    parent = output.parent
    if (not parent.is_dir() or parent.is_symlink() or
            output.is_symlink() or output.exists()):
        raise ValueError("Destino deve ser arquivo JSON novo em pasta regular existente")
    payload = json.dumps(report, ensure_ascii=False, indent=2,
                         sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(
            mode="wb", dir=parent, prefix=".fast-audit-",
            suffix=".tmp", delete=False) as stream:
        stage = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
    try:
        os.link(stage, output)  # Exclusive create: never replace existing report.
    finally:
        stage.unlink(missing_ok=True)
    return report
