"""OpenVINO model loading, device selection, and inference runtime helpers."""

from __future__ import annotations


def is_openvino_available() -> bool:
    try:
        import openvino  # noqa: F401
    except ImportError:
        return False
    return True


def available_devices() -> list[str]:
    from openvino import Core

    return Core().available_devices


def load_compiled_model(model_path: str, device: str = "AUTO"):
    from openvino import Core

    core = Core()
    return core.compile_model(model=model_path, device_name=device)
