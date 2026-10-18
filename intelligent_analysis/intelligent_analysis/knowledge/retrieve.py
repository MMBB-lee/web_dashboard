"""Retrieval helpers."""

from __future__ import annotations

from ..contracts import TextChunk
from ..knowledge.index import IndexedDocument, InMemoryKnowledgeIndex


def retrieve(
    index: InMemoryKnowledgeIndex, query: str, top_k: int = 3
) -> list[tuple[IndexedDocument, TextChunk, float]]:
    return [(document, chunk, score) for score, document, chunk in index.search(query, top_k)]
