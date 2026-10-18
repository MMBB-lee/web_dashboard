"""Actual D HTTP/worker + E/C on fresh SQLite, never the business MySQL.

Infrastructure/auth providers are injected. A separately labelled metric phase
binds C's reference adapter. A distinct phase verifies D's received resolver.
"""
import argparse
import copy
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run = ROOT / "artifacts" / ("d-c021-http-" + uuid4().hex[:8])
    run.mkdir()
    os.environ.update(DATA_DIR=str(run / "assets"), JOB_STORE_PATH=str(run / "unused-bridge.db"),
        RAG_INDEX_PATH=str(run / "rag.json"), DETECTOR_WEIGHTS_PATH=str(run / "unused-weights"),
        COUNTS_STORAGE_PATH=str(run / "unused-counts"), VIDEO_TMP_DIR=str(run / "unused-video"),
        P2PNET_BUNDLE_DIR=str(ROOT / "artifacts/leader-p2pnet-original"),
        DATABASE_URL="sqlite:///" + (run / "store.db").as_posix(),
        D_JOB_STORE_BACKEND="c_store", CSRF_SECRET=secrets.token_hex(32), PYTHONDONTWRITEBYTECODE="1")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT / "artifacts/c-0.2.1a2/runtime"), str(ROOT.parent / "api_platform"), str(ROOT)]
    from app.core.security import PasswordService
    from app.main import app
    from app.services.c_auth_store import DataFoundationAuthStore
    from app.services.c_store import CStore
    from app.worker import DWorker
    from data_foundation import JobCreate, LeaseLost, UserCreate, create_store
    from data_foundation.db import Base
    from data_foundation.db.models import Job
    from data_foundation.ingestion.real_dataset import import_real_dataset
    from data_foundation.seed import seed_catalog
    from fastapi.testclient import TestClient

    from intelligent_analysis import health

    store = create_store(os.environ["DATABASE_URL"], data_root=run / "assets", allow_sqlite_for_tests=True)
    Base.metadata.create_all(store.engine)
    seed_catalog(store)
    password = secrets.token_urlsafe(24)
    with store.write() as uow:
        admin = uow.auth.create_user(UserCreate(username="e-isolated-admin", display_name="E isolated test",
            password_hash=PasswordService.hash_password(password), role="admin"))
    auth, adapter = DataFoundationAuthStore(store=store), CStore(store)
    source = ROOT / "artifacts/c-0.2.1a2/handoff/data_foundation/real_data/2026-10-12-v2"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    import_real_dataset(store, source / "manifest.json")
    document = next(d for d in manifest["documents"] if d["id"] == "0c8e2020-2e0a-5842-bce9-d9193cf81095")
    report = {"e_version": health()["version"], "scope": "real_D_HTTP_worker_E_C_on_disposable_SQLite", "D_source_modified": False,
        "MySQL_verified": False, "real_video_verified": False, "human_semantics_verified": False,
        "D_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent / "api_platform", text=True).strip(), "checks": {}, "responses": {},
        "D_blockers": [], "run_relative_to_e": run.relative_to(ROOT).as_posix()}
    try:
        with ExitStack() as stack:
            for target in ("app.core.providers.get_auth_store", "app.api.v1.auth.get_auth_store",
                           "app.services.auth_service.get_auth_store"):
                stack.enter_context(patch(target, return_value=auth))
            for module in ("app.core.providers", "app.services.e_bridge", "app.api.v1.knowledge",
                           "app.api.v1.jobs", "app.api.v1.videos"):
                for name in ("get_c_store", "get_job_store"):
                    if hasattr(sys.modules[module], name):
                        stack.enter_context(patch(module + "." + name, return_value=adapter))
            with TestClient(app, raise_server_exceptions=False) as client:
                question = {"message": "2025年春节黄山运营时间"}
                assert client.post("/api/v1/assistant/chat", json=question).status_code == 401
                login = client.post("/api/v1/auth/login", json={"username": "e-isolated-admin", "password": password})
                assert login.status_code == 200, login.text
                headers = {"X-CSRF-Token": login.json()["data"]["csrf_token"]}
                fields = {k: document[k] for k in ("title", "publisher", "source_url", "published_at",
                    "permission_note", "valid_from", "valid_until")}
                response = client.post("/api/v1/knowledge/documents", headers=headers, data=fields,
                    files={"file": ("notice.txt", (source / document["text_path"]).read_bytes(), "text/plain")})
                assert response.status_code == 202, response.text
                accepted = response.json()["data"]
                job = adapter.get_job(accepted["job_id"])
                assert job["status"] == "queued"
                with store.read() as uow:
                    payload = uow.session.get(Job, accepted["job_id"]).payload
                assert payload["version"] != payload["document_version"]
                assert DWorker(adapter).run_once()["status"] == "succeeded"
                result = client.post("/api/v1/assistant/chat", headers=headers, json=question)
                assert result.status_code == 200, result.text
                answer = result.json()["data"]
                assert answer["citations"] and "历史资料" in answer["answer"]
                assert answer["citations"][0]["id"] == accepted["document_id"]
                report["responses"]["knowledge_answer"] = answer
                report["checks"].update(authenticated_real_queue_worker_and_answer=True,
                                        separate_source_and_index_versions=True)
                rebuild = client.post("/api/v1/knowledge/reindex", headers=headers,
                    json={"document_ids": [accepted["document_id"]], "reason": "E independent receiving"})
                assert rebuild.status_code == 202, rebuild.text
                for _ in range(2):
                    assert DWorker(adapter).run_once()["status"] == "succeeded"
                assert client.post("/api/v1/assistant/chat", headers=headers, json=question).status_code == 200
                numeric = {"message": "机场2025年1月吞吐量", "context": {
                    "metric": "airport_throughput", "entity_ids": [str(uuid4())], "grain": "month",
                    "period_start": "2025-01-01T00:00:00+08:00", "period_end": "2025-02-01T00:00:00+08:00"}}
                insufficient = client.post("/api/v1/assistant/chat", headers=headers, json=numeric)
                assert insufficient.status_code == 200, insufficient.text
                unavailable = insufficient.json()["data"]
                assert unavailable["status"] == "metric_tool_unavailable" and not unavailable["citations"]
                report["responses"]["D_metric_unavailable_HTTP"] = unavailable
                report["checks"]["D_real_metric_unavailable_does_not_use_documents"] = True
                # Separate contract probe: actual C observations + reference tool,
                # actual D route/E serialization; the real D resolver is tested separately.
                from importlib.util import module_from_spec, spec_from_file_location

                from sqlalchemy import text
                adapter_path = source.parents[1] / "examples/c_readonly_metric_tool.py"
                spec = spec_from_file_location("c_http_reference", adapter_path)
                reference_module = module_from_spec(spec)
                spec.loader.exec_module(reference_module)
                with store.read() as uow:
                    airport = uow.session.execute(text("SELECT id FROM entities WHERE name = :name"),
                        {"name": "合肥新桥机场"}).scalar_one()
                metric_query = {**numeric, "context": {**numeric["context"], "entity_ids": [airport]}}
                reference_tool = reference_module.build_metric_tool(store, authorized_entity_ids=[airport])
                with patch("app.services.metrics_query.metric_tool", reference_tool):
                    metric_http = client.post("/api/v1/assistant/chat", headers=headers, json=metric_query)
                assert metric_http.status_code == 200, metric_http.text
                reference_answer = metric_http.json()["data"]
                assert float(reference_answer["metric_result"]["value"]) == 1111644
                assert reference_answer["citations"] and reference_answer["used_metric_queries"]
                report["responses"]["C_reference_tool_via_D_HTTP_not_D_resolver"] = reference_answer
                report["checks"]["C_reference_metric_D_E_HTTP_shape"] = True
                # Probe the received D resolver without replacing its implementation.
                resolver_samples = {}
                for label, start, end in (
                    ("date_closed", "2025-01-01", "2025-01-31"),
                    ("date_half_open", "2025-01-01", "2025-02-01"),
                    ("UTC_equivalent", "2024-12-31T16:00:00Z", "2025-01-31T16:00:00Z"),
                    ("partial_month", "2025-01-02", "2025-01-30"),
                ):
                    query = {**metric_query, "context": {**metric_query["context"],
                        "period_start": start, "period_end": end}}
                    received = client.post("/api/v1/assistant/chat", headers=headers, json=query)
                    assert received.status_code == 200, received.text
                    data = received.json()["data"]
                    resolver_samples[label] = data
                    if label == "partial_month":
                        assert data["status"] == "metric_tool_unavailable" and not data["citations"]
                    else:
                        assert data["status"] == "answered" and data["metric_result"]["value"] == 1111644
                        assert data["metric_result"]["source_ids"] and data["used_metric_queries"]
                report["responses"]["D_resolver_samples"] = resolver_samples
                report["D_resolver_success_verified"] = True
                report["checks"]["D_resolver_dates_UTC_and_partial_period"] = True
                valid_metric = resolver_samples["date_closed"]["metric_result"]
                for mutation in ({"value": float("nan")}, {"value": True},
                                 {"source_ids": "invalid-string"}, {"period_start": None}):
                    with patch("app.services.metrics_query.metric_tool", return_value={**valid_metric, **mutation}):
                        malformed = client.post("/api/v1/assistant/chat", headers=headers, json=metric_query)
                    assert malformed.status_code == 503, malformed.text
                report["checks"]["malformed_metric_success_HTTP_503_not_500"] = True
                injection = {**question, "context": {"metric_tool": "client-cannot-inject"}}
                assert client.post("/api/v1/assistant/chat", headers=headers, json=injection).status_code == 400
                assert client.post("/api/v1/assistant/chat", json=question).status_code == 403
                report["checks"].update(client_tool_injection_rejected=True, missing_csrf_rejected=True)
                report["checks"]["reindex_parent_child_and_answer"] = True
                # Fail after the real C completion write, inside D's combined UoW.
                from data_foundation.repositories import JobRepository
                failed_version = "e-completion-probe-" + uuid4().hex
                probe = adapter.create_job("knowledge_index", {**payload, "version": failed_version})
                with store.read() as uow:
                    active_before = uow.knowledge.get_document(accepted["document_id"])["active_index_version"]
                original_complete = JobRepository.complete_job

                def fail_after_completion(repository, lease, refs, now):
                    completed = original_complete(repository, lease, refs, now)
                    if lease.job_id == probe:
                        raise RuntimeError("ISOLATED AFTER COMPLETION WRITE")
                    return completed

                with patch.object(adapter, "activate_and_complete", wraps=adapter.activate_and_complete) as publish:
                    with patch.object(JobRepository, "complete_job", fail_after_completion):
                        failed = DWorker(adapter).run_once()
                    old_publish = publish.call_args.kwargs
                assert failed["status"] == "failed"
                with store.read() as uow:
                    assert uow.knowledge.get_document(accepted["document_id"])["active_index_version"] == active_before
                    assert uow.jobs.get_job(probe)["status"] == "failed"
                report["checks"]["D_activate_complete_roll_back_after_completion_write"] = True
                with store.write() as uow:
                    uow.jobs.retry_failed(probe, "ISOLATED RETRY PROBE", admin["id"], datetime.now(UTC))
                try:
                    adapter.activate_and_complete(**old_publish)
                except LeaseLost:
                    report["checks"]["D_old_lease_cannot_publish_after_retry"] = True
                else:
                    raise AssertionError("D adapter allowed an old lease to publish")
                assert DWorker(adapter).run_once()["status"] == "succeeded"
                assert client.post("/api/v1/assistant/chat", headers=headers, json=question).status_code == 200
                report["checks"]["D_retry_new_attempt_publishes_and_answers"] = True
                with store.read() as uow:
                    reference = uow.knowledge.get_index_artifact_ref(accepted["document_id"])
                path = store.data_root / reference["relative_path"]
                original = path.read_bytes()
                try:
                    path.write_bytes(b"ISOLATED DAMAGED ARTIFACT")
                    damaged = client.post("/api/v1/assistant/chat", headers=headers, json=question)
                    assert damaged.status_code == 503, damaged.text
                    report["checks"]["damaged_artifact_503_no_stale_answer"] = True
                finally:
                    path.write_bytes(original)
                # Persist a correct-hash semantic mutation only in this fresh DB.
                mutated = copy.deepcopy(json.loads(original))
                mutated["documents"][accepted["document_id"]]["chunks"][0]["text"] += " SYNTHETIC TAMPER"
                content = json.dumps(mutated, ensure_ascii=False).encode()
                with store.read() as uow:
                    row = uow.knowledge.get_document(accepted["document_id"])
                altered = store.persist_file(source_id=row["source_id"], original_name="altered.json", stream=io.BytesIO(content))
                from data_foundation.db.models import KnowledgeIndex
                with store.write() as uow:
                    index = uow.session.get(KnowledgeIndex, reference["index_id"])
                    index.artifact_path, index.artifact_sha256 = altered["relative_path"], altered["sha256"]
                mismatch = client.post("/api/v1/assistant/chat", headers=headers, json=question)
                assert mismatch.status_code == 503, mismatch.text
                report["checks"]["valid_hash_wrong_text_HTTP_503"] = True
                with store.write() as uow:
                    index = uow.session.get(KnowledgeIndex, reference["index_id"])
                    index.artifact_path, index.artifact_sha256 = reference["relative_path"], hashlib.sha256(original).hexdigest()
                assert client.post("/api/v1/assistant/chat", headers=headers, json=question).status_code == 200
                # Check mixed-queue ownership; never submit to any shared queue.
                with store.write() as uow:
                    ingestion = uow.jobs.enqueue_or_get(JobCreate("ingestion", uuid4().hex,
                        {"source_ids": [row["source_id"]], "dry_run": True}, "SYNTHETIC ISOLATED QUEUE PROBE"), datetime.now(UTC))
                unsupported = DWorker(adapter).run_once()
                if unsupported is not None and unsupported["status"] == "failed":
                    report["D_blockers"].append({"code": "D_claims_C_ingestion",
                        "job_status": adapter.get_job(ingestion["id"])["status"], "worker_result": unsupported})
                else:
                    assert unsupported is None
                    assert adapter.get_job(ingestion["id"])["status"] == "queued"
                    report["checks"]["D_does_not_claim_C_ingestion"] = True
                report["checks"]["unauthenticated_chat_401"] = True
                encoded = json.dumps(report["responses"], ensure_ascii=False)
                assert not any(value in encoded for value in (password, str(run), "artifact_bytes", "lease_token"))
                report["checks"]["response_hides_internal_data"] = True
                assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
                assert client.post("/api/v1/assistant/chat", headers=headers, json=question).status_code == 401
                report["checks"]["logout_revokes_session"] = True
        report["status"] = "passed_scoped_checks_with_D_blockers" if report["D_blockers"] else "passed_scoped_checks"
    finally:
        store.dispose()
    (args.output or ROOT / "docs/acceptance/E061-D-C021a2-HTTP.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": report["checks"], "D_blockers": report["D_blockers"]}))


if __name__ == "__main__":
    main()
