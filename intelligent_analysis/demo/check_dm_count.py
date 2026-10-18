"""Run official-weight CPU smoke and compare against official architecture."""
import hashlib
import importlib.util
import json
import time
import urllib.request
from pathlib import Path

import numpy as np

from intelligent_analysis.contracts import ROI
from intelligent_analysis.vision.density_counter import DMCountCounter, WeightSpec, density_count

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "cc5f2132e0d1328909f31b6d665b8e0b15c30467"


def main():
    import torch
    from PIL import Image
    torch.set_num_threads(4)
    root = ROOT / "artifacts/dm-count"
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    weight = provenance["files"]["pretrained_models/model_qnrf.pth"]
    spec = WeightSpec(weight["path"], weight["sha256"], "dm-count-qnrf-cc5f2132")
    counter = DMCountCounter(spec)
    roi = ROI(points=[{"x": 0, "y": 0}, {"x": 1, "y": 0},
                      {"x": 1, "y": 1}, {"x": 0, "y": 1}])
    config = {"count_model": "dm_count", "weights_version": spec.version,
              "roi": roi.model_dump(), "inference_engine": "pytorch", "device": "CPU"}
    counter.validate_config(config)
    image = np.asarray(Image.open(root / "1.png").convert("RGB"))
    started = time.perf_counter()
    density = counter(image)
    elapsed = time.perf_counter() - started

    # Download only the inspected official model definition at the pinned commit.
    path = root / "official_models.py"
    data = urllib.request.urlopen(
        f"https://raw.githubusercontent.com/cvlab-stonybrook/DM-Count/{COMMIT}/models.py", timeout=60).read()
    path.write_bytes(data)
    module_spec = importlib.util.spec_from_file_location("official_dm_count", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    reference = module.VGG(module.make_layers(module.cfg["E"]))
    reference.load_state_dict(torch.load(spec.path, map_location="cpu", weights_only=True))
    reference.eval()
    tensor = torch.from_numpy(image.copy()).permute(2, 0, 1).float() / 255
    tensor = ((tensor - torch.tensor([0.485, 0.456, 0.406])[:, None, None]) /
              torch.tensor([0.229, 0.224, 0.225])[:, None, None])
    with torch.inference_mode():
        expected = reference(tensor.unsqueeze(0))[0][0, 0].numpy()
    difference = float(np.max(np.abs(density - expected)))
    assert difference < 1e-6
    report = {"status": "passed", "model": "dm_count", "weights_version": spec.version,
        "weights_sha256": spec.sha256, "official_commit": COMMIT,
        "official_model_source_sha256": hashlib.sha256(data).hexdigest(),
        "torch_version": torch.__version__, "device": "CPU", "inference_engine": "pytorch",
        "image_shape": list(image.shape), "density_shape": list(density.shape),
        "estimated_count": density_count(density, roi), "inference_seconds": elapsed,
        "max_absolute_difference_from_official": difference,
        "accuracy_verified": False, "reason": "Official example has no supplied project ROI ground truth"}
    output = ROOT / "docs/acceptance/DMCount-CPU.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
