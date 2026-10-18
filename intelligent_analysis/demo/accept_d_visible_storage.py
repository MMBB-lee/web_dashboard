"""Received D adapter + real E processor/C wheel; explicit synthetic fixtures only."""
import argparse
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = ROOT / "artifacts" / ("d-visible-" + uuid4().hex[:8])
    run.mkdir()
    os.environ.pop("COVERAGE_MYSQL_ADMIN_URL", None)
    os.environ.update(DATABASE_URL="sqlite:///" + (run / "unused.db").as_posix(),
                      DATA_DIR=str(run / "assets"), D_JOB_STORE_BACKEND="c_store")
    sys.path[:0] = [str(ROOT / "artifacts/c-0.2.1a2/runtime"), str(ROOT.parent / "api_platform"), str(ROOT)]
    receiving = json.loads((ROOT / "docs/acceptance/C021a2-Windows-regression.json").read_text())
    sys.path.insert(0, str(ROOT / receiving["run_relative_to_e"] / "tests"))
    import conftest
    import numpy as np
    import test_visible_people as fixtures
    from app.services.c_store import CStore
    from app.services.visible_pipeline import (
        filter_counts,
        run_visible_analysis,
        run_visible_forecast,
    )
    from data_foundation import JobCreate, JobLease

    from intelligent_analysis import health
    from intelligent_analysis.worker.video_worker import FrameData

    generator = conftest.store.__wrapped__(run)
    store = next(generator)
    checks = {}
    try:
        f = fixtures.setup_visible(store, run)
        adapter = CStore(store)
        class SyntheticCounter:
            def validate_config(self, config):
                assert config == fixtures.CONFIG

            def __call__(self, frame):
                return np.array([[.2, .2, .9], [.4, .4, .9], [.6, .6, .9]])
        frames = [FrameData(i*300, i*10, np.zeros((128,128,3), dtype=np.uint8)) for i in range(30)]
        with patch("intelligent_analysis.worker.video_worker._read_video_frames", return_value=iter(frames)):
            result = run_visible_analysis(adapter, job=f["job"], lease=f["lease"], counter=SyntheticCounter())
        counts = adapter.get_visible_counts(video_id=f["video"]["id"], analysis_id=f["analysis"]["id"])
        assert result["point_count"] == 31 and counts["status"] == "ready"
        assert adapter.get_job(f["job"]["id"])["status"] == "succeeded"
        checks["D_adapter_E_processor_C_atomic_completion"] = True
        frame = filter_counts(counts, grain="frame")
        aggregate = filter_counts(counts, grain="5min")
        assert len(frame["points"]) == 30 and len(aggregate["points"]) == 1
        assert aggregate["points"][0]["value"] == 3 and aggregate["points"][0]["sample_count"] == 30
        assert counts["manual_validation"] is None
        checks["same_points_array_grain_and_unvalidated_median"] = True
        cutoff = f["start"] + timedelta(seconds=300)
        assert not filter_counts(counts, grain="5min", start=(f["start"]+timedelta(seconds=1)).isoformat())["points"]
        checks["crossing_window_not_returned"] = True
        payload = {"video_id": f["video"]["id"], "analysis_id": f["analysis"]["id"],
                   "cutoff_at": cutoff.isoformat(), "horizon_minutes": 15, "step_minutes": 5}
        with store.write() as uow:
            job = uow.jobs.enqueue_or_get(JobCreate("visible_forecast", uuid4().hex, payload, "SYNTHETIC D E C"), f["now"])
        with store.write() as uow:
            lease = JobLease(**uow.jobs.claim_job("synthetic-D-E-C", f["now"], 600, job_id=job["id"])["lease"])
        forecast = run_visible_forecast(adapter, job=job, lease=lease, counter=None)
        assert forecast["status"] == "data_insufficient"
        assert adapter.get_job(job["id"])["status"] == "succeeded"
        saved = adapter.get_visible_forecast(forecast["forecast_id"])
        assert saved["result"]["status"] == "data_insufficient"
        checks["D_forecast_insufficient_saved_by_C_under_separate_job"] = True
        report = {"e_version": health()["version"], "status": "passed", "scope": "synthetic_D_adapter_E_processor_real_C_SQLite",
                  "D_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT.parent/"api_platform", text=True).strip(),
                  "checks": checks, "D_source_modified": False, "MySQL_verified": False,
                  "real_video_verified": False, "D_HTTP_verified": False,
                  "synthetic_frames_counter_permission_and_times": True,
                  "run_relative_to_e": run.relative_to(ROOT).as_posix()}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


if __name__ == "__main__":
    main()
