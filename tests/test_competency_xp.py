import pytest

from chesssim.competency_xp import (
    COMPETENCIES, MAX_LEVEL, cumulative_cost, quote_upgrade, upgrade_cost,
)


def test_every_increment_is_more_expensive():
    costs = [upgrade_cost(n) for n in range(MAX_LEVEL)]
    assert costs[0] == 100
    assert costs[90] == 1090
    assert all(b > a for a,b in zip(costs,costs[1:]))
    assert costs[-1] == 1506
    assert cumulative_cost(90,97)==sum(costs[90:97])
    assert cumulative_cost(110,110)==0


@pytest.mark.parametrize("level", [-1,110,111,True,90.0,"90",None])
def test_invalid_or_capped_upgrade(level):
    with pytest.raises(ValueError):
        upgrade_cost(level)


def test_quotes_do_not_change_historical_persona_or_rating():
    levels = {attr:90 for attr in COMPETENCIES}
    original = levels.copy()
    assert not quote_upgrade(levels,"opening",1089)["affordable"]
    quote = quote_upgrade(levels,"opening",1090)
    assert quote["affordable"] and quote["to_level"]==91
    assert levels==original and "rating" not in quote
    levels["opening"]=110
    assert quote_upgrade(levels,"opening",100000)["reason"]=="AT_CAP"
    levels["opening"]=None
    with pytest.raises(ValueError):
        quote_upgrade(levels,"opening",100000)


def test_no_downgrade_bad_xp_or_extra_dimensions():
    with pytest.raises(ValueError):
        cumulative_cost(100,99)
    levels = {attr:90 for attr in COMPETENCIES}
    for xp in (-1, True, 10.5):
        with pytest.raises(ValueError):
            quote_upgrade(levels,"opening",xp)
    with pytest.raises(ValueError):
        quote_upgrade({**levels,"rating":2500},"opening",10000)
