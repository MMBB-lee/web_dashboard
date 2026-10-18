"""Check the formal P2PNet counter on supplied frames, without invented times/labels."""
import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import cv2
    import torch

    from intelligent_analysis import health
    from intelligent_analysis.contracts import VideoAnalysisConfig
    from intelligent_analysis.vision.benchmark import point_count
    from intelligent_analysis.vision.p2p_counter import TEAM_WEIGHTS_VERSION, P2PNetCounter

    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=ROOT / "model_bundle/p2pnet")
    label = health()["version"].replace(".", "")
    parser.add_argument("--output", type=Path, default=ROOT / f"docs/acceptance/P2PNet-CPU-{label}.json")
    args = parser.parse_args()
    torch.set_num_threads(4)
    counter = P2PNetCounter.from_team_bundle(args.bundle)
    config = VideoAnalysisConfig.model_validate({"count_model": "p2pnet", "weights_version": TEAM_WEIGHTS_VERSION,
        "roi": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}]},
        "inference_engine": "pytorch", "device": "CPU"})
    counter.validate_config(config.model_dump(exclude_none=True))
    cap = cv2.VideoCapture(str(args.bundle / "mytest.mp4"))
    if not cap.isOpened():
        raise ValueError("Cannot open supplied video")
    report = {"status": "formal_counter_inference_smoke_passed", "e_version": health()["version"],
        "video_sha256": hashlib.sha256((args.bundle / "mytest.mp4").read_bytes()).hexdigest(),
        "analysis": config.model_dump(mode="json", exclude_none=True), "model_provenance": counter.provenance,
        "runtime": {"torch": torch.__version__, "python": platform.python_version(), "device": "CPU", "threads": 4},
        "GPU_verified": False, "upstream_commit_verified": False, "manual_validation": None,
        "real_video_accuracy_verified": False, "formal_D_HTTP_verified": False, "forecast_verified": False,
        "recorded_at": None, "frames": []}
    try:
        count, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
        if count < 1 or fps <= 0:
            raise ValueError("Invalid video timing")
        report["nominal_duration_seconds"] = count / fps
        report["complete_5min_windows"] = int(count / fps // 300)
        for index in sorted({0, count // 2, count - 1}):
            if not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError("Cannot seek frame")
            ok, frame = cap.read()
            if not ok:
                raise ValueError("Cannot read frame")
            start = time.perf_counter()
            value = point_count(counter(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), config.roi, config.confidence_threshold)
            report["frames"].append({"frame_index": index, "nominal_offset_seconds": index / fps,
                "value": value, "elapsed_seconds": time.perf_counter() - start, "metric": "camera_visible_people"})
    finally:
        cap.release()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "e_version": report["e_version"],
        "frame_values": [f["value"] for f in report["frames"]], "accuracy_verified": False}))


if __name__ == "__main__":
    main()
