from datetime import UTC, datetime

from intelligent_analysis.contracts import DocumentMetadata
from intelligent_analysis.knowledge.chunk import split_text
from intelligent_analysis.knowledge.index import InMemoryKnowledgeIndex


def test_index_can_save_and_load(tmp_path):
    metadata = DocumentMetadata(
        title="测试文档",
        publisher="测试单位",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        permission_note="测试授权",
    )
    index = InMemoryKnowledgeIndex()
    index.add_document("doc", metadata, split_text("黄山景区正常开放。", "doc"))
    target = tmp_path / "index.json"

    index.save(target)
    loaded = InMemoryKnowledgeIndex.load(target)

    assert len(loaded) == 1
    assert loaded.search("黄山开放")[0][1].metadata.title == "测试文档"
