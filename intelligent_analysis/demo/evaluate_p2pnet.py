"""Evaluate only the formal P2PNet on authorized, independently reviewed frames."""
import argparse
import hashlib
import json
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.benchmark_frames import load_samples
from intelligent_analysis.contracts import ROI
from intelligent_analysis.vision.benchmark import point_count


def measure(samples, counter, roi):
    rows, groups = [], defaultdict(list)
    for sample in samples:
        started = time.perf_counter()
        value = point_count(counter(sample["frame"]), roi, .5)
        row = {"id": sample["id"], "scenario": sample["scenario"],
               "manual_count": sample["manual_count"], "value": value,
               "absolute_error": abs(value - sample["manual_count"]),
               "seconds": time.perf_counter() - started}
        rows.append(row)
        groups[row["scenario"]].append(row)
    if not rows:
        raise ValueError("No reviewed samples to measure")
    return {"sample_count": len(rows), "mae": sum(r["absolute_error"] for r in rows) / len(rows),
            "frames": rows, "scenarios": {key: {"sample_count": len(items),
                "mae": sum(r["absolute_error"] for r in items) / len(items)}
                for key, items in groups.items()},
            "missing_scenarios": sorted({"sparse", "dense", "occluded", "small_targets"} - set(groups))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
        default=ROOT / "docs/fixtures/video-evaluation/manifest.template.json")
    parser.add_argument("--bundle", type=Path, default=ROOT / "model_bundle/p2pnet")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/p2p-evaluation/report.json")
    args = parser.parse_args()
    manifest, samples = load_samples(args.manifest.resolve())
    protected = {args.manifest.resolve()} | {
        (args.manifest.parent / sample.file).resolve() for sample in manifest.samples}
    if args.output.resolve() in protected or args.output.resolve().is_relative_to(args.bundle.resolve()):
        raise ValueError("Output must not overwrite labels, frames or model inputs")
    for sample in manifest.samples:
        if (not sample.annotator.strip() or not sample.reviewer.strip()
                or sample.annotator.strip().casefold() == sample.reviewer.strip().casefold()):
            raise ValueError("Two distinct nonblank human reviewers are required")
    report = {"count_model": "p2pnet", "metric": "camera_visible_people",
              "confidence_threshold": .5, "dataset_id": manifest.dataset_id,
              "data_kind": manifest.data_kind, "label_revision": manifest.label_revision,
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "roi": manifest.roi.model_dump(mode="json"),
              "sample_metadata": [s.model_dump(mode="json") for s in manifest.samples],
              "real_video_accuracy_verified": False, "holdout_independence_verified": False,
              "D_HTTP_verified": False, "forecast_verified": False, "mae": None}
    code = 2
    if not samples:
        report.update(status="blocked", reason="awaiting_authorized_reviewed_frames",
                      sample_count=0, frames=[], scenarios={})
    else:
        import cv2
        import numpy as np
        import torch

        from intelligent_analysis.vision.p2p_counter import TEAM_WEIGHTS_VERSION, P2PNetCounter

        torch.set_num_threads(4)
        for sample in samples:
            image = cv2.imdecode(np.frombuffer(sample.pop("image_bytes"), dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or min(image.shape[:2]) < 128:
                raise ValueError("Reviewed frames must decode and be at least 128x128")
            sample["frame"] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        counter = P2PNetCounter.from_team_bundle(args.bundle)
        counter.validate_config({"count_model": "p2pnet", "weights_version": TEAM_WEIGHTS_VERSION,
            "roi": manifest.roi.model_dump(mode="json"), "inference_engine": "pytorch", "device": "CPU"})
        report.update(measure(samples, counter, ROI.model_validate(manifest.roi)))
        report.update(status="measured_pending_review", reason=None, model_provenance=counter.provenance,
                      runtime={"python": platform.python_version(), "torch": torch.__version__,
                               "device": "CPU", "threads": 4},
                      timing_scope="per-frame inference and ROI count; no model-load time or warmup",
                      authorization_ref=manifest.authorization_ref,
                      authorization_status=manifest.authorization_status)
        code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "sample_count": report["sample_count"], "mae": report["mae"]}))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
