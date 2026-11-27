from chesssim import bilateral_cp as cp
from chesssim.bilateral_cp_recovery import choose_panels, robust_select, shortlist, verify_locks


def summary(delta, phase_gap=0):
    return {"delta_cp": delta, "coverage": 1, "phase_support": {p: 25 for p in cp.PHASES},
            "phase_delta": {"opening": phase_gap, "middlegame": 0, "endgame": 0},
            "severe_gap": 0, "mate_gap": 0}


def test_retention_binds_five_existing_product_profiles():
    records = verify_locks()
    assert set(records) == {"alekhine", "tal", "smyslov", "petrosian", "spassky"}
    assert records["spassky"]["depth"] == 20
    assert records["tal"]["book_hash_kind"] == "bytes"
    assert all(len(r["historical_book_sha256"]) == 64 for r in records.values())
    assert all(abs(r["delta_cp"]) <= 1 for r in records.values())


def test_robust_selection_disfavors_fit_overfitting():
    fit = {"baseline": summary(.8), "overfit": summary(0), "robust": summary(.6)}
    tune = {"baseline": summary(.9), "overfit": summary(7), "robust": summary(-.7)}
    assert robust_select(fit, tune) == "robust"
    tune["robust"] = summary(-.7, phase_gap=12)
    assert robust_select(fit, tune) == "baseline"


def test_shortlist_always_preserves_baseline():
    cases = {"baseline": ("original", 20, 5000000)}
    cases.update({str(i): ("original", i, 5000000) for i in range(8, 16)})
    summaries = {k: summary(8 if k == "baseline" else .1) for k in cases}
    result = shortlist(summaries, cases)
    assert len(result) == 6
    assert result["baseline"] == cases["baseline"]


def test_panels_exclude_reserved_and_duplicate_fens():
    reserved = [{"fen": "a b c d 0 1", "game": "test"}]
    group = {("fit", "opening"): [{"fen": "a b c d 1 2", "game": "fit0", "id": "a"},
                                  {"fen": "e f g h 0 1", "game": "fit1", "id": "b"}],
             ("tune", "opening"): [{"fen": "e f g h 1 2", "game": "tune0", "id": "a"},
                                   {"fen": "i j k l 0 1", "game": "tune1", "id": "b"}]}
    from collections import defaultdict
    panels = choose_panels(defaultdict(list, group), reserved)
    assert [r["game"] for r in panels["fit"]] == ["fit1"]
    assert [r["game"] for r in panels["tune"]] == ["tune1"]
