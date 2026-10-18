"""Run DM-Count and optional P2PNet on one frozen, reviewed frame manifest."""
import argparse
import hashlib
import importlib
import json
import os
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Literal

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligent_analysis.contracts import ROI
from intelligent_analysis.vision.benchmark import P2PNetAdapter, compare_counters
from intelligent_analysis.vision.density_counter import (
    DMCountCounter,
    WeightSpec,
    density_count,
)


class FrameLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    scenario: Literal["sparse", "dense", "occluded", "small_targets"]
    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_video_id: str = Field(min_length=1)
    source_video_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: AwareDatetime
    manual_count: StrictInt = Field(ge=0)
    uncertain_people: StrictInt = Field(ge=0)
    annotator: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    label_status: Literal["reviewed"]


class FrameManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    data_kind: Literal["real", "synthetic"]
    camera_id: str = Field(min_length=1)
    permission_note: str = Field(min_length=1)
    authorization_ref: str = Field(min_length=1)
    authorization_status: Literal["pending", "confirmed"]
    split: Literal["holdout"]
    label_revision: str = Field(min_length=1)
    roi: ROI
    samples: list[FrameLabel]


def load_samples(path: Path):
    manifest = FrameManifest.model_validate_json(path.read_bytes())
    if manifest.samples and manifest.authorization_status != "confirmed":
        raise ValueError("Authorization must be confirmed before evaluating frames")
    samples, ids, hashes, instants = [], set(), set(), set()
    for item in manifest.samples:
        if item.id in ids or item.sha256 in hashes or item.recorded_at in instants:
            raise ValueError("Duplicate frame identity, bytes or recording timestamp")
        if item.uncertain_people or item.annotator == item.reviewer:
            raise ValueError("Independent review and zero unresolved labels are required")
        relative = PurePosixPath(item.file)
        if (relative.is_absolute() or relative.as_posix() != item.file or "\\" in item.file
                or any(p in {".", ".."} or ":" in p for p in relative.parts)):
            raise ValueError("Frame path must be a safe relative POSIX path")
        target = (path.parent / relative).resolve()
        if not target.is_relative_to(path.parent.resolve()):
            raise ValueError("Frame path escapes manifest directory")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != item.sha256:
            raise ValueError("Frame SHA256 mismatch: " + item.id)
        samples.append({"id": item.id, "scenario": item.scenario, "manual_count": item.manual_count,
            "roi": manifest.roi.model_dump(mode="json"), "image_bytes": content})
        ids.add(item.id)
        hashes.add(item.sha256)
        instants.add(item.recorded_at)
    return manifest, samples


def measure(samples, dm_counter, p2p_counter, dm_provenance, p2p_provenance):
    if p2p_counter is not None:
        return compare_counters(samples, dm_counter, p2p_counter,
            dm_provenance=dm_provenance, p2p_provenance=p2p_provenance)
    rows, groups = [], defaultdict(list)
    for sample in samples:
        start = time.perf_counter()
        value = density_count(dm_counter(sample["frame"]), ROI.model_validate(sample["roi"]))
        row = {"id": sample["id"], "scenario": sample["scenario"], "manual_count": sample["manual_count"],
            "dm_count": value, "dm_absolute_error": abs(value - sample["manual_count"]),
            "dm_seconds": time.perf_counter() - start}
        rows.append(row)
        groups[sample["scenario"]].append(row)
    return {"metric": "camera_visible_people", "sample_count": len(rows), "frames": rows,
        "dm_provenance": dm_provenance, "p2p_provenance": None,
        "scenarios": {key: {"sample_count": len(items),
            "dm_mae": sum(r["dm_absolute_error"] for r in items) / len(items)} for key, items in groups.items()}}


def load_p2p(args):
    import torch
    if hashlib.sha256(args.p2p_weights.read_bytes()).hexdigest() != args.p2p_sha256:
        raise ValueError("P2PNet weights SHA256 mismatch")
    module, function = args.p2p_factory.split(":", 1)
    # Factory is an explicit trusted CLI argument, never taken from dataset JSON.
    model = getattr(importlib.import_module(module), function)().cpu()
    state = torch.load(args.p2p_weights, map_location="cpu", weights_only=True)
    state = state.get("model", state.get("state_dict", state))
    model.load_state_dict(state, strict=True)
    return P2PNetAdapter(model)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "docs/fixtures/video-evaluation/manifest.template.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/frame-benchmark/report.json")
    parser.add_argument("--dm-weights", type=Path, default=ROOT / "artifacts/dm-count/model_qnrf.pth")
    parser.add_argument("--dm-sha256", default="16ef954a2cef40c66ee664f69273559553b735d5e2c9f90e2444f3c25dd45e05")
    parser.add_argument("--dm-version", default="dm-count-qnrf-cc5f2132")
    parser.add_argument("--p2p-factory", help="Trusted local module:function, returns an uninitialized CPU model")
    parser.add_argument("--p2p-weights", type=Path)
    parser.add_argument("--p2p-sha256")
    parser.add_argument("--p2p-version")
    parser.add_argument("--p2p-source-revision")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    report = {"status": "blocked", "real_video_accuracy_verified": False,
        "P2PNet_project_compatibility_verified": False, "forecast_verified": False}
    exit_code = 2
    try:
        p2p_args = (args.p2p_factory, args.p2p_weights, args.p2p_sha256, args.p2p_version, args.p2p_source_revision)
        if any(p2p_args) and not all(p2p_args):
            raise ValueError("All P2PNet factory, weights, SHA256, version and source revision arguments are required together")
        if args.threads < 1:
            raise ValueError("CPU threads must be positive")
        manifest, samples = load_samples(args.manifest.resolve())
        report.update(dataset_id=manifest.dataset_id, camera_id=manifest.camera_id, data_kind=manifest.data_kind,
            manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
            sample_count=len(samples), sample_metadata=[s.model_dump(mode="json") for s in manifest.samples],
            roi=manifest.roi.model_dump(mode="json"), label_revision=manifest.label_revision,
            authorization_ref=manifest.authorization_ref, authorization_status=manifest.authorization_status,
            permission_note=manifest.permission_note,
            split=manifest.split, holdout_independence_verified=False)
        if not samples:
            report.update(reason="awaiting_authorized_reviewed_frames", frames=[], scenarios={})
        else:
            import cv2
            import torch
            torch.set_num_threads(args.threads)
            for sample in samples:
                try:
                    image = cv2.imdecode(np.frombuffer(sample.pop("image_bytes"), dtype=np.uint8), cv2.IMREAD_COLOR)
                except cv2.error as exc:
                    raise ValueError("Frame decoding failed") from exc
                if image is None or min(image.shape[:2]) < 128:
                    raise ValueError("Matched-model frames must decode and be at least 128x128")
                sample["frame"] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            dm = DMCountCounter(WeightSpec(str(args.dm_weights), args.dm_sha256, args.dm_version))
            dm.validate_config({"count_model": "dm_count", "weights_version": args.dm_version,
                "roi": manifest.roi.model_dump(), "inference_engine": "pytorch", "device": "CPU"})
            provenance = {"device": "CPU", "inference_engine": "pytorch", "torch_version": torch.__version__,
                "python": platform.python_version(), "platform": platform.platform(), "cpu_threads": args.threads}
            dm_provenance = {**provenance, "weights_version": args.dm_version, "weights_sha256": args.dm_sha256}
            p2p = load_p2p(args) if all(p2p_args) else None
            p2p_provenance = {**provenance, "weights_version": args.p2p_version,
                "weights_sha256": args.p2p_sha256, "source_revision": args.p2p_source_revision,
                "confidence_threshold": 0.5} if p2p is not None else None
            report.update(measure(samples, dm, p2p, dm_provenance, p2p_provenance))
            report.update(status="measured_pending_review", reason=None,
                P2PNet_runtime_smoke_passed=p2p is not None,
                p2p_status="measured" if p2p is not None else "pending_weights_and_compatible_factory",
                missing_scenarios=sorted({"sparse", "dense", "occluded", "small_targets"} - set(report["scenarios"])),
                timing_scope="per-frame inference and ROI counting; model load excluded, no warmup")
            exit_code = 0
    except (OSError, ValueError, RuntimeError, ImportError, AttributeError, KeyError, TypeError, IndexError) as exc:
        report.update(status="failed", reason=str(exc), error_type=type(exc).__name__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report.get("reason"), "output": os.fspath(args.output)}))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
