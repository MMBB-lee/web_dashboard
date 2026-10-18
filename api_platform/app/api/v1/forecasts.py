"""历史预测接口（03-接口总览 §5：12）。

算法核心在 ``app/services/historical_forecast/``（季节性中位数基线 + 滚动起点回测），
观测数据走 C 的 ``uow.observations.query_series``，预测运行落 C 的
``forecast_runs`` / ``forecast_points`` 表。**D 不自己造算法，也不自己存预测结果。**

两条红线（契约 §12 与算法模块一致）：

- 数据不足 → ``status=data_insufficient`` 且 ``forecast_points=[]``，**不用历史均值
  伪装预测**；
- 预测区间的上下界缺失时对外保持 ``null``，不补造。

⚠️ 与 C 表的一个约束差：C 的 ``forecast_points.lower_bound`` / ``upper_bound`` 是
**NOT NULL**，所以落库时下界退化到点预测值本身（区间宽度为零）也要写，不能写 null。
见 ``_db_points``。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._series import parse_series_date
from app.core.errors import CODE_BAD_REQUEST, AppError
from app.core.permissions import require_csrf
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.forecast import ForecastRequest, ForecastResponse
from app.services.historical_forecast import MODEL_VERSION, run_history_forecast

router = APIRouter(prefix="/forecasts", tags=["历史预测"])

#: 训练窗口回溯长度。周粒度门槛10个周期、月粒度3个周期；
#: 取 10 年留足余量；再长会撞 C 的 ``QueryLimitExceeded``（范围 > 20 年 / 点数 > 10000）。
_TRAINING_LOOKBACK_YEARS = 10


@router.post("/history", response_model=ForecastResponse)
async def history_forecast(
    body: ForecastRequest,
    _session=Depends(require_csrf),
    _dep=Depends(require_data_foundation),
) -> ForecastResponse:
    """12 POST /forecasts/history：周/月预测 + 滚动起点回测。

    约束：数据不足时 status=data_insufficient 且 forecast_points=[]，
    不用历史均值伪装预测；horizon 限界（周 1—8，月 1—6）由 schema 校验。
    """
    try:
        UUID(body.entity_id)
    except (ValueError, AttributeError, TypeError):
        raise AppError(400, CODE_BAD_REQUEST,
                       f"entity_id 不是合法的 UUID：{body.entity_id}") from None

    training_end = parse_series_date(body.training_end, "training_end")
    start = training_end - timedelta(days=365 * _TRAINING_LOOKBACK_YEARS)

    cstore = get_c_store()
    try:
        query = getattr(cstore, 'query_project_series', cstore.query_series)
        row = query(entity_id=body.entity_id, metric=body.metric,
                                  grain=body.grain, start=start, end=training_end)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "历史预测")
        raise

    series = _to_series(row.get("points"))
    source_ids = _collect_source_ids(row.get("points"))

    result = run_history_forecast(series, body.grain, body.horizon)
    result["source_ids"] = source_ids

    _persist(cstore, body, result, series, row, training_end)
    return ForecastResponse(**result)


# ---------- 内部 ----------


def _to_series(points) -> list[tuple]:
    """C 的观测点 → 算法要的 ``[(period_start: date, value: float), ...]``。

    跳过 ``value`` 为 None 的点：那是「有周期无取值」，不能当 0 喂给模型
    （03 §1：缺失保持 null，不补零）。
    """
    series: list[tuple] = []
    for point in points or []:
        period_start = point.get("period_start")
        value = point.get("value")
        if period_start is None or value is None:
            continue
        if isinstance(period_start, str):
            period_start = date.fromisoformat(period_start[:10])
        elif hasattr(period_start, "hour"):  # datetime → date
            period_start = period_start.date()
        series.append((period_start, float(value)))
    return series


def _collect_source_ids(points) -> list[str]:
    """预测输入用了哪些来源：各观测点 source_ids 的并集，保持首次出现顺序。"""
    seen: list[str] = []
    for point in points or []:
        for source_id in point.get("source_ids") or []:
            if source_id not in seen:
                seen.append(source_id)
    return seen


def _training_window(series: list[tuple], training_end: date) -> tuple[date, date]:
    """落库用的训练区间：C 的表有 ``training_end >= training_start`` CHECK。"""
    if not series:
        return training_end, training_end
    return series[0][0], min(series[-1][0], training_end)


def _manifest(series: list[tuple], row: dict) -> dict:
    """输入清单：用可 JSON 序列化的形式记下喂给模型的到底是什么。"""
    return {
        "quality": row.get('data_basis', 'verified'),
        "data_kinds": sorted({p.get('quality_status','verified') for p in row.get('points',[])}),
        "point_count": len(series),
        "points": [[p.isoformat(), v] for p, v in series],
        "coverage_ratio": float(row.get("coverage_ratio") or 0),
    }


def _sha256(value: dict) -> str:
    """输入指纹：字段序稳定的 JSON → SHA256（C 的 HASH 列是 64 hex）。"""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()


def _db_points(result: dict) -> list[dict]:
    """算法结果 → C 的 ``forecast_points`` 行。

    ⚠️ C 的 ``lower_bound`` / ``upper_bound`` 是 NOT NULL 且
    ``CHECK(lower_bound <= value <= upper_bound)``、``CHECK(lower_bound >= 0)``。
    算法给的下界已经过 ``max(0.0, ...)``，正常满足；若出现 None（防御），
    退化成点预测值本身把区间压成零宽——**这是表约束要求的，不是伪造区间**：
    对外响应仍然按算法原样给，见 ``_persist`` 之后未改写 ``result``。
    """
    points = []
    for item in result.get("forecast_points") or []:
        value = float(item["value"])
        lower = item.get("lower_bound")
        upper = item.get("upper_bound")
        lower = value if lower is None else max(0.0, float(lower))
        upper = value if upper is None else float(upper)
        if upper < value:  # 防御：表 CHECK 会拒，先对齐到 value
            upper = value
        if lower > value:
            lower = value
        points.append({
            "period_start": date.fromisoformat(item["period_start"]),
            "period_end": date.fromisoformat(item["period_end"]),
            "value": value,
            "lower_bound": lower,
            "upper_bound": upper,
        })
    return points


def _persist(cstore, body: ForecastRequest, result: dict,
             series: list[tuple], row: dict, training_end: date) -> None:
    """把这次预测运行写进 C 的表。落库失败不应让整条预测接口 500？——不，应该失败。

    预测是有副作用的写操作：入不了库就必须让调用方知道（由 C 异常族翻译），
    绝不能「算出来了但没存」还返回 200。
    """
    training_start, training_end = _training_window(series, training_end)
    manifest = _manifest(series, row)
    data = {
        "entity_id": body.entity_id,
        "metric_code": body.metric,
        "grain": body.grain,
        "training_start": training_start,
        "training_end": training_end,
        "horizon": body.horizon,
        "model_version": result.get("model_version") or MODEL_VERSION,
        "model_config": {"model": body.model},
        "input_manifest": manifest,
        "input_sha256": _sha256(manifest),
        "backtest": result.get("backtest"),
        "status": result["status"],
        "reason": result.get("reason"),
    }
    try:
        cstore.save_history_forecast(data, _db_points(result))
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "历史预测落库")
        raise
