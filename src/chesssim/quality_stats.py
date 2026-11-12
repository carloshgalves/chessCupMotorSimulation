"""Audit-constrained descriptive summary of one exact, completed UCI PGN.

Only the hash-matched, base-depth decisions with unambiguous centipawn
comparisons enter means. This is neither a chess phase detector nor an Elo,
accuracy, independence, or historical-identity estimator.
"""
from __future__ import annotations

import re

PHASES = ("opening", "middle", "late")
SIDES = ("white", "black")
RESULTS = {"1-0", "0-1", "1/2-1/2"}
SHA64 = re.compile(r"[0-9a-f]{64}\Z")


def phase_for_ply(ply: int) -> str:
    """Chronological proxy, not classification by chess position/material."""
    if type(ply) is not int or ply < 1:
        raise ValueError("Invalid ply for a chronological phase")
    return "opening" if ply <= 16 else "middle" if ply <= 60 else "late"


def _empty() -> dict:
    return {"sampled": 0, "usable": 0, "excluded": 0,
            "critical": 0, "loss_total": 0}


def _add(group: dict, *, usable: bool, loss: int, critical: bool) -> None:
    group["sampled"] += 1
    if usable:
        group["usable"] += 1
        group["loss_total"] += loss
        if critical:
            group["critical"] += 1
    else:
        group["excluded"] += 1


def _public(group: dict) -> dict:
    count = group["usable"]
    return {
        "sampled": group["sampled"], "usable": count,
        "excluded": group["excluded"], "critical": group["critical"],
        "mean_loss_cp": round(group["loss_total"] / count, 2) if count else None,
    }


def _player(headers: dict, side: str) -> dict:
    prefix = side.capitalize()
    name = headers.get(prefix)
    if not isinstance(name, str) or not name.strip():
        name = "Brancas" if side == "white" else "Pretas"
    name = name[:100]
    model = headers.get(prefix + "Model")
    profile_sha = headers.get(prefix + "ProfileSHA256")
    if (not isinstance(model, str) or
            not re.fullmatch(r"RodentIV:[A-Za-z0-9_.-]{1,64}", model)):
        model = None
    if not isinstance(profile_sha, str) or not SHA64.fullmatch(profile_sha):
        profile_sha = None
    return {"side": side, "name": name, "model_declared": model,
            "profile_sha256_prefix": profile_sha[:12] if profile_sha else None}


def summarize_quality(*, mapping: dict[int, dict], usable: dict[int, bool],
                      headers: dict, status: str, result: str,
                      total_plies: int, report_pgn_sha256: str,
                      stockfish_sha256: str, engine: str, depth: int) -> dict | None:
    """Return None if any completion/provenance gate is absent.

    Both mapping and usable are computed from a *validated* quality report
    and a byte-for-byte matching final PGN; callers must never supply
    scores from the public browser or an unverified file.
    """
    if (status != "COMPLETED" or result not in RESULTS or
            not mapping or not isinstance(headers, dict)):
        return None
    if not (isinstance(report_pgn_sha256, str) and SHA64.fullmatch(report_pgn_sha256)
            and isinstance(stockfish_sha256, str) and SHA64.fullmatch(stockfish_sha256)
            and type(depth) is int and 1 <= depth <= 35
            and type(total_plies) is int and 1 <= total_plies <= 10000):
        return None
    overall = _empty()
    phases = {phase: _empty() for phase in PHASES}
    sides = {side: _empty() for side in SIDES}
    cross = {phase: {side: _empty() for side in SIDES} for phase in PHASES}
    # Always sort: accounting cannot depend on JSON decision order.
    for ply in sorted(mapping):
        score = mapping[ply]
        if type(ply) is not int or not 1 <= ply <= total_plies:
            return None
        loss = score.get("loss_cp")
        if type(loss) is not int or loss < 0 or loss > 100000:
            return None
        phase = phase_for_ply(ply)
        side = "white" if ply % 2 else "black"
        is_usable = usable.get(ply) is True
        critical = score.get("critical") in ("mistake", "blunder")
        for group in (overall, phases[phase], sides[side], cross[phase][side]):
            _add(group, usable=is_usable, loss=loss, critical=critical)
    if (sum(phases[k]["sampled"] for k in PHASES) != overall["sampled"] or
            sum(sides[k]["usable"] for k in SIDES) != overall["usable"] or
            overall["sampled"] != overall["usable"] + overall["excluded"]):
        raise RuntimeError("Quality chart accounting mismatch")
    return {
        "schema": "chesssim-verified-quality-stats-v1",
        "status": "DESCRIPTIVE_SINGLE_COMPLETED_GAME_NOT_RATING",
        "result": result, "total_plies": total_plies,
        "coverage": _public(overall),
        "provenance": {
            "pgn_sha256_prefix": report_pgn_sha256[:12],
            "stockfish_sha256_prefix": stockfish_sha256[:12],
            "engine": engine, "depth": depth,
            "source": "independent-stockfish-postgame-v9.1",
        },
        "players": {side: _player(headers, side) for side in SIDES},
        "by_phase": {phase: _public(phases[phase]) for phase in PHASES},
        "by_side": {side: _public(sides[side]) for side in SIDES},
        "by_phase_side": {
            phase: {side: _public(cross[phase][side]) for side in SIDES}
            for phase in PHASES
        },
        "phase_definition": "plies 1–16, 17–60, 61+; chronological proxy only",
        "exclusion_definition": ("Mate-affected, search-disagreement or missing "
                                 "explicit evidence flags are excluded from CP means; "
                                 "all sampled decisions remain counted for coverage."),
        "warning": ("One game only; correlated and potentially selected decisions. "
                    "Means are descriptive, not model strength or historical fidelity. "
                    "No aggregation of fast_statistical, duplicate trajectories or holdout."),
    }
