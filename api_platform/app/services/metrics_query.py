"""19 号问答的**受约束只读数值查询工具**（注入 E 的 ``metric_tool``）。

背景（E 0.6.0 ``intelligent_analysis`` 实测契约）
------------------------------------------------

``service.answer_message(message, conversation_id, context)`` 在 ``context``
含 ``"metric_query"`` 键时**完全改走数值路径**、不再检索知识库：

    result = run_metric_query(context["metric_query"], context.get("metric_tool"))

- ``metric_tool`` 为 ``None``、或返回 ``{"status": "metric_tool_unavailable"}``
  → E 直接输出「指标查询服务尚未接入，无法核实数值。」（status 同名）。
- 否则 E 要求返回 dict **必须** 含 ``metric`` / ``value`` / ``source_ids`` /
  ``period_start`` / ``period_end`` / ``queried_at`` 六个键且 ``source_ids``
  非空，缺任一键就 ``raise ValueError``（经 D 的全局异常映射会变成 500）。

因此本模块的三条硬约束：

1. **绝不返回「半个成功」**——要么六个键齐全且 ``source_ids`` 非空，要么
   明确 ``metric_tool_unavailable``。没有中间态。
2. **数据源没接入就报不足，不用历史资料顶替**——E 的 metric 路径本就不看
   知识库，正是「不让历史资料冒充当前数值」的落点；D 也不在别处伪造数值。
3. **白名单收口**——指标、对象类型、粒度、时间范围全部服务端校验，调用方
   （含 B 的前端）只能从 ``METRIC_CATALOG`` 里选，传任意 SQL/任意指标名一律
   拒收（``ChatContext`` 是 ``extra="forbid"``，非法键在路由层就 400）。

2026-10-17 更新：**景区 / 铁路 / 机场三个指标已接真实观测**
--------------------------------------------------------------

``scenic_visits`` / ``rail_departures`` / ``airport_throughput`` 现在真去查
C 的 ``uow.observations.query_series``（与 08 / 09 / 10 同一条口径），
不再是「一律报不足」。取值规则：

- 区间恰好落在一个完整周期（如 2025-01-01 ~ 2025-01-31 的月粒度）→
  返回那一个点的值、单位、来源与**它自己的周期**；
- 区间跨多个周期 → **只有**在该粒度所有周期都有已核验观测（``missing_periods``
  为空）**且** C 指标字典的 ``aggregation_rule`` 允许时才合计；
  缺一期就整条报不足（C 021a2《27-契约收口》「缺天不能用已有天数分母」同一口径）；
- 一次只核实一个对象：跨对象合计属于看板 TOP-N 的口径，不在问答的数值核实
  范围内，宁可报不足也不给一个没地方挂单位的总数；
- 拿不到数的情况（无已核验观测、缺期、粒度不支持、指标与实体不匹配、查询
  超限、实体不存在）一律翻译成 ``metric_tool_unavailable`` + reason，
  绝不合成数值，也绝不让它漏成 500。

``camera_visible_people`` 仍诚实报不足：它依赖真实录像分析结果，而
``videos`` / ``permission_records`` 至今 0 行，没有可用的取值来源。

最后一个不变的前提：**E 会把 result 原样塞进 ``metric_result`` 回前端**，
所以对外字段只写 ``source_public``（接口级），内部表名 / 责任方只进日志。

指标字典来自 03-接口总览 §1（``scenic_visits`` / ``rail_departures`` /
``airport_throughput`` / ``camera_visible_people``）；**新增指标必须先更新
那儿再更新本表**，两处保持一致。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

logger = logging.getLogger("api_platform.metrics_query")

#: 业务时区（+08:00）。19 号的日期串不带时区，按 C 26 号文档 §6
#: 「按 +08 业务日转成 C 的闭区间 date」的口径落到同一天。
BUSINESS_TZ = timezone(timedelta(hours=8))

#: 指标字典（03-接口总览 §1 口径，键与前端/B 的 ``metric`` 字段同源）。
#:
#: - ``unit``：含数字返回的单位（人次 / 人）；
#: - ``entity_types``：允许的对象类型（``ChatContext.entity_ids`` 的身份类型）；
#: - ``grains``：允许的粒度（``ChatContext.grain`` 只到 day/week/month）；
#: - ``source``：**内部**来源说明（可写表名，只进日志 / README / 回执）；
#: - ``source_public``：可回给前端的来源说明（只到接口级，不暴露表与路径）；
#: - ``blocked_by``：当前拿不到数的责任方；``None`` 表示数据源已通。
METRIC_CATALOG: dict[str, dict[str, Any]] = {
    "scenic_visits": {
        "label": "景区接待人次",
        "unit": "人次",
        "entity_types": ("scenic",),
        "grains": ("day", "week", "month"),
        "source": "C 观测库（08 GET /scenic/{scenic_id}/series）",
        "source_public": "景区接待观测数据（08 号接口）",
        "blocked_by": None,
    },
    "rail_departures": {
        "label": "铁路发送人次",
        "unit": "人次",
        "entity_types": ("station",),
        "grains": ("day", "week", "month"),
        "source": "C 观测库（09 GET /transport/{entity_id}/series）",
        "source_public": "交通客流观测数据（09 号接口）",
        "blocked_by": None,
    },
    "airport_throughput": {
        "label": "机场旅客吞吐量",
        "unit": "人次",
        "entity_types": ("station",),
        "grains": ("day", "week", "month"),
        "source": "C 观测库（09 GET /transport/{entity_id}/series）",
        "source_public": "交通客流观测数据（09 号接口）",
        "blocked_by": None,
    },
    "camera_visible_people": {
        "label": "录像画面区域估计可见人数",
        "unit": "人",
        "entity_types": ("camera",),
        "grains": ("day", "week", "month"),
        # D 自有持久化（表 d_video_analyses），但 camera_id → video_id 的映射
        # 依赖 C 的 23/24 视频元数据，故仍卡在 C。
        "source": "D d_video_analyses + C 视频元数据（23 GET /videos）",
        "source_public": "录像画面人数分析结果（23/27 号接口）",
        "blocked_by": "C",
    },
}

#: ``ChatContext.metric`` 的合法取值（schema 校验用，顺序稳定便于报错可读）。
SUPPORTED_METRICS: tuple[str, ...] = tuple(METRIC_CATALOG)

#: ``ChatContext.grain`` 允许的取值（03 §1：业务客流用 day/week/month/holiday；
#: 19 号问答上下文只开放前三者，holiday 走 14 号对比接口）。
SUPPORTED_GRAINS: tuple[str, ...] = ("day", "week", "month")

#: 单次查询允许的对象数量上限（防止一次拉全库）。
MAX_ENTITY_IDS = 20

#: 由 C 观测库直接取值（``uow.observations.query_series``）的三个对象类指标。
#: ``camera_visible_people`` 不在其中——它依赖录像分析结果，当前没有真实录像。
OBSERVED_METRICS: frozenset[str] = frozenset({
    "scenic_visits", "rail_departures", "airport_throughput",
})

#: C 指标字典里允许「跨完整不重叠周期合计」的规则值。取值来自 C 的
#: ``list_metrics``，**不是 D 自己发明的口径**；字典不给这个值就不合计。
AGG_RULE_COMPLETE_NON_OVERLAPPING = "complete_non_overlapping_periods_only"

#: 不可用原因码（回执/日志/排障用，不向最终用户暴露内部细节）。
REASON_UNSUPPORTED_METRIC = "unsupported_metric"
REASON_UNSUPPORTED_GRAIN = "unsupported_grain"
REASON_INVALID_PERIOD = "invalid_period"
REASON_INVALID_ENTITY = "invalid_entity"
REASON_METRIC_ENTITY_MISMATCH = "metric_entity_mismatch"
REASON_NO_VERIFIED_DATA = "no_verified_data"
REASON_INCOMPLETE_PERIOD = "incomplete_period"
REASON_MULTI_PERIOD = "multi_period_not_comparable"
REASON_MULTI_ENTITY = "multi_entity_not_supported"
REASON_UNUSABLE_POINTS = "unusable_points"
REASON_DATA_SOURCE_UNAVAILABLE = "data_source_unavailable"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _unavailable(reason: str, *, metric: str | None = None,
                 detail: str = "", **extra: Any) -> dict[str, Any]:
    """统一构造「查不到数」的返回。

    E 见到 ``status == "metric_tool_unavailable"`` 就输出「无法核实数值」，
    **不会**退回知识库去编一个像样的答案——这正是我们要的行为。
    """
    payload = {"status": "metric_tool_unavailable", "reason": reason,
               "metric": metric, "detail": detail, "queried_at": _now_iso()}
    payload.update(extra)
    # E 0.6.2 rejects success fields even when their value is None on failures.
    return {key: value for key, value in payload.items() if key not in {
        "metric", "value", "source_ids", "period_start", "period_end", "queried_at"
    }}


def _normalise_window(start: datetime, end: datetime, grain: str
                      ) -> tuple[datetime, datetime]:
    """外部区间的**开闭语义收敛**（2026-10-17）。

    C 交付包里的参考适配器 ``examples/c_readonly_metric_tool.py`` 按 **[start,
    end)** 实现（26 号文档 §6 原话：「外部区间定义为 [start,end)」），而 D 的
    08/09/10/14 与 C 的 ``SeriesQuery`` 都是**闭区间**。两者只在「end 恰好落在
    下一个周期的起点」时有分歧。

    这里做**宽容归一**，让两种写法都指向同一批周期，而不是二选一：

    - ``grain=month``：end 是某月 1 号且不与 start 同月 → 右端点回退一天
      （``2025-01-01~2025-02-01`` 与 ``2025-01-01~2025-01-31`` 都指整个 1 月）；
    - ``grain=week``：end 是周一且跨度 ≥ 7 天 → 同样回退一天；
    - ``grain=day``：日粒度回退会让「到今天为止」少算一天，保持闭区间。

    中间残余场景（end 落在周期腰部）仍按闭区间——这一条已向 C 提问，等他给正式
    口径；就算他要改成严格半开，也只需要改这一个函数。
    """
    if grain == "month" and end.day == 1 and             (end.year, end.month) != (start.year, start.month):
        return start, end - timedelta(days=1)
    if grain == "week" and end.weekday() == 0 and (end - start) >= timedelta(days=7):
        return start, end - timedelta(days=1)
    return start, end

def _parse_period(value: Any) -> datetime | None:
    """解析 ISO 8601；**缺时区时按 +08:00 业务时区补全**，不再直接判无效。

    为什么必须补：19 号 HTTP 的 ``context.period_start / period_end`` 是
    **日期串**（03 §19 只写 ``period_start, period_end``，没要求带时区，前端按
    日期给它），而这一层本来硬性要求 aware datetime。结果就是——真机 HTTP 的每
    一条数值核实都走 ``invalid_period`` 变成「不足」，而 pytest 用例用的是带时区
    的 datetime，**测试全绿、真机全废**（第六次「单测入参形状与真实入参形状不
    一致」的同类误判，2026-10-17 由 E 的独立实测暴露）。

    时区口径与 C 一致：26 号文档 §6 明确「按 +08 业务日转成 C 的闭区间 date」。

    ⚠️ **带时区的输入必须先 ``astimezone(BUSINESS_TZ)`` 再 ``.date()``**（2026-10-17
    晚按 C《致D和E-021a2接收核对与补充答复》§6 修）：``2024-12-31T16:00Z`` 的北京
    业务日是 **2025-01-01**，而直接 ``.date()`` 会得到 2024-12-31——跨年、跨月的
    UTC 输入会整段错位一个周期。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TZ)
    else:
        parsed = parsed.astimezone(BUSINESS_TZ)
    return parsed


def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError):
        return False
    return True


def normalise_query(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """把 19 号请求的 ``context`` 收敛成一个受约束查询；**不该查数就返回 None**。

    触发条件（保守）：调用方**显式**指定了 ``metric``。只给 entity_ids /
    period 不给 metric 时不注入——那是「顺便带个范围」的文档问答，不能因为
    带了范围就把知识库问答整条掐掉（E 的 metric 路径是不检索知识库的）。

    返回的 query 只含白名单内、类型确定的键，E 会把它原样塞进
    ``used_metric_queries`` 供溯源，因此**不放任何内部表名或凭据**。
    """
    if not context:
        return None
    metric = context.get("metric")
    if not isinstance(metric, str) or metric not in METRIC_CATALOG:
        return None
    entity_ids = [str(e) for e in (context.get("entity_ids") or []) if e]
    query: dict[str, Any] = {
        "metric": metric,
        "entity_type": METRIC_CATALOG[metric]["entity_types"][0],
        "entity_ids": entity_ids,
        "grain": context.get("grain") or "day",
        "period_start": context.get("period_start"),
        "period_end": context.get("period_end"),
    }
    return query


def metric_tool(query: dict[str, Any]) -> dict[str, Any]:
    """注入 E 的 ``metric_tool``：``dict -> dict``。

    成功时必须返回 ``metric`` / ``value`` / ``source_ids`` /
    ``period_start`` / ``period_end`` / ``queried_at`` 六键且 ``source_ids``
    非空（E 会据此生成 citation 与 ``metric_result``）；**当前所有指标的数据
    源都还没接通（卡 C 的观测/视频接口），因此一律走「不足」分支**——
    宁可让 E 说「无法核实数值」，也不拿历史资料或旧快照顶替当前值。

    等 C 的 08/09/23 落地后，只需在本函数内按 metric 分支补 resolver，
    返回形状不变，E 侧零改动。
    """
    if not isinstance(query, dict):
        return _unavailable(REASON_INVALID_ENTITY, detail="查询体必须是对象")

    metric = query.get("metric")
    spec = METRIC_CATALOG.get(metric) if isinstance(metric, str) else None
    if spec is None:
        return _unavailable(
            REASON_UNSUPPORTED_METRIC, metric=metric if isinstance(metric, str) else None,
            detail=f"指标不在字典内，可用：{', '.join(SUPPORTED_METRICS)}",
        )

    grain = query.get("grain")
    if grain not in spec["grains"]:
        return _unavailable(
            REASON_UNSUPPORTED_GRAIN, metric=metric,
            detail=f"{metric} 不支持粒度 {grain}，可用：{', '.join(spec['grains'])}",
        )

    entity_ids = query.get("entity_ids") or []
    if not isinstance(entity_ids, list) or not entity_ids:
        return _unavailable(REASON_INVALID_ENTITY, metric=metric,
                            detail="必须指定至少一个 entity_id（对象白名单内）")
    if len(entity_ids) > MAX_ENTITY_IDS:
        return _unavailable(REASON_INVALID_ENTITY, metric=metric,
                            detail=f"单次最多 {MAX_ENTITY_IDS} 个对象")
    if not all(_is_uuid(e) for e in entity_ids):
        return _unavailable(REASON_INVALID_ENTITY, metric=metric,
                            detail="entity_ids 必须是 UUID")

    start = _parse_period(query.get("period_start"))
    end = _parse_period(query.get("period_end"))
    if start is None or end is None:
        return _unavailable(REASON_INVALID_PERIOD, metric=metric,
                            detail="period_start / period_end 必填且须为带时区的 ISO 8601")
    if start >= end:
        return _unavailable(REASON_INVALID_PERIOD, metric=metric,
                            detail="period_start 必须早于 period_end")

    start, end = _normalise_window(start, end, grain)

    if metric in OBSERVED_METRICS:
        if len(entity_ids) != 1:
            return _unavailable(
                REASON_MULTI_ENTITY, metric=metric,
                detail=(f"一次只能核实一个对象（本次给了 {len(entity_ids)} 个）："
                        "跨对象合计属于看板 TOP-N 的口径，不在问答的数值核实范围内"))
        cstore = _get_c_store()
        if cstore is None:
            return _unavailable(
                REASON_DATA_SOURCE_UNAVAILABLE, metric=metric, unit=spec["unit"],
                entity_type=spec["entity_types"][0],
                period_start=start.isoformat(), period_end=end.isoformat(),
                grain=grain,
                detail=f"指标「{spec['label']}」的数据源（{spec['source_public']}）当前不可用",
            )
        return _resolve_observations(
            cstore, metric=metric, spec=spec, entity_id=entity_ids[0],
            grain=grain, start=start, end=end,
        )

    # camera_visible_people：依赖真实录像分析结果（23/24/27），而 C 的 videos /
    # permission_records 表至今 0 行。没有素材就没有数值，不做合成。
    logger.info(
        "指标查询不足：metric=%s reason=%s blocked_by=%s source=%s",
        metric, REASON_DATA_SOURCE_UNAVAILABLE, spec.get("blocked_by"), spec["source"],
    )
    return _unavailable(
        REASON_DATA_SOURCE_UNAVAILABLE, metric=metric,
        detail=f"指标「{spec['label']}」的数据源（{spec['source_public']}）尚未接通，无法核实当前数值",
        unit=spec["unit"],
        entity_type=spec["entity_types"][0],
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        grain=grain,
    )


# ---------- 观测类指标：走 C 的 observations.query_series ----------


def _get_c_store():
    """函数内取 C store：模块顶部 from-import 会让测试的 monkeypatch 失效
    （用户文件卫生红线：默认路径漂移同理）。"""
    from app.core.providers import get_c_store
    return get_c_store()


def _iso_day(value: Any) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if iso is None:
        return str(value)
    text = iso()
    return text[:10]


def _unit_of(point: dict, fallback: str) -> str:
    return point.get("unit") or fallback


def _aggregation_rule(cstore, entity_type: str | None, metric: str) -> str | None:
    """从 C 的指标字典取聚合规则。

    字典读不到时返回 ``None``——调用方据此**拒绝合计**，不会因为读不到口径
    就默认可以相加。
    """
    try:
        rows = cstore.list_metrics(entity_type)
    except Exception as exc:  # noqa: BLE001 —— 字典读不出就当作不允许聚合
        logger.info("指标字典不可用，按不可合计处理：%s", exc)
        return None
    for row in rows or []:
        if row.get("code") == metric:
            return row.get("aggregation_rule")
    return None


def _result(*, metric: str, value: Any, unit: str, source_ids: list[str],
            period_start: Any, period_end: Any, grain: str,
            entity_type: str | None, entity_name: str | None,
            aggregated: bool, points_count: int,
            source_public: str, extra: dict | None = None) -> dict[str, Any]:
    """E 要求的最小六键 + D 追加的口径说明。

    E 拿到 ``status`` 为 ``metric_tool_unavailable`` 时不使用这些键；成功时不带
    status，E 会用 ``metric`` / ``source_ids`` 生成 citation。
    """
    payload = {
        "metric": metric,
        "value": value,
        "source_ids": source_ids,
        "period_start": _iso_day(period_start),
        "period_end": _iso_day(period_end),
        "queried_at": _now_iso(),
        "unit": unit,
        "grain": grain,
        "entity_type": entity_type,
        "entity_name": entity_name,
        "aggregated": aggregated,
        "points_count": points_count,
        "source_public": source_public,
    }
    if extra:
        payload.update(extra)
    return payload


def _resolve_observations(cstore, *, metric: str, spec: dict, entity_id: str,
                          grain: str, start: datetime, end: datetime) -> dict[str, Any]:
    """向 C 要一段序列，按周期完整性与字典口径决定「给一个值」还是「报不足」。"""
    try:
        from data_foundation import (DataFoundationError, InvalidArgument,
                                     MetricEntityMismatch, NotFound,
                                     QueryLimitExceeded, UnsupportedGrain)

        try:
            series = cstore.query_series(entity_id=entity_id, metric=metric,
                                         grain=grain, start=start.date(),
                                         end=end.date(), quality="verified")
        except NotFound as exc:
            return _unavailable(REASON_INVALID_ENTITY, metric=metric, unit=spec["unit"],
                                entity_type=spec["entity_types"][0],
                                detail="对象或指标不存在（或无权访问）",
                                **_window(start, end))
        except MetricEntityMismatch as exc:
            return _unavailable(REASON_METRIC_ENTITY_MISMATCH, metric=metric,
                                unit=spec["unit"], entity_type=spec["entity_types"][0],
                                detail=f"指标「{spec['label']}」不适用于该对象类型",
                                **_window(start, end))
        except UnsupportedGrain as exc:
            supported = getattr(exc, "supported", None) or []
            hint = f"，该序列可用粒度：{', '.join(supported)}" if supported else ""
            return _unavailable(REASON_UNSUPPORTED_GRAIN, metric=metric,
                                unit=spec["unit"], entity_type=spec["entity_types"][0],
                                detail=f"来源不支持 {grain} 粒度{hint}",
                                **_window(start, end))
        except (QueryLimitExceeded, InvalidArgument) as exc:
            return _unavailable(REASON_INVALID_PERIOD, metric=metric,
                                unit=spec["unit"], entity_type=spec["entity_types"][0],
                                detail="查询区间不被 C 接受（超长、逆序或粒度非法）",
                                **_window(start, end))
        except DataFoundationError as exc:
            logger.warning("观测查询失败 metric=%s：%s", metric, exc)
            return _unavailable(REASON_DATA_SOURCE_UNAVAILABLE, metric=metric,
                                unit=spec["unit"], entity_type=spec["entity_types"][0],
                                detail="数据层当前不可用，未能核实数值",
                                **_window(start, end))
        except Exception as exc:  # noqa: BLE001 —— 兜底：绝不让异常穿到 E 里炸 500
            logger.exception("观测查询异常 metric=%s", metric)
            return _unavailable(REASON_DATA_SOURCE_UNAVAILABLE, metric=metric,
                                unit=spec["unit"], entity_type=spec["entity_types"][0],
                                detail="未能核实数值（数据层异常）",
                                **_window(start, end))
    except ImportError:  # C 包缺失：明确报不足，不假装查过
        return _unavailable(REASON_DATA_SOURCE_UNAVAILABLE, metric=metric,
                            unit=spec["unit"], entity_type=spec["entity_types"][0],
                            detail="数据基座不可用，未能核实数值",
                            **_window(start, end))

    entity = series.get("entity") or {}
    points = [p for p in (series.get("points") or []) if p.get("value") is not None]
    missing = series.get("missing_periods") or []
    rule = _aggregation_rule(cstore, entity.get("type"), metric)
    common = {
        "unit": _unit_of(points[0] if points else {}, spec["unit"]),
        "grain": grain,
        "entity_type": entity.get("type") or spec["entity_types"][0],
        "entity_name": entity.get("name"),
        "source_public": spec["source_public"],
    }

    # 逐点可用性。这一步不能省：E 要求 source_ids **非空**，空列表会让 E 抛
    # ValueError → 经全局映射变成 500。单位与核验状态同理——比来源未登记更
    # 危险的是「来源不知所谓但数字照给」。
    bad = _unusable_points(points, spec["unit"])
    if bad:
        return _unavailable(
            REASON_UNUSABLE_POINTS, metric=metric,
            detail=(f"观测点不满足出示要求（{bad}）：单位须为 {spec['unit']}、"
                    "须为已核验且带非空来源，无法据此核实数值"),
            **common, **_window(start, end))

    if not points:
        if missing:
            logger.info("指标查询无已核验观测：metric=%s entity=%s %s~%s grain=%s",
                        metric, entity_id, start.date(), end.date(), grain)
            return _unavailable(
                REASON_NO_VERIFIED_DATA, metric=metric,
                detail=(f"该区间没有 {grain} 粒度的已核验观测"
                        f"（缺 {len(missing)} 个周期），无法核实数值"),
                **common, **_window(start, end))

        # 有代表性的一类：区间落在周期腰部。C 的 ``query_series`` 按**完整周期**
        # 出数，不是完整周期就一个点都不给，**而且 missing_periods 也是空的**
        # （2026-10-17 真机实测：合肥新桥机场 2025-01-01~2025-01-20 / 01-15~02-15 /
        # 单日三种情形，C 一律 points=[] 且 missing=[]）。
        # 这时候报 ``no_verified_data`` 是**误导**——观测明明存在（整月查得到
        # 1,111,644），只是「这个区间不构成一个完整周期」。这正是 C《致D和
        # E-021a2接收核对与补充答复》§6 要求的「中间边界要报不足」，理由要说准。
        logger.info(
            "查询区间未覆盖完整周期：metric=%s entity=%s %s~%s grain=%s",
            metric, entity_id, start.date(), end.date(), grain)
        return _unavailable(
            REASON_INCOMPLETE_PERIOD, metric=metric,
            detail=(f"请求区间 {start.date()}~{end.date()} 未覆盖任何完整的 "
                    f"{grain} 周期；C 的观测按完整周期出数，不能把周期的一部分"
                    f"当成区间的数值。请把区间对齐到 {grain} 周期边界"
                    f"（如整月取 月初~月末）"),
            **common, **_window(start, end))

    # 一个完整周期 ⟺ 没有缺期且只有一个点。区间只要跨到第二个周期，就必须
    # 每个周期都有观测才能给值——只拿到一期就回数，等于把一期的值谎报成区间值。
    if len(points) == 1 and not missing:
        point = points[0]
        return _result(
            metric=metric, value=_as_number(point["value"]),
            source_ids=list(point.get("source_ids") or []),
            period_start=point.get("period_start"), period_end=point.get("period_end"),
            aggregated=False, points_count=1,
            extra={"selection_policy_version": series.get("selection_policy_version")},
            **common)

    # 多周期：只有「字典允许合计 + 周期完整 + 同粒度同单位」才给合计值
    units = {p.get("unit") or spec["unit"] for p in points}
    if missing:
        return _unavailable(
            REASON_INCOMPLETE_PERIOD, metric=metric,
            detail=(f"区间跨 {len(points) + len(missing)} 个 {grain} 周期，"
                    f"其中 {len(missing)} 个缺已核验观测——缺期不能拿已有周期凑"),
            **common, **_window(start, end))
    if rule != AGG_RULE_COMPLETE_NON_OVERLAPPING:
        return _unavailable(
            REASON_MULTI_PERIOD, metric=metric,
            detail=(f"区间跨 {len(points)} 个 {grain} 周期，C 指标字典未允许该指标"
                    "跨周期合计；请把区间收窄到单个周期"),
            **common, **_window(start, end))
    if len(units) != 1:
        return _unavailable(
            REASON_MULTI_PERIOD, metric=metric,
            detail=f"区间内单位口径不唯一（{', '.join(sorted(units))}），不给合计值",
            **common, **_window(start, end))

    total = Decimal(0)
    for point in points:
        total += Decimal(str(point["value"]))
    source_ids: list[str] = []
    for point in points:
        for source_id in point.get("source_ids") or []:
            if source_id not in source_ids:
                source_ids.append(source_id)
    period_starts = [p.get("period_start") for p in points]
    period_ends = [p.get("period_end") for p in points]
    return _result(
        metric=metric, value=_as_number(total),
        source_ids=source_ids,
        period_start=min(period_starts), period_end=max(period_ends),
        aggregated=True, points_count=len(points),
        extra={"selection_policy_version": series.get("selection_policy_version")},
        **common)


def _unusable_points(points: list[dict], unit: str) -> str:
    """逐点可用性检查：单位、核验状态、来源三样缺一不可。

    返回一个描述不可用的原因；全部合格返回空串。理由同上——E 会拿
    ``source_ids`` 生成 citation，空列表直接 ValueError → 500。
    """
    for point in points:
        if point.get("unit") != unit:
            return f"单位口径为 {point.get('unit')!r}，与指标字典不符"
        if point.get("quality_status") != "verified":
            return f"存在 {point.get('quality_status')!r} 状态的观测"
        if not point.get("source_ids"):
            return "存在没有来源登记的观测"
    return ""


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"period_start": _iso_day(start), "period_end": _iso_day(end)}


def _as_number(value: Any) -> float | int:
    """Decimal → float / int，保持 E 侧可 JSON 序列化且便于展示。"""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value
