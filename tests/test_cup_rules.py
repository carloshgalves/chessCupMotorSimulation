"""#58: pure World Cup rules, roster eligibility and transparent sealed bids."""
from __future__ import annotations

from collections import Counter
import pytest

from chesssim.cup_rules import (
    CLASSICAL, FEMALE_ROSTER, FIDE_CUP_2025, RULES_VERSION,
    STAGES, CupInput, champion_eligibility, checksum, normalize,
    presets, projected_game, sealed_bid, seed_slots, stage_game, women_schedule,
)


def setup(preset="custom", participants=("fischer","tal"),**kw):
    config={
        "name":"Copa dos Imortais","edition":"Edição 1","preset":preset,
        "participants":list(participants),"execution_mode":"fast_statistical",
        "seed":2025,"depth":6,
    }
    config.update(kw)
    return normalize(config)


@pytest.mark.parametrize("n", [2,3,5,8,16,18,32,64])
def test_seeded_knockout_bracket_with_explicit_byes(n):
    from chesssim.catalog import load_roster
    config=setup(participants=[p.slug for p in load_roster()[:n]])
    slots=seed_slots(config)
    assert len(slots)==1 << (n-1).bit_length()
    assert len([x for x in slots if x])==n
    assert len(set(x for x in slots if x))==n
    assert sum(x is None for x in slots)==len(slots)-n
    assert all(pair[0] is not None or pair[1] is not None
               for i in range(0,len(slots),2)
               if (pair:=slots[i:i+2]))
    assert seed_slots(config)==slots


def test_world_champions_eligibility_is_documented_not_roster_group():
    eligible=champion_eligibility()
    assert len(CLASSICAL)==17
    assert len(eligible)==18
    assert "topalov" in eligible
    assert eligible["topalov"]["category"]=="split_fide_1993_2006"
    assert eligible["kasparov"]["split_classical"] is True
    assert eligible["kramnik"]["split_classical"] is True
    assert "morphy" not in eligible
    assert "short" not in eligible
    assert "judit-polgar" not in eligible
    assert presets()["country_filters"]["brazil"]["participants"]==["mequinho"]
    assert presets()["country_filters"]["brazil"]["source"]==(
        "https://ratings.fide.com/profile/2100010")
    assert "gukesh" not in eligible # Not present in the 64-person roster
    assert len(presets()["world_champions"]["participants"])==18
    assert all(eligible[x]["source"].startswith("https://")
               for x in eligible)
    with pytest.raises(CupInput,match="título mundial"):
        setup("world_champions",["fischer","morphy"])
    ok=setup("world_champions",["fischer","topalov"])
    assert ok["format"]=="knockout"
    assert ok["rules_version"]==RULES_VERSION


def test_five_women_double_round_robin_has_one_bye_each_round():
    cfg=setup("women",FEMALE_ROSTER)
    rounds=women_schedule(cfg)
    assert len(rounds)==10
    assert sum(len(r["games"]) for r in rounds)==20
    assert all(len(r["games"])==2 and r["bye"] in FEMALE_ROSTER
               for r in rounds)
    assert Counter(r["bye"] for r in rounds)=={p:2 for p in FEMALE_ROSTER}
    all_games=[g for r in rounds for g in r["games"]]
    assert len(set(g["id"] for g in all_games))==20
    colors=Counter((g["white"],g["black"]) for g in all_games)
    assert len(colors)==20
    assert set(colors.values())=={1}
    for r in rounds:
        players=[x for game in r["games"] for x in (game["white"],game["black"])]
        assert len(players)==len(set(players))==4
        assert r["bye"] not in players
    with pytest.raises(CupInput,match="cinco perfis"):
        setup("women",["menchik","gaprindashvili","chiburdanidze",
                        "hou-yifan","fischer"])


def test_2025_stages_and_armageddon_clocks_no_bullet():
    assert [s["key"] for s in STAGES]==[
        "classical","rapid15","rapid10","blitz5","blitz3","armageddon",
    ]
    assert [s["games"] for s in STAGES]==[2,2,2,2,2,1]
    assert STAGES[0]["clock"]==[5400,30]
    assert STAGES[0]["extra_after_40"]==1800
    assert STAGES[1]["clock"]==[900,10]
    assert STAGES[2]["clock"]==[600,10]
    assert STAGES[3]["clock"]==[300,3]
    assert STAGES[4]["clock"]==[180,2]
    assert len(STAGES)==6
    assert FIDE_CUP_2025.endswith(".pdf")


def test_each_tiebreak_stage_has_independent_seeded_color_draw():
    cfg=setup()
    match={"id":"r01-m01","round":1,"players":["fischer","tal"]}
    proofs=set()
    for stage in range(5):
        first=stage_game(match,cfg,stage,0)
        second=stage_game(match,cfg,stage,1)
        assert first["white"]==second["black"]
        assert first["black"]==second["white"]
        assert first["color_draw"]==second["color_draw"]
        assert first["color_draw"]["first_white"]==first["white"]
        assert first["color_draw"]["policy"]=="independent-seeded-stage-lottery-v1"
        assert first==stage_game(match,cfg,stage,0)
        proofs.add(first["color_draw"]["sha256"])
    assert len(proofs)==5, "New color draw for each stage"
    assert stage_game(match,cfg,5,0)["color_draw"] is None


def test_sealed_bidding_is_reproducible_and_not_UCI_claim():
    config=setup()
    a,b="fischer","tal"
    match_id="r01-m01"
    proof=sealed_bid(config,match_id,a,b)
    assert proof==sealed_bid(config,match_id,a,b)
    assert proof["policy"]=="chesssim-deterministic-sealed-bid-v1"
    assert proof["black"]!=proof["white"]
    assert proof["black"] in (a,b)
    assert proof["winning_black_seconds"]<=240
    assert proof["increment_seconds"]==2
    for slug,offer in proof["offers"].items():
        assert 60<=offer["seconds"]<=240
        assert offer["commitment"]==checksum({
            "match":match_id,"slug":slug,
            "seconds":offer["seconds"],"nonce":offer["nonce"]})
    spec=stage_game({"id":match_id,"round":1,"players":[a,b]},config,5,0)
    assert spec["white"]==proof["white"]
    assert spec["clock"]==[240,proof["winning_black_seconds"],2]
    assert spec["bid_proof"]==proof


def test_equal_bids_use_seeded_lottery_and_black_draw_odds():
    c=setup(armageddon_white_seconds=300,
            armageddon_min_black_seconds=210,
            armageddon_max_black_seconds=210,
            armageddon_increment_seconds=3)
    bid=sealed_bid(c,"r01-m01","fischer","tal")
    assert bid["tie"] is True
    assert bid["lottery"] in (0,1)
    assert bid["winning_black_seconds"]==210
    assert bid["white_seconds"]==300
    assert bid["increment_seconds"]==3


@pytest.mark.parametrize("field,value",[
    ("participants",["fischer","fischer"]),("participants",["unknown","tal"]),
    ("participants",["fischer"]),("participants",list(range(4))),
    ("preset","invalid"),("seed",True),("depth",99),
    ("name","a"),("edition","\n"),("execution_mode","historical_true"),
    ("seed_method","secret_UCI"),("armageddon_white_seconds",0),
    ("armageddon_increment_seconds",31),
    ("armageddon_min_black_seconds",400),
    ("armageddon_max_black_seconds",241),
])
def test_invalid_settings_fail_closed(field,value):
    with pytest.raises(CupInput):
        setup(**{field:value})


def test_synthetic_projection_does_not_depend_on_engine_or_elo():
    c=setup()
    match={"id":"r01-m01","round":1,"players":["tal","fischer"]}
    spec=stage_game(match,c,0,0)
    assert projected_game(c,spec) in ("1-0","0-1","1/2-1/2")
    assert projected_game(c,spec)==projected_game(c,spec)
    assert c["execution_mode"]=="fast_statistical"
