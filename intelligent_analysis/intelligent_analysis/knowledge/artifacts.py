"""Load E keyword artifacts through C's verified internal reference."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

from ..contracts import DocumentMetadata, TextChunk
from ..integration import DependencyUnavailable
from ..worker.knowledge_worker import (
    ARTIFACT_SCHEMA_VERSION,
    EMBEDDING_MODEL_VERSION,
    PARSER_VERSION,
)


def _read_verified_bytes(data_root: Path, reference: dict) -> bytes:
    relative = reference["relative_path"]
    if not isinstance(relative, str) or not relative or len(relative) > 500:
        raise ValueError("Invalid relative artifact path")
    parts = PurePosixPath(relative)
    if (parts.is_absolute() or parts.as_posix() != relative or "\\" in relative
            or "\0" in relative or any(p in {".", ".."} or ":" in p for p in parts.parts)):
        raise ValueError("Unsafe artifact path")
    root = data_root.resolve()
    target = root
    for part in parts.parts:
        target /= part
        if target.is_symlink():
            raise ValueError("Artifact path contains a symlink")
    if not target.resolve().is_relative_to(root) or not stat.S_ISREG(target.stat().st_mode):
        raise ValueError("Artifact must be a regular file inside DATA_ROOT")
    size = reference["byte_size"]
    digest = reference["sha256"]
    if type(size) is not int or size < 0 or not isinstance(digest, str):
        raise ValueError("Invalid artifact integrity reference")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(target, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Artifact changed to a non-file")
        content = stream.read(size + 1)
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Artifact changed after C reference verification")
    return content


def _validate_document(payload: dict, document_id: str, reference: dict,
                       stored_chunks: dict, stored_metadata: dict) -> dict:
    if type(payload["schema_version"]) is not int or payload["schema_version"] != ARTIFACT_SCHEMA_VERSION:
        raise ValueError("Unsupported E artifact schema; rebuild legacy artifacts")
    for key, supported in (("parser_version", PARSER_VERSION),
                           ("embedding_model_version", EMBEDDING_MODEL_VERSION)):
        if payload[key] != reference[key] or payload[key] != supported:
            raise ValueError("Artifact processing version mismatch")
    if set(payload["documents"]) != {document_id} or reference["document_id"] != document_id:
        raise ValueError("Artifact document identity mismatch")
    item = payload["documents"][document_id]
    if item["index_version"] != reference["version"] or stored_chunks["version"] != reference["version"]:
        raise ValueError("Artifact index version mismatch")
    if not item["document_version"] or item["document_version"] != stored_metadata["document_version"]:
        raise ValueError("Artifact source document version mismatch")
    metadata = DocumentMetadata.model_validate(item["metadata"])
    stored = DocumentMetadata.model_validate(stored_metadata)
    for field in DocumentMetadata.model_fields:
        # Applicability note is manifest provenance, not a C 0.2.0a1 DB column.
        if field != "applicability_note" and getattr(metadata, field) != getattr(stored, field):
            raise ValueError("Artifact metadata does not match C document")
    chunks = [TextChunk.model_validate(c) for c in item["chunks"]]
    rows = stored_chunks["items"]
    if not chunks or len(chunks) != len(rows):
        raise ValueError("Artifact chunk count mismatch")
    if len({c.chunk_id for c in chunks}) != len(chunks):
        raise ValueError("Duplicate chunk IDs")
    for number, (chunk, row) in enumerate(zip(chunks, rows)):
        if (chunk.document_id != document_id or chunk.order != number or row["chunk_no"] != number
                or chunk.chunk_id != row["locator"]["chunk_id"]
                or row["locator"]["order"] != number or chunk.text != row["text"]):
            raise ValueError("Artifact chunks do not match committed C chunks")
    return item


def load_verified_knowledge_snapshot(store, document_ids: list[str], data_root: str | Path) -> dict:
    """Return all-or-nothing committed documents; never fall back to stale RAM.

    D passes its authorized active document IDs. Pending documents must be
    excluded by D. No internal artifact paths are returned to HTTP callers.
    """
    from data_foundation import DataFoundationError

    snapshot = {"documents": {}}
    if len(set(document_ids)) != len(document_ids):
        raise ValueError("Document IDs must be unique")
    try:
        with store.read() as uow:
            if not hasattr(uow.knowledge, "get_index_artifact_ref"):
                raise DependencyUnavailable("C 0.2.0a1 artifact reference API is required")
            for document_id in document_ids:
                reference = uow.knowledge.get_index_artifact_ref(document_id)
                if reference["status"] != "ready":
                    raise DependencyUnavailable("Requested knowledge index is not ready")
                content = _read_verified_bytes(Path(data_root), reference)
                payload = json.loads(content)
                chunks = uow.knowledge.get_chunks(document_id, reference["version"])
                if chunks["status"] != "ready":
                    raise DependencyUnavailable("Committed knowledge chunks are not ready")
                document = uow.knowledge.get_document(document_id)
                snapshot["documents"][document_id] = _validate_document(
                    payload, document_id, reference, chunks, document)
    except (DataFoundationError, OSError, ValueError, KeyError, TypeError) as exc:
        raise DependencyUnavailable("Knowledge artifact verification failed; rebuild or repair required") from exc
    return snapshot
