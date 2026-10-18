"""08 / 09 时序响应的公共形状转换。

C 的 ``observations.query_series`` 返回的是**数据库视角**的结构（``metric`` 是
指标字典整条、``coverage_ratio`` 是 ``Decimal``、时间点一律 UTC aware datetime、
``missing_periods`` 与 ``points`` 里的周期是 ``date``）。03 §08/§09 要的是
**对外视角**：``metric`` 是指标代码、时间统一 +08:00 的 ISO 串、覆盖率是浮点。

转换集中在这里，两条时序接口共用一套口径。

两点刻意**不外泄**：

- ``points[].provenance``（含 ``asset_url`` / ``source_locator``）属取数细节，
  03 §1 只要求 ``source_ids``——保留 ``source_ids``，不回 provenance；
- ``diagnostics`` / ``selection_policy_version`` 是 C 的选点诊断，调试用，
  不进对外响应（需要时看服务端日志）。
"""
from __future__ import annotations

from datetime import date as _date

from app.core.errors import CODE_BAD_REQUEST, AppError
from app.schemas.common import iso_cn

#: 03 §08/§09：业务客流只接受这三种粒度；frame / 5min 是录像计数专用。
SERIES_GRAINS = ("day", "week", "month")

#: 03 §08：quality 默认 verified（只取已核验观测）。
SERIES_QUALITIES = ("verified", "all")


def parse_series_date(value: str, field: str) -> _date:
    """``YYYY-MM-DD`` → date；格式不对直接 400（03 §1：日期用 YYYY-MM-DD）。"""
    try:
        return _date.fromisoformat(value)
    except (TypeError, ValueError):
        raise AppError(400, CODE_BAD_REQUEST,
                       f"{field} 不是合法日期：应为 YYYY-MM-DD",
                       {"detail": f"{field}={value!r}"}) from None


def series_response(row: dict, *, transport_type: str | None = None) -> dict:
    """C 的 query_series 返回 → 03 §08/§09 的 ``SeriesResponse`` 形状。"""
    metric = row.get("metric")
    if isinstance(metric, dict):  # C 给的是指标字典整条，对外只要代码
        metric = metric.get("code")
    coverage = row.get("coverage_ratio")
    return {
        "entity": row.get("entity") or {},
        "metric": metric,
        "grain": row.get("grain"),
        "points": points_out(row.get("points")),
        "missing_periods": missing_out(row.get("missing_periods")),
        "coverage_ratio": float(coverage) if coverage is not None else 0.0,
        "transport_type": transport_type,
    }


def points_out(points) -> list[dict]:
    """C 的 ``points`` → 03 §1 的通用数字点列表（08 / 09 / 11 共用）。"""
    return [_point_out(p) for p in points or []]


def missing_out(missing) -> list[dict]:
    """C 的 ``missing_periods`` → ``{"period_start","period_end"}`` 列表。"""
    return [{"period_start": _day(p.get("period_start")),
             "period_end": _day(p.get("period_end"))}
            for p in missing or []]


def _point_out(point: dict) -> dict:
    """C 的 ``_point`` → 03 §1 的通用数字点（十项溯源字段齐全）。"""
    return {
        "metric": point.get("metric"),
        "value": _num(point.get("value")),
        "unit": point.get("unit"),
        "period_start": _day(point.get("period_start")),
        "period_end": _day(point.get("period_end")),
        "grain": point.get("grain"),
        "source_ids": list(point.get("source_ids") or []),
        "published_at": iso_cn(point.get("published_at")),
        "ingested_at": iso_cn(point.get("ingested_at")),
        "quality_status": point.get("quality_status"),
        # 观测值不是预测值：不带模型版本
        "is_forecast": False,
        "model_version": None,
    }


def _day(value) -> str | None:
    """date（或 datetime）→ ``YYYY-MM-DD``。"""
    if value is None:
        return None
    if isinstance(value, _date) and not hasattr(value, "hour"):
        return value.isoformat()
    text = iso_cn(value)
    return text[:10] if text else None


def _num(value):
    if value is None:
        return None
    return float(value)
