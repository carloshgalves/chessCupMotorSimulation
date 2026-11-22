"""Common 0–110 scale requires real CC0 anchors; synthetic rows are tests only."""
import pytest

from chesssim import common_competency_scale as scale
from chesssim import fischer_competency_audit as audit


def _anchors():
    records=[]
    for i in range(90):
        arm="train" if i<60 else "validation"
        rating=1300+22*(i%30)
        for j in range(24):
            # Synthetic contexts stay phase-consistent and fully validated.
            phase=("opening","middlegame","endgame")[j%3]
            skills={"opening":["opening"],
                    "middlegame":["tactics","dynamics","defense","positional"],
                    "endgame":["endgames"]}[phase]
            difficult=j%2==0
            stratum=("many|balanced|close" if difficult else "few|advantage|clear")
            good=rating>(1700 if difficult else 1500)
            records.append({"source":scale.SOURCE,"license":"CC0-1.0",
                "rating_system":scale.RATING_SYSTEM,"variant":"standard","speed":"rapid",
                "game":f"{1000*i+j:064x}","player":f"{i+1:064x}",
                "arm":arm,"rating":rating,"competencies":skills,
                "phase":phase,"stratum":stratum,
                "classifier_schema":audit.SCHEMA,
                "reference_depth":audit.REFERENCE_DEPTH,
                "score_threshold_cp":25,"mate_sensitive":False,
                "within_25cp":good})
    return records


def test_full_external_anchor_model_and_no_official_transfer():
    learned=scale.fit_anchors(_anchors())
    assert learned["reference_theta_95"]>learned["reference_theta_05"]
    assert learned["historical_fide_elo_calibrated"] is False
    assert learned["approved_for_competitive_gameplay"] is False
    assert set(learned["skill_models"])==set(audit.COMPETENCIES)
    assert all(m["status"]=="VALIDATED_EXPLORATORY"
               for m in learned["skill_models"].values())


def test_anchor_player_or_game_split_leakage_fails_closed():
    rows=_anchors()
    with pytest.raises(ValueError,match="player"):
        scale.fit_anchors([*rows,{**rows[0],"arm":"validation",
                                   "game":"f"*64}])
    with pytest.raises(ValueError,match="game"):
        scale.fit_anchors([*rows,{**rows[0],"player":"f"*64,
                                   "arm":"validation"}])
    with pytest.raises(ValueError,match="leaked"):
        scale.fit_anchors(rows,forbidden_games={rows[0]["game"]})
    with pytest.raises(ValueError,match="source"):
        scale.fit_anchors([{**rows[0],"license":"unknown"},*rows[1:]])


def test_same_fixed_calibration_scores_human_and_engine_or_returns_null():
    learned=scale.fit_anchors(_anchors())
    strata=["many|balanced|close","few|advantage|clear"]
    demo={"status":"ALL_FISCHER_DECISIONS_AUDITED_NOT_CALIBRATED",
          "arms":{}}
    for arm in ("fit","tune","excluded_test"):
        by_skill={}
        aggregate={}
        for skill in audit.COMPETENCIES:
            cells={}
            phase=("opening" if skill=="opening" else
                   "endgame" if skill=="endgames" else "middlegame")
            for stratum in strata:
                cells[phase+"::"+stratum]={"policies":{
                    "human":{"numeric":200,"within_25cp":175},
                    "d16":{"numeric":200,"within_25cp":170},
                    "d18":{"numeric":200,"within_25cp":180}}}
            by_skill[skill]=cells
            aggregate[skill]={"distinct_games":100}
        demo["arms"][arm]={"by_competency_stratum":by_skill,
                           "by_competency":aggregate}
    fit=scale.estimate_from_audit(learned,demo,"fit")
    assert fit["status"]=="EXPLORATORY_SIX_VECTOR_NO_FIDE_TRANSFER"
    assert fit["career_ready"] is False
    for skill in audit.COMPETENCIES:
        s=fit["competencies"][skill]
        assert s["d18"]["score_0_110"] is not None
        assert s["d18"]["fide_elo_equivalent"] is None
    # Strict out-of-training projection: insufficient games blocks numeric.
    demo["arms"]["tune"]["by_competency"]["tactics"]["distinct_games"]=3
    tune=scale.estimate_from_audit(learned,demo,"tune")
    assert tune["competencies"]["tactics"]["human"]["score_0_110"] is None
    assert tune["competencies"]["tactics"]["human"]["reason"]=="INSUFFICIENT_TRANSFER_SUPPORT"
