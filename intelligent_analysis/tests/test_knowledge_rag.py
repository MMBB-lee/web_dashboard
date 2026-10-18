from datetime import datetime, timedelta, timezone

from intelligent_analysis.agent.orchestrator import answer_with_index
from intelligent_analysis.contracts import DocumentMetadata
from intelligent_analysis.knowledge.chunk import split_text
from intelligent_analysis.knowledge.index import InMemoryKnowledgeIndex


def test_rag_answers_with_citation():
    index = InMemoryKnowledgeIndex()
    metadata = DocumentMetadata(
        title="景区开放说明",
        publisher="测试单位",
        source_url="https://example.com/doc",
        published_at=datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=8))),
        permission_note="测试授权",
    )
    chunks = split_text("黄山风景区开放时间为每日六点至十八点。", "doc")
    index.add_document("doc", metadata, chunks)

    response = answer_with_index("黄山什么时候开放？", index)

    assert response.status == "answered"
    assert response.citations[0].title == "景区开放说明"
    assert "六点" in response.answer


def test_rag_reports_insufficient_evidence():
    response = answer_with_index("机场客流是多少？", InMemoryKnowledgeIndex())

    assert response.status == "insufficient_evidence"
    assert not response.citations
