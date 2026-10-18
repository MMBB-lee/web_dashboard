"""Citation assembly helpers."""

from __future__ import annotations

from ..contracts import Citation, TextChunk
from ..knowledge.index import IndexedDocument


def document_citation(document: IndexedDocument, chunk: TextChunk) -> Citation:
    metadata = document.metadata
    return Citation(
        type="document",
        id=chunk.document_id,
        title=metadata.title,
        url=metadata.source_url,
        date=metadata.published_at,
        chunk_id=chunk.chunk_id,
    )
