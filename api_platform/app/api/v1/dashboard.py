"""驾驶舱总览（03-接口总览 §4：10）。

2026-10-17 接线：C 0.2.0a2 的 ``uow.catalog`` / ``uow.observations`` /
``uow.events`` 足以支撑这条接口，之前一律 503「等 C」是**过时判断**
（与 23/25、以及 05—09/13/15/16 那 8 条是同一类误判）。

分工仍是**口径归 C，形状归 D**：

- 「哪个实体有哪些指标与粒度可用」「最新已核验点是什么」由 C 算，D 不自己查表；
- D 只决定**卡片怎么合成**、以及缺数据时给 ``null`` 而不是零。

三条必须守住的口径：

1. **不同指标分卡，绝不求和成「总客流」**（03 §10 红线）。代码上体现为：
   卡片按指标循环生成，一次合计只碰**同一个指标**。
2. **同一指标跨实体求和，只加周期完全一致的**（C 指标字典
   ``aggregation_rule = complete_non_overlapping_periods_only``，
   ``comparability_note`` 也写了「不同景区和时期须核对统计范围」）。
   周期对不上的实体不计入合计，但会体现在 ``status=partial`` 与
   ``covered_entity_count`` 上，**不静默当它不存在**。
3. **没数据就给 null**——``status=no_data``，不用零、不用均值、不用上一周期填充。

一处刻意的降级：某个实体不支持请求粒度时，C 抛 ``UnsupportedGrain``（正常
语义是 422）。但**一个实体缺 month 不该毁掉整张看板**，所以总览里逐条捕获
降级为「该实体无数据」，其余实体照常出卡。这是聚合接口特有的容缺，
单条时序接口（08/09）不这么做——那里就该如实 422。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._pending import pending
from app.api.v1._series import parse_series_date
from app.core.errors import CODE_BAD_REQUEST, AppError
from app.core.permissions import require_authenticated
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.common import Envelope, envelope, iso_cn
from app.schemas.dashboard import (
    DataFreshness,
    MapEntity,
    OverviewCard,
    OverviewResponse,
)

router = APIRouter(prefix="/dashboard", tags=["驾驶舱总览"])

# 03 §10：总览只接受这两种粒度。日粒度是 08/09 的时序专用，看板不做。
OVERVIEW_GRAINS = ("week", "month")

# ⚠️ C 的 ``contracts.Page`` 硬性限制 **page_size 必须在 1—100**（超过直接抛
# ``ValueError``，不在 C 的异常族里，会漏成 500）。所以每页最多取 100，
# 靠翻页把一个地区的实体取全；超过 MAX_PAGES 页就明确拒收，不静默截断——
# 与 05 的「q 超过 50 字符直接 400 而不截断」是同一个原则：宁可让调用方
# 收窄条件，也不给他一份「看起来全、其实缺了一半」的看板。
PAGE_SIZE = 100
MAX_PAGES = 10


@router.get("/overview", response_model=Envelope[OverviewResponse])
async def overview(
    request: Request,
    region_code: str,
    grain: str,
    as_of: str | None = None,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """10 GET /dashboard/overview：分指标卡片；不同指标不求和成总客流。"""
    if not region_code.strip():
        raise AppError(400, CODE_BAD_REQUEST, "region_code 不能为空")
    if grain not in OVERVIEW_GRAINS:
        raise AppError(400, CODE_BAD_REQUEST,
                       f"总览粒度只支持 {' / '.join(OVERVIEW_GRAINS)}",
                       {"detail": f"grain={grain!r}"})
    as_of_date = parse_series_date(as_of, "as_of") if as_of else date.today()

    cstore = get_c_store()
    if cstore is None:
        pending("驾驶舱总览", "C", "data_foundation")

    try:
        # AppError 不是 C 的异常，raise_mapped_c_error 不认，会被原样重抛。
        entities = _fetch_entities(cstore, region_code.strip())
        metrics = cstore.list_metrics()
        # 取点也在同一个 try 里：NotFound / MetricEntityMismatch / QueryLimitExceeded
        # 都可能在逐个实体取最新值时才冒出来。
        cards, latest_by_entity = _build_cards(cstore, entities, metrics,
                                               grain, as_of_date)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "驾驶舱总览")
        raise
    map_entities = _build_map(entities, latest_by_entity)
    freshness = _build_freshness(latest_by_entity)

    # 03 §15：旧公告不自动显示为当前有效——C 的 status=active 按当前时间只返回
    # 仍生效的，正好是「active_events_count」要的口径，D 不自己判时间。
    try:
        active_events = cstore.list_events(region_code=region_code.strip(), status="active")
    except Exception as exc:  # noqa: BLE001
        raise_mapped_c_error(exc, "驾驶舱总览")
        raise

    # 03 §10：as_of 默认「数据库最新已核验日期」。没传时回显**卡片实际取到的
    # 最新周期**，而不是今天——否则调用方会以为数据更新到了今天。一张卡都没有
    # 时才回退到今天（此时确实是「截至今天仍无数据」）。
    if as_of:
        as_of_out = as_of_date.isoformat()
    else:
        periods = [c.period_end for c in cards if c.period_end]
        as_of_out = max(periods) if periods else date.today().isoformat()

    payload = OverviewResponse(
        region_code=region_code.strip(),
        as_of=as_of_out,
        grain=grain,
        cards=cards,
        map_entities=map_entities,
        active_events_count=len(active_events),
        data_freshness=freshness,
    )
    return envelope(request, payload.model_dump(mode="json"))


# ---------- 卡片、地图与新鲜度 ----------


def _fetch_entities(cstore, region_code: str) -> list[dict]:
    """取该地区全部实体：每页最多 100（C 的 Page 上限），翻页取完。"""
    entities: list[dict] = []
    page_no = 1
    while True:
        page = cstore.list_entities(region_code=region_code,
                                    page=page_no, page_size=PAGE_SIZE)
        if page["total"] > PAGE_SIZE * MAX_PAGES:
            raise AppError(400, CODE_BAD_REQUEST,
                           f"该地区实体过多（{page['total']} 个），请改用更精确的 region_code",
                           {"detail": f"region_code={region_code!r}"})
        entities.extend(page["items"])
        if not page["items"] or len(entities) >= page["total"]:
            break
        page_no += 1
    return entities


def _build_cards(cstore, entities: list[dict], metrics: list[dict],
                 grain: str, as_of: date) -> tuple[list[OverviewCard], dict]:
    """按指标出卡（红线：一次合计只碰同一个指标）。

    顺带把每个实体的「主指标最新点」记下来，供地图与新鲜度复用——避免同一
    个 ``get_latest_verified`` 被打两遍。
    """
    cards: list[OverviewCard] = []
    # entity_id -> (metric_code, point)；point 为 None 表示取不到
    latest_by_entity: dict[str, tuple[str | None, dict | None]] = {}

    for metric in metrics:
        code = metric.get("code")
        supported = set(metric.get("supported_entity_types") or [])
        targets = [e for e in entities if e.get("type") in supported]
        if not targets:
            continue  # 该地区没有该指标适用的实体 → 不出这张卡

        contributions: list[tuple[str, dict]] = []
        for entity in targets:
            entity_id = entity.get("id")
            point = _latest_point(cstore, entity_id, code, grain, as_of)
            if point is not None:
                contributions.append((entity_id, point))
            # 主指标（地图打点展示的那个）：取指标字典里第一个适用于该实体类型
            # 的；若它在当前粒度下没数据，退到第一个**有数据**的适用指标。
            # C 的 list_metrics 按 code 排序，所以对 scenic / rail_station /
            # airport 分别稳定落在 scenic_visits / rail_departures /
            # airport_throughput 上。
            current = latest_by_entity.get(entity_id)
            if current is None or (current[1] is None and point is not None):
                latest_by_entity[entity_id] = (code, point)

        cards.append(_card(metric, contributions, targets, grain))

    for entity in entities:  # 没有任何适用指标的实体也要在地图上出现
        latest_by_entity.setdefault(entity.get("id"), (None, None))
    return cards, latest_by_entity


def _latest_point(cstore, entity_id: str, metric: str, grain: str, as_of: date):
    """取该序列截至 as_of 的最新已核验点；不支持该粒度时给 None。

    只吞 ``UnsupportedGrain``：这是「该序列没有这个粒度的数据」，在聚合接口
    里属于正常缺数。其它异常（实体不存在、指标不适用于该类型、查询超限…）
    一律继续上抛，由调用方统一映射，绝不吞成「没数据」。
    """
    from data_foundation import UnsupportedGrain

    try:
        point = cstore.get_latest_verified(entity_id=entity_id, metric=metric,
                                          grain=grain, as_of=as_of)
    except UnsupportedGrain:
        point = None
    if hasattr(cstore,'read'):
        from data_foundation.ingestion.project_flow import read_rows,point as project_point
        project=[r for r in read_rows(cstore,entity_id,grain,end=as_of) if r['metric']==metric]
        if project and (point is None or str(project[-1]['period_end'])>_day(point.get('period_end'))):
            point=project_point(project[-1])
    if point is None and hasattr(cstore,'read'):
        from app.services.native_series import query_series
        series = query_series(entity_id,metric,preferred_grain=grain,as_of=as_of,store=cstore)
        point = series['points'][-1] if series['points'] else None
    return {**point,'period_start':_day(point.get('period_start')),'period_end':_day(point.get('period_end'))} if point else None


def _card(metric: dict, contributions: list[tuple[str, dict]],
          targets: list[dict], grain: str) -> OverviewCard:
    """同一指标的多个实体点 → 一张卡。

    按 ``aggregation_rule = complete_non_overlapping_periods_only``：
    **只合计周期完全相同的点**。先挑该地区最新的那个周期，落在该周期之外的
    实体不计入合计，但计入 ``entity_count``，于是 ``status`` 会是 ``partial``
    ——把「缺了谁」说清楚，而不是假装全。
    """
    if not contributions:
        return OverviewCard(
            metric=metric.get("code"),
            metric_display_name=metric.get("display_name") or metric.get("code"),
            unit=metric.get("unit"),
            grain=grain, value=None, source_ids=[], quality_status=None,
            published_at=None, status="no_data",
            entity_count=len(targets), covered_entity_count=0,
        )

    # period_end 最大的那个周期就是「该地区该指标的最新周期」
    newest = max(contributions, key=lambda item: item[1]["period_end"])
    period_start, period_end = newest[1]["period_start"], newest[1]["period_end"]
    same = [item for item in contributions
            if item[1]["period_start"] == period_start
            and item[1]["period_end"] == period_end
            and item[1].get('grain',grain) == newest[1].get('grain',grain)
            and item[1].get('unit',metric.get('unit')) == newest[1].get('unit',metric.get('unit'))]

    # value 是 Decimal（C 用 Numeric 存观测值）：先用 Decimal 求和，最后一次转
    # float，避免浮点逐项累加的误差。观测值理论上非空，这里仍跳过 None——
    # 一条脏数据不该让整张看板 500。
    total = sum((Decimal(item[1]["value"]) for item in same
                 if item[1].get("value") is not None), Decimal("0"))
    source_ids = sorted({sid for _, point in same for sid in (point.get("source_ids") or [])})
    published = max((point.get("published_at") for _, point in same
                     if point.get("published_at")), default=None)
    qualities = {point.get("quality_status") for _, point in same}
    status = "ok" if len(same) == len(targets) else "partial"

    return OverviewCard(
        metric=metric.get("code"),
        metric_display_name=metric.get("display_name") or metric.get("code"),
        unit=metric.get("unit"),
        period_start=_day(period_start),
        period_end=_day(period_end),
        grain=newest[1].get('grain',grain),
        value=float(total),
        source_ids=source_ids,
        quality_status=qualities.pop() if len(qualities) == 1 else "mixed",
        published_at=iso_cn(published),
        status=status,
        entity_count=len(targets),
        covered_entity_count=len(same),
    )


def _build_map(entities: list[dict], latest_by_entity: dict) -> list[MapEntity]:
    """地图打点：位置 + 主指标最新值。

    经纬度**可为 null**（与 05 一致）——C 允许实体不填坐标，没有就不打点，
    调用方别把 null 当 0，否则会出现 (0,0) 的假点位。
    """
    result: list[MapEntity] = []
    for entity in entities:
        metric_code, point = latest_by_entity.get(entity.get("id"), (None, None))
        result.append(MapEntity(
            id=entity.get("id"),
            name=entity.get("name"),
            type=entity.get("type"),
            longitude=_num(entity.get("longitude")),
            latitude=_num(entity.get("latitude")),
            latest_value=_num(point.get("value")) if point else None,
            latest_period_end=_day(point.get("period_end")) if point else None,
            metric=metric_code,
            latest_metric=metric_code,
            latest_period_start=_day(point.get('period_start')) if point else None,
            latest_grain=point.get('grain') if point else None,
            unit=point.get('unit') if point else None,
        ))
    return result


def _build_freshness(latest_by_entity: dict) -> list[DataFreshness]:
    """数据新鲜度：只列**真的取到点**的实体，取不到的不编一条出来。"""
    result: list[DataFreshness] = []
    for entity_id, (metric_code, point) in latest_by_entity.items():
        if point is None or not metric_code:
            continue
        result.append(DataFreshness(
            entity_id=entity_id,
            metric=metric_code,
            latest_period_end=_day(point.get("period_end")),
            ingested_at=iso_cn(point.get("ingested_at")),
        ))
    return result


def _day(value) -> str | None:
    """date（或 datetime）→ ``YYYY-MM-DD``。"""
    if value is None:
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value.isoformat()
    text = iso_cn(value)
    return text[:10] if text else None


def _num(value) -> float | None:
    if value is None:
        return None
    return float(value)
