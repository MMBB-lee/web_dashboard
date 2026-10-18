"""Explicit D-owned queue/storage ports; E never creates an unregistered job."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class DependencyUnavailable(RuntimeError):
    """D maps this to dependency_not_available (HTTP 503)."""


class UnsupportedAnalysis(ValueError):
    """D maps this to unsupported (HTTP 422)."""


@dataclass(frozen=True)
class ServiceBindings:
    submit_job: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
    load_counts: Callable[[str, str], dict[str, Any]] | None = None
    validate_analysis: Callable[[dict[str, Any]], None] | None = None
    knowledge_index_path: str | None = None
    knowledge_snapshot_loader: Callable[[], dict[str, Any]] | None = None


_bindings = ServiceBindings()


def configure_services(bindings: ServiceBindings) -> None:
    global _bindings
    if bindings.knowledge_index_path and bindings.knowledge_snapshot_loader:
        raise ValueError("Configure either a local index path or a verified snapshot loader")
    _bindings = bindings


def get_bindings() -> ServiceBindings:
    return _bindings


def submit(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    callback = get_bindings().submit_job
    if callback is None:
        raise DependencyUnavailable("D submit_job binding is required; no job was queued")
    result = callback(kind, payload)
    if result.get("status") != "queued" or not result.get("job_id"):
        raise RuntimeError("D submit_job must return a committed queued job with job_id")
    return result
