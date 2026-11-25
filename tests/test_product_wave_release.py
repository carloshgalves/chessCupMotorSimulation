import json
import chess.pgn
import pytest

from chesssim.fischer_full_games import atomic_json
from chesssim.product_wave_release import PRIOR, WAVE2_PRIOR, WAVE2_RUN, prior_binding, read_candidate, replay
from chesssim.roster_product_wave import repair_blank_search, WAVE2, WAVE3, WAVE
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
    assert all(prior_binding(slug) == (None, None) for slug in WAVE3)
