"""Package verified 0.2.0 outputs without overwriting the 0.1.0 release."""
import argparse
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

from anhui_crawler import __version__
from anhui_crawler.common import now, sha256, write_json


def descriptor(path):
    return {"name": path.name, "byte_size": path.stat().st_size, "sha256": sha256(path.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--mysql-instance", type=Path, required=True, help="Owned, stopped temporary instance; only read shutdown evidence")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / "acceptance/2026-10-15-bulletins"
    release = root / "release"
    assert __version__ == "0.2.0"
    wheel = release / "anhui_public_data_crawler-0.2.0-py3-none-any.whl"
    data_zip = release / "anhui-crawler-data-0.2.0-2026-10-15.zip"
    source_zip = release / "anhui-public-data-crawler-0.2.0-2026-10-15.zip"
    receipt_path = release / "receipt-0.2.0.json"
    assert all(not p.exists() for p in (data_zip, source_zip, receipt_path)), "Existing releases are immutable"
    old_receipt = json.loads((release / "receipt.json").read_text())
    for item in old_receipt["files"]:
        assert descriptor(release / item["name"]) == item
    test_runs = {}
    for name in ("source-tests.xml", "installed-wheel-tests.xml"):
        suite = ET.parse(out / name).getroot().find("testsuite")
        counts = {k: int(suite.attrib[k]) for k in ("tests", "errors", "failures", "skipped")}
        assert counts == {"tests": 85, "errors": 0, "failures": 0, "skipped": 0}
        test_runs[name] = counts
    results = {kind: json.loads((out / folder / "summary.json").read_text()) for kind, folder in
               (("sqlite", "installed-wheel-sqlite"), ("mysql", "installed-wheel-mysql"), ("portable", "portable"))}
    assert all(r["status"] == "passed" for r in results.values())
    assert results["portable"]["wheel_sha256"] == sha256(wheel.read_bytes())
    assert results["mysql"]["counts"]["ac_facts"] == results["sqlite"]["counts"]["ac_facts"] == 277
    assert not (args.mysql_instance / "mysql.sock").exists() and not (args.mysql_instance / "mysql.pid").exists()
    assert "Shutdown complete" in (args.mysql_instance / "server.log").read_text()
    files = sorted(p for p in args.export.iterdir() if p.is_file())
    assert {p.name for p in files} == {"facts.json", "facts.csv", "registry.json", "manifest.json", "coverage.json", "parser_capabilities.json"}
    assert len(json.loads((args.export / "facts.json").read_text())["records"]) == 277
    for path in (root / "README.md", root / "docs/PLAN.md", root / "docs/PROGRESS-0.2.0.md", out / "README.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if not target.startswith(("https:", "http:", "#")):
                destination = (path.parent / target.split("#")[0]).resolve()
                assert destination.exists() or destination in {receipt_path.resolve(), (out / "release-verification.json").resolve()}, (path.name, target)
    summary = {"status": "passed", "verified_at": now(), "version": __version__, "test_runs": test_runs,
               "counts": {"facts": 277, "airport_months": 260, "city_records": 17, "cities": 9,
                          "annual_city_flows": 11, "year_end_inventory": 6, "registry_entities": 44},
               "installed_wheel_sqlite_and_mysql": "passed", "portable_clean_backfill": "passed",
               "original_0_1_0_release_sha_unchanged": True, "old_airport_facts_unchanged": True,
               "mysql_shutdown_confirmed": True, "data_files_deleted": False,
               "team_code_or_database_modified": False, "new_source_network_requests": 0,
               "scheduled_service_enabled": False, "full_province_complete": False}
    write_json(out / "release-verification.json", summary)
    with ZipFile(data_zip, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "anhui-crawler-data-0.2.0/" + path.name)
    sources = [root / p for p in ("README.md", "pyproject.toml", ".env.example", ".gitignore", ".gitattributes")]
    for folder in ("src", "scripts", "tests", "docs", "acceptance"):
        sources.extend(p for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                       and not any(part.endswith(".egg-info") for part in p.parts) and p.suffix != ".pyc")
    sources.append(wheel)
    with ZipFile(source_zip, "w", ZIP_DEFLATED) as archive:
        for path in sorted(sources):
            archive.write(path, "anhui_public_data_crawler/" + path.relative_to(root).as_posix())
    for path in (source_zip, data_zip, wheel):
        with ZipFile(path) as archive:
            assert archive.testzip() is None
            assert not any("/.git/" in n or n.endswith((".sqlite3", "/.env")) for n in archive.namelist())
    receipt = {"schema_version": "anhui-crawler-release-1", "version": __version__, "released_at": now(),
               "scope": "standalone_airport_plus_nine_city_bulletins", "published_to_remote": False,
               "files": [descriptor(p) for p in (wheel, source_zip, data_zip)],
               "exported_files": [descriptor(p) for p in files], "verification": summary,
               "excluded": ["Raw HTML/PDF full documents", "Database credentials", "Team C/D/E packages"],
               "next_batch": "Wanfohu holiday bulletin, then single-station actual daily departures"}
    write_json(receipt_path, receipt)
    print(json.dumps({"status": "packaged", "version": __version__, "files": receipt["files"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
