"""Prove installed E wheel loads the real committed C knowledge after a restart."""
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
    receipt = json.loads((ROOT / "docs/acceptance/C020a2-knowledge.json").read_text(encoding="utf-8"))
    run = (ROOT / receipt["acceptance_run_relative_to_e"]).resolve()
    if not run.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Invalid acceptance database path")
    ids = [doc["document_id"] for doc in receipt["documents"]]
    c_runtime = ROOT / "artifacts/c-0.2.0a2/runtime"
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
store = create_store({('sqlite:///' + (run / 'acceptance.db').as_posix())!r},
    data_root={str(run / 'assets')!r}, allow_sqlite_for_tests=True)
try:
    snapshot = load_verified_knowledge_snapshot(store, {ids!r}, {str(run / 'assets')!r})
    configure_services(ServiceBindings(knowledge_snapshot_loader=lambda:snapshot))
    answer = answer_message("2025年春节黄山运营时间")
    assert answer["citations"] and "历史资料" in answer["answer"]
    print(json.dumps({{"status":"passed","documents":len(snapshot["documents"]),
        "e_version":health()["version"], "e_module_origin":intelligent_analysis.__file__,
        "c_module_origin":data_foundation.__file__}}))
finally:
    store.dispose()
'''
    result = subprocess.check_output([sys.executable, "-X", "utf8", "-c", code], cwd=runtime,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(runtime), str(c_runtime)])}, text=True)
    report = json.loads(result)
    for key in ("e_module_origin", "c_module_origin"):
        report[key] = Path(report[key]).relative_to(ROOT).as_posix()
    report.update(installed_official_E_wheel=True, installed_official_C_wheel=True,
        source_package_excluded=True, new_process_reads_committed_C_store=True,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), formal_DE_mysql_verified=False)
    (ROOT / "docs/acceptance/E051-wheel-installed.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
