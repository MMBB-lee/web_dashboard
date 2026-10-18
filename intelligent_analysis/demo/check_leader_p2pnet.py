"""Headless smoke of the user's team bundle, without labels or accuracy claims."""
import argparse
import hashlib
import json
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=ROOT / "artifacts/leader-p2pnet-original")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/acceptance/Leader-P2PNet-smoke.json")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    receipt = json.loads((bundle / "E-receipt.json").read_text(encoding="utf-8"))
    for name, expected in receipt["files"].items():
        if not (bundle / name).resolve().is_relative_to(bundle) or digest(bundle / name) != expected:
            raise ValueError("Received bundle changed: " + name)
    sys.path.insert(0, str(bundle))
    import cv2
    import torch
    import torchvision

    from demo.leader_p2p_factory import build
    from intelligent_analysis.contracts import ROI
    from intelligent_analysis.vision.benchmark import P2PNetAdapter, point_count

    torch.set_num_threads(4)
    model = build()
    checkpoint = torch.load(bundle / "SHTechA.pth", map_location="cpu", weights_only=True)
    model.load_state_dict(checkpoint["model"], strict=True)
    adapter = P2PNetAdapter(model)
    roi = ROI.model_validate({"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0},
        {"x": 1, "y": 1}, {"x": 0, "y": 1}]})
    cap = cv2.VideoCapture(str(bundle / "mytest.mp4"))
    if not cap.isOpened():
        raise ValueError("Cannot open supplied test video")
    report = {"source": "user_supplied_team_P2PNet_bundle", "archive_sha256": receipt["archive_sha256"],
        "weights_sha256": digest(bundle / "SHTechA.pth"), "video_sha256": digest(bundle / "mytest.mp4"),
        "source_hashes": {k: v for k, v in receipt["files"].items() if k.endswith(".py")},
        "crossing_script_sha256": receipt["crossing_script_sha256"],
        "python": platform.python_version(), "torch": torch.__version__, "torchvision": torchvision.__version__,
        "opencv": cv2.__version__, "numpy": np.__version__, "scipy": version("scipy"), "Pillow": version("Pillow"),
        "device": "CPU", "cuda_available": torch.cuda.is_available(), "GPU_verified": False,
        "upstream_commit_verified": False, "real_video_accuracy_verified": False,
        "manual_labels_supplied": False, "MAE": None, "forecast_verified": False,
        "source_code_modified": False, "frames": []}
    try:
        fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not np.isfinite(fps) or fps <= 0 or count < 1:
            raise ValueError("Invalid video timing metadata")
        report["video"] = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), "fps": fps, "frame_count": count,
            "nominal_duration_seconds": count / fps, "recorded_at": None}
        for number in sorted({0, count // 2, count - 1}):
            if not cap.set(cv2.CAP_PROP_POS_FRAMES, number):
                raise ValueError("Video seek failed")
            ok, bgr = cap.read()
            if not ok:
                raise ValueError("Video frame read failed")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            start = time.perf_counter()
            points = adapter(rgb)
            value = point_count(points, roi)
            report["frames"].append({"requested_frame_index": number,
                "decoded_frame_index": cap.get(cv2.CAP_PROP_POS_FRAMES) - 1,
                "nominal_offset_seconds": number / fps, "estimated_visible_people": value,
                "elapsed_seconds": time.perf_counter() - start,
                "manual_count": None, "accuracy_verified": False})
        report.update(status="inference_smoke_passed", confidence_threshold=0.5,
            roi=roi.model_dump(mode="json"), preprocessing="E P2PNetAdapter PIL LANCZOS, not team cv2 INTER_LINEAR",
            line_crossing_used=False, package_pin_environment_reproduced=False,
            five_minute_windows_available=count / fps >= 300)
    finally:
        cap.release()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
