"""时序与通用数字点模型（08 / 09）。

口径红线：含数字的返回项必须携带完整溯源字段（03-接口总览 §1）；
缺失保持缺失；预测值另标 is_forecast 与模型版本。
"""

from pydantic import BaseModel, Field


class EntityRef(BaseModel):
    id: str
    name: str
    type: str


class ObservationPoint(BaseModel):
    """通用数字点：观测值或预测值。"""

    metric: str
    value: float
    unit: str
    period_start: str
    period_end: str
    grain: str = Field(description="day | week | month | holiday")
    source_ids: list[str]
    published_at: str
    ingested_at: str
    quality_status: str
    is_forecast: bool = False
    model_version: str | None = Field(default=None, description="预测值必填")


class MissingPeriod(BaseModel):
    period_start: str
    period_end: str


class SeriesResponse(BaseModel):
    """08 / 09 时序响应。"""

    entity: EntityRef = Field(description="实体概要")
    metric: str
    grain: str
    points: list[ObservationPoint] = Field(default_factory=list, description="无数据时为空数组")
    missing_periods: list[MissingPeriod] = Field(default_factory=list)
    coverage_ratio: float = Field(ge=0, le=1, description="无数据时为 0")
    transport_type: str | None = Field(default=None, description="09 专属：rail | airport")
