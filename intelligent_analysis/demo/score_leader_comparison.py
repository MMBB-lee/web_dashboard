"""Score frozen exploratory predictions only after independent human review."""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

SCENARIOS = {"sparse", "dense", "occluded", "small_targets"}
ROOT = Path(__file__).resolve().parents[1]


def score(predictions, labels):
    if labels["dataset_id"] != predictions["dataset_id"] or labels["roi"] != predictions["roi"]:
        raise ValueError("Labels belong to a different dataset or ROI")
    if len({s["id"] for s in labels["samples"]}) != len(labels["samples"]):
        raise ValueError("Duplicate labels")
    by_id = {s["id"]: s for s in labels["samples"]}
    if set(by_id) != {s["id"] for s in predictions["frames"]}:
        raise ValueError("Labels must cover exactly the frozen frames")
    pending, rows = [], []
    revision = labels.get("label_revision")
    if not isinstance(revision, str) or not revision.strip():
        pending.append({"id": "dataset", "reason": "label_revision_required"})
    for frame in predictions["frames"]:
        label = by_id[frame["id"]]
        if any(label.get(k) != frame[k] for k in ("file", "sha256", "frame_index", "nominal_offset_seconds")):
            raise ValueError("Frame identity changed: " + frame["id"])
        manual = label.get("manual_count")
        annotator, reviewer = label.get("annotator"), label.get("reviewer")
        if (label.get("label_status") != "reviewed" or type(manual) is not int or manual < 0
                or type(label.get("uncertain_people")) is not int or label["uncertain_people"] != 0
                or not isinstance(annotator, str) or not annotator.strip()
                or not isinstance(reviewer, str) or not reviewer.strip() or annotator.strip() == reviewer.strip()
                or label.get("scenario") not in SCENARIOS):
            pending.append({"id": frame["id"], "reason": "complete_integer_count_scenario_and_independent_review"})
            continue
        for key in ("dm_count", "p2pnet"):
            value = frame[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid frozen model count")
        rows.append({"id": frame["id"], "scenario": label["scenario"], "manual_count": manual,
            "dm_count": frame["dm_count"], "p2pnet": frame["p2pnet"],
            "dm_absolute_error": abs(frame["dm_count"] - manual),
            "p2p_absolute_error": abs(frame["p2pnet"] - manual)})
    result = {"dataset_id": predictions["dataset_id"], "split": "exploratory",
        "sample_count": len(predictions["frames"]), "label_revision": revision,
        "real_video_accuracy_verified": False, "formal_holdout_verified": False,
        "model_replacement_approved": False, "forecast_verified": False,
        "dm_mae": None, "p2p_mae": None, "frames": [], "scenarios": {}}
    if pending:
        return {**result, "status": "awaiting_reviewed_labels", "pending": pending}
    if not rows:
        raise ValueError("Empty comparison cannot be scored")
    groups = defaultdict(list)
    for row in rows:
        groups[row["scenario"]].append(row)
    result.update(status="exploratory_scored", frames=rows,
        dm_mae=sum(r["dm_absolute_error"] for r in rows) / len(rows),
        p2p_mae=sum(r["p2p_absolute_error"] for r in rows) / len(rows),
        scenarios={key: {"sample_count": len(items),
            "dm_mae": sum(r["dm_absolute_error"] for r in items) / len(items),
            "p2p_mae": sum(r["p2p_absolute_error"] for r in items) / len(items)} for key, items in groups.items()},
        missing_scenarios=sorted(SCENARIOS - set(groups)))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=ROOT / "artifacts/leader-comparison")
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.run_dir.resolve()
    receipt = json.loads((root / "run-receipt.json").read_text(encoding="utf-8"))
    prediction_bytes = (root / "predictions/results.json").read_bytes()
    if hashlib.sha256(prediction_bytes).hexdigest() != receipt["prediction_sha256"]:
        raise ValueError("Frozen predictions changed")
    predictions = json.loads(prediction_bytes)
    labels = json.loads((args.labels or root / "labels.json").read_text(encoding="utf-8"))
    if labels["prediction_sha256"] != receipt["prediction_sha256"]:
        raise ValueError("Labels bind a different model run")
    for frame in predictions["frames"]:
        for file_key, digest_key in (("file", "sha256"), ("density_file", "density_sha256"), ("point_file", "point_sha256")):
            target = (root / frame[file_key]).resolve()
            if not target.is_relative_to(root) or hashlib.sha256(target.read_bytes()).hexdigest() != frame[digest_key]:
                raise ValueError("Frozen frame or prediction asset changed")
    report = score(predictions, labels)
    report.update(prediction_sha256=receipt["prediction_sha256"],
        labels_sha256=hashlib.sha256((args.labels or root / "labels.json").read_bytes()).hexdigest(),
        dm_provenance=predictions["dm_provenance"], p2p_provenance=predictions["p2p_provenance"])
    output = args.output or root / "score-report.json"
    if (output.resolve().is_relative_to(root / "frames") or output.resolve().is_relative_to(root / "predictions")
            or output.resolve() in {root / "labels.json", (args.labels or root / "labels.json").resolve(),
                                   root / "run-receipt.json"}):
        raise ValueError("Output must not overwrite labels or frozen predictions")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "sample_count", "dm_mae", "p2p_mae")}))
    if report["status"] != "exploratory_scored":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
