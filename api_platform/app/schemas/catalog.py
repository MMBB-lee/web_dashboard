"""目录与来源模型（05 / 06 / 07）。

字段集合严格对齐 03-接口总览 §4，**不多也不少**——C 的仓储通过
``public_columns`` 返回的其它列（如实体的 ``parent_entity_id`` /
``location_note``，指标的 ``dictionary_version``，来源的 ``adapter_key`` /
``enabled``）一律**不外泄**，由路由层挑字段后构造本模型。

可空性按 C 的 ``db/models.py`` 实测定：经度纬度、来源的发布时间/抓取时间/
覆盖区间/原生粒度都可以为空（03 §07 未要求这些必填）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class EntityOut(BaseModel):
    """05 GET /catalog/entities 每项。

    ``available_metrics`` / ``available_grains`` **只列已核验观测实际支持的**——
    这是 03 §05「不在目录中暗示尚未取得的数据可用」的落地：C 只统计
    ``quality_status == "verified"`` 的观测，D 不做任何补全。
    """

    id: str = Field(description="实体 UUID")
    name: str
    type: str = Field(description="scenic | rail_station | airport")
    region_code: str
    longitude: float | None = None
    latitude: float | None = None
    available_metrics: list[str] = Field(default_factory=list)
    available_grains: list[str] = Field(
        default_factory=list, description="仅列已核验观测实际支持的粒度"
    )
    status: str


class MetricOut(BaseModel):
    """06 GET /catalog/metrics 每项。

    ``comparability_note`` 是硬要求：03 §06 明确「例如 airport_throughput
    不可作为铁路发送量直接相加」，这条口径由 C 的指标字典维护，D 原样透传。
    """

    code: str
    display_name: str
    definition: str
    unit: str
    supported_entity_types: list[str]
    aggregation_rule: str = Field(description="聚合规则；不同指标不直接相加")
    comparability_note: str


class SourceOut(BaseModel):
    """07 GET /sources/{source_id}。"""

    id: str
    publisher: str
    title: str
    url: str
    published_at: str | None = None
    retrieved_at: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    native_grain: str | None = Field(
        default=None, description="来源原生粒度；接口不提供其不支持的粒度"
    )
    usage_note: str
    verification_status: str
