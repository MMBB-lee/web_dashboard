"""Verify the user-provided C021 ZIP manifest before isolated extraction."""
import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.archive) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError("Duplicate archive paths")
        for item in members:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or "\\" in item.filename or ":" in item.filename
                    or ".." in path.parts or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Unsafe archive path")
        if archive.testzip() is not None:
            raise ValueError("Archive CRC failed")
        manifest = json.loads(archive.read("DELIVERY-MANIFEST.json"))
        if manifest["version"] != "0.2.1a2":
            raise ValueError("This receiving entry expects C 0.2.1a2")
        entries = manifest["files"]
        paths = [item["path"] for item in entries]
        if len(set(paths)) != len(paths) or set(paths) != set(names) - {"DELIVERY-MANIFEST.json"}:
            raise ValueError("Manifest does not cover every archive member")
        for item in entries:
            content = archive.read(item["path"])
            if len(content) != item["byte_size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("Manifest hash mismatch: " + item["path"])
        target = ROOT / "artifacts/c-0.2.1a2/handoff"
        if target.exists():
            raise FileExistsError("Receiving directory already exists; no files overwritten")
        target.mkdir(parents=True)
        archive.extractall(target)
        wheel = target / "data_foundation/release/anhui_data_foundation-0.2.1a2-py3-none-any.whl"
        report = {"status": "manifest_and_crc_verified", "c_version": manifest["version"],
            "source_commit_claimed_by_C": manifest["source_commit"],
            "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
            "archive_byte_size": args.archive.stat().st_size, "archive_members": len(names),
            "manifest_files_verified": len(entries), "embedded_manifest_verified": True,
            "independent_external_archive_receipt_provided": False,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "handoff_relative_to_e": target.relative_to(ROOT).as_posix(),
            "installed": False, "MySQL_verified": False}
        (ROOT / "docs/acceptance/C021a2-receipt.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
