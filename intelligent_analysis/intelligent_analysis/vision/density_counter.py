"""DM-Count CPU inference, fixed polygon ROI and full-window medians."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

import numpy as np

from ..contracts import ROI, CountPoint, LegacyDMCountConfig, VideoAnalysisConfig
from ..integration import UnsupportedAnalysis


def roi_mask(shape: tuple[int, int], roi: ROI) -> np.ndarray:
    """Pixel-center ray casting in normalized coordinates (no image resizing)."""
    height, width = shape
    y, x = np.mgrid[:height, :width]
    x, y = (x + 0.5) / width, (y + 0.5) / height
    inside = np.zeros(shape, dtype=bool)
    for a, b in zip(roi.points, roi.points[1:] + roi.points[:1]):
        if a.y != b.y:
            crossing = ((a.y > y) != (b.y > y)) & (
                x < (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x)
            inside ^= crossing
    return inside


def density_count(density: np.ndarray, roi: ROI) -> float:
    density = np.asarray(density, dtype=float)
    if density.ndim != 2 or not density.size or not np.isfinite(density).all() or (density < 0).any():
        raise ValueError("Density must be a finite nonnegative 2D map")
    return float(density[roi_mask(density.shape, roi)].sum())


@dataclass(frozen=True)
class WeightSpec:
    path: str
    sha256: str
    version: str


class DMCountCounter:
    """Load official DM-Count state_dict lazily; no ImageNet network download.

    Architecture follows cvlab-stonybrook/DM-Count models.py. CPU is the only
    enabled runtime until separately measured configurations are registered.
    """
    def __init__(self, weights: WeightSpec):
        self.weights = weights
        self._model = None

    def validate_config(self, config: dict) -> None:
        parsed = LegacyDMCountConfig.model_validate(config)
        if parsed.inference_engine != "pytorch" or parsed.device != "CPU":
            raise UnsupportedAnalysis("Only PyTorch CPU is enabled; other runtimes are unverified")
        if parsed.weights_version != self.weights.version:
            raise UnsupportedAnalysis("Unknown DM-Count weights_version")
        path = Path(self.weights.path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != self.weights.sha256:
            raise UnsupportedAnalysis("DM-Count weights missing or SHA256 mismatch")
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise UnsupportedAnalysis("PyTorch is not installed") from exc
        self._load()

    def _load(self):
        if self._model is not None:
            return
        path = Path(self.weights.path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != self.weights.sha256:
            raise UnsupportedAnalysis("DM-Count weights missing or SHA256 mismatch")
        import torch
        from torch import nn

        class DMCount(nn.Module):
            def __init__(self):
                super().__init__()
                layers, channels = [], 3
                for size in [64, 64, "M", 128, 128, "M", 256, 256, 256, 256,
                             "M", 512, 512, 512, 512, "M", 512, 512, 512, 512]:
                    if size == "M":
                        layers.append(nn.MaxPool2d(2, 2))
                    else:
                        layers.extend([nn.Conv2d(channels, size, 3, padding=1), nn.ReLU(True)])
                        channels = size
                self.features = nn.Sequential(*layers)
                self.reg_layer = nn.Sequential(nn.Conv2d(512, 256, 3, padding=1), nn.ReLU(True),
                                               nn.Conv2d(256, 128, 3, padding=1), nn.ReLU(True))
                self.density_layer = nn.Sequential(nn.Conv2d(128, 1, 1), nn.ReLU())

            def forward(self, image):
                features = self.features(image)
                features = nn.functional.interpolate(features, scale_factor=2,
                                                     mode="bilinear", align_corners=True)
                return self.density_layer(self.reg_layer(features))

        model = DMCount()
        state = torch.load(self.weights.path, map_location="cpu", weights_only=True)
        if "state_dict" in state:
            state = state["state_dict"]
        model.load_state_dict(state, strict=True)
        self._model = model.eval()

    def __call__(self, rgb_frame: np.ndarray) -> np.ndarray:
        import torch
        self._load()
        image = np.asarray(rgb_frame)
        if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 16:
            raise ValueError("Expected RGB image HxWx3 with dimensions at least 16")
        if image.dtype != np.uint8:
            raise ValueError("Expected uint8 RGB pixels")
        tensor = torch.from_numpy(image.copy()).permute(2, 0, 1).float() / 255
        mean = torch.tensor([0.485, 0.456, 0.406])[:, None, None]
        std = torch.tensor([0.229, 0.224, 0.225])[:, None, None]
        with torch.inference_mode():
            return self._model(((tensor - mean) / std).unsqueeze(0))[0, 0].numpy()


def process_visible_frames(frames: Iterable, config: VideoAnalysisConfig | LegacyDMCountConfig,
                           counter: Callable, recorded_start: datetime,
                           recorded_end: datetime, source_ids: list[str]) -> list[dict]:
    if recorded_start.utcoffset() is None or recorded_end.utcoffset() is None:
        raise ValueError("Recording timestamps must be timezone-aware")
    if recorded_end <= recorded_start or not source_ids:
        raise ValueError("Recording duration and source provenance are required")
    interval = config.sample_interval_seconds
    duration = (recorded_end - recorded_start).total_seconds()
    by_slot: dict[int, CountPoint] = {}
    previous = -math.inf
    for frame in frames:
        seconds = frame.timestamp_seconds
        if not math.isfinite(seconds) or seconds <= previous or seconds < 0:
            raise ValueError("Frame timestamps must be finite and strictly increasing")
        previous = seconds
        if seconds >= duration:
            break
        slot = int(seconds // interval)
        # Avoid filling earlier missing samples with a later frame.
        if slot in by_slot or seconds - slot * interval > min(1.0, interval / 2):
            continue
        timestamp = recorded_start + timedelta(seconds=seconds)
        if config.count_model == "p2pnet":
            from .benchmark import point_count
            value = point_count(counter(frame.frame), config.roi, config.confidence_threshold)
        else:
            value = density_count(counter(frame.frame), config.roi)
        by_slot[slot] = CountPoint(grain="frame", value=value, recorded_at=timestamp,
                                   quality_status="good", source_ids=source_ids)
    points = list(by_slot.values())
    for window in range(int(duration // 300)):
        start = recorded_start + timedelta(seconds=window * 300)
        end = start + timedelta(minutes=5)
        samples = [p for p in points if start <= p.recorded_at < end]
        expected = sum(1 for s in range(0, math.ceil(duration), interval)
                       if window * 300 <= s < (window + 1) * 300)
        quality = "good" if len(samples) == expected and expected else "degraded"
        if not samples:
            quality = "unavailable"
        points_window = CountPoint(grain="5min", period_start=start, period_end=end,
                                    value=median(p.value for p in samples) if samples else None,
                                    sample_count=len(samples), aggregation_rule="median",
                                    quality_status=quality, source_ids=source_ids)
        by_slot[-window - 1] = points_window
    return [p.model_dump(mode="json") for p in by_slot.values()]
