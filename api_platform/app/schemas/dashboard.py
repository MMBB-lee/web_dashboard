"""驾驶舱总览模型（10）。

红线：不同指标分卡展示，不求和成「总客流」。

03 §10 要求每张卡「含指标、时间、数值或 null、来源和状态」——``status`` 就是
那个「状态」，它说明这张卡的合计到底覆盖了哪些实体：

- ``ok``：该地区所有候选实体在该周期都有已核验数据，合计完整；
- ``partial``：只有部分实体有（``covered_entity_count`` / ``entity_count``
  给出覆盖度），合计**只含有的那部分**，不是全地区；
- ``no_data``：一个都没有，``value`` 为 ``null``，**不用零或均值填充**。
"""

from pydantic import BaseModel, Field

#: 卡片的三种状态，03 §10「来源和状态」里的「状态」。
CARD_STATUSES = ("ok", "partial", "no_data")


class OverviewCard(BaseModel):
    """每张卡片只承载一个指标。

    ``period_start`` / ``period_end`` 是**这张卡数据所在的周期**；``no_data``
    时两者都是 ``null``——没有数据就不编一个时间出来。
    """

    metric: str
    metric_display_name: str
    unit: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    grain: str
    value: float | None = Field(description="无数据或部分覆盖时为 null，不伪造")
    source_ids: list[str]
    quality_status: str | None = None
    published_at: str | None = Field(default=None, description="参与合计的点里最晚的发布时间（03 §1 溯源）")
    status: str = Field(description="ok | partial | no_data")
    entity_count: int = Field(default=0, description="该地区该指标适用实体总数")
    covered_entity_count: int = Field(default=0, description="真正计入本卡合计的实体数")


class MapEntity(BaseModel):
    """地图打点：位置 + 该实体主指标的最新值。

    ``longitude`` / ``latitude`` 与 05 一致**可为 null**——C 允许实体不填坐标，
    没有坐标就不打点（调用方别把 null 当 0，否则会出现 (0,0) 假点）。
    ``metric`` 在实体没有任何适用指标时为 ``null``。
    """

    id: str
    name: str
    type: str
    longitude: float | None = None
    latitude: float | None = None
    latest_value: float | None = None
    latest_period_end: str | None = None
    metric: str | None = None
    latest_metric: str | None = None
    latest_period_start: str | None = None
    latest_grain: str | None = None
    unit: str | None = None


class DataFreshness(BaseModel):
    """数据新鲜度：每个实体一条，说明这块的数到什么时候为止。"""

    entity_id: str
    metric: str
    latest_period_end: str
    ingested_at: str | None = None


class OverviewResponse(BaseModel):
    region_code: str
    as_of: str = Field(description="实际生效的基准日期，见 dashboard.py 的注释")
    grain: str
    cards: list[OverviewCard]
    map_entities: list[MapEntity]
    active_events_count: int
    data_freshness: list[DataFreshness]
