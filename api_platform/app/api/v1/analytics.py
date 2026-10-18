"""序列比较（03-接口总览 §5：11）。

2026-10-17 接线：C 0.2.0a2 的 ``uow.observations.query_series`` 完全够用——
11 就是把 2—4 条序列各查一遍再合成一个响应，之前 503「等 C」同样是过时判断。

口径红线（03 §11）：**混合不同指标时只能用 separate_axes 或 index_100，
不能把原始值画到同一数值轴或求和**。代码上的落实：

- 卡片/图表级的求和在 D 这里**根本不存在**，D 只把每条序列原样返回；
- ``index_100`` 下点位值换成无量纲指数，``unit`` 置为 ``index``，避免调用方
  把「指数」和「人次」混着读；
- 基期取不到有效值时**整条 400 拒收**，而不是返回一半指数一半原始值——
  半指数半原始值正是契约最担心的那种「看似可比实则不可比」。

``comparison_note`` 里逐条写清：涉及哪些指标、C 指标字典的可比性口径原文、
每条序列覆盖了多少期、基期与基数。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._pending import pending
from app.api.v1._series import missing_out, parse_series_date, points_out
from app.core.errors import CODE_BAD_REQUEST, AppError
from app.core.permissions import require_csrf
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.analytics import CompareRequest, CompareResponse, ComparedSeries
from app.schemas.common import Envelope, envelope

router = APIRouter(prefix="/analytics", tags=["分析比较"])


@router.post("/compare", response_model=Envelope[CompareResponse])
async def compare(
    request: Request,
    body: CompareRequest,
    _session=Depends(require_csrf),
    _dep=Depends(require_data_foundation),
) -> dict:
    """11 POST /analytics/compare：2—4 条序列比较。

    约束：混合指标只能 separate_axes 或 index_100，不能同轴求和。
    """
    start = parse_series_date(body.start, "start")
    end = parse_series_date(body.end, "end")
    if start > end:
        raise AppError(400, CODE_BAD_REQUEST, "start 不能晚于 end",
                       {"detail": f"start={body.start} end={body.end}"})
    base_date = (parse_series_date(body.base_period, "base_period")
                 if body.base_period else None)
    for spec in body.series:
        try:
            UUID(spec.entity_id)
        except ValueError:
            # 与 08/09 一致：entity_id 不是 UUID 就别往 C 那边送，直接 400 说清楚。
            raise AppError(400, CODE_BAD_REQUEST,
                           "entity_id 不是合法 UUID",
                           {"detail": f"entity_id={spec.entity_id!r}"}) from None

    cstore = get_c_store()
    if cstore is None:
        pending("序列比较", "C", "data_foundation")

    rows: list[dict] = []
    try:
        for spec in body.series:
            rows.append(cstore.query_series(
                entity_id=spec.entity_id, metric=spec.metric, grain=body.grain,
                start=start, end=end, quality="verified",
            ))
        metric_notes = {m.get("code"): m for m in cstore.list_metrics()}
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "序列比较")
        raise

    if body.display_mode == "index_100":
        series = _indexed_series(rows, base_date)
    else:
        series = _raw_series(rows)

    payload = CompareResponse(
        grain=body.grain,
        start=body.start,
        end=body.end,
        display_mode=body.display_mode,
        base_period=body.base_period,
        series=series,
        comparison_note=_comparison_note(body, rows, series, metric_notes),
    )
    return envelope(request, payload.model_dump(mode="json"))


# ---------- 序列合成 ----------


def _raw_series(rows: list[dict]) -> list[ComparedSeries]:
    """separate_axes：原始值原样返回，各自独立的数值轴。"""
    result: list[ComparedSeries] = []
    for row in rows:
        points = points_out(row.get("points"))
        result.append(ComparedSeries(
            entity_id=_entity_id(row),
            metric=_metric_code(row),
            points=points,
            missing_periods=missing_out(row.get("missing_periods")),
            source_ids=_source_ids(points),
            unit=_first_unit(points),
            base_value=None,
        ))
    return result


def _indexed_series(rows: list[dict], base_date) -> list[ComparedSeries]:
    """index_100：按基期归一化为 100。

    任一条序列在基期取不到**有效**基数（没点、点为 0）就整条 400——宁可让
    调用方换个 base_period，也不返回「一半指数一半原始值」这种看似可比、
    实则不可比的响应。
    """
    bases: list[float] = []
    missing: list[str] = []
    for row in rows:
        label = f"{_metric_code(row)}@{_entity_name(row)}"
        base = _base_value(row.get("points"), base_date)
        if base is None or base == 0:
            missing.append(label)
        bases.append(base if base else 0.0)
    if missing:
        raise AppError(400, CODE_BAD_REQUEST,
                       "基期无有效数据，无法指数化：" + "、".join(missing),
                       {"detail": f"base_period={base_date.isoformat()}"})

    result: list[ComparedSeries] = []
    for row, base in zip(rows, bases):
        raw = points_out(row.get("points"))
        points = []
        for point in raw:
            value = point.get("value")
            if value is None:  # 观测值理论上非空；真遇到就跳过，不让它炸掉整条比较
                continue
            item = dict(point)
            item["value"] = float(value) / base * 100.0
            item["unit"] = "index"  # 无量纲；原始单位放在序列级 unit
            points.append(item)
        result.append(ComparedSeries(
            entity_id=_entity_id(row),
            metric=_metric_code(row),
            points=points,
            missing_periods=missing_out(row.get("missing_periods")),
            source_ids=_source_ids(points),
            unit=_first_unit(raw),
            base_value=base,
        ))
    return result


def _base_value(points: list[dict], base_date) -> float | None:
    """基期落在哪个周期里，就取那个周期的值（03 §11：base_period 是日期或
    周/月起始日，所以按「period_start <= base_date <= period_end」匹配）。"""
    for point in points or []:
        start, end = point.get("period_start"), point.get("period_end")
        if start is None or end is None:
            continue
        if start <= base_date <= end:
            value = point.get("value")
            return None if value is None else float(value)
    return None


# ---------- 说明文案与字段取值 ----------


def _comparison_note(body: CompareRequest, rows: list[dict],
                     series: list[ComparedSeries],
                     metric_notes: dict) -> str:
    """03 §11 要求的 comparison_note：口径差异与不可比之处，一条不省。"""
    metrics = sorted({_metric_code(row) for row in rows})
    parts: list[str] = []

    if len(metrics) > 1:
        parts.append("涉及 " f"{len(metrics)} 个不同指标（{'、'.join(metrics)}）："
                     "不得把原始值画到同一数值轴，也不得跨指标求和")
    else:
        parts.append(f"涉及同一指标 {metrics[0]}")

    notes = [metric_notes.get(code, {}).get("comparability_note")
             for code in metrics]
    notes = [n for n in notes if n]
    if notes:
        parts.append("指标字典可比性口径：" + "；".join(notes))

    for row, item in zip(rows, series):
        total = len(item.points) + len(item.missing_periods)
        parts.append(f"{item.metric}@{_entity_name(row)}："
                     f"{len(item.points)}/{total} 期有数据"
                     + (f"，缺失 {len(item.missing_periods)} 期"
                        if item.missing_periods else ""))

    if body.display_mode == "index_100":
        parts.append(f"已按基期 {body.base_period} 归一化为 100（指数无量纲，"
                     "点位 unit 为 index，原始单位见各序列 unit）")
    else:
        parts.append("display_mode=separate_axes：请使用各自独立的数值轴，"
                     "不要共用一根轴")
    return "；".join(parts) + "。"


def _entity_id(row: dict) -> str:
    return (row.get("entity") or {}).get("id") or ""


def _entity_name(row: dict) -> str:
    entity = row.get("entity") or {}
    return entity.get("name") or entity.get("id") or "?"


def _metric_code(row: dict) -> str:
    metric = row.get("metric")
    if isinstance(metric, dict):  # C 给的是指标字典整条
        return metric.get("code") or ""
    return metric or ""


def _source_ids(points: list[dict]) -> list[str]:
    ids: list[str] = []
    for point in points:
        for sid in point.get("source_ids") or []:
            if sid not in ids:
                ids.append(sid)
    return ids


def _first_unit(points: list[dict]) -> str | None:
    for point in points:
        if point.get("unit") is not None:
            return point["unit"]
    return None
