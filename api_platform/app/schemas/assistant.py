"""智能体对话模型（19）。

E 0.5.0 契约（2026-10-14）：
- 请求体不接收任何可执行注入（metric_query / metric_tool 由服务端在
  C 的指标查询契约到位后注入，用户传入的一律拒收）；
- 响应可携带 `metric_result`（数值结论的公开溯源：查询时间、指标、
  周期与来源），无数值查询时为 null。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.metrics_query import SUPPORTED_GRAINS, SUPPORTED_METRICS


class ChatContext(BaseModel):
    """问答上下文：只允许白名单字段（拒绝注入可执行对象）。

    2026-10-15 新增 ``metric``：**只有显式给出指标时才走数值核实路径**
    （E 的 metric 分支不检索知识库，见 ``app/services/metrics_query.py``）。
    指标只能从 ``03-接口总览`` §1 的指标字典里选，非法值在路由层 400。
    """

    model_config = ConfigDict(extra="forbid")

    metric: str | None = Field(
        default=None,
        description=f"要核实的指标，取值：{' | '.join(SUPPORTED_METRICS)}；不给则走文档问答",
    )
    entity_ids: list[str] = Field(default_factory=list)
    period_start: str | None = None
    period_end: str | None = None
    grain: str | None = Field(default=None, pattern="^(day|week|month)$")

    @field_validator("metric")
    @classmethod
    def _metric_in_catalog(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_METRICS:
            raise ValueError(
                f"未知指标 {value!r}，可用：{', '.join(SUPPORTED_METRICS)}"
            )
        return value

    @field_validator("grain")
    @classmethod
    def _grain_supported(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_GRAINS:
            raise ValueError(f"未知粒度 {value!r}，可用：{', '.join(SUPPORTED_GRAINS)}")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = None
    context: ChatContext | None = None


class Citation(BaseModel):
    type: str = Field(description="metric | document | event")
    id: str
    title: str
    url: str | None
    date: str | None
    chunk_id: str | None = Field(default=None, description="文档片段 ID（E 契约）")


class MetricResult(BaseModel):
    """数值结论的公开溯源结构（保留查询时间、指标、周期与来源）。"""

    metric: str
    value: float
    period_start: str | None = None
    period_end: str | None = None
    queried_at: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    status: str = Field(description="answered | insufficient_evidence | metric_tool_unavailable")
    citations: list[Citation] = Field(default_factory=list)
    used_metric_queries: list[str] = Field(default_factory=list, description="数值结论的指标查询清单")
    limitations: list[str] = Field(default_factory=list)
    metric_result: MetricResult | None = Field(
        default=None, description="数值结论溯源；纯文档问答时为 null"
    )
