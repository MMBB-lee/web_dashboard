"""Real C-store failure checks used only by E's disposable acceptance runner."""
import copy
import hashlib
import io
import json
from datetime import UTC, datetime


def verify_failures(store, manifest, run_root, ids, manifest_root):
    from data_foundation import Conflict, JobCreate, JobLease, LeaseLost, UserCreate

    from intelligent_analysis import answer_message
    from intelligent_analysis.integration import DependencyUnavailable
    from intelligent_analysis.knowledge.artifacts import load_verified_knowledge_snapshot
    from intelligent_analysis.worker import process_knowledge_job
    from intelligent_analysis.worker.knowledge_worker import EMBEDDING_MODEL_VERSION, PARSER_VERSION

    doc = next(d for d in manifest["documents"] if d["id"] == "0c8e2020-2e0a-5842-bce9-d9193cf81095")
    doc_id = doc["id"]
    with store.read() as uow:
        original_ref = uow.knowledge.get_index_artifact_ref(doc_id)
    path = run_root / "assets" / original_ref["relative_path"]
    original = path.read_bytes()
    checks = {}

    def rejects_answer():
        try:
            answer_message("2025年春节黄山运营时间")
        except DependencyUnavailable:
            return True
        raise AssertionError("Damaged knowledge was answered using stale data")

    try:
        path.write_bytes(b"damaged-artifact")
        checks["damaged_file_blocks_answer_without_stale_fallback"] = rejects_answer()
        with store.read() as uow:
            assert uow.knowledge.get_chunks(doc_id)["status"] == "ready"
        checks["damage_does_not_delete_committed_chunks"] = True
    finally:
        path.write_bytes(original)
    missing = path.with_name(path.name + ".missing")
    try:
        path.rename(missing)
        checks["missing_file_blocks_answer"] = rejects_answer()
    finally:
        missing.rename(path)

    def prepare(version):
        payload = {"document_id": doc_id, "version": version,
                   "file_path": str(manifest_root / doc["text_path"]), "metadata": doc}
        prepared = process_knowledge_job(payload)
        with store.write() as uow:
            job = uow.jobs.enqueue_or_get(JobCreate("knowledge_index", version, payload,
                                                   "E isolated failure acceptance"), datetime.now(UTC))
            claimed = uow.jobs.claim_job("e-failure-check", datetime.now(UTC), job_id=job["id"])
        lease = JobLease(**claimed["lease"])
        with store.write() as uow:
            index = uow.knowledge.begin_index(document_id=doc_id, version=version, job_id=job["id"],
                parser_version=PARSER_VERSION, embedding_model_version=EMBEDDING_MODEL_VERSION, lease=lease)
            uow.knowledge.write_chunks(index["id"], prepared["chunks"], lease)
        staged = store.persist_file(source_id=doc["source_id"], original_name="index.json",
                                    stream=io.BytesIO(prepared["artifact_bytes"]))
        return prepared, lease, staged, index

    def activate(uow, prepared, lease, staged, digest=None, fail_completion=False):
        uow.knowledge.activate_index(document_id=doc_id, version=prepared["version"],
            artifact_path=staged["relative_path"], artifact_sha256=digest or staged["sha256"],
            expected_artifact_byte_size=staged["byte_size"],
            expected_chunk_count=prepared["chunk_count"], lease=lease)
        if fail_completion:
            raise RuntimeError("injected completion failure")
        uow.jobs.complete_job(lease, {"document_id": doc_id}, datetime.now(UTC))

    prepared, lease, staged, index = prepare("e020-failure-retry")
    try:
        with store.write() as uow:
            activate(uow, prepared, lease, staged, "0" * 64)
    except Conflict:
        checks["bad_new_digest_rejected"] = True
    else:
        raise AssertionError("C accepted a bad digest")
    with store.read() as uow:
        assert uow.knowledge.get_index_artifact_ref(doc_id)["version"] == original_ref["version"]
        assert uow.jobs.get_job(lease.job_id)["status"] == "running"
    checks["bad_publish_keeps_old_active_version_and_job_uncompleted"] = True
    try:
        with store.write() as uow:
            activate(uow, prepared, lease, staged)
            raise RuntimeError("injected transaction rollback")
    except RuntimeError as exc:
        assert str(exc) == "injected transaction rollback"
    with store.read() as uow:
        assert uow.knowledge.get_index_artifact_ref(doc_id)["version"] == original_ref["version"]
        assert uow.jobs.get_job(lease.job_id)["status"] == "running"
    checks["activation_and_job_completion_rollback_together"] = True
    try:
        with store.write() as uow:
            activate(uow, prepared, lease, staged, fail_completion=True)
    except RuntimeError as exc:
        assert str(exc) == "injected completion failure"
    with store.read() as uow:
        assert uow.knowledge.get_index_artifact_ref(doc_id)["version"] == original_ref["version"]
        assert uow.jobs.get_job(lease.job_id)["status"] == "running"
    checks["completion_failure_rolls_back_activation"] = True
    with store.write() as uow:
        admin = uow.auth.create_user(UserCreate(username="e-acceptance-admin", display_name="E acceptance",
                                               role="admin", password_hash="test-only-no-login"))
        uow.jobs.fail_job(lease, "synthetic interruption", datetime.now(UTC))
        uow.jobs.retry_failed(lease.job_id, "E retry regression", admin["id"], datetime.now(UTC))
    with store.write() as uow:
        claimed = uow.jobs.claim_job("e-retry", datetime.now(UTC), job_id=lease.job_id)
    new_lease = JobLease(**claimed["lease"])
    try:
        with store.write() as uow:
            activate(uow, prepared, lease, staged)
    except LeaseLost:
        checks["old_attempt_cannot_publish_after_retry"] = True
    else:
        raise AssertionError("C allowed an old attempt to publish")
    with store.write() as uow:
        repeated = uow.knowledge.write_chunks(index["id"], prepared["chunks"], new_lease)
        assert repeated["inserted"] == 0
        activate(uow, prepared, new_lease, staged)
    load_verified_knowledge_snapshot(store, ids, run_root / "assets")
    checks["new_attempt_reuses_chunks_and_publishes"] = True

    # C verifies bytes, not semantic content. Prove E rejects a valid-hash mismatch.
    for mutation in ("chunks", "locator", "document_version", "index_version", "metadata"):
        wrong, wrong_lease, _, _ = prepare("e020-semantic-mismatch-" + mutation)
        payload = copy.deepcopy(wrong["snapshot"])
        document = payload["documents"][doc_id]
        if mutation == "chunks":
            document["chunks"][0]["text"] += " altered content"
        elif mutation == "locator":
            document["chunks"][0]["chunk_id"] = "wrong-chunk-id"
        elif mutation == "metadata":
            document["metadata"]["title"] += " SYNTHETIC WRONG METADATA"
        else:
            document[mutation] = "wrong-version"
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        altered = store.persist_file(source_id=doc["source_id"], original_name="index.json", stream=io.BytesIO(content))
        assert altered["sha256"] == hashlib.sha256(content).hexdigest()
        with store.write() as uow:
            activate(uow, wrong, wrong_lease, altered)
        checks[f"valid_hash_but_wrong_{mutation}_blocks_answer"] = rejects_answer()
    repair, repair_lease, repaired, _ = prepare("e020-semantic-repaired")
    with store.write() as uow:
        activate(uow, repair, repair_lease, repaired)
    load_verified_knowledge_snapshot(store, ids, run_root / "assets")
    checks["repaired_version_loads"] = True
    return checks
