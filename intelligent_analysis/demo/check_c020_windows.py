"""Reproduce native C persist_file/fingerprint compatibility without a DB."""
import argparse
import io
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["0.2.0a1", "0.2.0a2"], default="0.2.0a2")
    version = parser.parse_args().version
    sys.path.insert(0, str(ROOT / f"artifacts/c-{version}/runtime"))
    from data_foundation import create_store
    from data_foundation.artifacts import verify_artifact
    from data_foundation.errors import Conflict

    root = ROOT / "artifacts" / ("c020-fingerprint-" + uuid4().hex[:8])
    root.mkdir()
    store = create_store("sqlite:///:memory:", data_root=root, allow_sqlite_for_tests=True)
    content = b'{"synthetic_fingerprint_probe":true}'
    class SlowStream(io.BytesIO):
        def read(self, size=-1):
            time.sleep(0.02)
            return super().read(size)

    staged = store.persist_file(source_id="e-probe", original_name="index.json", stream=SlowStream(content))
    target = root / staged["relative_path"]
    with target.open("rb") as stream:
        fd_stat = os.fstat(stream.fileno())
    path_stat = target.stat()
    fields = ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"]
    report = {"platform": sys.platform, "python": sys.version.split()[0], "c_version": version,
        "fingerprint": {k: {"fstat": getattr(fd_stat,k), "stat": getattr(path_stat,k)} for k in fields},
        "synthetic_probe": True, "stream_read_delay_seconds": 0.02, "c_source_modified": False}
    try:
        verify_artifact(root, staged["relative_path"], staged["sha256"], staged["byte_size"])
        report["native_persist_file_verified"] = True
    except Conflict as exc:
        report["native_persist_file_verified"] = False
        report["native_error"] = str(exc)
    report["status"] = "passed" if report["native_persist_file_verified"] else "blocked_native_storage"
    store.dispose()
    output = ROOT / ("docs/acceptance/C020a2-Windows-fingerprint.json" if version == "0.2.0a2"
                     else "docs/acceptance/C020-Windows-fingerprint.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
