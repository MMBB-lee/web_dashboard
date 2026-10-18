"""作业模型（29 / 30）。"""

from pydantic import BaseModel, Field


class IngestionRequest(BaseModel):
    """29 POST /jobs/ingestion 请求体。"""

    source_ids: list[str] = Field(min_length=1, max_length=50)
    dry_run: bool = Field(default=True, description="默认 true：只检查不写库")
    reason: str = Field(min_length=1, description="手动触发原因，留痕")


class IngestionAccepted(BaseModel):
    job_id: str
    status: str = Field(description="queued")


class JobOut(BaseModel):
    """30 GET /jobs/{job_id}；不返回密码、会话 ID 或原始视频日志。"""

    id: str
    type: str = Field(description="ingestion | video_analysis | knowledge_index")
    status: str = Field(description="queued | running | succeeded | failed")
    started_at: str | None
    finished_at: str | None
    processed_count: int | None = None
    imported_count: int | None = Field(default=None, description="幂等导入无新数据时为 0")
    error_summary: str | None
    analysis_id: str | None = Field(default=None, description="video_analysis / knowledge_index 作业关联的分析或文档对象")
