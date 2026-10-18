"""Verify the repository-provided P2PNet sources, weights and CPU configuration."""
import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "model_bundle/p2pnet")
    parser.add_argument("--config", type=Path, default=ROOT / "config/p2pnet-cpu.example.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import torch

    from intelligent_analysis import health
    from intelligent_analysis.contracts import VideoAnalysisConfig
    from intelligent_analysis.vision.p2p_counter import P2PNetCounter

    config = VideoAnalysisConfig.model_validate_json(args.config.read_bytes())
    output = args.output.resolve()
    if output.is_relative_to(args.bundle.resolve()) or output == args.config.resolve():
        raise ValueError("Output must not overwrite model inputs or configuration")
    torch.set_num_threads(4)
    counter = P2PNetCounter.from_team_bundle(args.bundle)
    counter.validate_config(config.model_dump(mode="json", exclude_none=True))
    predictions = counter(np.zeros((128, 128, 3), dtype=np.uint8))
    assert predictions.ndim == 2 and predictions.shape[1] == 3 and np.isfinite(predictions).all()
    report = {"status": "cpu_model_inference_smoke_passed", "e_version": health()["version"],
        "config": config.model_dump(mode="json", exclude_none=True), "model": counter.provenance,
        "runtime": {"python": platform.python_version(), "torch": torch.__version__, "device": "CPU", "threads": 4},
        "source_and_weights_sha256_verified": True, "synthetic_frame_only": True,
        "actual_video_verified": False, "accuracy_verified": False, "D_HTTP_verified": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "e_version": report["e_version"], "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
