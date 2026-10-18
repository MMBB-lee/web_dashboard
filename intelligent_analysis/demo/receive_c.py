"""Receive the pinned C handoff without modifying C or a business database."""
from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

COMMIT = "641b4a8"
BASE = ("https://raw.githubusercontent.com/Hydr0genG/"
        f"anhui-tourism-transport-visualization/{COMMIT}/data_foundation/release/")
NAME = "C-observation-quality-0.1.9-2026-10-14"


def receive(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    receipt_bytes = urllib.request.urlopen(BASE + NAME + ".receipt.json", timeout=60).read()
    receipt = json.loads(receipt_bytes)
    archive = root / (NAME + ".zip")
    if not archive.exists():
        archive.write_bytes(urllib.request.urlopen(BASE + NAME + ".zip", timeout=120).read())
    content = archive.read_bytes()
    if len(content) != receipt["byte_size"] or hashlib.sha256(content).hexdigest() != receipt["sha256"]:
        raise ValueError("C archive size/SHA256 mismatch")
    (root / (NAME + ".receipt.json")).write_bytes(receipt_bytes)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("C archive CRC mismatch")
        for member in bundle.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Unsafe archive path")
        bundle.extractall(root)
    wheels = list(root.rglob("*0.1.9*.whl"))
    if not wheels or any(hashlib.sha256(p.read_bytes()).hexdigest() != receipt["wheel_sha256"] for p in wheels):
        raise ValueError("C wheel SHA256 mismatch")
    result = {"commit": COMMIT, "version": receipt["package_version"],
              "migration_head": receipt["migration_head"], "archive_sha256": receipt["sha256"],
              "archive_verified": True, "wheel_verified": True,
              "business_database_verified": False}
    (root / "E-receipt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(receive(Path(__file__).resolve().parents[1] / "artifacts" / "c-0.1.9")))
