"""Freeze uniformly sampled frames and both model outputs for blind human review."""
import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=ROOT / "artifacts/leader-p2pnet-original")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/leader-comparison")
    parser.add_argument("--frames", type=int, default=6)
    args = parser.parse_args()
    if not 3 <= args.frames <= 60:
        raise ValueError("Choose between 3 and 60 evenly spaced exploratory frames")
    bundle = args.bundle.resolve()
    receipt = json.loads((bundle / "E-receipt.json").read_text(encoding="utf-8"))
    for name, expected in receipt["files"].items():
        target = (bundle / name).resolve()
        if not target.is_relative_to(bundle) or sha(target) != expected:
            raise ValueError("Received source changed: " + name)
    sys.path.insert(0, str(bundle))
    import cv2
    import torch

    from demo.leader_p2p_factory import build
    from intelligent_analysis.contracts import ROI
    from intelligent_analysis.vision.benchmark import P2PNetAdapter, point_count
    from intelligent_analysis.vision.density_counter import (
        DMCountCounter,
        WeightSpec,
        density_count,
    )

    torch.set_num_threads(4)
    roi = ROI.model_validate({"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0},
        {"x": 1, "y": 1}, {"x": 0, "y": 1}]})
    dm_spec = WeightSpec(str(ROOT / "artifacts/dm-count/model_qnrf.pth"),
        "16ef954a2cef40c66ee664f69273559553b735d5e2c9f90e2444f3c25dd45e05", "dm-count-qnrf-cc5f2132")
    dm = DMCountCounter(dm_spec)
    dm.validate_config({"count_model": "dm_count", "weights_version": dm_spec.version,
        "roi": roi.model_dump(), "inference_engine": "pytorch", "device": "CPU"})
    model = build()
    model.load_state_dict(torch.load(bundle / "SHTechA.pth", map_location="cpu", weights_only=True)["model"], strict=True)
    p2p = P2PNetAdapter(model)
    cap = cv2.VideoCapture(str(bundle / "mytest.mp4"))
    try:
        fps, frame_count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or not np.isfinite(fps) or fps <= 0 or frame_count < args.frames:
            raise ValueError("Cannot read valid source video timing")
        root = args.output_dir.resolve()
        root.mkdir(parents=True, exist_ok=False)  # Never overwrite human work on a rerun.
        (root / "frames").mkdir()
        (root / "predictions").mkdir()
        dataset_id = "leader-video-exploratory-" + receipt["files"]["mytest.mp4"][:12]
        report = {"dataset_id": dataset_id, "status": "predictions_frozen_awaiting_labels",
            "metric": "camera_visible_people", "split": "exploratory", "frames_independent": False,
            "roi": roi.model_dump(mode="json"), "sampling": "uniform frame indices including first and last",
            "source_video_sha256": receipt["files"]["mytest.mp4"], "recorded_at": None,
            "nominal_duration_seconds": frame_count / fps, "real_video_accuracy_verified": False,
            "formal_holdout_verified": False, "GPU_verified": False, "forecast_verified": False,
            "dm_mae": None, "p2p_mae": None,
            "dm_provenance": {"weights_version": dm_spec.version, "weights_sha256": dm_spec.sha256},
            "p2p_provenance": {"weights_version": "leader-SHTechA-50604773",
                "weights_sha256": receipt["files"]["SHTechA.pth"], "source_archive_sha256": receipt["archive_sha256"],
                "upstream_commit_verified": False, "confidence_threshold": 0.5},
            "runtime": {"device": "CPU", "threads": 4, "python": platform.python_version(),
                "torch": torch.__version__, "opencv": cv2.__version__, "numpy": np.__version__}, "frames": []}
        labels = {"dataset_id": dataset_id, "roi": report["roi"], "label_revision": None, "samples": []}
        for number in np.linspace(0, frame_count - 1, args.frames, dtype=int).tolist():
            if not cap.set(cv2.CAP_PROP_POS_FRAMES, number):
                raise ValueError("Video seek failed")
            ok, bgr = cap.read()
            if not ok:
                raise ValueError("Missing sampled frame")
            if round(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1 != number:
                raise ValueError("Decoded frame index does not match frozen sample")
            sample_id = f"frame-{number:06d}"
            image_path = root / "frames" / (sample_id + ".png")
            encoded, png = cv2.imencode(".png", bgr)
            if not encoded:
                raise ValueError("PNG encoding failed")
            image_path.write_bytes(png.tobytes())
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            start = time.perf_counter()
            density = dm(rgb)
            dm_value = density_count(density, roi)
            dm_seconds = time.perf_counter() - start
            start = time.perf_counter()
            points = p2p(rgb)
            p2p_value = point_count(points, roi)
            p2p_seconds = time.perf_counter() - start
            density_path = root / "predictions" / (sample_id + "-dm-density.npy")
            np.save(density_path, density, allow_pickle=False)
            point_path = root / "predictions" / (sample_id + "-p2p-points.json")
            valid_points = [p.tolist() for p in points[points[:, 2] > 0.5]
                            if point_count(np.asarray([p]), roi) == 1]
            save(point_path, {"normalized_xy_confidence": valid_points})
            row = {"id": sample_id, "file": image_path.relative_to(root).as_posix(), "sha256": sha(image_path),
                "frame_index": number, "nominal_offset_seconds": number / fps,
                "dm_count": dm_value, "p2pnet": p2p_value, "dm_seconds": dm_seconds, "p2p_seconds": p2p_seconds,
                "density_file": density_path.relative_to(root).as_posix(), "density_sha256": sha(density_path),
                "point_file": point_path.relative_to(root).as_posix(), "point_sha256": sha(point_path)}
            report["frames"].append(row)
            labels["samples"].append({k: row[k] for k in ("id", "file", "sha256", "frame_index", "nominal_offset_seconds")})
            labels["samples"][-1].update(manual_count=None, uncertain_people=None, annotator=None,
                reviewer=None, scenario=None, label_status="pending")
            print(json.dumps({"frame_index": number, "status": "predictions_saved"}), flush=True)
        prediction_path = root / "predictions/results.json"
        save(prediction_path, report)
        labels["prediction_sha256"] = sha(prediction_path)
        save(root / "labels.json", labels)
        save(root / "run-receipt.json", {"dataset_id": dataset_id, "prediction_sha256": sha(prediction_path),
            "frame_count": len(report["frames"]), "label_status": "pending"})
        (root / "先读我.txt").write_text(
            "先只看frames中的原始PNG，按固定全画面ROI独立计数。请两人复核后填写labels.json。\n"
            "manual_count为非负整数；uncertain_people未解决时不能填0；标注和复核用不同别名。\n"
            "不要查看predictions后把模型估计抄成真值；此短片用于探索，不是独立holdout。\n"
            "实际录制时间未知，只有帧序号和相对时刻；不能据此做5分钟聚合或预测。\n", encoding="utf-8")
        print(json.dumps({"output_dir": str(root), "samples": len(report["frames"]), "MAE": None}))
    finally:
        cap.release()


if __name__ == "__main__":
    main()
