from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from intelligent_analysis import ServiceBindings, answer_message, configure_services
from intelligent_analysis.contracts import VideoAnalysisConfig
from intelligent_analysis.vision.density_counter import density_count, process_visible_frames
from intelligent_analysis.worker import dispatch_analysis_job, publish_knowledge_snapshot


def test_full_windows_roi_median_and_missing(config):
    parsed = VideoAnalysisConfig.model_validate(config)
    start = datetime(2026, 10, 14, tzinfo=UTC)
    frames = [SimpleNamespace(timestamp_seconds=s, frame=None)
              for s in range(0, 300, 10)]
    predictions = np.array([[(x + 0.5) / 4, (y + 0.5) / 4, 0.9] for y in range(4) for x in range(4)])
    points = process_visible_frames(frames, parsed, lambda f: predictions, start,
                                    start + timedelta(minutes=10, seconds=10), ["source"])
    windows = [p for p in points if p["grain"] == "5min"]
    assert len(windows) == 2  # partial third window excluded
    assert windows[0]["value"] == 16
    assert windows[0]["sample_count"] == 30
    assert windows[0]["quality_status"] == "good"
    assert windows[1]["value"] is None
    assert windows[1]["quality_status"] == "unavailable"
    assert windows[1]["sample_count"] == 0


def test_partial_sampling_is_degraded_not_zero(config):
    start = datetime(2026, 10, 14, tzinfo=UTC)
    points = process_visible_frames([SimpleNamespace(timestamp_seconds=0, frame=1)],
        VideoAnalysisConfig.model_validate(config),
        lambda f: np.array([[0.2, 0.2, 0.9], [0.2, 0.8, 0.9], [0.8, 0.2, 0.9], [0.8, 0.8, 0.9]]),
        start, start + timedelta(minutes=5), ["source"])
    assert points[-1]["value"] == 4
    assert points[-1]["quality_status"] == "degraded"


def test_roi_integration_preserves_fractional_count(config):
    config["roi"]["points"] = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0},
                                  {"x": 0.5, "y": 1}, {"x": 0, "y": 1}]
    roi = VideoAnalysisConfig.model_validate(config).roi
    assert density_count(np.full((4, 4), 0.3), roi) == pytest.approx(2.4)
    with pytest.raises(ValueError):
        density_count(np.array([[float("nan")]]), roi)


def test_dispatched_knowledge_and_restart_load(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("黄山春节开放公告。", encoding="utf-8")
    prepared = dispatch_analysis_job("knowledge_index", {
        "document_id": "c-stable-id", "version": "v1", "file_path": str(source),
        "metadata": {"document_version": "source-v1", "title": "公告", "publisher": "官方", "permission_note": "测试"}})
    assert prepared["chunks"]
    target = tmp_path / "published.json"
    publish_knowledge_snapshot(prepared["snapshot"], str(target))
    configure_services(ServiceBindings(knowledge_index_path=str(target)))
    assert answer_message("黄山春节")["citations"][0]["id"] == "c-stable-id"


def test_no_queue_claiming_or_unconfigured_video():
    with pytest.raises(RuntimeError, match="counter"):
        dispatch_analysis_job("video_analysis", {})
    with pytest.raises(ValueError):
        dispatch_analysis_job("ingestion", {})
