"""Run shipped D tests on a copied source tree with E/C, without editing D."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Save a new receiving record without replacing prior evidence")
    parser.add_argument("--initialize-empty-snapshot", action="store_true",
                        help="Explicitly seed an empty published knowledge fixture for D tests")
    args = parser.parse_args()
    source = ROOT.parent / "api_platform"
    target = ROOT / "artifacts" / ("d-regression-" + uuid4().hex[:8])
    target.mkdir()
    for name in ("app", "tests"):
        shutil.copytree(source / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(source / "pyproject.toml", target / "pyproject.toml")
    bundle = ROOT / "artifacts/leader-p2pnet-original"
    if (bundle / "SHTechA.pth").is_file():
        model_target = target / "data/p2pnet_bundle"
        for folder in ("models", "util"):
            shutil.copytree(bundle / folder, model_target / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(bundle / "SHTechA.pth", model_target / "SHTechA.pth")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(target),
        str(ROOT / "artifacts/c-0.2.1a2/runtime"), str(ROOT)]), "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "D_JOB_STORE_BACKEND": "sqlite"}
    for key in ("COVERAGE_MYSQL_ADMIN_URL", "TEST_DATABASE_URL"):
        env.pop(key, None)
    env.update(DATABASE_URL="sqlite:///" + (target / "unused-business.db").as_posix())
    for field, value in {"DATA_DIR": "data", "RAG_INDEX_PATH": "data/rag.json",
        "JOB_STORE_PATH": "data/jobs.db", "COUNTS_STORAGE_PATH": "data/counts",
        "DETECTOR_WEIGHTS_PATH": "data/weights", "P2PNET_BUNDLE_DIR": "data/p2pnet_bundle",
        "VIDEO_TMP_DIR": "data/videos"}.items():
        env[field] = str(target / value)
    if args.initialize_empty_snapshot:
        snapshot = target / "data/rag.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text('{"documents":{}}', encoding="utf-8")
    junit = target / "pytest.xml"
    completed = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "tests", "-q", "--tb=short", "-p", "no:cacheprovider",
        "--basetemp=" + str(target / "pytest-tmp"),
        "--junitxml=" + str(junit)], cwd=target, env=env, capture_output=True, text=True, check=False)
    (target / "pytest.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    tree = ET.parse(junit).getroot()
    suites = list(tree.iter("testsuite"))
    counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    failures = [{"case": case.get("name"), "detail": issue.get("message")}
                for case in tree.iter("testcase") for issue in case
                if issue.tag in {"failure", "error", "skipped"}]
    report = {"status": "passed" if completed.returncode == 0 else "needs_D_review",
        "D_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
        "counts": counts, "failures_or_skips": failures, "scope": "D_shipped_tests_copied_source",
        "D_source_modified": False, "MySQL_verified": False, "real_video_verified": False,
        "initialized_empty_published_snapshot": args.initialize_empty_snapshot,
        "run_relative_to_e": target.relative_to(ROOT).as_posix()}
    suffix = "-empty-snapshot" if args.initialize_empty_snapshot else ""
    output = args.output or ROOT / f"docs/acceptance/E061-D-regression{suffix}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
