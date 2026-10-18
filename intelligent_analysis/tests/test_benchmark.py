import numpy as np
import pytest

from intelligent_analysis.contracts import ROI
from intelligent_analysis.vision.benchmark import compare_counters, point_count


def test_same_roi_frames_and_scenario_mae(config):
    sample = {"id": "synthetic-1", "scenario": "sparse", "manual_count": 2,
              "frame": np.zeros((2, 2)), "roi": config["roi"]}
    result = compare_counters([sample], lambda f: np.full((2, 2), 0.5),
        lambda f: np.array([[0.2, 0.2, 0.9], [0.8, 0.8, 0.9], [0.5, 0.5, 0.1]]),
        dm_provenance={"weights": "synthetic"}, p2p_provenance={"weights": "synthetic"})
    assert result["scenarios"]["sparse"]["dm_mae"] == 0
    assert result["scenarios"]["sparse"]["p2p_mae"] == 0
    with pytest.raises(ValueError):
        point_count(np.array([[float("nan"), 0, 1]]), ROI.model_validate(config["roi"]))
