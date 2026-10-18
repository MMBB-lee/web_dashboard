import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from demo.score_leader_comparison import score

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def comparison():
    predictions = {"dataset_id": "synthetic-scoring-test", "roi": {"points": []}, "frames": [],
        "dm_provenance": {"synthetic": True}, "p2p_provenance": {"synthetic": True}}
    labels = {"dataset_id": predictions["dataset_id"], "roi": copy.deepcopy(predictions["roi"]),
        "label_revision": "synthetic-v1", "samples": []}
    for index, (dm, p2p, manual) in enumerate([(2.25, 3, 2), (5.5, 4, 5)]):
        frame = {"id": f"f{index}", "file": f"frames/f{index}.png", "sha256": "1" * 64,
            "frame_index": index, "nominal_offset_seconds": float(index), "dm_count": dm, "p2pnet": p2p}
        predictions["frames"].append(frame)
        label = {k: frame[k] for k in ("id", "file", "sha256", "frame_index", "nominal_offset_seconds")}
        label.update(manual_count=manual, uncertain_people=0, annotator="A", reviewer="B",
            scenario="sparse" if index == 0 else "dense", label_status="reviewed")
        labels["samples"].append(label)
    return predictions, labels


def test_known_errors_and_out_of_order_labels_preserve_frame_identity(comparison):
    predictions, labels = comparison
    labels["samples"].reverse()
    result = score(predictions, labels)
    assert result["status"] == "exploratory_scored"
    assert result["dm_mae"] == 0.375 and result["p2p_mae"] == 1
    assert result["frames"][0]["manual_count"] == 2
    assert result["scenarios"]["dense"]["dm_mae"] == 0.5
    assert not result["formal_holdout_verified"] and not result["model_replacement_approved"]


@pytest.mark.parametrize("mutation", ["missing", "fractional", "boolean", "uncertain", "reviewer", "revision", "status", "scenario"])
def test_pending_or_invalid_review_never_produces_partial_mae(comparison, mutation):
    predictions, labels = comparison
    label = labels["samples"][0]
    if mutation == "missing":
        label["manual_count"] = None
    elif mutation == "fractional":
        label["manual_count"] = 2.5
    elif mutation == "boolean":
        label["manual_count"] = True
    elif mutation == "uncertain":
        label["uncertain_people"] = 1
    elif mutation == "reviewer":
        label["reviewer"] = " A "
    elif mutation == "revision":
        labels["label_revision"] = None
    elif mutation == "status":
        label["label_status"] = "pending"
    else:
        label["scenario"] = None
    result = score(predictions, labels)
    assert result["status"] == "awaiting_reviewed_labels"
    assert result["dm_mae"] is None and result["p2p_mae"] is None
    assert not result["frames"] and not result["scenarios"]


@pytest.mark.parametrize("mutation", ["roi", "hash", "duplicate", "missing_frame", "nonfinite"])
def test_mismatched_frozen_input_is_rejected(comparison, mutation):
    predictions, labels = comparison
    if mutation == "roi":
        labels["roi"] = {"points": [{"x": 0, "y": 0}]}
    elif mutation == "hash":
        labels["samples"][0]["sha256"] = "wrong"
    elif mutation == "duplicate":
        labels["samples"][1] = copy.deepcopy(labels["samples"][0])
    elif mutation == "missing_frame":
        labels["samples"].pop()
    else:
        predictions["frames"][0]["dm_count"] = float("nan")
    with pytest.raises(ValueError):
        score(predictions, labels)


def test_cli_protects_human_labels_images_and_frozen_predictions(comparison, tmp_path):
    predictions, labels = comparison
    for frame, label in zip(predictions["frames"], labels["samples"]):
        for file_key, hash_key, relative in (("file", "sha256", frame["file"]),
                ("density_file", "density_sha256", f"predictions/{frame['id']}.npy"),
                ("point_file", "point_sha256", f"predictions/{frame['id']}.json")):
            path = tmp_path / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"synthetic asset")
            frame.update({file_key: relative, hash_key: hashlib.sha256(path.read_bytes()).hexdigest()})
        label["sha256"] = frame["sha256"]
    data = json.dumps(predictions).encode()
    (tmp_path / "predictions/results.json").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "run-receipt.json").write_text(json.dumps({"prediction_sha256": digest}), encoding="utf-8")
    labels["prediction_sha256"] = digest
    (tmp_path / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    command = [sys.executable, "demo/score_leader_comparison.py", "--run-dir", str(tmp_path)]
    for target in (tmp_path / "labels.json", tmp_path / "frames/f0.png", tmp_path / "predictions/results.json"):
        original = target.read_bytes()
        result = subprocess.run([*command, "--output", str(target)], cwd=ROOT, check=False, capture_output=True)
        assert result.returncode != 0 and target.read_bytes() == original
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
    assert json.loads((tmp_path / "score-report.json").read_text())["dm_mae"] == 0.375
    (tmp_path / "frames/f0.png").write_bytes(b"tampered")
    output = tmp_path / "tampered-report.json"
    result = subprocess.run([*command, "--output", str(output)], cwd=ROOT, check=False, capture_output=True)
    assert result.returncode != 0 and not output.exists()
