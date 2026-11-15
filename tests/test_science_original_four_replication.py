"""Engineering selection contracts only; historical CI downloads real PGNs."""
import pytest
from chesssim.science_original_four_replication import COUNT, SEED, PAIRED_SEED, _choose_fit_replication


def test_pre_registered_selection_parameters_are_frozen():
    assert COUNT==128 and SEED==3109 and PAIRED_SEED==4109


def test_invalid_sample_size_rejected_without_reading_any_source(tmp_path):
    with pytest.raises(ValueError,match="sample size"):
        _choose_fit_replication(tmp_path,129)
    with pytest.raises(ValueError,match="sample size"):
        _choose_fit_replication(tmp_path,1)
