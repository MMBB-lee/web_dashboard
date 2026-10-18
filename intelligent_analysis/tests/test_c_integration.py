"""Optional real C receiving regression. Never targets the business database."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.environ.get("RUN_C_ACCEPTANCE") != "1",
                    reason="Set RUN_C_ACCEPTANCE=1 after demo/receive_c.py")
def test_real_c_knowledge_receiving(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "report.json"
    subprocess.run([sys.executable, "-X", "utf8", "demo/accept_c_knowledge.py"], cwd=root,
        env={**os.environ, "E_ACCEPTANCE_REPORT": str(report)}, check=True)
    import json
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert len(result["documents"]) == 16
    assert result["checks"]["restart_load"] == "passed"


@pytest.mark.skipif(os.environ.get("RUN_C020_ACCEPTANCE") != "1",
                    reason="Run demo/receive_c020.py --version 0.2.0a2 first")
def test_c020_official_receiving_preserves_failure_protection(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "official-report.json"
    subprocess.run([sys.executable, "-X", "utf8", "demo/accept_c_knowledge.py"], cwd=root,
        env={**os.environ, "E_ACCEPTANCE_REPORT": str(report),
             "C_HANDOFF_DIR": str(root / "artifacts/c-0.2.0a2/data_foundation"),
             "C_RUNTIME_DIR": str(root / "artifacts/c-0.2.0a2/runtime")}, check=True)
    import json
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["status"] == "passed"
    assert result["c_runtime_mode"] == "unmodified"
    assert len(result["documents"]) == 16
    assert all(result["artifact_failure_checks"].values())
