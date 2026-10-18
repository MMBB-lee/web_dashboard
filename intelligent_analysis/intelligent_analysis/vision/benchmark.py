"""P2PNet point adapter and historical matched-model evaluation helpers."""
from __future__ import annotations

import math
import time
from collections import defaultdict

import numpy as np

from ..contracts import ROI
from .density_counter import density_count


def point_count(points: np.ndarray, roi: ROI, threshold: float = 0.5) -> float:
    """P2P points are normalized (x,y,confidence), not trajectories."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("P2P predictions must be finite Nx3 normalized points")
    if ((points < 0) | (points > 1)).any() or not 0 <= threshold <= 1:
        raise ValueError("P2P coordinates/confidences must be in [0,1]")
    count = 0
    for x, y, confidence in points:
        if confidence <= threshold:
            continue
        inside = False
        for a, b in zip(roi.points, roi.points[1:] + roi.points[:1]):
            if (a.y > y) != (b.y > y) and x < (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x:
                inside = not inside
        count += int(inside)
    return float(count)


class P2PNetAdapter:
    """CPU point adapter for a caller-loaded P2PNet-compatible model.

    D/E supply an eval model whose weights provenance is recorded.
    This is registered as p2pnet, never dm_count. Old official CLI CUDA
    assumptions are deliberately not inherited by this adapter.
    """
    def __init__(self, model):
        self.model = model.cpu().eval()

    def __call__(self, rgb_frame):
        import torch
        from PIL import Image
        image = Image.fromarray(np.asarray(rgb_frame, dtype=np.uint8), "RGB")
        width, height = image.size
        width, height = width // 128 * 128, height // 128 * 128
        if min(width, height) == 0:
            raise ValueError("P2PNet comparison requires images at least 128x128")
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        tensor = torch.from_numpy(np.asarray(image).copy()).permute(2, 0, 1).float() / 255
        mean = torch.tensor([0.485, 0.456, 0.406])[:, None, None]
        std = torch.tensor([0.229, 0.224, 0.225])[:, None, None]
        with torch.inference_mode():
            output = self.model(((tensor - mean) / std).unsqueeze(0))
            scores = output["pred_logits"].softmax(-1)[0, :, 1].cpu().numpy()
            points = output["pred_points"][0].cpu().numpy()
        if (points.ndim != 2 or points.shape[1] != 2 or scores.ndim != 1 or len(points) != len(scores)
                or not np.isfinite(points).all() or not np.isfinite(scores).all()):
            raise ValueError("P2PNet returned invalid or nonfinite points/confidences")
        normalized = points / np.array([width, height])
        valid = ((normalized >= 0) & (normalized <= 1)).all(axis=1)
        return np.column_stack((normalized[valid], scores[valid]))


def compare_counters(samples: list[dict], dm_counter, p2p_counter, *,
                     dm_provenance: dict, p2p_provenance: dict) -> dict:
    """Both models see the same labeled holdout RGB frames and ROI.

    Required sample keys: id, scenario, frame, roi, manual_count. Frame paths,
    licenses, split and label revision are recorded by the caller's manifest.
    """
    if not samples or len({s["id"] for s in samples}) != len(samples):
        raise ValueError("Nonempty unique held-out frame IDs are required")
    rows, groups = [], defaultdict(list)
    for sample in samples:
        manual = float(sample["manual_count"])
        if not math.isfinite(manual) or manual < 0:
            raise ValueError("Manual visible count must be nonnegative and finite")
        roi = ROI.model_validate(sample["roi"])
        started = time.perf_counter()
        dm_value = density_count(dm_counter(sample["frame"]), roi)
        dm_seconds = time.perf_counter() - started
        started = time.perf_counter()
        p2p_value = point_count(p2p_counter(sample["frame"]), roi)
        p2p_seconds = time.perf_counter() - started
        row = {"id": sample["id"], "scenario": sample["scenario"], "manual_count": manual,
               "dm_count": dm_value, "p2pnet": p2p_value,
               "dm_absolute_error": abs(dm_value - manual),
               "p2p_absolute_error": abs(p2p_value - manual),
               "dm_seconds": dm_seconds, "p2p_seconds": p2p_seconds}
        rows.append(row)
        groups[sample["scenario"]].append(row)
    summary = {name: {"sample_count": len(items),
        "dm_mae": sum(r["dm_absolute_error"] for r in items) / len(items),
        "p2p_mae": sum(r["p2p_absolute_error"] for r in items) / len(items)}
        for name, items in groups.items()}
    return {"metric": "camera_visible_people", "dm_provenance": dm_provenance,
            "p2p_provenance": p2p_provenance, "sample_count": len(samples),
            "scenarios": summary, "frames": rows}
