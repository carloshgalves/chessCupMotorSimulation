""" explicitly justified peak windows and evidence-based time controls.

UNKNOWN must never silently become CLASSICAL. Event labels are *hints* and
not verified time controls. This module never evaluates a chess engine.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from .catalog import load_roster

MODES = ("classical", "rapid", "blitz", "bullet")
UNKNOWN = "unknown"

# These are tournament *presets*, not historical time controls and not Elo claims.
CLOCK_PRESETS = {
    "classical": (3600, 30),
    "rapid": (900, 10),
    "blitz": (180, 2),
    "bullet": (60, 0),
}

# Only a few historically motivated candidates are supplied; all are provisional.
# An unreviewed player must not get a guessed peak date.
PROVISIONAL_PEAKS = {
    "mequinho": (1973, 1978, "Interzonais de 1973/1976 e ciclos de Candidatos; hipótese de pesquisa"),
    "fischer": (1970, 1972, "Ciclo de Candidatos e Campeonato Mundial de 1972; hipótese de pesquisa"),
    "karpov": (1974, 1985, "Consolidação no topo e primeiros ciclos de título; intervalo candidato amplo"),
    "kasparov": (1985, 1995, "Primeira década como campeão mundial; intervalo candidato amplo"),
}


def default_peak_registry() -> dict:
    players = {}
    for p in load_roster():
        if p.slug in PROVISIONAL_PEAKS:
            start, end, justification = PROVISIONAL_PEAKS[p.slug]
            players[p.slug] = {"name": p.name, "from_year": start, "to_year": end,
                               "status": "PROVISIONAL", "justification": justification,
                               "references": []}
        else:
            players[p.slug] = {"name": p.name, "from_year": None, "to_year": None,
                               "status": "UNREVIEWED", "justification": "", "references": []}
    return {"schema": "peak-windows-v6", "policy": "Historical windows are predeclared, never tuned to motor agreement.",
            "players": players}


def load_peak_window(path: Path, slug: str, *, allow_provisional: bool = False) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != "peak-windows-v6":
        raise ValueError("Expected peak-windows-v6 registry")
    p = doc.get("players", {}).get(slug)
    if p is None:
        raise ValueError(f"Player missing from peak registry: {slug}")
    status = p.get("status")
    if status not in ("APPROVED", "PROVISIONAL"):
        raise ValueError(f"Peak window not reviewed for {slug}; supply independently justified dates")
    if status == "PROVISIONAL" and not allow_provisional:
        raise ValueError("Provisional peak requires --allow-provisional (not historically homologated)")
    start, end = p.get("from_year"), p.get("to_year")
    if not (isinstance(start, int) and isinstance(end, int) and 1750 <= start <= end <= 2100):
        raise ValueError("Invalid peak date interval")
    if not str(p.get("justification", "")).strip():
        raise ValueError("Peak window requires justification")
    if status == "APPROVED" and not p.get("references"):
        raise ValueError("Approved peak window requires independent historic references")
    return {"slug": slug, "from_year": start, "to_year": end,
            "status": status, "justification": p["justification"],
            "references": p.get("references", []),
            "registry_sha256": sha256(path.read_bytes()).hexdigest(),
            "window_sha256": sha256(json.dumps(p,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}


def _time_control_seconds(value: str) -> tuple[int, int] | None:
    """Return (base seconds, increment seconds) when PGN TimeControl is unambiguous.

    PGN: `seconds`, `seconds+increment`, or `moves/seconds:seconds[+inc]`.
    `?`, `-`, `*`, `N` and unsupported structures are not verified.
    """
    value = value.strip()
    if not value or value in ("?", "-", "*", "N"):
        return None
    if not re.fullmatch(r"[0-9/:+]+", value):
        return None
    segments = value.split(":")
    total = 0
    increment = 0
    for index, segment in enumerate(segments):
        if segment.count("+") > 1:
            return None
        control, *inc = segment.split("+")
        if inc:
            increment = int(inc[0])
        if "/" in control:
            count, time = control.split("/", 1)
            if not count.isdigit() or int(count) <= 0 or not time.isdigit():
                return None
            seconds = int(time)
        else:
            if not control.isdigit():
                return None
            seconds = int(control)
            if index == 0 and len(segments) > 1:  # initial non-final stage needs move count
                return None
        if seconds <= 0 or seconds > 86400 * 7 or increment > 3600:
            return None
        total += seconds
    return total, increment


def classify_mode(time_control: str | None, event: str = "", *, speed_tag: str = "") -> dict:
    """Strictly separate documented time controls from unverified event-name hints.

    Working thresholds based on base+60*increment; they are *heuristics*, not
    FIDE certification (whose thresholds and wording can differ by era).
    """
    parsed = _time_control_seconds(time_control or "?")
    if parsed is not None:
        base, increment = parsed
        effective = base + increment * 60
        if effective < 180:
            mode = "bullet"
        elif effective <= 600:
            mode = "blitz"
        elif effective < 3600:
            mode = "rapid"
        else:
            mode = "classical"
        return {"mode": mode, "evidence": "TIMECONTROL_TAG", "seconds_at_60": effective,
                "time_control": time_control}
    hint = " ".join((event, speed_tag)).casefold()
    detected = [name for name, pattern in (
        ("bullet", r"\b(bullet|ultrabullet)\b"),
        ("blitz", r"\b(blitz|lightning)\b"),
        ("rapid", r"\b(rapid|active|quickplay)\b"),
        ("classical", r"\b(classical|standard time|slow chess)\b"),
    ) if re.search(pattern, hint)]
    if len(detected) == 1:
        return {"mode": detected[0], "evidence": "EVENT_LABEL_ONLY", "seconds_at_60": None,
                "time_control": time_control or "?"}
    return {"mode": UNKNOWN, "evidence": "NO_VERIFIED_TIME_CONTROL", "seconds_at_60": None,
            "time_control": time_control or "?"}


def load_annotations(path: Path | None) -> dict:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "modalities-annotations-v6" or not isinstance(data.get("games"), dict):
        raise ValueError("Invalid modalities annotation manifest")
    approved = {}
    for gid, a in data["games"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", gid):
            raise ValueError("Invalid annotation game hash")
        if a.get("mode") is None:
            continue  # Unreviewed template entry is NEVER a classification.
        if a.get("mode") not in MODES:
            raise ValueError("Invalid annotated mode")
        if a.get("evidence") != "DOCUMENTED_EXTERNAL" or not a.get("reference"):
            raise ValueError("Manual modality annotations require documented external evidence/reference")
        approved[gid] = a
    return approved


def annotate_decisions(rows: list[dict], annotations: dict) -> list[dict]:
    annotated=[]
    for d in rows:
        mode = classify_mode(d.get("time_control"), d.get("event", ""), speed_tag=d.get("speed_tag", ""))
        if d["game"] in annotations:
            if mode['evidence'] == 'TIMECONTROL_TAG' and mode['mode'] != annotations[d['game']]['mode']:
                raise ValueError('Documented modality conflicts with explicit PGN TimeControl; inspect provenance')
            mode = {"mode": annotations[d["game"]]["mode"], "evidence": "DOCUMENTED_EXTERNAL",
                    "reference": annotations[d["game"]]["reference"], "seconds_at_60": None,
                    "time_control": d.get("time_control", "?")}
        annotated.append({**d, "modality": mode["mode"], "modality_evidence": mode["evidence"]})
    return annotated


def modality_inventory(rows: list[dict]) -> dict:
    by_game = {}
    for d in rows:
        by_game[d["game"]] = (d["modality"], d["modality_evidence"], d["year"])
    total = Counter((m,e) for m,e,_ in by_game.values())
    return {"games":len(by_game), "by_modality_and_evidence": [
        {"modality":m,"evidence":e,"games":n} for (m,e),n in sorted(total.items())],
        "by_era": {str(year):sum(y==year for _,_,y in by_game.values())
                   for year in sorted({y for _,_,y in by_game.values()})},
        "unknown_games":sum(m==UNKNOWN for m,_,_ in by_game.values())}


def modality_plan(rows: list[dict], *, min_games: int=12, min_positions: int=120) -> dict:
    """A = directly documented peak evidence; B = style extrapolation; C = no style basis.

    This reports *availability*, not an approved engine profile or Elo.
    """
    if min_games < 2 or min_positions < 10:
        raise ValueError("Sample minimums too small")
    summary = {}
    for mode in MODES:
        documented = [d for d in rows if d["modality"]==mode and
                      d["modality_evidence"] in ("TIMECONTROL_TAG","DOCUMENTED_EXTERNAL")]
        games = len({d["game"] for d in documented})
        adequate = games>=min_games and len(documented)>=min_positions
        summary[mode] = {"documented_games":games,"documented_decisions":len(documented),
                         "sufficient":adequate, "tier":"A" if adequate else "PENDING",
                         "reason":"Adequate documented peak games" if adequate else "Insufficient documented peak games"}
    baseline = summary["classical"]["sufficient"]
    for mode in MODES:
        if not summary[mode]["sufficient"]:
            summary[mode]["tier"] = "B" if baseline else "C"
            summary[mode]["reason"] = ("Requires classical-peak style extrapolation" if baseline
                                       else "Requires explicit uncertain reconstruction; no documented classical base")
    return {"schema":"mode-plan-v6", "thresholds":{"games":min_games,"decisions":min_positions},
            "policy":"Tier A is data availability only; B/C are not historically validated",
            "modes":summary}


def annotation_template(rows: list[dict]) -> dict:
    # Never fill the mode from a name-only event hint.
    games = {}
    for d in rows:
        gid = d['game']
        if gid not in games:
            hint = classify_mode(d.get('time_control'), d.get('event',''),
                                 speed_tag=d.get('speed_tag',''))
            games[gid] = {'mode':None,'evidence':None,'reference':'',
                          'date':d.get('date','?'),'event':d.get('event','?'),
                          'time_control':d.get('time_control','?'),
                          'event_hint':hint['mode'] if hint['evidence']=='EVENT_LABEL_ONLY' else None}
    return {'schema':'modalities-annotations-v6',
            'notice':'PENDING entries are ignored. Fill mode+DOCUMENTED_EXTERNAL+reference only with evidence.',
            'games':{k:games[k] for k in sorted(games)}}
