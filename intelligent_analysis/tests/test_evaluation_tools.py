import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from demo.benchmark_frames import load_samples, measure
from demo.check_rag_questions import citation_matches, run_suite

ROOT = Path(__file__).resolve().parents[1]


def test_real_notice_question_suite_and_citation_tampering():
    snapshot = json.loads((ROOT / "docs/fixtures/C020a2-historical-notice/index.json").read_text(encoding="utf-8"))
    suite = json.loads((ROOT / "docs/fixtures/rag-acceptance/questions.json").read_text(encoding="utf-8"))
    report = run_suite(snapshot, suite)
    assert report["passed"] == 9
    assert report["human_semantic_review"] == "pending"
    assert not report["D_HTTP_and_intent_routing_verified"]
    citation = report["cases"][0]["citations"][0]
    assert citation_matches(citation, snapshot)
    assert not citation_matches({**citation, "url": "https://example.invalid/wrong"}, snapshot)
    assert not citation_matches({**citation, "chunk_id": "invented-chunk"}, snapshot)
    bad_suite = copy.deepcopy(suite)
    bad_suite["cases"][0]["document_id"] = "wrong-id"
    assert run_suite(snapshot, bad_suite)["status"] == "failed"


@pytest.fixture
def label_manifest(tmp_path):
    manifest = json.loads((ROOT / "docs/fixtures/video-evaluation/manifest.template.json").read_text(encoding="utf-8"))
    content = b"synthetic bytes for manifest validation only, not an image"
    (tmp_path / "frame.png").write_bytes(content)
    manifest.update(authorization_status="confirmed", data_kind="synthetic")
    manifest["samples"] = [{"id": "test-frame-1", "scenario": "sparse", "file": "frame.png",
        "sha256": hashlib.sha256(content).hexdigest(), "source_video_id": "synthetic-video",
        "source_video_sha256": "1" * 64, "recorded_at": "2026-10-15T08:00:00+08:00",
        "manual_count": 2, "uncertain_people": 0, "annotator": "test-A", "reviewer": "test-B",
        "label_status": "reviewed"}]
    return tmp_path / "manifest.json", manifest


@pytest.mark.parametrize("mutation", ["sha", "duplicate", "fractional", "boolean", "uncertain",
    "same_reviewer", "naive_time", "path_escape", "authorization"])
def test_invalid_labels_fail_before_inference(label_manifest, mutation):
    path, manifest = label_manifest
    item = manifest["samples"][0]
    if mutation == "sha":
        item["sha256"] = "0" * 64
    elif mutation == "duplicate":
        manifest["samples"].append(copy.deepcopy(item))
    elif mutation == "fractional":
        item["manual_count"] = 2.5
    elif mutation == "boolean":
        item["manual_count"] = True
    elif mutation == "uncertain":
        item["uncertain_people"] = 1
    elif mutation == "same_reviewer":
        item["reviewer"] = item["annotator"]
    elif mutation == "naive_time":
        item["recorded_at"] = "2026-10-15T08:00:00"
    elif mutation == "path_escape":
        item["file"] = "../frame.png"
    else:
        manifest["authorization_status"] = "pending"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_samples(path)


def test_dm_only_and_paired_metrics_share_labels_without_fake_p2p(label_manifest):
    path, manifest = label_manifest
    path.write_text(json.dumps(manifest), encoding="utf-8")
    _, samples = load_samples(path)
    samples[0]["frame"] = np.zeros((128, 128, 3), dtype=np.uint8)
    dm = lambda f: np.full((2, 2), 0.625)
    dm_only = measure(samples, dm, None, {"synthetic": True}, None)
    assert dm_only["frames"][0]["dm_count"] == 2.5
    assert dm_only["scenarios"]["sparse"]["dm_mae"] == 0.5
    assert dm_only["p2p_provenance"] is None
    assert "p2p_mae" not in dm_only["scenarios"]["sparse"]
    seen = []

    def p2p(frame):
        seen.append(frame)
        return np.array([[0.25, 0.25, 0.9], [0.75, 0.75, 0.9]])

    paired = measure(samples, dm, p2p, {"synthetic": True}, {"synthetic": True})
    assert seen[0] is samples[0]["frame"]
    assert paired["scenarios"]["sparse"]["p2p_mae"] == 0
    assert paired["scenarios"]["sparse"]["dm_mae"] == 0.5


def test_empty_manifest_cli_reports_pending_without_mae(tmp_path):
    output = tmp_path / "report.json"
    result = subprocess.run([sys.executable, "demo/benchmark_frames.py", "--output", str(output)],
        cwd=ROOT, check=False, capture_output=True, text=True)
    assert result.returncode == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["reason"] == "awaiting_authorized_reviewed_frames"
    assert report["scenarios"] == {} and report["frames"] == []
    assert not report["real_video_accuracy_verified"]


@pytest.mark.skipif(os.environ.get("RUN_DM_BENCHMARK") != "1",
    reason="Set RUN_DM_BENCHMARK=1 with the verified DM-Count weights installed")
def test_cli_with_official_dm_weights_and_synthetic_p2p_model(label_manifest, tmp_path):
    import cv2
    import torch
    path, manifest = label_manifest
    frame = np.zeros((128, 128, 3), dtype=np.uint8)
    ok, png = cv2.imencode(".png", frame)
    assert ok
    (tmp_path / "frame.png").write_bytes(png.tobytes())
    (tmp_path / "source.rgb").write_bytes(frame.tobytes())
    manifest["samples"][0].update(sha256=hashlib.sha256(png.tobytes()).hexdigest(), manual_count=0,
        source_video_sha256=hashlib.sha256(frame.tobytes()).hexdigest())
    path.write_text(json.dumps(manifest), encoding="utf-8")
    factory = tmp_path / "synthetic_p2p.py"
    factory.write_text('''import torch
class Model(torch.nn.Module):
    def forward(self, x):
        return {"pred_logits":torch.tensor([[[0.,10.],[0.,10.]]]),
                "pred_points":torch.tensor([[[32.,32.],[96.,96.]]])}
def build():
    return Model()
''', encoding="utf-8")
    weights = tmp_path / "synthetic.pth"
    torch.save({}, weights)
    output = tmp_path / "paired.json"
    subprocess.run([sys.executable, "-X", "utf8", "demo/benchmark_frames.py",
        "--manifest", str(path), "--output", str(output), "--p2p-factory", "synthetic_p2p:build",
        "--p2p-weights", str(weights), "--p2p-sha256", hashlib.sha256(weights.read_bytes()).hexdigest(),
        "--p2p-version", "synthetic-test-only", "--p2p-source-revision", "synthetic-test-fixture"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT)])}, check=True)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "measured_pending_review"
    assert report["scenarios"]["sparse"]["p2p_mae"] == 2
    assert np.isfinite(report["scenarios"]["sparse"]["dm_mae"])
    assert report["P2PNet_runtime_smoke_passed"]
    assert not report["P2PNet_project_compatibility_verified"]
    assert not report["real_video_accuracy_verified"]
