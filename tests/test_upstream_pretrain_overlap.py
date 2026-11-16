"""Mutation-oriented guards for the TRAIN-only pretrain-overlap probe."""
from __future__ import annotations

from hashlib import sha256
import json

import pytest

from chesssim.upstream_pretrain_overlap import (
    FINGERPRINT_CONTRACT, INDEX_SCHEMA, MODELS,
    audit_train_rows, canonical_position_digest, validate_candidate_index,
    _strict_json, assert_train_layout,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SECOND = "rnbqkbnr/pppppppp/8/8/8/4P3/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def row(slug, game, ply, fen=START, year=1972):
    return {"player": slug, "game_sha256": game, "ply": ply,
            "policy_input": {"player": slug, "year": year, "fen": fen}}


def index(model, games=(), positions=()):
    return {"schema": INDEX_SCHEMA, "model": model,
            "coverage": "CANDIDATE_SUBSET_NOT_TRAINING_ATTESTED",
            "fingerprint_contract": FINGERPRINT_CONTRACT,
            "source_url": "https://example.org/immutable/snapshot",
            "source_artifact_sha256": "a" * 64,
            "game_sha256": list(games), "position_sha256": list(positions)}


def test_absence_of_external_indexes_is_explicit_unknown():
    report = audit_train_rows([row("fischer", "1" * 64, 21)])
    assert report["status"] == "UNVERIFIABLE_PRETRAIN_OVERLAP"
    assert report["globally_distinct_training_games"] == 1
    assert not report["validation_opened"] and not report["final_holdout_opened"]
    for model in MODELS:
        value = report["models"][model]
        assert value["status"] == "UNVERIFIABLE_PRETRAIN_OVERLAP"
        assert not value["candidate_index_supplied"]
        y = value["per_player_year"]["fischer"]["1972"]
        assert y["candidate_exact_game_matches"] == 0
        assert y["unknown_training_provenance_games"] == 1


def test_external_subset_positive_game_and_position_are_only_candidate_matches():
    digest = canonical_position_digest(START)
    r = audit_train_rows([row("fischer", "1" * 64, 21),
                          row("fischer", "1" * 64, 23, SECOND),
                          row("tal", "2" * 64, 21)],
                         {"dsc150": index("dsc150", games=["1" * 64],
                                          positions=[digest])})
    f = r["models"]["dsc150"]["per_player_year"]["fischer"]["1972"]
    assert f == {"arm": "train", "decisions": 2, "games": 1,
                 "candidate_exact_game_matches": 1,
                 "candidate_position_match_games": 1,
                 "candidate_position_match_decisions": 1,
                 "unknown_training_provenance_games": 1}
    assert r["models"]["maia79"]["per_player_year"]["fischer"]["1972"][
        "unknown_training_provenance_games"] == 1


def test_same_global_game_shared_between_players_is_not_double_counted_globally():
    r = audit_train_rows([row("karpov", "2" * 64, 33),
                          row("kasparov", "2" * 64, 34)])
    assert r["globally_distinct_training_games"] == 1
    assert r["training_decisions"] == 2
    assert r["models"]["maia79"]["per_player_year"]["karpov"]["1972"]["games"] == 1


@pytest.mark.parametrize("bad", [
    {"arm": "holdout"}, {"arm": "validation"}, {"arm": "audit"},
    {"game_sha256": "xyz"}, {"player": "carlsen"},
    {"policy_input": {"player": "fischer", "year": 2027, "fen": START}},
    {"policy_input": {"player": "tal", "year": 1972, "fen": START}},
    {"policy_input": {"player": "fischer", "year": 1972, "fen": "bad"}},
])
def test_nontrain_invalid_rows_refused(bad):
    case = row("fischer", "1" * 64, 21)
    case.update(bad)
    with pytest.raises(ValueError):
        audit_train_rows([case])


def test_duplicate_observation_and_game_date_collision_are_refused():
    a = row("fischer", "1" * 64, 21)
    with pytest.raises(ValueError, match="Duplicate"):
        audit_train_rows([a, a])
    with pytest.raises(ValueError, match="different years"):
        audit_train_rows([a, row("fischer", "1" * 64, 23, year=1973)])


def test_canonical_position_ignores_counters_not_rights():
    assert canonical_position_digest(START) == canonical_position_digest(
        START.replace(" 0 1", " 19 42"))
    assert canonical_position_digest(START) != canonical_position_digest(
        START.replace("KQkq", "KQ"))
    assert len(canonical_position_digest(START)) == 64


@pytest.mark.parametrize("field,value", [
    ("coverage", "COMPLETE"), ("coverage", "VERIFIED"),
    ("model", "maia79"), ("fingerprint_contract", "other"),
    ("game_sha256", ["1" * 64, "1" * 64]),
    ("position_sha256", ["NAN"]),
    ("source_url", "http://example.org"),
    ("source_url", "https://user:secret@example.org/a"),
    ("source_artifact_sha256", "unknown"),
])
def test_reject_fake_completeness_and_invalid_external_indexes(field, value):
    bad = index("dsc150")
    bad[field] = value
    with pytest.raises(ValueError):
        validate_candidate_index(bad, "dsc150")


def test_json_parser_rejects_duplicate_keys_and_symlink(tmp_path):
    p = tmp_path / "candidate.json"
    p.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        _strict_json(p)
    target = tmp_path / "target.json"
    target.write_text(json.dumps(index("dsc150")), encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        _strict_json(link)


def test_roundtrip_real_index_contract_and_nonmatch_stays_unknown():
    raw = index("maia79", games=[sha256(b"unrelated").hexdigest()])
    checked = validate_candidate_index(raw, "maia79")
    assert checked["game_sha256"]
    r = audit_train_rows([row("tal", "2" * 64, 21)], {"maia79": raw})
    observed = r["models"]["maia79"]["per_player_year"]["tal"]["1972"]
    assert observed["candidate_exact_game_matches"] == 0
    assert observed["unknown_training_provenance_games"] == 1


def test_no_train_directory_symlink_or_other_arm_path(tmp_path):
    outside = tmp_path / "reserved"
    outside.mkdir()
    (outside / "1972.jsonl").write_text("{}\n", encoding="utf-8")
    cohorts = tmp_path / "cohorts"
    cohorts.mkdir()
    (cohorts / "train").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        assert_train_layout(cohorts, {"training_files": ["train/fischer/1972.jsonl"]})
    with pytest.raises(ValueError, match="non-TRAIN"):
        assert_train_layout(cohorts, {"training_files": ["holdout/fischer/1972.jsonl"]})


def test_train_subdirectory_symlink_blocked(tmp_path):
    cohorts = tmp_path / "cohorts"
    (cohorts / "train").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "1972.jsonl").write_text("{}\n", encoding="utf-8")
    (cohorts / "train" / "fischer").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        assert_train_layout(cohorts, {"training_files": ["train/fischer/1972.jsonl"]})
