"""Minimal RAG assistant orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ..contracts import ChatResponse
from ..knowledge.index import InMemoryKnowledgeIndex
from ..knowledge.retrieve import retrieve
from .citations import document_citation


def answer_with_index(
    message: str,
    index: InMemoryKnowledgeIndex,
    conversation_id: UUID | None = None,
    as_of: datetime | str | None = None,
) -> ChatResponse:
    matches = retrieve(index, message)
    if not matches:
        return ChatResponse(
            conversation_id=conversation_id or uuid4(),
            answer="抱歉，当前知识库中没有检索到足以回答该问题的资料。",
            status="insufficient_evidence",
            limitations=["No matching authorized document was found."],
        )

    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of)
    as_of = as_of or datetime.now(UTC)
    if as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    limitations = ["This answer is based only on retrieved authorized documents."]
    for document, _, _ in matches:
        metadata = document.metadata
        if metadata.valid_until and metadata.valid_until < as_of:
            limitations.append(f"历史资料：{metadata.title}，适用期已结束，不能作为当前运营事实。")
        if metadata.valid_from and metadata.valid_from > as_of:
            limitations.append(f"尚未生效：{metadata.title}。")
        if metadata.applicability_note:
            limitations.append(metadata.applicability_note)
    citations = [document_citation(document, chunk) for document, chunk, _ in matches]
    evidence = "\n".join(f"- {chunk.text}" for _, chunk, _ in matches)
    answer = f"根据带日期的来源资料，可参考以下信息：\n{evidence}"
    if len(limitations) > 1:
        answer = "资料时效与适用范围提示：" + "；".join(limitations[1:]) + "\n" + answer
    return ChatResponse(
        conversation_id=conversation_id or uuid4(),
        answer=answer,
        status="answered",
        citations=citations,
        limitations=limitations,
    )
