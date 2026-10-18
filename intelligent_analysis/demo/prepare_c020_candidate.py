"""Test a proposed C Windows fix only on a copied wheel in E artifacts."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "artifacts/c-0.2.0a1/runtime"
    target = source.with_name("runtime-candidate")
    if target.exists():
        raise FileExistsError("Candidate already exists; reuse it or choose a fresh acceptance checkout")
    shutil.copytree(source, target)
    module = target / "data_foundation/artifacts.py"
    original = module.read_text(encoding="utf-8")
    old = "            current = path.stat()"
    new = """            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0))
            try:
                current = os.fstat(descriptor)
            finally:
                os.close(descriptor)"""
    if original.count(old) != 1:
        raise ValueError("C source does not match the inspected fix location")
    modified = original.replace(old, new)
    module.write_text(modified, encoding="utf-8")
    report = {"candidate_only": True, "c_repository_modified": False,
        "original_module_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "candidate_module_sha256": hashlib.sha256(modified.encode()).hexdigest(),
        "change": "Use fstat for both baseline and post-read comparison; preserve all fingerprint fields",
        "upstream_confirmation": "pending"}
    (target / "E-candidate-patch.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    output = ROOT / "docs/acceptance/C020-candidate-patch.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
