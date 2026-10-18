"""历史预测模型（12）。

红线：数据不足返回 status=data_insufficient 且 forecast_points=[]，
不用历史均值伪装预测；horizon 按粒度限界（周 1—8，月 1—6）。
"""

from pydantic import BaseModel, Field, model_validator


class ForecastRequest(BaseModel):
    entity_id: str
    metric: str
    grain: str = Field(pattern="^(week|month)$")
    training_end: str = Field(description="训练截止日期 YYYY-MM-DD")
    horizon: int = Field(ge=1)
    model: str = Field(default="baseline", pattern="^(baseline|auto)$")

    @model_validator(mode="after")
    def _horizon_within_grain_limit(self) -> "ForecastRequest":
        limits = {"week": 8, "month": 6}
        if self.horizon > limits[self.grain]:
            raise ValueError(f"{self.grain} 粒度的 horizon 最大为 {limits[self.grain]}")
        return self


class ForecastPoint(BaseModel):
    period_start: str
    period_end: str
    value: float = Field(description="点预测")
    lower_bound: float | None = Field(default=None, description="区间下界；缺失保持 null，不补造")
    upper_bound: float | None = Field(default=None, description="区间上界；缺失保持 null，不补造")


class BacktestResult(BaseModel):
    method: str = Field(description="回测方法，如 rolling_origin")
    mae: float | None = Field(description="样本不足时为 null")
    smape: float | None
    sample_count: int = Field(ge=0)


class ForecastResponse(BaseModel):
    status: str = Field(description="ready | data_insufficient")
    forecast_points: list[ForecastPoint] = Field(default_factory=list)
    backtest: BacktestResult | None = None
    training_window: str | None = Field(default=None, description="实际训练区间")
    model_version: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, description="data_insufficient 时给出原因")
