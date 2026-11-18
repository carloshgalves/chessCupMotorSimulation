"""Pure career economy rules; not wired to historical engine calibration.

The schedule is an experimental gameplay proposal, not a calibrated claim
about the effort required to gain a real chess skill.
"""
from __future__ import annotations

SCHEDULE = "competency-xp-v1-experimental"
MIN_LEVEL = 0
MAX_LEVEL = 110
COMPETENCIES = ("opening", "tactics", "dynamics", "defense", "positional", "endgames")


def _level(value: int) -> int:
    if type(value) is not int or not MIN_LEVEL <= value <= MAX_LEVEL:
        raise ValueError("Competency must be an integer in 0..110")
    return value


def upgrade_cost(level: int) -> int:
    """Price in integer XP for exactly one step, level -> level+1.

    Strictly increasing and convex in the underlying polynomial; integer
    rounding makes marginal increases nonuniform by at most one XP.
    At the cap a purchase is forbidden, never priced at zero.
    """
    level = _level(level)
    if level == MAX_LEVEL:
        raise ValueError("Competency already at maximum")
    return 100 + 2 * level + level * level // 10


def cumulative_cost(current: int, target: int) -> int:
    """Sum each incremental price; bulk purchases have no flat-rate shortcut."""
    current, target = _level(current), _level(target)
    if target < current:
        raise ValueError("Cannot buy a downgrade")
    return sum(upgrade_cost(level) for level in range(current, target))


def quote_upgrade(levels: dict[str, int], attribute: str, xp: int) -> dict:
    """Read-only quote, no balance, rating, historical persona or engine changes."""
    if set(levels) != set(COMPETENCIES):
        raise ValueError("Exactly the six calibrated career competencies are required")
    for level in levels.values():
        _level(level)
    if attribute not in COMPETENCIES:
        raise ValueError("Unknown competency")
    if type(xp) is not int or xp < 0:
        raise ValueError("XP balance must be a nonnegative integer")
    current = levels[attribute]
    if current == MAX_LEVEL:
        return {"schedule":SCHEDULE,"attribute":attribute,"from_level":current,
                "to_level":None,"xp_cost":None,"affordable":False,"reason":"AT_CAP"}
    cost = upgrade_cost(current)
    return {"schedule":SCHEDULE,"attribute":attribute,"from_level":current,
            "to_level":current+1,"xp_cost":cost,"affordable":xp>=cost,
            "reason":"AVAILABLE" if xp>=cost else "INSUFFICIENT_XP"}
