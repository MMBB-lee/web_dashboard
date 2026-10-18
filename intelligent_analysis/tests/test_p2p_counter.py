import hashlib

import numpy as np
import pytest

from intelligent_analysis import ServiceBindings, analyze_video, configure_services
from intelligent_analysis.contracts import VideoAnalysisConfig
from intelligent_analysis.integration import UnsupportedAnalysis
from intelligent_analysis.vision.density_counter import WeightSpec
from intelligent_analysis.vision.p2p_counter import P2PNetCounter, build_team_model


def test_formal_contract_uses_p2p_and_fixed_threshold(config):
    parsed = VideoAnalysisConfig.model_validate(config)
    assert parsed.count_model == "p2pnet" and parsed.confidence_threshold == 0.5
    for invalid in ({"count_model": "dm_count"}, {"confidence_threshold": 0.6}):
        with pytest.raises(ValueError):
            VideoAnalysisConfig.model_validate({**config, **invalid})


def test_bad_weights_cannot_enqueue_video(config, tmp_path):
    from uuid import uuid4
    path = tmp_path / "changed.pth"
    path.write_bytes(b"wrong weights")
    counter = P2PNetCounter(WeightSpec(str(path), "0" * 64, "test-p2pnet"),
        lambda: None, source_revision="synthetic-test")
    queued = []
    configure_services(ServiceBindings(validate_analysis=counter.validate_config,
        submit_job=lambda kind, payload: queued.append(payload)))
    with pytest.raises(UnsupportedAnalysis, match="SHA256"):
        analyze_video(uuid4(), config)
    assert not queued


def test_team_source_hashes_must_match(tmp_path):
    pytest.importorskip("torch")
    with pytest.raises(UnsupportedAnalysis, match="source"):
        build_team_model(tmp_path)


def test_retired_dm_results_are_not_relabelled_as_p2p(config):
    from uuid import uuid4

    from intelligent_analysis import get_camera_counts
    video, analysis = uuid4(), uuid4()
    raw = {"video_id": str(video), "analysis_id": str(analysis), "status": "ready", "points": [],
        "roi": config["roi"], "analysis": {**config, "count_model": "dm_count"}}
    configure_services(ServiceBindings(load_counts=lambda v, a: raw))
    with pytest.raises(UnsupportedAnalysis, match="retired"):
        get_camera_counts(video, analysis)


def test_weights_loaded_strictly_and_points_validate_rgb(config, tmp_path):
    torch = pytest.importorskip("torch")

    class SyntheticModel(torch.nn.Module):
        def forward(self, image):
            return {"pred_logits": torch.tensor([[[0., 10.], [10., 0.]]]),
                "pred_points": torch.tensor([[[32., 32.], [96., 96.]]])}

    path = tmp_path / "synthetic.pth"
    torch.save({"model": SyntheticModel().state_dict()}, path)
    counter = P2PNetCounter(WeightSpec(str(path), hashlib.sha256(path.read_bytes()).hexdigest(), "test-p2pnet"),
        SyntheticModel, source_revision="synthetic-test")
    counter.validate_config(config)
    points = counter(np.zeros((128, 128, 3), dtype=np.uint8))
    assert points.shape == (2, 3)
    assert points[0, 0] == 0.25 and points[0, 2] > 0.5
    assert counter.provenance["count_model"] == "p2pnet"
    for invalid in (np.zeros((128, 128, 3)), np.zeros((64, 64, 3), dtype=np.uint8)):
        with pytest.raises(ValueError, match="RGB"):
            counter(invalid)
    with pytest.raises(UnsupportedAnalysis, match="weights_version"):
        counter.validate_config({**config, "weights_version": "unknown"})
    torch.save({"model": {"unknown_parameter": torch.ones(1)}}, path)
    incompatible = P2PNetCounter(WeightSpec(str(path), hashlib.sha256(path.read_bytes()).hexdigest(), "test-p2pnet"),
        SyntheticModel, source_revision="synthetic-test")
    with pytest.raises(UnsupportedAnalysis, match="cannot be loaded"):
        incompatible.validate_config(config)


def test_p2p_roi_threshold_and_even_sample_median(config):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from intelligent_analysis.vision.density_counter import process_visible_frames

    config["roi"]["points"] = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0},
        {"x": 0.5, "y": 1}, {"x": 0, "y": 1}]
    frames = [SimpleNamespace(timestamp_seconds=0,
        frame=np.array([[0.2, 0.2, 0.9], [0.8, 0.2, 0.9], [0.2, 0.8, 0.5]])),
        SimpleNamespace(timestamp_seconds=10, frame=np.array([[0.2, 0.2, 0.9], [0.3, 0.3, 0.9]]))]
    start = datetime(2026, 10, 15, tzinfo=UTC)
    points = process_visible_frames(frames, VideoAnalysisConfig.model_validate(config), lambda f: f,
        start, start + timedelta(minutes=5), ["synthetic-source"])
    assert [p["value"] for p in points if p["grain"] == "frame"] == [1, 2]
    assert points[-1]["value"] == 1.5
    assert points[-1]["sample_count"] == 2 and points[-1]["quality_status"] == "degraded"
    with pytest.raises(ValueError, match="Nx3"):
        process_visible_frames(frames, VideoAnalysisConfig.model_validate(config), lambda f: np.ones((2, 2)),
            start, start + timedelta(minutes=5), ["synthetic-source"])


@pytest.mark.parametrize("invalid", ["points", "scores"])
def test_nonfinite_model_output_is_not_silently_reported_as_zero(invalid):
    torch = pytest.importorskip("torch")
    from intelligent_analysis.vision.benchmark import P2PNetAdapter

    class InvalidModel(torch.nn.Module):
        def forward(self, image):
            points = torch.tensor([[[32., 32.]]])
            logits = torch.tensor([[[0., 10.]]])
            if invalid == "points":
                points[0, 0, 0] = float("nan")
            else:
                logits[0, 0, 0] = float("nan")
            return {"pred_points": points, "pred_logits": logits}

    with pytest.raises(ValueError, match="nonfinite"):
        P2PNetAdapter(InvalidModel())(np.zeros((128, 128, 3), dtype=np.uint8))
