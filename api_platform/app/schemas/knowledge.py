"""知识库模型（20—22）。"""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DocumentAccepted(BaseModel):
    """20 POST /knowledge/documents 的 202 响应。"""

    document_id: str
    index_status: str = Field(description="pending；解析与索引异步执行")
    job_id: str


class DocumentOut(BaseModel):
    """21 GET /knowledge/documents/{document_id}。"""

    id: str
    title: str
    publisher: str
    source_url: str
    published_at: str
    valid_from: str | None
    valid_until: str | None
    index_status: str
    permission_note: str
    allowed_snippets: list[str] = Field(default_factory=list, description="仅获准展示的片段")


class ReindexRequest(BaseModel):
    """22 POST /knowledge/reindex 请求体（03 契约：1—100 个 UUID）。"""

    document_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("document_ids")
    @classmethod
    def _ids_must_be_uuids(cls, value: list[str]) -> list[str]:
        for item in value:
            try:
                UUID(item)
            except ValueError:
                raise ValueError(f"document_ids 含非 UUID 项：{item}") from None
        return value


class ReindexAccepted(BaseModel):
    job_id: str
    pending_count: int = Field(ge=1)
