"""Formal P2PNet CPU point counter with explicit weights and source provenance."""
import hashlib
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..contracts import VideoAnalysisConfig
from ..integration import UnsupportedAnalysis
from .benchmark import P2PNetAdapter
from .density_counter import WeightSpec

TEAM_WEIGHTS_VERSION = "leader-SHTechA-50604773"
TEAM_WEIGHTS_SHA256 = "506047732b128ff09efef18e94bfacbe35fcfef300e5e9eeeece259b0488c63f"
TEAM_SOURCE_REVISION = "team-zip-2981dc75"
TEAM_SOURCE_HASHES = {
    "models/__init__.py": "dad01c6bd92f1251ed430ad2e7e305da4581da0c05fbae78a3e0bfa66642b4ab",
    "models/backbone.py": "92b5fc36f79359aa04a80db6dd222e81e77cd44ae7e5a9de55494c49901ff5f0",
    "models/matcher.py": "4426df389f763510d9879b858ddc15f8d6aae935d0812e5b957b281bcf549bc2",
    "models/p2pnet.py": "a3705b70b9253f37babe437fc79f1be8a7a05563ac2f2430a4dfbda3f21546a0",
    "models/vgg_.py": "2e4d08e1b04d549b414fd4fc3034399224f8140cf0d6f628a1a68c92507175e7",
    "util/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "util/misc.py": "0ef59aa8d0a4a77fe7fd8f84f278b6975bcc8bbebea3c4f23121632ccbfe0c52",
}


def build_team_model(bundle_dir: str | Path):
    """Load the pinned, separately supplied sources from D's trusted directory."""
    import importlib
    import sys
    from types import SimpleNamespace

    import torch

    root = Path(bundle_dir).resolve()
    for relative, expected in TEAM_SOURCE_HASHES.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise UnsupportedAnalysis("P2PNet team source missing or SHA256 mismatch")
    if torch.cuda.is_available():
        raise UnsupportedAnalysis("Unmodified team source selects CUDA globally; this CPU runtime is not validated on CUDA hosts")
    for name in ("models", "util"):
        loaded = sys.modules.get(name)
        if loaded is not None and (not getattr(loaded, "__file__", None)
                or not Path(loaded.__file__).resolve().is_relative_to(root)):
            raise UnsupportedAnalysis("Conflicting P2PNet source module is already loaded")
    sys.path.insert(0, str(root))
    try:
        return importlib.import_module("models").build_model(
            SimpleNamespace(backbone="vgg16_bn", row=2, line=2), training=False)
    finally:
        sys.path.pop(0)


class P2PNetCounter:
    """Predict normalized x/y/confidence; the worker counts points inside ROI."""
    def __init__(self, weights: WeightSpec, model_factory: Callable, *, source_revision: str):
        if not source_revision.strip():
            raise ValueError("P2PNet source revision is required")
        self.weights = weights
        self.model_factory = model_factory
        self.source_revision = source_revision
        self._adapter = None

    @classmethod
    def from_team_bundle(cls, bundle_dir: str | Path):
        root = Path(bundle_dir).resolve()
        return cls(WeightSpec(str(root / "SHTechA.pth"), TEAM_WEIGHTS_SHA256, TEAM_WEIGHTS_VERSION),
            lambda: build_team_model(root), source_revision=TEAM_SOURCE_REVISION)

    @property
    def provenance(self):
        return {"count_model": "p2pnet", "weights_version": self.weights.version,
            "weights_sha256": self.weights.sha256, "model_source_revision": self.source_revision,
            "confidence_threshold": 0.5, "inference_engine": "pytorch", "device": "CPU"}

    def validate_config(self, config: dict) -> None:
        parsed = VideoAnalysisConfig.model_validate(config)
        if parsed.inference_engine != "pytorch" or parsed.device != "CPU":
            raise UnsupportedAnalysis("Only P2PNet PyTorch CPU is validated; GPU/OpenVINO are unverified")
        if parsed.weights_version != self.weights.version:
            raise UnsupportedAnalysis("Unknown P2PNet weights_version")
        self._load()

    def _load(self):
        path = Path(self.weights.path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != self.weights.sha256:
            raise UnsupportedAnalysis("P2PNet weights missing or SHA256 mismatch")
        if self._adapter is not None:
            return
        try:
            import torch
            model = self.model_factory().cpu()
            state = torch.load(path, map_location="cpu", weights_only=True)
            state = state.get("model", state.get("state_dict", state))
            model.load_state_dict(state, strict=True)
            self._adapter = P2PNetAdapter(model)
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise UnsupportedAnalysis("P2PNet model/dependencies cannot be loaded") from exc

    def __call__(self, rgb_frame):
        image = np.asarray(rgb_frame)
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 128:
            raise ValueError("P2PNet requires uint8 RGB frames at least 128x128")
        self._load()
        return self._adapter(image)
