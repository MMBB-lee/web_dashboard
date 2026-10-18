"""Verify the official C handoff and install its wheel into an isolated runtime."""
import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract_safe(archive: Path, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("Archive CRC verification failed")
        for member in bundle.infolist():
            if not (target / member.filename).resolve().is_relative_to(target.resolve()):
                raise ValueError("Unsafe archive path")
        bundle.extractall(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["0.2.0a1", "0.2.0a2"], default="0.2.0a2")
    parser.add_argument("--download", action="store_true", help="Fetch pinned official a2 release when git is unavailable")
    args = parser.parse_args()
    version = args.version
    name = f"C-index-artifacts-{version}-2026-10-14"
    c_repo = ROOT.parent / "data_foundation"
    source = c_repo / "data_foundation/release"
    commit = "06d192f068817aa9ffd24ad988e92d9737592aff"
    if args.download:
        if version != "0.2.0a2":
            raise ValueError("Only a2 has a pinned download commit")
        source = ROOT / "artifacts/official-c020a2-download"
        source.mkdir(parents=True, exist_ok=True)
        base = ("https://raw.githubusercontent.com/Hydr0genG/anhui-tourism-transport-visualization/"
                + commit + "/data_foundation/release/")
        for suffix in (".receipt.json", ".contents.json", ".zip"):
            path = source / (name + suffix)
            if not path.exists():
                path.write_bytes(urllib.request.urlopen(base + path.name, timeout=60).read())
    receipt = json.loads((source / (name + ".receipt.json")).read_text(encoding="utf-8"))
    archive = source / (name + ".zip")
    content = archive.read_bytes()
    archive_size = receipt.get("archive_byte_size", receipt.get("byte_size"))
    archive_digest = receipt.get("archive_sha256", receipt.get("sha256"))
    if len(content) != archive_size or hashlib.sha256(content).hexdigest() != archive_digest:
        raise ValueError("C ZIP integrity mismatch")
    contents = source / (name + ".contents.json")
    # Git checkout may translate JSON LF to CRLF on Windows; verify canonical blob.
    canonical_contents = contents.read_bytes() if args.download else subprocess.check_output(
        ["git", "show", "HEAD:data_foundation/release/" + contents.name], cwd=c_repo)
    if hashlib.sha256(canonical_contents).hexdigest() != receipt["contents_sha256"]:
        raise ValueError("C contents manifest integrity mismatch")
    if version == "0.2.0a2":
        expected = {"archive_sha256": "7fcbbc0031a13520b5d02aa9ebc8f9066d7b5a237609b0b2c6b0a87a96e54ad5",
                    "wheel_sha256": "4cf48f5c8c4303e2cb73a4169d62dd6111dba03df266256eced48ec565590fbe",
                    "contents_sha256": "a554fd38650cf8942958cc1224b2c278c5746306e9ddc4759e07e34d8ff93b0d"}
        if any(receipt[key] != value for key, value in expected.items()):
            raise ValueError("C official a2 handoff pin mismatch")
    target = ROOT / f"artifacts/c-{version}"
    extract_safe(archive, target)
    inventory = json.loads(canonical_contents)["files"]
    for relative, digest in inventory.items():
        path = target / relative
        if not path.resolve().is_relative_to(target.resolve()):
            raise ValueError("Unsafe contents inventory path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("C inventory mismatch: " + relative)
    wheel = target / f"data_foundation/release/anhui_data_foundation-{version}-py3-none-any.whl"
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != receipt["wheel_sha256"]:
        raise ValueError("C wheel integrity mismatch")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-compile",
                    "--target", str(target / "runtime"), str(wheel)], check=True)
    modules = list((target / "runtime/data_foundation").rglob("*.py"))
    for module in modules:
        relative = module.relative_to(target / "runtime")
        if module.read_text(encoding="utf-8") != (target / "data_foundation" / relative).read_text(encoding="utf-8"):
            raise ValueError("C wheel/source mismatch: " + str(relative))
    report = {"status": "passed", "c_version": receipt["package_version"],
        "c_release_commit": commit if args.download else subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=c_repo, text=True).strip(),
        "migration_head": receipt["migration_head"], "zip_sha256": archive_digest,
        "wheel_sha256": receipt["wheel_sha256"], "verified_python_modules": len(modules),
        "verified_inventory_files": len(inventory),
        "contents_manifest_verified": True, "downloaded_from_pinned_commit": args.download,
        "isolated_wheel_runtime": True, "pip_installed_official_wheel": True,
        "production_database_verified": False}
    output = ROOT / ("docs/acceptance/C020a2-receipt.json" if version == "0.2.0a2"
                     else "docs/acceptance/C020-receipt.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
