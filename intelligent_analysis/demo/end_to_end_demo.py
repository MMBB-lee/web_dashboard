"""Synthetic new-contract pipeline; never represents real video/model accuracy."""
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intelligent_analysis import ServiceBindings, configure_services
from intelligent_analysis.service import forecast_short_term, get_camera_counts
from intelligent_analysis.worker import dispatch_analysis_job


def main():
    start = datetime(2026, 10, 14, 8, tzinfo=UTC)
    end = start + timedelta(hours=3)
    video_id = UUID("40000000-0000-4000-8000-000000000001")
    analysis_id = UUID("60000000-0000-4000-8000-000000000001")
    config = {"count_model": "p2pnet", "weights_version": "synthetic-points-only",
        "roi": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0},
                           {"x": 1, "y": 1}, {"x": 0, "y": 1}]},
        "sample_interval_seconds": 10, "aggregation_minutes": 5,
        "inference_engine": "pytorch", "device": "CPU"}

    class SyntheticCounter:
        def validate_config(self, config):
            if config["weights_version"] != "synthetic-points-only":
                raise ValueError("Only synthetic inputs")

        def __call__(self, frame):
            return frame

    predictions = np.array([[(x + 0.5) / 5, (y + 0.5) / 4, 0.9] for y in range(4) for x in range(5)])
    frames = [SimpleNamespace(timestamp_seconds=s, frame=predictions)
              for s in range(0, 3 * 3600, 10)]
    result = dispatch_analysis_job("video_analysis", {
        "video_id": str(video_id), "analysis_id": str(analysis_id), "config": config,
        "recorded_start_at": start.isoformat(), "recorded_end_at": end.isoformat(),
        "source_ids": ["synthetic-fixture"], "file_path": "not-a-real-video"},
        counter=SyntheticCounter(), frames=frames)
    configure_services(ServiceBindings(load_counts=lambda v, a: result))
    counts = get_camera_counts(video_id, analysis_id)
    report = {"synthetic": True, "real_video_accuracy_verified": False,
        "window_count": len(counts["points"]), "metric": result["metric"], "forecasts": {}}
    for horizon in (15, 30, 60):
        forecast = forecast_short_term(video_id, analysis_id, end.isoformat(), horizon)
        report["forecasts"][str(horizon)] = forecast
    assert report["forecasts"]["15"]["status"] == "ready"
    assert report["forecasts"]["30"]["status"] == "ready"
    assert report["forecasts"]["60"]["status"] == "data_insufficient"
    root = Path(__file__).resolve().parents[1]
    target = root / "docs/acceptance/Synthetic-pipeline.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fixture = {"synthetic": True, "notice": "合成接口联调样例，不代表真实录像或模型准确率",
        "counts": {**counts, "points": [*result["points"][:3], *counts["points"]]},
        "forecast": report["forecasts"]["15"]}
    target = root / "docs/fixtures/visible-people-demo.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"synthetic": True, "window_count": report["window_count"],
                      "15min": "ready", "30min": "ready", "60min": "data_insufficient"}))


if __name__ == "__main__":
    main()
