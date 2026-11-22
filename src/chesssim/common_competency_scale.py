"""A common empirical 0..110 research scale requiring external rated anchors.

Input reference: assessed CC0 Lichess rapid standard-chess decisions with
explicit Glicko-2 ratings, game/player-disjoint train and validation.
No FIDE conversion, no guessing or per-player min/max rescaling.
"""
from __future__ import annotations

from collections import defaultdict
import json
from math import exp
from pathlib import Path
from statistics import median

from .fischer_competency_audit import (COMPETENCIES, REFERENCE_DEPTH,
                                       SCHEMA as AUDIT_SCHEMA)

SCHEMA="chesssim-common-six-skill-empirical-scale-v1"
SOURCE="LICHESS_CC0_STANDARD_RAPID"
RATING_SYSTEM="LICHESS_GLICKO2_RAPID"
SPLITS=("train","validation")
POLICIES=("human","d16","d18")
MIN_FIT_DECISIONS=400
MIN_FIT_PLAYERS=20
MIN_VALIDATION_DECISIONS=100
MIN_VALIDATION_PLAYERS=10
MIN_CELL_SUPPORT=30
MIN_FISCHER_DECISIONS=200
MIN_FISCHER_GAMES=30
MIN_MODELED_COVERAGE=.9


def logistic(x):
    return 1/(1+exp(-max(-35.0,min(35.0,x))))


def _solve(values, initial=0.0, *, prior=.01):
    """MAP logistic ability: rows of (fixed_offset, successes, trials)."""
    value=float(initial)
    for _ in range(60):
        score=-prior*value
        curvature=prior
        for offset,yes,total in values:
            prob=logistic(value-offset)
            score+=yes-total*prob
            curvature+=total*prob*(1-prob)
        updated=max(-12.0,min(12.0,value+score/curvature))
        if abs(updated-value)<1e-9:
            return updated
        value=updated
    return value


def _game_id(s):
    if not isinstance(s,str) or len(s)!=64 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError("Expected hashed game identity")
    return s


def validate_anchor_rows(records, forbidden_games=()):
    """Fail closed on unlicensed, cross-split, low-integrity anchor rows."""
    forbidden=set(forbidden_games)
    by_player={}
    by_game={}
    clean=[]
    for r in records:
        if (r.get("source")!=SOURCE or r.get("license")!="CC0-1.0" or
                r.get("rating_system")!=RATING_SYSTEM or
                r.get("variant")!="standard" or r.get("speed")!="rapid"):
            raise ValueError("Incompatible historical/rated anchor source")
        gid=_game_id(r.get("game"))
        if gid in forbidden:
            raise ValueError("Historical target game leaked into anchor source")
        player=r.get("player")
        arm=r.get("arm")
        rating=r.get("rating")
        skills=r.get("competencies")
        if (not isinstance(player,str) or len(player)!=64 or
                any(c not in "0123456789abcdef" for c in player) or
                arm not in SPLITS or type(rating) is not int or not 1000<=rating<=3000 or
                not isinstance(skills,list) or not skills or
                not set(skills)<=set(COMPETENCIES) or
                len(set(skills))!=len(skills) or
                r.get("phase") not in ("opening","middlegame","endgame") or
                not isinstance(r.get("stratum"),str) or
                r["stratum"].count("|")!=2 or
                (r["stratum"].split("|")[0] not in ("few","medium","many")) or
                (r["stratum"].split("|")[1] not in
                 ("balanced","advantage","disadvantage","mate")) or
                (r["stratum"].split("|")[2] not in
                 ("close","moderate","clear","mate")) or
                r.get("classifier_schema")!=AUDIT_SCHEMA or
                r.get("reference_depth")!=REFERENCE_DEPTH or
                r.get("score_threshold_cp")!=25 or
                r.get("mate_sensitive") is not False or
                type(r.get("within_25cp")) is not bool):

            raise ValueError("Invalid anchored historical difficulty/label metadata")
        previous=by_player.setdefault(player,arm)
        if previous!=arm:
            raise ValueError("Anchor player appears in train and validation")
        previous_game=by_game.setdefault(gid,arm)
        if previous_game!=arm:
            raise ValueError("Anchor game appears in train and validation")
        clean.append(r)
    if {x["arm"] for x in clean}!=set(SPLITS):
        raise ValueError("Missing disjoint anchor validation arm")
    if len(set(by_game))<50:
        raise ValueError("Insufficient independently observed anchor games")
    return clean


def _quantile(values,q):
    if not values:
        raise ValueError("No reference values")
    a=sorted(values)
    n=(len(a)-1)*q
    i=int(n)
    return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(n-i)


def fit_anchors(records,forbidden_games=()):
    """Build difficulty offsets using every rated observation in each arm."""
    rows=validate_anchor_rows(records,forbidden_games)
    fit=[r for r in rows if r["arm"]=="train"]
    validation=[r for r in rows if r["arm"]=="validation"]
    # Every player has equal weight in the rating-span transform, not every
    # prolific player's individual position.
    histories=defaultdict(list)
    for row in fit:
        histories[row["player"]].append(row["rating"])
    players={name:median(values) for name,values in histories.items()}
    center=median(players.values())
    rating_min=min(players.values())
    rating_max=max(players.values())
    if len(players)<MIN_FIT_PLAYERS or rating_max-rating_min<600:
        raise ValueError("Rating span or distinct train players insufficient")
    theta={player:(rating-center)/400 for player,rating in players.items()}
    low=_quantile(list(theta.values()),.05)
    high=_quantile(list(theta.values()),.95)
    if high-low<1:
        raise ValueError("Common anchor dynamic range too narrow")
    models={}
    for skill in COMPETENCIES:
        a=[r for r in fit if skill in r["competencies"]]
        b=[r for r in validation if skill in r["competencies"]]
        ntrain={r["player"] for r in a}
        nval={r["player"] for r in b}
        if (len(a)<MIN_FIT_DECISIONS or len(ntrain)<MIN_FIT_PLAYERS or
                len(b)<MIN_VALIDATION_DECISIONS or len(nval)<MIN_VALIDATION_PLAYERS or
                len({r["game"] for r in a})<30 or len({r["game"] for r in b})<15):
            models[skill]={"status":"INSUFFICIENT_ANCHOR_EVIDENCE",
                           "train_decisions":len(a),"validation_decisions":len(b)}
            continue
        bins=defaultdict(list)
        for row in a:
            bins[row["phase"]+"::"+row["stratum"]].append(row)
        offsets={}
        for cell,items in bins.items():
            if (len(items)<MIN_CELL_SUPPORT or
                    len({x["player"] for x in items})<5 or
                    len({x["game"] for x in items})<10):
                continue
            # Solve offset b: probability σ(theta-b) = σ(-b+theta)
            vals=[(-(row["rating"]-center)/400,int(row["within_25cp"]),1)
                  for row in items]
            offsets[cell]=_solve(vals)
        supported=[r for r in b if r["phase"]+"::"+r["stratum"] in offsets]
        if not supported or len(supported)/len(b)<MIN_MODELED_COVERAGE:
            models[skill]={"status":"INSUFFICIENT_STRATUM_COVERAGE",
                           "modeled_validation":len(supported),"validation_decisions":len(b)}
            continue
        # A useful statistical calibration must predict an unseen player's
        # choices. Held-out player identities never contribute to offsets.
        predictions=[(logistic((r["rating"]-center)/400+
                               offsets[r["phase"]+"::"+r["stratum"]]),
                      int(r["within_25cp"])) for r in supported]
        brier=sum((p-y)**2 for p,y in predictions)/len(predictions)
        empirical=sum(y for _,y in predictions)/len(predictions)
        null_brier=sum((empirical-y)**2 for _,y in predictions)/len(predictions)
        # Calibration is diagnosed, not assumed. This null model ignores
        # individual ratings; failures are explicitly disqualified.
        status=("VALIDATED_EXPLORATORY" if brier<=null_brier else
                "FAILED_HELDOUT_PREDICTION")
        models[skill]={"status":status,"offsets":offsets,
                       "train_decisions":len(a),"validation_decisions":len(b),
                       "validated_decisions":len(supported),
                       "brier":brier,"null_brier":null_brier,
                       "train_players":len(ntrain),"validation_players":len(nval)}
    return {"schema":SCHEMA,"rating_system":RATING_SYSTEM,"source":SOURCE,
            "license":"CC0-1.0","rating_center":center,
            "reference_theta_05":low,"reference_theta_95":high,
            "common_scale":(0,110),"skill_models":models,
            "historical_fide_elo_calibrated":False,
            "human_time_control_transfer_validated":False,
            "approved_for_competitive_gameplay":False}


def estimate_from_audit(calibration,audit,arm="fit"):
    if (calibration.get("schema")!=SCHEMA or
            audit.get("status")!="ALL_FISCHER_DECISIONS_AUDITED_NOT_CALIBRATED" or
            arm not in ("fit","tune","excluded_test")):
        raise ValueError("Invalid common calibration or historical audit")
    low=calibration["reference_theta_05"]
    high=calibration["reference_theta_95"]
    result={}
    for skill in COMPETENCIES:
        model=calibration["skill_models"][skill]
        output={}
        table=audit["arms"][arm]["by_competency_stratum"][skill]
        for policy in POLICIES:
            if model["status"]!="VALIDATED_EXPLORATORY":
                output[policy]={"score_0_110":None,"reason":model["status"]}
                continue
            offsets=model["offsets"]
            blocks=[]
            total=0
            recognized=0
            games=audit["arms"][arm]["by_competency"][skill]["distinct_games"]
            for stratum,cell in table.items():
                measure=cell["policies"][policy]
                n=measure["numeric"]
                total+=n
                if stratum in offsets:
                    recognized+=n
                    blocks.append((-offsets[stratum],measure["within_25cp"],n))
            if (total<MIN_FISCHER_DECISIONS or games<MIN_FISCHER_GAMES or
                    not total or recognized/total<MIN_MODELED_COVERAGE or
                    not blocks):
                output[policy]={"score_0_110":None,"reason":"INSUFFICIENT_TRANSFER_SUPPORT",
                                "numeric_decisions":total,"matched":recognized}
                continue
            ability=_solve(blocks)
            raw=110*(ability-low)/(high-low)
            output[policy]={"score_0_110":round(max(0,min(110,raw))),
                            "saturated":not 0<=raw<=110,
                            "theta":ability,"numeric_decisions":total,
                            "matched":recognized,"rating_system":RATING_SYSTEM,
                            "qualification":"RESEARCH_ONLY_ONLINE_RAPID_ANCHOR",
                            "fide_elo_equivalent":None}
        result[skill]=output
    return {"schema":SCHEMA,"status":"EXPLORATORY_SIX_VECTOR_NO_FIDE_TRANSFER",
            "arm":arm,"competencies":result,"career_ready":False,
            "official_history_changed":False}


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anchors",type=Path,required=True)
    p.add_argument("--audit",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args(argv)
    if a.out.exists() or a.out.is_symlink() or not a.out.parent.is_dir():
        raise ValueError("Cannot overwrite common scale evidence")
    records=[json.loads(line) for line in a.anchors.read_text().splitlines() if line]
    calibration=fit_anchors(records)
    audit=json.loads(a.audit.read_text())
    projections={arm:estimate_from_audit(calibration,audit,arm) for arm in (
        "fit","tune","excluded_test")}
    with a.out.open("x",encoding="utf-8") as output:
        json.dump({"calibration":calibration,"projections":projections},output,
                  sort_keys=True,indent=2,allow_nan=False)
        output.write("\n")
    print(json.dumps({"statuses":{k:v["status"] for k,v in
          calibration["skill_models"].items()},
          "human_transfer_validated":False},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
