"""Keyword knowledge index with optional JSON persistence."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from ..contracts import DocumentMetadata, TextChunk


def tokenize(text: str) -> set[str]:
    lowered = text.lower()
    latin_terms = {token for token in re.split(r"[^\w]+|_", lowered) if token and not _is_cjk(token)}
    cjk_chars = {char for char in lowered if _is_cjk(char)}
    return latin_terms | cjk_chars


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


@dataclass
class IndexedDocument:
    metadata: DocumentMetadata
    chunks: list[TextChunk]


@dataclass
class InMemoryKnowledgeIndex:
    documents: dict[str, IndexedDocument] = field(default_factory=dict)

    def add_document(self, document_id: str, metadata: DocumentMetadata, chunks: list[TextChunk]):
        self.documents[document_id] = IndexedDocument(metadata=metadata, chunks=chunks)

    def search(self, query: str, top_k: int = 3) -> list[tuple[float, IndexedDocument, TextChunk]]:
        query_terms = tokenize(query)
        if not query_terms:
            return []

        document_frequency = self._document_frequency()
        total_documents = max(len(self.documents), 1)
        matches: list[tuple[float, IndexedDocument, TextChunk]] = []

        for document in self.documents.values():
            for chunk in chunks_sorted(document):
                chunk_terms = tokenize(chunk.text)
                shared = query_terms & chunk_terms
                if not shared:
                    continue
                score = sum(
                    1.0 + math.log((1 + total_documents) / (1 + document_frequency.get(term, 0)))
                    for term in shared
                ) / len(query_terms)
                matches.append((score, document, chunk))
        return sorted(matches, key=lambda item: (-item[0], item[2].order))[:top_k]

    def save(self, path: str | Path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "documents": {
                document_id: {
                    "metadata": document.metadata.model_dump(mode="json"),
                    "chunks": [chunk.model_dump(mode="json") for chunk in document.chunks],
                }
                for document_id, document in self.documents.items()
            }
        }
        staging = target.with_name(target.name + "." + uuid4().hex + ".tmp")
        try:
            staging.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> InMemoryKnowledgeIndex:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: dict) -> InMemoryKnowledgeIndex:
        index = cls()
        for document_id, item in payload["documents"].items():
            metadata = DocumentMetadata.model_validate(item["metadata"])
            chunks = [TextChunk.model_validate(chunk) for chunk in item.get("chunks", [])]
            if any(c.document_id != document_id for c in chunks):
                raise ValueError("Chunk document identity mismatch")
            index.add_document(document_id, metadata, chunks)
        return index

    def _document_frequency(self) -> dict[str, int]:
        frequency: dict[str, int] = {}
        for document in self.documents.values():
            terms: set[str] = set()
            for chunk in document.chunks:
                terms.update(tokenize(chunk.text))
            for term in terms:
                frequency[term] = frequency.get(term, 0) + 1
        return frequency

    def __len__(self) -> int:
        return len(self.documents)


def chunks_sorted(document: IndexedDocument) -> list[TextChunk]:
    return sorted(document.chunks, key=lambda chunk: chunk.order)
