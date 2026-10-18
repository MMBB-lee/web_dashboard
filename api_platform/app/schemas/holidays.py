"""节假日模型（13 / 14）。"""

from pydantic import BaseModel, Field, model_validator


class HolidayOut(BaseModel):
    id: str
    name: str
    kind: str = Field(description="may_day | national_day | summer | winter")
    year: int
    start: str
    end: str
    definition_source_id: str = Field(description="假期定义可追溯")
    days_count: int = Field(ge=1)


class HolidayCompareRequest(BaseModel):
    holiday_ids: list[str] = Field(min_length=2, max_length=2, description="恰好 2 个假期")
    series: list[dict] = Field(min_length=1, max_length=4, description="每项 {entity_id, metric}")
    mode: str = Field(pattern="^(daily_average|period_total|day_index)$")

    @model_validator(mode="after")
    def _two_distinct_holidays(self) -> "HolidayCompareRequest":
        if len(set(self.holiday_ids)) != 2:
            raise ValueError("holiday_ids 必须是两个不同的假期")
        return self


class HolidaySeriesResult(BaseModel):
    holiday_id: str
    entity_id: str
    metric: str
    values: list[float] = Field(description="实际可用数值；缺失不补造")
    missing_days: int = Field(ge=0)
    source_ids: list[str]


class HolidayCompareResponse(BaseModel):
    mode: str
    results: list[HolidaySeriesResult]
    comparable: bool
    reason: str = Field(description="不可比时给出原因，如 day_index 要求完整日数据")
