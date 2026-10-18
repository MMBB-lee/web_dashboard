"""Export unmarked frames and empty human-review sheets from a received run."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    import cv2

    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.report.read_text(encoding="utf-8"))
    source_run = (ROOT / source["run_relative_to_e"]).resolve()
    if not source_run.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Expected an isolated received run inside artifacts")
    inputs = source_run / "fixed_camera_crowds_20261017"
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    originals = {v["id"]: v for v in manifest["videos"]}
    target = ROOT / "artifacts" / ("human-annotation-" + uuid4().hex[:8])
    (target / "frames").mkdir(parents=True)
    rows = []
    for video in source["videos"]:
        path = (inputs / originals[video["id"]]["file"]).resolve()
        if not path.is_relative_to(inputs.resolve()):
            raise ValueError("Video path escapes received inputs")
        if hashlib.file_digest(path.open("rb"), "sha256").hexdigest() != video["sha256"]:
            raise ValueError("Input video changed")
        cap = cv2.VideoCapture(str(path))
        try:
            for frame in video["samples"]:
                if not cap.set(cv2.CAP_PROP_POS_FRAMES, frame["frame_index"]):
                    raise ValueError("Cannot seek annotation sample")
                ok, image = cap.read()
                if not ok:
                    raise ValueError("Cannot decode annotation sample")
                ok, encoded = cv2.imencode(".png", image)
                if not ok:
                    raise ValueError("Cannot encode unmarked sample")
                content = encoded.tobytes()
                relative = f"frames/{video['id']}-{frame['frame_index']}.png"
                (target / relative).write_bytes(content)
                rows.append({"id": f"{video['id']}-{frame['frame_index']}", "file": relative,
                    "sha256": hashlib.sha256(content).hexdigest(), "source_video_id": video["id"],
                    "source_video_sha256": video["sha256"], "frame_index": frame["frame_index"],
                    "relative_seconds": frame["nominal_offset_seconds"], "recorded_at": "",
                    "scenario": "", "annotator_count": "", "reviewer_count": "", "manual_count": "",
                    "uncertain_people": "", "annotator": "", "reviewer": "", "label_status": "pending"})
        finally:
            cap.release()
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    (target / "labels.csv").write_text(stream.getvalue(), encoding="utf-8-sig", newline="")
    draft = {"status": "awaiting_human_labels", "sample_count": len(rows), "manual_validation": None,
        "mae": None, "roi": source["roi"], "label_revision": "pending-v1", "model_estimates_included": False,
        "recorded_times_known": False, "holdout_independence_verified": False,
        "production_authorization_verified": False, "source_inference_report": args.report.as_posix(),
        "license_note": manifest["license_scope"], "rows": rows}
    (target / "annotation-draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    sources = "# Sources and license (as supplied; not independently reverified)\n\n"
    sources += manifest["license_scope"] + "\n\nAttribution: MOTChallenge and sequence authors.\n"
    for video in manifest["videos"]:
        sources += f"- {video['id']}: {video['source_page']} ; {video['license_url']}\n"
    (target / "SOURCES.md").write_text(sources, encoding="utf-8")
    (target / "README.md").write_text(
        "# 人工标注工作包（未完成）\n\n"
        "frames 是原视频解码的无模型标点 PNG；labels.csv 不附模型估计。请保留来源许可。\n"
        "每段固定全画面 ROI。按可见人头计数，不数人次；边缘/遮挡无法判断时记 uncertain_people，先协商口径。\n"
        "两位不同的人独立填写 annotator_count/reviewer_count 与别名；核对后填写 manual_count。\n"
        "scenario 由人确认 sparse/dense/occluded/small_targets；不确定人物未解决时不标 reviewed。\n"
        "recorded_at 未知留空，不把相对秒数变造为业务绝对时间。每段分开记录机位。\n"
        "这批样本已经用于模型试跑，不宣称独立留出集；正式泛化评估另留未看过的样本。\n"
        "annotation-draft.json 是待填工作稿，不是已审核评测 manifest，不直接交正式 evaluate_p2pnet.py。\n",
        encoding="utf-8")
    print(json.dumps({"directory": str(target), "sample_count": len(rows), "mae": None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
