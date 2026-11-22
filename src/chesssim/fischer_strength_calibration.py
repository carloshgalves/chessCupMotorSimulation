"""Fischer-only developmental depth calibration; no fabricated six-vector scores.

Select d16 or d18 by pre-move phase on FIT only, before assessing the
disjoint TUNE and previously inspected TEST panels. The original Rodent
personality and all career ratings remain untouched.
"""
from __future__ import annotations

from .fischer_baseline import phase

SCHEMA="chesssim-fischer-phase-budget-experimental-v1"
DEPTHS=(16,18)
PHASES=("opening","middlegame","endgame")
MIN_GAMES=30
MIN_NUMERIC=100
MIN_ATTAINED=.95
MIN_MEAN_GAIN_CP=.5


def _quality(data,depth):
    policy=data["policies"][f"d{depth}"]
    total=policy["decisions"]
    paired=policy["paired_numeric"]
    if (data["distinct_games"]<MIN_GAMES or paired<MIN_NUMERIC or
            total==0 or paired/total<.9 or
            policy["mean_excess_vs_human_cp"] is None or
            policy.get("attained_requested_depth",0)/total<MIN_ATTAINED or
            policy.get("overshot_node_cap",1)>0):
        return None
    return policy


def choose_fit_depths(fit):
    """Develop on every FIT decision, with conservative safety gates."""
    if "by_phase" not in fit or set(fit["by_phase"])!=set(PHASES):
        raise ValueError("Require every phase, including empty ones")
    recommendations={}
    for key in PHASES:
        group=fit["by_phase"][key]
        a=_quality(group,16)
        b=_quality(group,18)
        reason="INSUFFICIENT_SUPPORT"
        chosen=16
        if a is not None and b is not None:
            improvement=(abs(a["mean_excess_vs_human_cp"])-
                         abs(b["mean_excess_vs_human_cp"]))
            if (improvement>=MIN_MEAN_GAIN_CP and
                    b["over_100cp"]<=a["over_100cp"] and
                    b["lost_positive_mate"]<=a["lost_positive_mate"] and
                    b["negative_mate_selected"]<=a["negative_mate_selected"]):
                chosen=18
                reason="FIT_MEAN_GAIN_WITH_TAIL_AND_MATE_GATES"
            else:
                reason="NO_SAFE_FIT_GAIN"
        recommendations[key]={"depth":chosen,"reason":reason,
                              "fit_decisions":group["decisions"],
                              "fit_games":group["distinct_games"],
                              "absolute_excess_d16":abs(a["mean_excess_vs_human_cp"]) if a else None,
                              "absolute_excess_d18":abs(b["mean_excess_vs_human_cp"]) if b else None}
    return {"schema":SCHEMA,"status":"UNVALIDATED_EXPERIMENTAL_BUDGET",
            "training_arm":"fit","depths":recommendations,
            "six_competency_values":None,
            "official_engine_modified":False,"eligible_for_arena":False}


def summarize_out_of_training(policy,groups):
    """Diagnostic only; neither validation arm can select the budget."""
    if policy.get("schema")!=SCHEMA or not set(groups)<= {"tune","excluded_test"}:
        raise ValueError("Invalid frozen development plan or evaluation arms")
    result={}
    for arm,arm_data in groups.items():
        out={}
        for stage in PHASES:
            group=arm_data["by_phase"][stage]
            depth=policy["depths"][stage]["depth"]
            candidate=group["policies"][f"d{depth}"]
            baseline=group["policies"]["d16"]
            out[stage]={"selected_depth":depth,"decisions":group["decisions"],
                        "distinct_games":group["distinct_games"],
                        "paired_numeric":candidate["paired_numeric"],
                        "excess_vs_human_cp":candidate["mean_excess_vs_human_cp"],
                        "ci95_cp":candidate["game_cluster_ci95_cp"],
                        "baseline_d16_excess_cp":baseline["mean_excess_vs_human_cp"],
                        "over_100cp":candidate["over_100cp"],
                        "mate_losses":candidate["lost_positive_mate"],
                        "sufficient_support":group["distinct_games"]>=MIN_GAMES and
                             candidate["paired_numeric"]>=MIN_NUMERIC}
        result[arm]=out
    return result


def choice_depth(board,policy):
    """Safe opt-in helper: no permanent profile or game behavior mutation."""
    if policy.get("schema")!=SCHEMA or policy.get("eligible_for_arena") is not False:
        raise ValueError("Only explicitly unpromoted experimental policy is allowed")
    stage=phase(board)
    value=policy["depths"][stage]["depth"]
    if type(value) is not int or value not in DEPTHS:
        raise ValueError("Invalid phase search budget")
    return value
