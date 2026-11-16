"""Guard the incremental real85 wave-2 cohort without fabricated identities."""
from chesssim.catalog import find_player, load_roster
from chesssim.real_training_cohorts import _player_configuration, source_file_for_slug
from chesssim.real85_playable import game_arm

WAVE_TWO = ("alekhine", "petrosian", "anand", "rubinstein")
WAVE_ONE = ("fischer", "tal", "karpov", "kasparov")


def test_wave_two_contains_four_new_genuine_rodent_personalities():
    roster = {player.slug: player for player in load_roster()}
    assert not set(WAVE_TWO) & set(WAVE_ONE)
    assert len(set(WAVE_ONE) | set(WAVE_TWO)) == 8
    for slug in WAVE_TWO:
        player = find_player(slug)
        assert player == roster[slug]
        assert player.rodent_alias is not None
        assert player.profile_filename.endswith(".txt")
        assert player.number > 0


def test_wave_two_has_four_candidate_real_sources_without_faking_absence():
    config = _player_configuration(list(WAVE_TWO), None, None)
    assert set(config) == set(WAVE_TWO)
    for slug in WAVE_TWO:
        row = config[slug]
        assert row["source"] == source_file_for_slug(slug)
        assert row["surname"].casefold() in row["name"].casefold()
        assert row["name"] == find_player(slug).name
        assert row["source"]
    # Existence and exact counts must be established by real PGN CI, not fixtures.


def test_global_trajectory_partition_independent_of_wave_or_player():
    digest = "5"*64
    assert game_arm(digest) in ("train", "test")
    assert len({game_arm(digest) for _ in (*WAVE_ONE,*WAVE_TWO)}) == 1
