import json
import chess.pgn
import pytest

from chesssim.fischer_full_games import atomic_json
from chesssim.product_wave_release import (
    PRIOR, WAVE2_PRIOR, WAVE2_RUN, WAVE3_PRIOR, WAVE3_RUN, prior_binding, read_candidate, replay,
)
from chesssim.roster_product_wave import (
    repair_blank_search, WAVE2, WAVE3, WAVE4, WAVE, INDIVIDUAL_SEEDS,
    TOPALOV_REPAIR, candidate_parameters, identity_comparison, shortlist_for,
)
from chesssim.catalog import find_player


def test_blank_search_repair_preserves_evaluation_and_fills_only_missing_values():
    raw = "setoption name OwnAttack value 125\nsetoption name SlowMover value \nsetoption name SearchSkill value\nsetoption name Contempt value 7\n"
    simple = "setoption name SlowMover value 100\nsetoption name SearchSkill value 10\nsetoption name Contempt value -5\n"
    repaired = repair_blank_search(raw, simple)
    assert repaired == "setoption name OwnAttack value 125\nsetoption name SlowMover value 100\nsetoption name SearchSkill value 10\nsetoption name Contempt value 7\n"
    assert repair_blank_search(repaired, simple) == repaired
    with pytest.raises(ValueError, match="Missing pinned search control"):
        repair_blank_search(raw, "")


def test_wave_two_uses_four_distinct_individual_profiles():
    assert len(set(WAVE2)) == 4
    assert not set(WAVE2) & (set(PRIOR) | {"alekhine", "fischer", "tal", "karpov", "kasparov"})
    assert all(find_player(slug).rodent_alias and WAVE[slug][0] < WAVE[slug][1] for slug in WAVE2)


def test_rejected_candidate_never_loads_book(tmp_path):
    atomic_json(tmp_path / "report.json", {"slug": "alekhine", "status": "NO_ELIGIBLE_CANDIDATE"})
    report, book, variant = read_candidate("alekhine", tmp_path, tmp_path)
    assert report["status"] == "NO_ELIGIBLE_CANDIDATE"
    assert book is variant is None


def test_replay_rejects_incomplete_and_fake_terminal(tmp_path):
    atomic_json(tmp_path / "summary.json", {"status": "INCOMPLETE", "games": []})
    with pytest.raises(ValueError, match="Two complete"):
        replay(tmp_path, {}, "botvinnik", "fischer", 2029)
    (tmp_path / "summary.json").write_text(json.dumps({"status": "COMPLETED", "games": [
        {"opening_trace": [], "result": "1/2-1/2"}, {"opening_trace": [], "result": "1/2-1/2"}]}))
    for i in (1, 2):
        game = chess.pgn.Game()
        game.headers["Result"] = "1/2-1/2"
        (tmp_path / f"jogo_{i}.pgn").write_text(str(game))
    with pytest.raises(ValueError, match="Unverified terminal"):
        replay(tmp_path, {}, "botvinnik", "fischer", 2029)


def test_wave_two_exact_artifact_bindings_and_wave_three_disjoint():
    from chesssim.qualified_runtime import SLUGS
    assert set(WAVE2_PRIOR) == set(WAVE2)
    assert WAVE2_RUN == 45597119316
    assert len({row[0] for row in WAVE2_PRIOR.values()}) == 4
    for slug, row in WAVE2_PRIOR.items():
        assert prior_binding(slug) == (WAVE2_RUN, row)
        assert row[1].endswith("20")
        assert all(len(h) == 64 and set(h) <= set("0123456789abcdef") for h in row[2:])
    assert len(set(WAVE3)) == 8
    assert not set(WAVE3) & (SLUGS | set(WAVE2))
    assert all(find_player(slug).rodent_alias and WAVE[slug][0] < WAVE[slug][1] for slug in WAVE3)
    assert set(WAVE3_PRIOR) == set(WAVE3) - {"topalov"}
    assert WAVE3_RUN == 45633448337
    assert len({v[0] for v in WAVE3_PRIOR.values()}) == 7
    assert all(prior_binding(slug) == (WAVE3_RUN, v) for slug, v in WAVE3_PRIOR.items())
    assert prior_binding("topalov") == (None, None)


def test_individual_recipes_are_distinct_and_do_not_allow_simple():
    from chesssim.qualified_runtime import SLUGS
    assert len(WAVE4) == 8
    assert not set(WAVE4) & (SLUGS | set(WAVE2) | set(WAVE3))
    signatures = set()
    for slug in WAVE4:
        assert find_player(slug).rodent_alias is None
        params = candidate_parameters(slug, "")
        assert params["original"] == {}
        assert params["individual"] == INDIVIDUAL_SEEDS[slug]
        for label in ("individual", "initiative", "restriction"):
            signature = tuple(sorted(params[label].items()))
            assert signature not in signatures
            signatures.add(signature)
    assert len(signatures) == 24


def test_topalov_screens_six_strong_depth_candidates():
    keys = ("OwnAttack", "PiecePressure", "OwnMobility", "OppMobility", "Outposts", "PawnStructure")
    raw = "\n".join(f"setoption name {k} value 100" for k in keys)
    params = candidate_parameters("topalov", raw)
    assert set(TOPALOV_REPAIR) <= set(params)
    stats = {f"{label}{depth}": {"numeric": 60, "excess_cp": 0, "severe_excess": 0,
                                "depth_reached": 1, "objective": i}
             for i, label in enumerate(params) for depth in (12, 16, 20)}
    selected = shortlist_for("topalov", params, [], stats)
    assert len(set(selected)) == 6
    assert sum(label.endswith("16") for label in selected) == 3
    assert sum(label.endswith("20") for label in selected) == 3


def test_individual_control_gate_requires_behavior_difference_and_quality(monkeypatch):
    from chesssim import roster_product_wave as wave
    records = [{"values": {"individual16": {"move": "e2e4"}, "original16": {"move": "d2d4"}}}
               for _ in range(60)]
    def summary(rows):
        return {"individual16": {"style_gap": .04, "excess_cp": 3},
                "original16": {"style_gap": .03, "excess_cp": 0}}
    monkeypatch.setattr(wave, "summarize", summary)
    assert identity_comparison(records, "individual16")["passed"]
    for row in records:
        row["values"]["original16"]["move"] = "e2e4"
    assert not identity_comparison(records, "individual16")["passed"]
    for row in records:
        row["values"]["original16"]["move"] = "d2d4"
    monkeypatch.setattr(wave, "summarize", lambda rows: {
        "individual16": {"style_gap": .10, "excess_cp": 3},
        "original16": {"style_gap": .03, "excess_cp": 0}})
    assert not identity_comparison(records, "individual16")["passed"]
