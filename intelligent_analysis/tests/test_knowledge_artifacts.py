import copy
import hashlib
import json
from contextlib import contextmanager

import pytest

from intelligent_analysis import ServiceBindings, answer_message, configure_services
from intelligent_analysis.integration import DependencyUnavailable
from intelligent_analysis.knowledge.artifacts import load_verified_knowledge_snapshot
from intelligent_analysis.worker import process_knowledge_job

pytest.importorskip("data_foundation")


@pytest.fixture
def artifact_store(tmp_path):
    text = tmp_path / "source.md"
    text.write_text("黄山历史公告：春节期间开放时间请以当年公告为准。", encoding="utf-8")
    metadata = {"document_version": "source-v1", "title": "历史公告", "publisher": "测试", "permission_note": "合成测试",
                "source_url": "https://example.invalid/notice", "valid_until": "2025-02-01T00:00:00Z"}
    prepared = process_knowledge_job({"document_id": "c-doc", "version": "v1",
        "file_path": str(text), "metadata": metadata})
    file = tmp_path / "index.json"
    file.write_bytes(prepared["artifact_bytes"])
    payload = prepared["snapshot"]
    reference = {"status": "ready", "document_id": "c-doc", "version": "v1",
        "parser_version": payload["parser_version"], "embedding_model_version": payload["embedding_model_version"],
        "relative_path": "index.json", "sha256": prepared["artifact_sha256"],
        "byte_size": len(prepared["artifact_bytes"])}

    class Repo:
        def get_index_artifact_ref(self, document_id):
            assert document_id == "c-doc"
            return reference

        def get_chunks(self, document_id, version):
            assert version == "v1"
            return {"status": "ready", "version": "v1", "items": prepared["chunks"]}

        def get_document(self, document_id):
            return metadata

    class Store:
        knowledge = Repo()

        @contextmanager
        def read(self):
            yield self

    return Store(), tmp_path, file, payload, reference


def test_verified_source_ids_expiration_and_no_internal_paths(artifact_store):
    store, root, _, _, _ = artifact_store
    configure_services(ServiceBindings(knowledge_snapshot_loader=lambda:
        load_verified_knowledge_snapshot(store, ["c-doc"], root)))
    answer = answer_message("黄山春节历史公告")
    assert answer["citations"][0]["id"] == "c-doc"
    assert "历史资料" in answer["answer"]
    assert str(root) not in json.dumps(answer, ensure_ascii=False)


@pytest.mark.parametrize("mutation", ["index_version", "document_version", "parser", "embedding", "chunks", "locator", "metadata", "schema", "identity"])
def test_valid_digest_does_not_hide_semantic_mismatch(artifact_store, mutation):
    store, root, file, original, reference = artifact_store
    payload = copy.deepcopy(original)
    item = payload["documents"]["c-doc"]
    if mutation in {"index_version", "document_version"}:
        item[mutation] = "other-version"
    elif mutation == "parser":
        payload["parser_version"] = "unsupported-parser"
    elif mutation == "embedding":
        payload["embedding_model_version"] = "unsupported-model"
    elif mutation == "chunks":
        item["chunks"][0]["text"] += " altered"
    elif mutation == "metadata":
        item["metadata"]["title"] = "wrong title"
    elif mutation == "locator":
        item["chunks"][0]["chunk_id"] = "wrong-chunk"
    elif mutation == "schema":
        del payload["schema_version"]
    else:
        payload["documents"] = {"different-document": item}
    content = json.dumps(payload, ensure_ascii=False).encode()
    file.write_bytes(content)
    reference.update(sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content))
    with pytest.raises(DependencyUnavailable):
        load_verified_knowledge_snapshot(store, ["c-doc"], root)


@pytest.mark.parametrize("mutation", ["missing", "changed_after_reference", "unsafe_path", "pending"])
def test_file_reference_errors_fail_closed(artifact_store, mutation):
    store, root, file, _, reference = artifact_store
    if mutation == "missing":
        file.unlink()
    elif mutation == "changed_after_reference":
        original_reader = store.knowledge.get_index_artifact_ref

        def change_file(document_id):
            ref = original_reader(document_id)
            file.write_bytes(b"changed after C check")
            return ref

        store.knowledge.get_index_artifact_ref = change_file
    elif mutation == "unsafe_path":
        reference["relative_path"] = "../index.json"
    else:
        reference["status"] = "index_not_ready"
    with pytest.raises(DependencyUnavailable):
        load_verified_knowledge_snapshot(store, ["c-doc"], root)


def test_broken_verified_source_does_not_fall_back_to_old_memory(artifact_store):
    store, root, file, _, _ = artifact_store
    from intelligent_analysis.service import process_document_index
    process_document_index(str(root / "source.md"), {
        "document_id": "stale", "title": "旧内存", "publisher": "测试", "permission_note": "测试"})
    configure_services(ServiceBindings(knowledge_snapshot_loader=lambda:
        load_verified_knowledge_snapshot(store, ["c-doc"], root)))
    file.write_bytes(b"broken")
    with pytest.raises(DependencyUnavailable):
        answer_message("黄山春节")


def test_configure_single_knowledge_source():
    with pytest.raises(ValueError):
        configure_services(ServiceBindings(knowledge_index_path="local.json",
                                           knowledge_snapshot_loader=lambda: {"documents": {}}))


@pytest.mark.parametrize("content", [b"broken JSON", b'{"documents":{"c-doc":{}}}'])
def test_damaged_legacy_snapshot_raises_dependency_error_without_memory_fallback(tmp_path, content):
    from intelligent_analysis.service import process_document_index
    source = tmp_path / "source.md"
    source.write_text("黄山春节历史资料", encoding="utf-8")
    process_document_index(str(source), {"document_id": "stale", "title": "旧资料",
        "publisher": "测试", "permission_note": "测试"})
    path = tmp_path / "published.json"
    path.write_bytes(content)
    configure_services(ServiceBindings(knowledge_index_path=str(path)))
    with pytest.raises(DependencyUnavailable):
        answer_message("黄山春节")
