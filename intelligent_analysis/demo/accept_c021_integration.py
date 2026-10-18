"""E/C installed-wheel receiving: synthetic storage and real public metric data.

No D HTTP, business MySQL, real video authorization or human labels are claimed.
"""
import importlib.util
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
C_VERSION = os.environ.get("C_VERSION", "0.2.1a1")
C_LABEL = "C" + C_VERSION.replace(".", "")
sys.path.insert(0, str(ROOT / f"artifacts/c-{C_VERSION}/runtime"))
sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    import numpy as np
    from data_foundation import create_store

    from intelligent_analysis import ServiceBindings, answer_message, configure_services, health
    from intelligent_analysis.service import get_camera_counts
    from intelligent_analysis.worker.video_worker import FrameData, process_video_job

    c_source = Path(os.environ.get("C_HANDOFF_DIR", ROOT.parent / "data_foundation/data_foundation"))
    receiving = json.loads((ROOT / f"docs/acceptance/{C_LABEL}-Windows-regression.json").read_text())
    tests = ROOT / receiving["run_relative_to_e"] / "tests"
    sys.path.insert(0, str(tests))
    import conftest
    import test_visible_people as fixtures

    # Shipped C fixtures are synthetic and use a disposable SQLite by default.
    os.environ.pop("COVERAGE_MYSQL_ADMIN_URL", None)
    run = ROOT / "artifacts" / ("c021-e-integration-" + uuid4().hex[:8])
    run.mkdir()
    generator = conftest.store.__wrapped__(run)
    store = next(generator)
    try:
        fixture = fixtures.setup_visible(store, run)
        ref = store.get_visible_input(fixture["analysis"]["id"], fixture["lease"])

        class SyntheticCounter:
            def validate_config(self, config):
                assert config == fixtures.CONFIG

            def __call__(self, frame):
                return np.array([[.2, .2, .9], [.4, .4, .9], [.6, .6, .9]])

        frames = [FrameData(i * 300, i * 10, np.zeros((128, 128, 3), dtype=np.uint8))
                  for i in range(30)]
        with store.write() as uow:
            uow.visible.begin_analysis(ref["analysis_id"], fixture["lease"])
        result = process_video_job(ref, counter=SyntheticCounter(), frames=frames)
        with store.write() as uow:
            first = uow.visible.write_points(ref["analysis_id"], result["points"], fixture["lease"])
            replay = uow.visible.write_points(ref["analysis_id"], result["points"], fixture["lease"])
            uow.visible.complete_analysis(ref["analysis_id"], len(result["points"]), fixture["lease"])
        with store.read() as uow:
            counts = uow.visible.get_counts(ref["video_id"], ref["analysis_id"])
            job = uow.jobs.get_job(fixture["job"]["id"])
        configure_services(ServiceBindings(load_counts=lambda *args: counts))
        from uuid import UUID
        readback = get_camera_counts(UUID(ref["video_id"]), UUID(ref["analysis_id"]))
        assert job["status"] == "succeeded" and first["inserted"] == 31
        assert replay == {"inserted": 0, "skipped": 31}
        assert readback["points"][0]["value"] == 3 and readback["points"][0]["sample_count"] == 30
        checks = {"c_input_to_e_processor": True, "atomic_complete_and_readback": True,
                  "identical_points_deduplicated": True, "full_window_median": 3.0}
    finally:
        try:
            next(generator)
        except StopIteration:
            pass
        configure_services(ServiceBindings())

    receipt = json.loads((ROOT / f"docs/acceptance/E061-{C_LABEL}-knowledge.json").read_text(encoding="utf-8"))
    knowledge_run = ROOT / receipt["acceptance_run_relative_to_e"]
    store = create_store("sqlite:///" + (knowledge_run / "acceptance.db").as_posix(),
        data_root=knowledge_run / "assets", allow_sqlite_for_tests=True)
    adapter = load_module("c_reference_metric", c_source / "examples/c_readonly_metric_tool.py")
    try:
        with store.read() as uow:
            from sqlalchemy import text
            airport = uow.session.execute(text(
                "SELECT id FROM entities WHERE name = :name"), {"name": "合肥新桥机场"}).scalar_one()
        tool = adapter.build_metric_tool(store, authorized_entity_ids=[airport])
        query = {"metric": "airport_throughput", "entity_type": "airport", "entity_ids": [airport],
                 "grain": "month", "period_start": "2025-01-01T00:00:00+08:00",
                 "period_end": "2025-02-01T00:00:00+08:00"}
        answer = answer_message("合肥新桥机场2025年1月吞吐量", context={"metric_query": query, "metric_tool": tool})
        assert Decimal(str(answer["metric_result"]["value"])) == Decimal(1111644)
        assert answer["citations"] and answer["used_metric_queries"]
        unavailable = tool({**query, "grain": "5min"})
        assert unavailable["status"] == "metric_tool_unavailable"
        assert "value" not in unavailable and "source_ids" not in unavailable
        missing = tool({**query, "period_start": "2027-01-01T00:00:00+08:00",
                        "period_end": "2027-02-01T00:00:00+08:00"})
        unauthorized = adapter.build_metric_tool(store, authorized_entity_ids=[])(query)
        for failure in (missing, unauthorized):
            assert failure["status"] == "metric_tool_unavailable"
            assert "value" not in failure and "source_ids" not in failure
        rejected = answer_message("不足样例", context={"metric_query": {**query, "grain": "5min"}, "metric_tool": tool})
        assert rejected["status"] == "metric_tool_unavailable" and rejected["citations"] == []
        metric = {"adapter": "C reference, not D resolver", "answer": answer,
                  "unavailable": unavailable, "missing_period": missing, "unauthorized": unauthorized}
    finally:
        store.dispose()

    d_module = load_module("d_metric_receiving", ROOT.parent / "api_platform/app/services/metrics_query.py")
    d_unavailable = d_module.metric_tool(query)
    assert d_unavailable["status"] == "metric_tool_unavailable"
    assert "value" not in d_unavailable and "source_ids" not in d_unavailable
    report = {"status": "passed", "e_version": health()["version"], "c_version": C_VERSION,
              "target": "isolated_sqlite", "synthetic_visible_checks": checks,
              "real_historical_metric_via_C_reference": metric,
              "D_current_tool_unavailable": d_unavailable, "D_resolver_success_verified": False,
              "D_HTTP_verified": False, "MySQL_verified": False, "real_video_verified": False,
              "manual_MAE_verified": False, "run_relative_to_e": run.relative_to(ROOT).as_posix()}
    (ROOT / f"docs/acceptance/E061-{C_LABEL}-integration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "visible": checks, "metric_value": 1111644,
                      "D_success_verified": False}))


if __name__ == "__main__":
    main()
