from datetime import UTC, datetime
from uuid import uuid4

import pytest

from intelligent_analysis import ServiceBindings, configure_services
from intelligent_analysis.integration import DependencyUnavailable, UnsupportedAnalysis
from intelligent_analysis.service import (
    analyze_video,
    answer_message,
    index_document,
    process_document_index,
    reindex_documents,
)

METADATA = {"title": "公告", "publisher": "测试", "permission_note": "测试授权"}


def test_no_phantom_pending_jobs(tmp_path, config):
    metadata = {**METADATA, "document_id": str(uuid4()), "document_version": "v1"}
    with pytest.raises(DependencyUnavailable):
        index_document(str(tmp_path / "notice.md"), metadata)
    with pytest.raises(DependencyUnavailable):
        reindex_documents([metadata["document_id"]], "更新")
    with pytest.raises(UnsupportedAnalysis):
        analyze_video(uuid4(), config)


def test_real_submit_ids_are_preserved(config):
    job, analysis, video = uuid4(), uuid4(), uuid4()
    calls = []

    def submit(kind, payload):
        calls.append((kind, payload))
        return {"job_id": str(job), "analysis_id": str(analysis), "status": "queued"}

    configure_services(ServiceBindings(submit_job=submit, validate_analysis=lambda c: None))
    result = analyze_video(video, config)
    assert result["job_id"] == str(job)
    assert result["analysis_id"] == str(analysis)
    assert result["count_metric"] == "camera_visible_people"
    assert calls[0][0] == "video_analysis"


def test_source_document_version_survives_metadata_validation(tmp_path):
    calls = []
    configure_services(ServiceBindings(submit_job=lambda kind, payload:
        calls.append(payload) or {"job_id": str(uuid4()), "status": "queued"}))
    metadata = {**METADATA, "document_id": str(uuid4()), "document_version": "source-v1"}
    index_document(str(tmp_path / "source.txt"), metadata)
    assert calls[0]["document_version"] == "source-v1"
    assert calls[0]["version"] != "source-v1"


def test_independent_index_versions_reach_worker(tmp_path):
    from intelligent_analysis.worker import process_knowledge_job

    path = tmp_path / "source.md"
    path.write_text("黄山历史公告，仅用于测试。", encoding="utf-8")
    calls = []
    configure_services(ServiceBindings(submit_job=lambda kind, payload:
        calls.append(payload) or {"job_id": str(uuid4()), "status": "queued"}))
    metadata = {**METADATA, "document_id": str(uuid4()), "document_version": "sha256:source"}
    index_document(str(path), metadata, index_version="v2")
    prepared = process_knowledge_job(calls[0])
    assert prepared["document_version"] == "sha256:source"
    assert prepared["index_version"] == "v2"
    index_document(str(path), metadata)
    index_document(str(path), metadata)
    assert calls[1]["version"] != calls[2]["version"]


@pytest.mark.parametrize("version", ["", "  ", 4])
def test_bad_index_version_does_not_queue(tmp_path, version):
    calls = []
    configure_services(ServiceBindings(submit_job=lambda *args: calls.append(args)))
    with pytest.raises(ValueError, match="Index version"):
        index_document(str(tmp_path / "source.md"),
            {**METADATA, "document_id": str(uuid4()), "document_version": "source-v1"},
            index_version=version)
    assert calls == []


def test_d_assigned_analysis_id_and_validation_order(config):
    analysis, video, job = uuid4(), uuid4(), uuid4()
    calls = []

    def checker(value):
        calls.append("validate")
        if value["device"] != "CPU":
            raise UnsupportedAnalysis("CPU only")

    def submit(kind, payload):
        calls.append("submit")
        assert payload["analysis_id"] == str(analysis)
        assert "file_path" not in payload
        return {"job_id": str(job), "analysis_id": str(analysis), "status": "queued"}

    configure_services(ServiceBindings(submit_job=submit, validate_analysis=checker))
    assert analyze_video(video, config, analysis_id=analysis)["analysis_id"] == str(analysis)
    assert calls == ["validate", "submit"]
    calls.clear()
    with pytest.raises(UnsupportedAnalysis):
        analyze_video(video, {**config, "device": "GPU"}, analysis_id=analysis)
    assert calls == ["validate"]


def test_submitting_different_analysis_identity_is_rejected(config):
    configure_services(ServiceBindings(submit_job=lambda *args: {
        "job_id": str(uuid4()), "analysis_id": str(uuid4()), "status": "queued"},
        validate_analysis=lambda config: None))
    with pytest.raises(RuntimeError, match="identity"):
        analyze_video(uuid4(), config, analysis_id=uuid4())


def test_public_config_cannot_contain_internal_file_path(config):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        analyze_video(uuid4(), {**config, "file_path": "internal/video.mp4"})


def test_unavailable_metric_without_success_fields_does_not_use_knowledge():
    result = answer_message("当前人数多少", context={"metric_query": {"metric": "camera_visible_people"},
        "metric_tool": lambda q: {"status": "metric_tool_unavailable", "reason": "no_data"}})
    assert result["status"] == "metric_tool_unavailable"
    assert result["citations"] == [] and result["used_metric_queries"] == []
    assert "metric_result" not in result


def test_document_identity_and_expiration(tmp_path):
    path = tmp_path / "misleading-filename.md"
    path.write_text("黄山春节开放时间六点。", encoding="utf-8")
    with pytest.raises(ValueError, match="document_id"):
        process_document_index(str(path), METADATA)
    metadata = {**METADATA, "document_id": "c-id", "valid_until": "2025-02-01T00:00:00Z"}
    process_document_index(str(path), metadata)
    answer = answer_message("黄山春节开放", context={"as_of": datetime(2026, 10, 14, tzinfo=UTC)})
    assert answer["citations"][0]["id"] == "c-id"
    assert "历史资料" in answer["answer"]


def test_numeric_tool_requires_provenance():
    query = {"entity_id": "airport", "metric": "airport_passengers"}
    assert answer_message("多少人", context={"metric_query": query})["status"] == "metric_tool_unavailable"
    with pytest.raises(DependencyUnavailable, match="provenance"):
        answer_message("多少人", context={"metric_query": query, "metric_tool": lambda q: {"value": 10}})
    data = {"metric": "airport_passengers", "value": 10, "source_ids": ["official"],
            "period_start": "2025-01-01", "period_end": "2025-02-01", "queried_at": "2026-10-14"}
    answer = answer_message("多少人", context={"metric_query": query, "metric_tool": lambda q: data})
    assert answer["metric_result"] == data
    assert answer["citations"][0]["type"] == "metric"
    assert answer["citations"][0]["url"] is None
    assert answer["citations"][0]["date"] is None


@pytest.mark.parametrize("field", ["value", "source_ids"])
def test_unavailable_metric_cannot_include_success_fields(field):
    with pytest.raises(DependencyUnavailable, match="omit success"):
        answer_message("人数", context={"metric_query": {}, "metric_tool": lambda q: {
            "status": "metric_tool_unavailable", field: None}})


@pytest.mark.parametrize("field", ["metric", "value", "source_ids", "period_start", "period_end", "queried_at"])
def test_partial_metric_success_is_dependency_failure(field):
    result = {"metric": "airport_throughput", "value": "1111644", "source_ids": ["official"],
              "period_start": "2025-01-01", "period_end": "2025-02-01", "queried_at": "2026-10-17"}
    del result[field]
    with pytest.raises(DependencyUnavailable):
        answer_message("人数", context={"metric_query": {}, "metric_tool": lambda q: result})


@pytest.mark.parametrize("change", [
    {"value": float("nan")}, {"value": float("inf")}, {"value": float("-inf")},
    {"value": True}, {"value": -1}, {"value": "not-a-number"},
    {"source_ids": "official"}, {"source_ids": [""]}, {"source_ids": [None]},
    {"metric": " "}, {"period_start": None}, {"period_end": "unknown"},
    {"queried_at": " "},
])
def test_malformed_metric_success_never_becomes_an_answer(change):
    result = {"metric": "airport_throughput", "value": 1111644, "source_ids": ["official"],
              "period_start": "2025-01-01", "period_end": "2025-01-31",
              "queried_at": "2026-10-18T09:00:00+08:00", **change}
    with pytest.raises(DependencyUnavailable):
        answer_message("人数", context={"metric_query": {}, "metric_tool": lambda q: result})


@pytest.mark.parametrize("value", [0, 0.5, "1111644"])
def test_numeric_string_and_zero_are_preserved_with_valid_provenance(value):
    result = {"metric": "airport_throughput", "value": value, "source_ids": ["official"],
              "period_start": "2025-01-01", "period_end": "2025-01-31",
              "queried_at": "2026-10-18T09:00:00+08:00"}
    answer = answer_message("人数", context={"metric_query": {}, "metric_tool": lambda q: result})
    assert answer["metric_result"] == result
    assert len(answer["citations"]) == 1
