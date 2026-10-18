"""Local P2PNet preview on supplied benchmark clips; no business timestamps/labels."""
import argparse
import hashlib
import json
import platform
import sys
import time
import zipfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import cv2
    import numpy as np
    import torch

    from intelligent_analysis import health
    from intelligent_analysis.contracts import ROI
    from intelligent_analysis.vision.benchmark import point_count
    from intelligent_analysis.vision.p2p_counter import P2PNetCounter

    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, default=ROOT / "model_bundle/p2pnet")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = ROOT / "artifacts" / ("fixed-camera-" + uuid4().hex[:8])
    run.mkdir()
    with zipfile.ZipFile(args.zip) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC failed")
        for item in archive.infolist():
            destination = (run / item.filename).resolve()
            if not destination.is_relative_to(run.resolve()):
                raise ValueError("Unsafe ZIP path")
        archive.extractall(run)
    inputs = run / "fixed_camera_crowds_20261017"
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    for video in manifest["videos"]:
        path = (inputs / video["file"]).resolve()
        if not path.is_relative_to(inputs.resolve()):
            raise ValueError("Unsafe input path")
        if path.stat().st_size != video["byte_size"] or hashlib.file_digest(path.open("rb"), "sha256").hexdigest() != video["sha256"]:
            raise ValueError("Video hash/size mismatch")
    torch.set_num_threads(4)
    counter = P2PNetCounter.from_team_bundle(args.bundle)
    roi = ROI(points=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}])
    report = {"status": "local_benchmark_inference_only", "e_version": health()["version"],
        "input_zip_sha256": hashlib.file_digest(args.zip.open("rb"), "sha256").hexdigest(),
        "zip_crc_verified": True, "video_hashes_verified": True,
        "run_relative_to_e": run.relative_to(ROOT).as_posix(), "model": counter.provenance,
        "runtime": {"python": platform.python_version(), "torch": torch.__version__, "device": "CPU", "threads": 4},
        "sample_interval_seconds": 10, "roi": roi.model_dump(), "manual_validation": None,
        "MAE": None, "accuracy_verified": False, "forecast_verified": False,
        "production_authorization_verified": False, "D_HTTP_verified": False,
        "recorded_start_at": None, "videos": []}
    overview = []
    for video in manifest["videos"]:
        cap = cv2.VideoCapture(str(inputs / video["file"]))
        if not cap.isOpened():
            raise ValueError("Cannot open video")
        frames, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
        if frames <= 0 or fps <= 0:
            raise ValueError("Invalid video metadata")
        row = {"id": video["id"], "scene": video["scene"], "sha256": video["sha256"],
            "source_page": video["source_page"], "license": video["license"],
            "duration_seconds": frames / fps, "fps": fps, "complete_5min_windows": int(frames / fps // 300), "samples": []}
        indices = range(0, frames, max(1, round(fps * 10)))
        previews = []
        try:
            for index in indices:
                if not cap.set(cv2.CAP_PROP_POS_FRAMES, index):
                    raise ValueError("Cannot seek frame")
                ok, frame = cap.read()
                if not ok:
                    raise ValueError("Cannot decode sample")
                start = time.perf_counter()
                points = counter(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                value = point_count(points, roi, 0.5)
                elapsed = time.perf_counter() - start
                selected = points[points[:, 2] > 0.5]
                out = run / "predictions" / video["id"]
                out.mkdir(parents=True, exist_ok=True)
                for x, y, _ in selected:
                    cv2.circle(frame, (round(x * (frame.shape[1]-1)), round(y * (frame.shape[0]-1))), 3, (0, 0, 255), -1)
                caption = f"{video['id']} t={index/fps:.1f}s estimate={value:g} UNVALIDATED"
                cv2.rectangle(frame, (0, 0), (frame.shape[1], 26), (0, 0, 0), -1)
                cv2.putText(frame, caption, (5, 18), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1)
                image_path = out / f"frame-{index}.jpg"
                encoded_ok, encoded = cv2.imencode(".jpg", frame)
                if not encoded_ok:
                    raise ValueError("Cannot encode preview")
                image_path.write_bytes(encoded.tobytes())
                sample = {"frame_index": index, "nominal_offset_seconds": index/fps, "value": value,
                    "elapsed_seconds": elapsed, "points": selected.tolist(),
                    "preview": image_path.relative_to(ROOT).as_posix()}
                row["samples"].append(sample)
                previews.append(frame)
            row["estimate_range"] = [min(s["value"] for s in row["samples"]), max(s["value"] for s in row["samples"])]
            row["sample_count"] = len(row["samples"])
            row["inference_seconds"] = sum(s["elapsed_seconds"] for s in row["samples"])
            tiles = []
            for image in [previews[0], previews[len(previews)//2], previews[-1]]:
                h, w = image.shape[:2]
                scale = min(480 / w, 320 / h)
                scaled = cv2.resize(image, (round(w*scale), round(h*scale)))
                tile = np.zeros((320,480,3), dtype=np.uint8)
                tile[:scaled.shape[0], :scaled.shape[1]] = scaled
                tiles.append(tile)
            overview.append(np.hstack(tiles))
        finally:
            cap.release()
        report["videos"].append(row)
        print(json.dumps({k:row[k] for k in ["id", "sample_count", "estimate_range", "inference_seconds"]}), flush=True)
    image_path = run / "P2PNet-overview.jpg"
    image_path.write_bytes(cv2.imencode(".jpg", np.vstack(overview))[1].tobytes())
    report["overview"] = image_path.relative_to(ROOT).as_posix()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "overview": str(image_path)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
