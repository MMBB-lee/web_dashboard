from uuid import uuid4

import pytest
from pydantic import ValidationError

from intelligent_analysis import ServiceBindings, configure_services
from intelligent_analysis.contracts import VideoAnalysisConfig
from intelligent_analysis.integration import DependencyUnavailable, UnsupportedAnalysis
from intelligent_analysis.service import forecast_short_term, get_camera_counts


@pytest.mark.parametrize("extra", [{"direction": "entries"}, {"interval_minutes": 5},
                                  {"count_line": {}}, {"count_model": "dm_count"},
                                  {"openvino_version": None}, {"sample_interval_seconds": True}])
def test_reject_old_or_invalid_config(config, extra):
    with pytest.raises(ValidationError):
        VideoAnalysisConfig.model_validate({**config, **extra})


def test_openvino_requires_version(config):
    with pytest.raises(ValidationError):
        VideoAnalysisConfig.model_validate({**config, "inference_engine": "openvino"})


def test_counts_not_connected_is_dependency_error():
    with pytest.raises(DependencyUnavailable):
        get_camera_counts(uuid4(), uuid4())


def test_ready_unavailable_and_ownership_checks():
    video, analysis = uuid4(), uuid4()
    raw = {"video_id": str(video), "analysis_id": str(analysis),
           "status": "analysis_not_ready", "points": []}
    configure_services(ServiceBindings(load_counts=lambda v, a: raw))
    assert get_camera_counts(video, analysis)["status"] == "analysis_not_ready"
    assert forecast_short_term(video, analysis, "2026-10-14T10:00:00Z", 15)["status"] == "data_insufficient"
    with pytest.raises(ValueError, match="belong"):
        get_camera_counts(uuid4(), analysis)
    raw["points"] = [{"entries": 3, "exits": 2}]
    with pytest.raises(UnsupportedAnalysis):
        get_camera_counts(video, analysis)


def test_prediction_no_direction():
    with pytest.raises(TypeError):
        forecast_short_term(uuid4(), uuid4(), "2026-10-14T10:00:00Z", 15, direction="entries")


@pytest.mark.parametrize("points", [
    [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.5}, {"x": 1, "y": 1}],
    [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 0}],
    [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
])
def test_invalid_roi(config, points):
    with pytest.raises(ValidationError):
        VideoAnalysisConfig.model_validate({**config, "roi": {"points": points}})


def test_filter_excludes_window_crossing_cutoff(config):
    from datetime import UTC, datetime, timedelta
    video, analysis = uuid4(), uuid4()
    cutoff = datetime(2026, 10, 14, 10, tzinfo=UTC)
    points = [{"grain": "5min", "period_start": (cutoff - timedelta(minutes=5 * (36-i))).isoformat(),
        "period_end": (cutoff - timedelta(minutes=5 * (35-i))).isoformat(),
        "value": 20, "quality_status": "good", "sample_count": 30,
        "aggregation_rule": "median", "source_ids": ["source"]} for i in range(36)]
    raw = {"video_id": str(video), "analysis_id": str(analysis), "status": "ready",
        "roi": config["roi"], "analysis": config, "points": points,
        "recorded_start_at": (cutoff - timedelta(hours=3)).isoformat(),
        "recorded_end_at": (cutoff + timedelta(hours=1)).isoformat()}
    configure_services(ServiceBindings(load_counts=lambda v, a: raw))
    before = forecast_short_term(video, analysis, cutoff.isoformat(), 15)
    raw["points"].append({**points[-1], "value": 9999999,
        "period_start": (cutoff - timedelta(minutes=1)).isoformat(),
        "period_end": (cutoff + timedelta(minutes=4)).isoformat()})
    assert forecast_short_term(video, analysis, cutoff.isoformat(), 15) == before
    assert len(get_camera_counts(video, analysis, end=cutoff.isoformat())["points"]) == 36
    with pytest.raises(ValueError, match="recording"):
        forecast_short_term(video, analysis, (cutoff + timedelta(hours=2)).isoformat(), 15)


def test_unverified_runtimes_and_weights_are_rejected(config, tmp_path):
    from intelligent_analysis.vision.density_counter import WeightSpec
    from intelligent_analysis.vision.p2p_counter import P2PNetCounter
    counter = P2PNetCounter(WeightSpec(str(tmp_path / "missing.pth"), "0" * 64, "test-p2pnet"),
        lambda: None, source_revision="synthetic-test-only")
    with pytest.raises(UnsupportedAnalysis, match="weights"):
        counter.validate_config(config)
    with pytest.raises(UnsupportedAnalysis, match="CPU"):
        counter.validate_config({**config, "device": "GPU"})
