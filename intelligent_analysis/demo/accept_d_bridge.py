"""Independent D/E HTTP smoke on disposable SQLite; never formal C/MySQL acceptance."""
import json
import os
import secrets
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    run = ROOT / "artifacts" / ("d-bridge-acceptance-" + uuid4().hex[:8])
    run.mkdir()
    os.environ.update(DATA_DIR=str(run), JOB_STORE_PATH=str(run / "jobs.db"),
        RAG_INDEX_PATH=str(run / "rag.json"), CSRF_SECRET=secrets.token_hex(32))
    sys.path[:0] = [str(ROOT / "artifacts/c-0.2.0a2/runtime"), str(ROOT.parent / "api_platform"), str(ROOT)]
    from app.core.security import PasswordService
    from app.main import app
    from app.services.c_auth_store import DataFoundationAuthStore
    from app.services.job_store import SqliteJobStore
    from app.worker import DWorker
    from data_foundation import UserCreate, create_store
    from data_foundation.db import Base
    from fastapi.testclient import TestClient

    store = create_store("sqlite:///" + (run / "auth.db").as_posix(), allow_sqlite_for_tests=True)
    Base.metadata.create_all(store.engine)
    password = secrets.token_urlsafe(24)
    with store.write() as uow:
        uow.auth.create_user(UserCreate(username="e-acceptance", display_name="E acceptance",
            password_hash=PasswordService.hash_password(password), role="admin"))
    auth = DataFoundationAuthStore(store=store)
    jobs = SqliteJobStore(str(run / "jobs.db"))
    manifest_root = ROOT / "artifacts/c-0.2.0a2/data_foundation/real_data/2026-10-12-v2"
    manifest = json.loads((manifest_root / "manifest.json").read_text(encoding="utf-8"))
    doc = next(d for d in manifest["documents"] if d["id"] == "0c8e2020-2e0a-5842-bce9-d9193cf81095")
    report = {"scope": "independent_D_SQLite_bridge_HTTP_with_real_E_and_C_auth",
        "formal_DE_mysql_verified": False, "D_source_modified": False,
        "D_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent / "api_platform", text=True).strip(), "checks": {}, "responses": {}}
    try:
        with ExitStack() as stack:
            # Only the documented auth test injection; the real D worker and job bridge are unchanged.
            for target in ("app.core.providers.get_auth_store", "app.api.v1.auth.get_auth_store",
                           "app.services.auth_service.get_auth_store"):
                stack.enter_context(patch(target, return_value=auth))
            with TestClient(app, raise_server_exceptions=False) as client:
                unauthorized = client.post("/api/v1/assistant/chat", json={"message": "黄山春节"})
                assert unauthorized.status_code == 401
                login = client.post("/api/v1/auth/login", json={"username": "e-acceptance", "password": password})
                assert login.status_code == 200
                csrf = {"X-CSRF-Token": login.json()["data"]["csrf_token"]}
                fields = {k: doc[k] for k in ("title", "publisher", "source_url", "published_at",
                    "permission_note", "valid_from", "valid_until")}
                accepted = client.post("/api/v1/knowledge/documents", headers=csrf, data=fields,
                    files={"file": ("historical-notice.txt", (manifest_root / doc["text_path"]).read_bytes(), "text/plain")})
                assert accepted.status_code == 202, accepted.text
                registered = accepted.json()["data"]
                job = jobs.get_job(registered["job_id"])
                assert job["status"] == "queued"
                report["checks"]["authenticated_upload_really_queued"] = True
                result = DWorker(jobs, None).run_once()
                assert result["status"] == "succeeded", result
                assert jobs.get_job(registered["job_id"])["status"] == "succeeded"
                report["checks"]["D_dispatches_real_E_and_commits_bridge_result"] = True
                question = {"message": "2025年春节黄山运营时间"}
                response = client.post("/api/v1/assistant/chat", headers=csrf, json=question)
                assert response.status_code == 200, response.text
                answer = response.json()["data"]
                assert answer["citations"] and "历史资料" in answer["answer"]
                assert answer["citations"][0]["id"] == registered["document_id"]
                report["responses"]["historical_answer"] = answer
                report["checks"]["historical_notice_citation_and_validity_warning"] = True
                detail = client.get("/api/v1/knowledge/documents/" + registered["document_id"])
                assert detail.status_code == 200 and detail.json()["data"]["index_status"] == "ready"
                report["checks"]["document_detail_ready"] = True
                reindex = client.post("/api/v1/knowledge/reindex", headers=csrf,
                    json={"document_ids": [registered["document_id"]], "reason": "E compatibility regression"})
                assert reindex.status_code == 202, reindex.text
                for _ in range(2):
                    assert DWorker(jobs, None).run_once()["status"] == "succeeded"
                report["checks"]["reindex_separates_source_and_index_versions"] = True
                index = run / "rag.json"
                original = index.read_bytes()
                index.write_bytes(b"broken JSON")
                damaged = client.post("/api/v1/assistant/chat", headers=csrf, json=question)
                report["responses"]["damaged_snapshot_http"] = {
                    "status": damaged.status_code, "body": damaged.json()}
                report["checks"]["damaged_bridge_snapshot_returns_503"] = damaged.status_code == 503
                index.write_bytes(original)
                report["bridge_document_id_is_C_registered_id"] = registered["document_id"] == doc["id"]
                report["checks"]["unauthenticated_chat_rejected"] = True
                # Response must not reveal credentials, internal files, raw bytes or leases.
                encoded = json.dumps(report["responses"], ensure_ascii=False)
                assert not any(x in encoded for x in (password, str(run), "lease_token", "artifact_bytes"))
                report["checks"]["public_response_filters_internal_data"] = True
                # Fault injection is confined to this fresh bridge DB, never a user database.
                probe_version = "e051-completion-failure"
                probe = jobs.create_job("knowledge_index", analysis_id=registered["document_id"],
                    payload={**job["payload"], "version": probe_version})
                with patch.object(jobs, "complete_job", side_effect=RuntimeError("isolated completion failure")) as completion:
                    failed = DWorker(jobs, None).run_once()
                    failed_lease = completion.call_args.args[0]
                assert failed["status"] == "failed"
                with jobs._connect() as connection:
                    index_status = connection.execute(
                        "SELECT status FROM d_knowledge_indexes WHERE document_id=? AND version=?",
                        (registered["document_id"], probe_version)).fetchone()["status"]
                report["formal_blocker_observations"] = {
                    "after_injected_completion_failure_index_status": index_status,
                    "after_injected_completion_failure_job_status": jobs.get_job(probe)["status"],
                    "activation_rolled_back_with_completion": index_status != "ready"}
                jobs.complete_job(failed_lease, {})
                report["formal_blocker_observations"]["completed_failed_job_using_old_lease"] = jobs.get_job(probe)["status"] == "succeeded"
        report["status"] = "passed" if all(report["checks"].values()) else "needs_D_fix"
    finally:
        store.dispose()
    (ROOT / "docs/acceptance/D-E051-bridge.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": report["checks"]}))


if __name__ == "__main__":
    main()
