"""Run C's shipped tests away from its source package, against the installed wheel."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["0.2.0a2", "0.2.1a1", "0.2.1a2"], default="0.2.0a2")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    version = args.version
    label = "C" + version.replace(".", "")
    source = args.source or ROOT / f"artifacts/c-{version}/data_foundation"
    runtime = ROOT / f"artifacts/c-{version}/runtime"
    target = ROOT / "artifacts" / (label.lower() + "-wheel-regression-" + uuid4().hex[:8])
    target.mkdir()
    for name in ("tests", "examples", "alembic", "assets", "real_data"):
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name)
    for name in ("alembic.ini", "pyproject.toml"):
        shutil.copy2(source / name, target / name)
    env = {**os.environ, "PYTHONPATH": str(runtime), "PYTHONUTF8": "1"}
    for key in ("COVERAGE_MYSQL_ADMIN_URL", "TEST_DATABASE_URL", "DATABASE_URL"):
        env.pop(key, None)
    origin = subprocess.check_output([sys.executable, "-c",
        "import data_foundation; print(data_foundation.__file__)"], cwd=target, env=env, text=True).strip()
    if not Path(origin).is_relative_to(runtime):
        raise ValueError("C source package shadows installed wheel")
    junit = target / "pytest.xml"
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "tests", "-ra",
        "--junitxml=" + str(junit)], cwd=target, env=env, capture_output=True, text=True, check=False)
    (target / "pytest.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    suites = list(ET.parse(junit).getroot().iter("testsuite"))
    counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    reasons = [n.get("message") for n in ET.parse(junit).getroot().iter("skipped")]
    wheel = source / "release" / f"anhui_data_foundation-{version}-py3-none-any.whl"
    report = {"status": "passed" if result.returncode == 0 else "failed", "c_version": version,
        "windows": platform.platform(), "python": platform.python_version(),
        "installed_official_wheel": True, "source_package_excluded": True,
        "module_origin_relative_to_e": Path(origin).relative_to(ROOT).as_posix(),
        "counts": counts, "skip_reasons": reasons, "mysql_verified": False,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "run_relative_to_e": target.relative_to(ROOT).as_posix()}
    (ROOT / f"docs/acceptance/{label}-Windows-regression.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    if result.returncode:
        print(result.stdout[-6000:])
        print(result.stderr[-2000:])
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
