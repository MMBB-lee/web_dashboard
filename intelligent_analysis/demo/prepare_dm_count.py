"""Fetch pinned official model artifacts and verify Git blob hashes."""
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "cc5f2132e0d1328909f31b6d665b8e0b15c30467"
REPO = "cvlab-stonybrook/DM-Count"


def main():
    tree = json.load(urllib.request.urlopen(
        f"https://api.github.com/repos/{REPO}/git/trees/{COMMIT}?recursive=1", timeout=60))
    blobs = {p["path"]: p for p in tree["tree"] if p["type"] == "blob"}
    root = ROOT / "artifacts/dm-count"
    root.mkdir(parents=True, exist_ok=True)
    files = ["pretrained_models/model_qnrf.pth", "example_images/1.png", "LICENSE"]
    report = {"repository": REPO, "commit": COMMIT, "files": {}}
    for name in files:
        target = root / Path(name).name
        if not target.exists():
            target.write_bytes(urllib.request.urlopen(
                f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{name}", timeout=180).read())
        data = target.read_bytes()
        git_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if git_hash != blobs[name]["sha"]:
            raise ValueError("Git blob integrity mismatch: " + name)
        report["files"][name] = {"path": str(target), "sha256": hashlib.sha256(data).hexdigest()}
    (root / "provenance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
