"""Synthetic E forecast -> C021 persistence, with explicit horizon/quality gates."""
import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "artifacts/c-0.2.1a2/runtime"), str(ROOT)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    import numpy as np
    from data_foundation import JobCreate, JobLease

    from intelligent_analysis import (
        ServiceBindings,
        configure_services,
        forecast_from_count_history,
    )
    from intelligent_analysis.service import forecast_short_term
    from intelligent_analysis.worker.video_worker import FrameData, process_video_job

    os.environ.pop("COVERAGE_MYSQL_ADMIN_URL", None)
    receipt = json.loads((ROOT / "docs/acceptance/C021a2-Windows-regression.json").read_text())
    sys.path.insert(0, str(ROOT / receipt["run_relative_to_e"] / "tests"))
    import conftest
    import test_visible_people as fixtures

    run = ROOT / "artifacts" / ("forecast-storage-" + uuid4().hex[:8])
    run.mkdir()
    generator = conftest.store.__wrapped__(run)
    store = next(generator)
    report = {"data_kind": "synthetic", "real_forecast_verified": False,
              "MySQL_verified": False, "D_HTTP_verified": False, "horizons": {}, "checks": {},
              "run_relative_to_e": run.relative_to(ROOT).as_posix()}
    try:
        fixture = fixtures.setup_visible(store, run, duration=36 * 300)
        lease, analysis = fixture["lease"], fixture["analysis"]["id"]
        ref = store.get_visible_input(analysis, lease)

        class Counter:
            def validate_config(self, config):
                assert config == fixtures.CONFIG

            def __call__(self, frame):
                return np.array([[.2, .2, .9], [.4, .4, .9], [.6, .6, .9]])

        image = np.zeros((128, 128, 3), dtype=np.uint8)
        frames = (FrameData(i * 300, i * 10, image) for i in range(36 * 30))
        result = process_video_job(ref, counter=Counter(), frames=frames)
        with store.write() as uow:
            uow.visible.begin_analysis(analysis, lease)
            for start in range(0, len(result["points"]), 1000):
                uow.visible.write_points(analysis, result["points"][start:start + 1000], lease)
            uow.visible.complete_analysis(analysis, len(result["points"]), lease)

        def reader(video, identity):
            with store.read() as uow:
                return uow.visible.get_counts(video, identity)

        configure_services(ServiceBindings(load_counts=reader))
        cutoff = fixture["start"] + timedelta(minutes=180)
        for horizon in (15, 30, 60):
            forecast = forecast_short_term(UUID(ref["video_id"]), UUID(analysis), cutoff.isoformat(), horizon)
            assert forecast["status"] == ("data_insufficient" if horizon == 60 else "ready")
            assert len(forecast["forecast_points"]) == (0 if horizon == 60 else horizon // 5)
            assert all(p["lower"] is None and p["upper"] is None for p in forecast["forecast_points"])
            with store.read() as uow:
                fingerprint = uow.visible.forecast_input(analysis, cutoff)
            with store.write() as uow:
                job = uow.jobs.enqueue_or_get(JobCreate("visible_forecast", uuid4().hex,
                    {"analysis_id": analysis, "cutoff_at": cutoff.isoformat(), "horizon_minutes": horizon},
                    "SYNTHETIC E/C forecast acceptance"), fixture["now"])
                forecast_lease = JobLease(**uow.jobs.claim_job("e-forecast-test", fixture["now"],
                    600, job_id=job["id"])["lease"])
            with store.write() as uow:
                saved = uow.visible.publish_forecast(analysis_id=analysis, cutoff_at=cutoff,
                    horizon_minutes=horizon, input_sha256=fingerprint["input_sha256"],
                    result=forecast, lease=forecast_lease)
            with store.read() as uow:
                assert uow.visible.get_forecast(saved["id"])["result"] == forecast
                assert uow.jobs.get_job(job["id"])["status"] == "succeeded"
            report["horizons"][str(horizon)] = forecast
        crossing_cutoff = cutoff - timedelta(seconds=150)
        with store.read() as uow:
            assert len(uow.visible.forecast_input(analysis, crossing_cutoff)["history"]) == 35
        report["checks"]["window_crossing_cutoff_excluded"] = True
        history = [(fixture["start"] + timedelta(minutes=5 * (i + 1)), 3.) for i in range(36)]
        original = forecast_from_count_history(history, cutoff, 30)
        assert forecast_from_count_history(history + [(cutoff + timedelta(minutes=5), 1e9)], cutoff, 30) == original
        for value in (None, float("nan"), -1.):
            damaged = [*history[:-1], (cutoff, value)]
            rejected = forecast_from_count_history(damaged, cutoff, 15)
            assert rejected["status"] == "quality_insufficient" and rejected["forecast_points"] == []
        assert forecast_from_count_history(history[:12], history[11][0], 15)["status"] == "data_insufficient"
        report["checks"].update(future_values_do_not_change_fit=True, invalid_history_has_no_predictions=True,
                                minimum_without_backtest_rejected=True, C_jobs_complete_atomically=True)
        report["status"] = "synthetic_acceptance_passed"
    finally:
        configure_services(ServiceBindings())
        try:
            next(generator)
        except StopIteration:
            pass
    (args.output or ROOT / "docs/acceptance/E061-C021a2-forecast-storage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": report["checks"],
                      "horizons": {k: v["status"] for k, v in report["horizons"].items()}}))


if __name__ == "__main__":
    main()
