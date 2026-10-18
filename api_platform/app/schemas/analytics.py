"""序列比较模型（11）。

红线：混合不同指标时只能 separate_axes 或 index_100，
不能把原始值画到同一数值轴或求和。

``index_100`` 下点位的值是**无量纲指数**，其 ``unit`` 会被置为 ``index``；
原始单位保留在序列级的 ``unit`` 字段，基期数值保留在 ``base_value``——
否则调用方拿到一串 100 左右的数，却不知道它是拿什么除出来的。
"""

from pydantic import BaseModel, Field, model_validator

from app.schemas.series import MissingPeriod, ObservationPoint


class SeriesSpec(BaseModel):
    entity_id: str
    metric: str


class CompareRequest(BaseModel):
    series: list[SeriesSpec] = Field(min_length=2, max_length=4)
    grain: str = Field(pattern="^(day|week|month)$")
    start: str
    end: str
    display_mode: str = Field(pattern="^(separate_axes|index_100)$")
    base_period: str | None = Field(default=None, description="index_100 时必填")

    @model_validator(mode="after")
    def _base_period_required_for_index(self) -> "CompareRequest":
        if self.display_mode == "index_100" and not self.base_period:
            raise ValueError("display_mode=index_100 时必须提供 base_period")
        return self


class ComparedSeries(BaseModel):
    entity_id: str
    metric: str
    points: list[ObservationPoint]
    missing_periods: list[MissingPeriod]
    source_ids: list[str]
    unit: str | None = Field(default=None, description="该序列的原始单位；index_100 下点位 unit 为 index")
    base_value: float | None = Field(default=None, description="index_100 下基期的原始值")


class CompareResponse(BaseModel):
    grain: str
    start: str
    end: str
    display_mode: str
    base_period: str | None = None
    series: list[ComparedSeries]
    comparison_note: str = Field(description="说明口径差异与不可比之处")
