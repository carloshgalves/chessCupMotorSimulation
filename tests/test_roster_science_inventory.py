"""Engineering fixtures: do not use as historical evidence."""
from chesssim.roster_science_inventory import _summarize

def test_phase_proxies_and_underpowered_cannot_promote():
    rows = [
        {"date": 1971, "color": "white", "plies": 80},
        {"date": 1972, "color": "black", "plies": 61},
        {"date": None, "color": "black", "plies": 15},
    ]
    parts = {
        "train": {"games": 1, "years": [1971]},
        "validation": {"games": 1, "years": [1972]},
        "holdout": {"games": 0, "years": []},
    }
    r = _summarize(rows, parts)
    assert r["accepted_distinct_trajectories"] == 3
    assert r["unknown_year_games"] == 1
    assert r["phase_proxy_focal_decisions"] == {
        "middle_ply_21_to_60": 40, "late_ply_61_plus": 10}
    assert r["sufficiency"] == "UNDERPOWERED_INDEPENDENT_EVALUATION"

def test_even_sufficient_source_is_not_validated():
    parts = {
        "train": {"games": 300, "years": [1900]},
        "validation": {"games": 100, "years": [1901]},
        "holdout": {"games": 100, "years": [1902]},
    }
    assert _summarize([], parts)["sufficiency"] == "CANDIDATE_NOT_STYLE_VALIDATED"
