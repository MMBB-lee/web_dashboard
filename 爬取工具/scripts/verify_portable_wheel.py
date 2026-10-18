"""Verify an installed wheel and copied seed outside any team checkout."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile

import anhui_crawler
from anhui_crawler.common import now, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use an empty/new output directory")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    package = Path(anhui_crawler.__file__).resolve().parent
    assert "site-packages" in package.parts and package.is_relative_to(Path(sys.prefix))
    checked = 0
    with ZipFile(args.wheel) as archive:
        for name in archive.namelist():
            if name.startswith("anhui_crawler/") and name.endswith((".py", ".json")):
                assert (package / Path(name).relative_to("anhui_crawler")).read_bytes() == archive.read(name)
                checked += 1
    assert checked >= 11
    absent = {m: importlib.util.find_spec(m) is None for m in ("data_foundation", "app", "intelligent_analysis")}
    assert all(absent.values())
    portable = Path(tempfile.mkdtemp(prefix="anhui-crawler-020-portable-"))
    shutil.copytree(args.seed, portable / "seed")
    env = {**os.environ, "DATABASE_URL": "mysql+pymysql://not_used@invalid/team_db",
           "TEST_DATABASE_URL": "mysql+pymysql://not_used@invalid/team_test"}
    env.pop("CRAWLER_DATABASE_URL", None)
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-I", "-m", "anhui_crawler", "--data-root", str(portable / "data")]
    for phase, inserted, skipped in (("first", 277, 0), ("repeat", 0, 277)):
        report_path = out / (phase + ".json")
        result = subprocess.run(command + ["ingest", str(portable / "seed/seed.json"), "--report", str(report_path)],
                                cwd=portable, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr[-500:]
        report = json.loads(report_path.read_text())
        assert report["status"] == "succeeded" and report["assets_failed"] == 0
        assert report["records"] == {"inserted": inserted, "skipped": skipped, "revised": 0, "quarantined": 0}
    result = subprocess.run(command + ["export", "--output", str(portable / "export"), "--end", "2026-08"],
                            cwd=portable, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-500:]
    facts = json.loads((portable / "export/facts.json").read_text())["records"]
    assert len(facts) == 277 and sum(r["entity_id"].startswith("city:") for r in facts) == 17
    assert (portable / "export/parser_capabilities.json").exists()
    summary = {"status": "passed", "verified_at": now(), "version": anhui_crawler.__version__,
               "wheel_sha256": sha256(args.wheel.read_bytes()), "installed_package_files_matching_wheel": checked,
               "team_modules_absent": absent, "cwd_outside_team_checkout": True, "seed_copied": True,
               "first_inserted": 277, "repeat_inserted": 0, "repeat_skipped": 277,
               "team_database_environment_ignored": True, "exported_facts": len(facts),
               "temporary_data_retained": True}
    write_json(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
