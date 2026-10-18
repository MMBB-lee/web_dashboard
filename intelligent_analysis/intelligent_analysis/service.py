"""E services; D owns jobs, authorization, input resolution and persistence."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from typing import Any
from uuid import UUID, uuid4

from .agent.metric_tools import run_metric_query
from .agent.orchestrator import answer_with_index
from .contracts import Citation, CountsResponse, DocumentMetadata, JobAccepted, VideoAnalysisConfig
from .integration import DependencyUnavailable, UnsupportedAnalysis, get_bindings, submit
from .knowledge.chunk import split_text
from .knowledge.index import InMemoryKnowledgeIndex
from .knowledge.parse import read_document
from .short_forecast.baseline import forecast_visible_history

MODULE_VERSION = "0.6.2"
MODEL_VERSION = "visible-damped-holt-0.5.0"
_global_index = InMemoryKnowledgeIndex()


def health() -> dict[str, str]:
    return {"module": "intelligent_analysis", "version": MODULE_VERSION, "status": "ready"}


def answer_message(message: str, conversation_id: UUID | None = None,
                   context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    if "metric_query" in context:
        result = run_metric_query(context["metric_query"], context.get("metric_tool"))
        if not isinstance(result, dict):
            raise DependencyUnavailable("Metric tool must return a result object")
        if result.get("status") == "metric_tool_unavailable":
            if "value" in result or "source_ids" in result:
                raise DependencyUnavailable("Unavailable metric result must omit success fields")
            return {"conversation_id": str(conversation_id or uuid4()),
                    "answer": "指标查询服务尚未接入，无法核实数值。",
                    "status": "metric_tool_unavailable", "citations": [],
                    "used_metric_queries": [], "limitations": []}
        required = {"metric", "value", "source_ids", "period_start", "period_end", "queried_at"}
        if not required.issubset(result) or result["value"] is None or not result["source_ids"]:
            raise DependencyUnavailable("Metric tool result must include time, metric and source provenance")
        value, sources = result["value"], result["source_ids"]
        if (isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal))
                or not isinstance(result["metric"], str) or not result["metric"].strip()
                or not isinstance(sources, list)
                or any(not isinstance(source, str) or not source.strip() for source in sources)):
            raise DependencyUnavailable("Metric tool result has invalid value or source provenance")
        try:
            if not isfinite(float(value)) or float(value) < 0:
                raise ValueError("Metric value must be finite and nonnegative")
            for field in ("period_start", "period_end", "queried_at"):
                if not isinstance(result[field], str) or not result[field].strip():
                    raise ValueError("Metric time must be an ISO date or datetime")
                datetime.fromisoformat(result[field])
        except (ValueError, TypeError, OverflowError) as exc:
            raise DependencyUnavailable("Metric tool result has invalid numeric or time provenance") from exc
        return {"conversation_id": str(conversation_id or uuid4()), "status": "answered",
                "answer": f"{result['metric']}：{result['value']}（{result['period_start']}—{result['period_end']}）。",
                "citations": [Citation(type="metric", id=str(s), title=result["metric"]).model_dump(mode="json")
                              for s in result["source_ids"]],
                "used_metric_queries": [str(context["metric_query"])],
                "metric_result": result, "limitations": []}
    bindings = get_bindings()
    if bindings.knowledge_snapshot_loader is not None:
        verified_index = InMemoryKnowledgeIndex.from_payload(bindings.knowledge_snapshot_loader())
        return answer_with_index(message, verified_index, conversation_id,
            as_of=context.get("as_of", datetime.now(UTC))).model_dump(mode="json")
    path = bindings.knowledge_index_path
    if path:
        try:
            load_knowledge_index(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DependencyUnavailable("Published knowledge index is unavailable or invalid") from exc
    return answer_with_index(message, _global_index, conversation_id,
                             as_of=context.get("as_of", datetime.now(UTC))).model_dump(mode="json")


def index_document(file_path: str, metadata: dict[str, Any], *,
                   index_version: str | None = None) -> dict[str, Any]:
    validated = DocumentMetadata.model_validate(metadata)
    if not metadata.get("document_id"):
        raise ValueError("C document_id is required")
    if not metadata.get("document_version"):
        raise ValueError("C document_version is required")
    if index_version is None:
        index_version = "e-index-" + uuid4().hex
    if not isinstance(index_version, str) or not index_version.strip():
        raise ValueError("Index version must be a nonempty string")
    payload = {"document_id": metadata["document_id"], "file_path": file_path,
               "version": index_version,
               "document_version": metadata["document_version"],
               "metadata": validated.model_dump(mode="json")}
    result = submit("knowledge_index", payload)
    return {"document_id": metadata["document_id"], "index_status": "pending",
            "job_id": str(UUID(str(result["job_id"])))}


def process_document_index(file_path: str, metadata: dict[str, Any],
                           document_id: str | None = None) -> dict[str, Any]:
    document_id = document_id or metadata.get("document_id")
    if not document_id:
        raise ValueError("C document_id is required; filenames are not document identities")
    document_metadata = DocumentMetadata.model_validate(metadata)
    chunks = split_text(read_document(file_path), document_id=document_id)
    if not chunks:
        raise ValueError("Document has no extractable text")
    _global_index.add_document(document_id, document_metadata, chunks)
    return {"document_id": document_id, "index_status": "ready", "chunk_count": len(chunks)}


def reindex_documents(document_ids: list[str], reason: str) -> dict[str, Any]:
    if not 1 <= len(document_ids) <= 100 or not reason.strip():
        raise ValueError("1—100 document IDs and a reason are required")
    result = submit("knowledge_reindex", {"document_ids": document_ids, "reason": reason})
    return {"job_id": str(UUID(str(result["job_id"]))), "pending_count": len(document_ids)}


def save_knowledge_index(path: str) -> None:
    _global_index.save(path)


def load_knowledge_index(path: str) -> None:
    global _global_index
    _global_index = InMemoryKnowledgeIndex.load(path)


def analyze_video(video_id: UUID, config: dict[str, Any], *,
                  analysis_id: UUID | None = None) -> dict[str, Any]:
    """Validate before D queues; D resolves file paths after claiming a lease.

    analysis_id is D's optional stable identity. HTTP clients must not supply
    internal file paths through config; C get_visible_input supplies worker input.
    """
    requested_id = UUID(str(analysis_id)) if analysis_id is not None else None
    validated = VideoAnalysisConfig.model_validate(config)
    checker = get_bindings().validate_analysis
    if checker is None:
        raise UnsupportedAnalysis("No validated P2PNet weights/runtime configured")
    checker(validated.model_dump(mode="json", exclude_none=True))
    payload = {"video_id": str(video_id),
               "config": validated.model_dump(mode="json", exclude_none=True)}
    if requested_id is not None:
        payload["analysis_id"] = str(requested_id)
    result = submit("video_analysis", payload)
    if requested_id is not None and UUID(str(result["analysis_id"])) != requested_id:
        raise RuntimeError("D submit_job changed the requested analysis identity")
    accepted = JobAccepted(job_id=result["job_id"], analysis_id=result["analysis_id"],
                           inference_engine=validated.inference_engine)
    return {**accepted.model_dump(mode="json"), "video_id": str(video_id),
            "count_metric": "camera_visible_people"}


def get_camera_counts(video_id: UUID, analysis_id: UUID, start: str | None = None,
                      end: str | None = None, grain: str = "5min") -> dict[str, Any]:
    if grain not in {"frame", "5min"}:
        raise ValueError("grain must be frame or 5min")
    start_at = _aware(start) if start else None
    end_at = _aware(end) if end else None
    if start_at and end_at and start_at >= end_at:
        raise ValueError("start must precede end")
    reader = get_bindings().load_counts
    if reader is None:
        raise DependencyUnavailable("D load_counts binding is required")
    raw = reader(str(video_id), str(analysis_id))
    if any("entries" in p or "exits" in p for p in raw.get("points", [])):
        raise UnsupportedAnalysis("Legacy entries/exits are not visible people")
    if raw.get("analysis") and raw["analysis"].get("count_model") != "p2pnet":
        raise UnsupportedAnalysis("Stored analysis uses a retired count model; rebuild as P2PNet")
    response = CountsResponse.model_validate(raw)
    if response.video_id != video_id or response.analysis_id != analysis_id:
        raise ValueError("Count result does not belong to requested video/analysis")
    response.points = [p for p in response.points if p.grain == grain
                       and (start_at is None or (p.recorded_at or p.period_start) >= start_at)
                       and (end_at is None or (p.recorded_at or p.period_end) <= end_at)]
    return response.model_dump(mode="json", exclude_none=False)


def forecast_short_term(video_id: UUID, analysis_id: UUID, cutoff_at: str,
                        horizon_minutes: int, step_minutes: int = 5) -> dict[str, Any]:
    if type(horizon_minutes) is not int or horizon_minutes not in (15, 30, 60):
        raise ValueError("horizon_minutes must be 15, 30 or 60")
    if type(step_minutes) is not int or step_minutes != 5:
        raise ValueError("step_minutes must be 5")
    cutoff = _aware(cutoff_at)
    counts = get_camera_counts(video_id, analysis_id, end=cutoff_at, grain="5min")
    if counts["status"] != "ready":
        return {"metric": "camera_visible_people", "status": "data_insufficient",
                "forecast_points": [], "model_version": MODEL_VERSION,
                "reason": "Analysis is not ready"}
    if not counts["recorded_start_at"] or not counts["recorded_end_at"]:
        raise DependencyUnavailable("Recording bounds are required for forecast validation")
    if not _aware(counts["recorded_start_at"]) <= cutoff <= _aware(counts["recorded_end_at"]):
        raise ValueError("cutoff_at must lie within recording bounds")
    history = [(_aware(p["period_end"]), p["value"] if p["quality_status"] == "good" else None)
               for p in counts["points"]]
    return forecast_from_count_history(history, cutoff, horizon_minutes, step_minutes)


def forecast_from_count_history(history: list[tuple[Any, float | None]], cutoff_at: Any,
                                horizon_minutes: int, step_minutes: int = 5) -> dict[str, Any]:
    cutoff = _aware(cutoff_at) if isinstance(cutoff_at, str) else cutoff_at
    return forecast_visible_history(history, cutoff, horizon_minutes, step_minutes)


def _aware(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return timestamp
