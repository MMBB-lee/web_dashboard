"""突发事件与公告模型（15—18）。

红线：区分官方公告（official）与模型提示（model_signal）；
没有可核验来源不得创建官方事件；变更必填 change_note 保留历史。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.c_enums import (event_kinds, event_severities,
                                  event_statuses, hint, pattern)

EVENT_KINDS = pattern(event_kinds())
SEVERITIES = pattern(event_severities())
EVENT_STATUS = pattern(event_statuses())


class EventSummary(BaseModel):
    """15 GET /events 每项。"""

    id: str
    title: str
    kind: str = Field(description="取值以 C 的 EVENT_KINDS 为准：" + hint(event_kinds()))
    status: str = Field(description="取值以 C 的 EVENT_STATUSES 为准：" + hint(event_statuses(), 0))
    authority: str = Field(description="official | model_signal")
    severity: str
    affected_entity_ids: list[str]
    starts_at: datetime
    ends_at: datetime | None
    published_at: datetime
    last_verified_at: datetime | None
    source_id: str


class EventTimelineItem(BaseModel):
    kind: str = Field(description="announce | update | resolve")
    at: datetime
    source_id: str | None
    note: str | None


class EventDetail(EventSummary):
    """16 GET /events/{event_id}。"""

    row_version: int = Field(
        description=("乐观锁版本号。03 §18 未定义此字段，但 C 的 update_event "
                     "要求 expected_version；这里回显出来，调用方才能启用并发保护。"))
    description: str
    source_url: str | None
    timeline: list[EventTimelineItem]
    operator_note: str | None


class EventCreate(BaseModel):
    """17 POST /events 请求体；authority 固定 official。"""

    title: str = Field(min_length=1, max_length=120)
    description: str
    kind: str = Field(pattern=EVENT_KINDS)
    severity: str = Field(pattern=SEVERITIES)
    region_code: str
    affected_entity_ids: list[str] = Field(default_factory=list)
    starts_at: datetime
    ends_at: datetime | None = None
    source_id: str = Field(description="必填：无可核验来源不得创建官方事件")
    asset_id: str = Field(
        description=("事件原件。03 §17 未列此字段，但 C 的 events.asset_id 是 "
                     "NOT NULL，且 create_event 直接取 values['asset_id']——"
                     "缺失会走 KeyError/IntegrityError 漏成 500。"
                     "C 的 provenance 仓储只有 get_asset(asset_id)，"
                     "**没有按来源查原件的入口**，D 无法从 source_id 反查，"
                     "也不能凭空造一条原件（那是伪造溯源）→ 只能由调用方给。"))


class EventUpdate(BaseModel):
    """18 PATCH /events/{event_id} 请求体。"""

    description: str | None = None
    severity: str | None = Field(default=None, pattern=SEVERITIES)
    affected_entity_ids: list[str] | None = None
    ends_at: datetime | None = None
    status: str | None = Field(default=None, pattern=EVENT_STATUS)
    new_source_id: str | None = Field(default=None, description="重新激活已解除事件时必填")
    new_asset_id: str | None = Field(
        default=None,
        description=("换来源时必须同时给：C 只在 asset_id 一并提交时才校验"
                     "「来源与原件一致」，只换 source_id 会留下不匹配的状态。"))
    expected_version: int = Field(
        description=("**必填**乐观锁版本号，取 16 号详情回显的 row_version。"
                     "缺失 → 400；与库中版本不一致 → 409。"
                     "（C 021a2《致D和E-今日确认与接收清单》第 5 条）"))
    change_note: str = Field(min_length=1, description="必填：保留变更历史")
