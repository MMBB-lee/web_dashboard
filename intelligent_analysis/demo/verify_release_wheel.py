"""Verify installed current E wheel: C-backed knowledge and formal P2PNet CPU."""
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    wheel = ROOT / f"artifacts/release/shumei_intelligent_analysis-{version}-py3-none-any.whl"
    runtime = ROOT / "artifacts" / ("e-wheel-runtime-" + uuid4().hex[:8])
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
        "--target", str(runtime), str(wheel)], check=True)
    c_version = os.environ.get("C_VERSION", "0.2.1a2")
    c_label = "C" + c_version.replace(".", "")
    receipt = json.loads((ROOT / f"docs/acceptance/E{version.replace('.', '')}-{c_label}-knowledge.json").read_text(encoding="utf-8"))
    run = (ROOT / receipt["acceptance_run_relative_to_e"]).resolve()
    if not run.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Invalid acceptance database path")
    ids = [doc["document_id"] for doc in receipt["documents"]]
    c_runtime = ROOT / f"artifacts/c-{c_version}/runtime"
    code = f'''
import json
from pathlib import Path
from importlib.metadata import version
import data_foundation
import intelligent_analysis
from data_foundation import create_store
from intelligent_analysis import ServiceBindings, configure_services, answer_message, health
from intelligent_analysis.knowledge.artifacts import load_verified_knowledge_snapshot
assert Path(intelligent_analysis.__file__).is_relative_to({str(runtime)!r})
assert Path(data_foundation.__file__).is_relative_to({str(c_runtime)!r})
assert version("shumei-intelligent-analysis") == {version!r}
assert health()["version"] == {version!r}
from intelligent_analysis.integration import DependencyUnavailable
for invalid in (float("nan"), True, -1):
    try:
        answer_message("numeric guard", context={{"metric_query": {{}}, "metric_tool": lambda q, value=invalid: {{
            "metric": "airport_throughput", "value": value, "source_ids": ["official"],
            "period_start": "2025-01-01", "period_end": "2025-01-31", "queried_at": "2026-10-18"}}}})
    except DependencyUnavailable:
        pass
    else:
        raise AssertionError("Installed wheel accepted an invalid metric result")
import cv2
import torch
from intelligent_analysis.contracts import VideoAnalysisConfig
from intelligent_analysis.vision.p2p_counter import P2PNetCounter, TEAM_WEIGHTS_VERSION
from intelligent_analysis.vision.benchmark import point_count
torch.set_num_threads(4)
counter = P2PNetCounter.from_team_bundle({str(ROOT / 'artifacts/leader-p2pnet-original')!r})
config = VideoAnalysisConfig.model_validate({{"count_model":"p2pnet","weights_version":TEAM_WEIGHTS_VERSION,
    "roi":{{"points":[{{"x":0,"y":0}},{{"x":1,"y":0}},{{"x":1,"y":1}},{{"x":0,"y":1}}]}},
    "inference_engine":"pytorch","device":"CPU"}})
counter.validate_config(config.model_dump(exclude_none=True))
cap = cv2.VideoCapture({str(ROOT / 'artifacts/leader-p2pnet-original/mytest.mp4')!r})
try:
    ok, image = cap.read()
    assert ok
    estimate = point_count(counter(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)),config.roi)
    assert estimate == 31
finally:
    cap.release()
store = create_store({('sqlite:///' + (run / 'acceptance.db').as_posix())!r},
    data_root={str(run / 'assets')!r}, allow_sqlite_for_tests=True)
try:
    snapshot = load_verified_knowledge_snapshot(store, {ids!r}, {str(run / 'assets')!r})
    configure_services(ServiceBindings(knowledge_snapshot_loader=lambda:
        load_verified_knowledge_snapshot(store, {ids!r}, {str(run / 'assets')!r})))
    answer = answer_message("2025年春节黄山运营时间")
    assert answer["citations"] and "历史资料" in answer["answer"]
    print(json.dumps({{"status":"passed","documents":len(snapshot["documents"]),
        "e_version":health()["version"], "e_module_origin":intelligent_analysis.__file__,
        "c_module_origin":data_foundation.__file__, "installed_P2PNet_inference_verified":True,
        "count_model":"p2pnet", "first_frame_estimate":estimate}}))
finally:
    store.dispose()
'''
    result = subprocess.check_output([sys.executable, "-X", "utf8", "-c", code], cwd=runtime,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(runtime), str(c_runtime)])}, text=True)
    report = json.loads(result)
    for key in ("e_module_origin", "c_module_origin"):
        report[key] = Path(report[key]).relative_to(ROOT).as_posix()
    report.update(installed_official_E_wheel=True, installed_official_C_wheel=True, c_version=c_version,
        source_package_excluded=True, new_process_reads_committed_C_store=True,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), formal_DE_mysql_verified=False)
    report_path = Path(os.environ.get("E_WHEEL_REPORT",
        ROOT / f"docs/acceptance/E{version.replace('.', '')}-wheel-installed.json"))
    report_path.write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
