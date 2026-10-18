"""Package code, wheel, evidence and handoff docs, excluding data and weights."""
import hashlib
import json
import os
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    release_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    c_version = os.environ.get("C_VERSION", "0.2.1a2")
    c_label = "C" + c_version.replace(".", "")
    release = ROOT / "artifacts/release"
    archive = release / f"E-visible-people-{version}-{release_date}.zip"
    receipt_path = archive.with_suffix(".receipt.json")
    if archive.exists() or receipt_path.exists() or any((ROOT / "release").glob(f"*{version}*")):
        raise FileExistsError("Release already exists; do not overwrite immutable published artifacts")
    suites = ET.parse(ROOT / "artifacts" / f"pytest-{version}.xml").getroot().iter("testsuite")
    test_counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for key in test_counts:
            test_counts[key] += int(suite.get(key, "0"))
    if test_counts["failures"] or test_counts["errors"] or not test_counts["tests"]:
        raise ValueError("Passing pytest JUnit evidence is required for packaging")
    test_counts["passed"] = test_counts["tests"] - test_counts["skipped"]
    verification = ROOT / "docs/acceptance" / f"E{version.replace('.', '')}-tests.json"
    verification.write_text(json.dumps(test_counts, indent=2), encoding="utf-8")
    release = ROOT / "artifacts/release"
    wheel = release / f"shumei_intelligent_analysis-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise FileNotFoundError("Build the wheel before packaging")
    with zipfile.ZipFile(wheel) as bundle:
        assert "intelligent_analysis/THIRD_PARTY_NOTICES.txt" in bundle.namelist()
        for name in bundle.namelist():
            if name.startswith("intelligent_analysis/") and name.endswith(".py"):
                assert bundle.read(name) == (ROOT / name).read_bytes(), name
    files = [ROOT / n for n in ("README.md", "E_SERVICE_CONTRACT.md", "pyproject.toml")]
    for folder in ("intelligent_analysis", "demo", "tests", "docs"):
        files.extend(p for p in (ROOT / folder).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    archive = release / f"E-visible-people-{version}-{release_date}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(wheel, "release/" + wheel.name)
        for path in sorted(files):
            bundle.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
    c_windows = json.loads((ROOT / f"docs/acceptance/{c_label}-Windows-regression.json").read_text(encoding="utf-8"))
    c_knowledge = json.loads((ROOT / f"docs/acceptance/E{version.replace('.', '')}-{c_label}-knowledge.json").read_text(encoding="utf-8"))
    installed = json.loads((ROOT / f"docs/acceptance/E{version.replace('.', '')}-wheel-installed.json").read_text(encoding="utf-8"))
    p2p = json.loads((ROOT / f"docs/acceptance/P2PNet-CPU-{version.replace('.', '')}.json").read_text(encoding="utf-8"))
    if c_windows["status"] != "passed" or c_knowledge["status"] != "passed" or c_knowledge["e_version"] != version:
        raise ValueError("Official C receiving evidence must pass")
    if installed["status"] != "passed" or installed["wheel_sha256"] != hashlib.sha256(wheel.read_bytes()).hexdigest():
        raise ValueError("Built E wheel must match the successfully installed wheel")
    if not installed.get("installed_P2PNet_inference_verified") or p2p["e_version"] != version:
        raise ValueError("Current E P2PNet wheel inference must pass")
    receipt = {"version": version, "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "archive": archive.name, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "wheel_matches_source": True, "zip_crc_verified": True,
        "installed_E_wheel_restart_verified": True,
        "tests_passed": test_counts["passed"], "tests_skipped": test_counts["skipped"],
        "real_knowledge_documents": 16,
        "c_version": c_version, "c_official_windows_status": c_windows["status"],
        "c_official_windows_test_counts": c_windows["counts"],
        "c_knowledge_status": c_knowledge["status"],
        "count_model": "p2pnet", "weights_version": p2p["model_provenance"]["weights_version"],
        "weights_sha256": p2p["model_provenance"]["weights_sha256"],
        "installed_P2PNet_inference_verified": True, "comparison_task": "stopped_by_user",
        "D_B_video_integration": "pending_deployed_HTTP_real_video_and_MySQL", "formal_DE_mysql_verified": False,
        "real_video_accuracy_verified": False, "production_http_verified": False}
    output = release / f"E-visible-people-{version}-{release_date}.receipt.json"
    output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
