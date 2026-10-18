import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from demo.evaluate_p2pnet import measure
from intelligent_analysis.contracts import ROI


def test_single_model_mae_uses_roi_threshold_and_scenario_groups():
    roi = ROI.model_validate({"points": [{"x": 0, "y": 0}, {"x": .5, "y": 0},
                                        {"x": .5, "y": 1}, {"x": 0, "y": 1}]})
    samples = [{"id": "one", "scenario": "sparse", "manual_count": 2, "frame": 1},
               {"id": "two", "scenario": "dense", "manual_count": 4, "frame": 2}]
    # Only one point is inside ROI and strictly above 0.5.
    result = measure(samples, lambda frame: np.array([[.2, .2, .9], [.2, .3, .5], [.8, .2, .9]]), roi)
    assert result["mae"] == 2 and result["sample_count"] == 2
    assert result["scenarios"]["sparse"]["mae"] == 1
    assert result["scenarios"]["dense"]["mae"] == 3
    assert "dm_count" not in json.dumps(result)


def test_empty_p2p_manifest_blocks_without_loading_any_weights(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "result.json"
    completed = subprocess.run([sys.executable, "demo/evaluate_p2pnet.py", "--output", str(output),
        "--bundle", str(tmp_path / "nonexistent")], cwd=root, capture_output=True, check=False)
    assert completed.returncode == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "blocked" and result["mae"] is None and result["sample_count"] == 0


def test_no_samples_cannot_be_reported_as_zero_error():
    with pytest.raises(ValueError, match="No reviewed"):
        measure([], lambda image: None, None)
