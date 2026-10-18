"""节假日接口（03-接口总览 §5：13 / 14）。

13 已接线：C 0.2.0a2 的 ``uow.holidays.list_holidays`` 早已提供（year / kind /
region_code 三个筛选），D 之前做成 503「等 C」是过时判断。

14 已接线：两个假期各取日粒度观测（``uow.observations.query_series``），
按 ``mode`` 折算，缺的天数如实报 ``missing_days``，可比性由 ``comparable`` +
``reason`` 说清楚。两条硬约束（03 §14）：

- ``day_index`` **要求两段时间均有完整日数据**，缺一天就整条不比——
  绝不返回「前半段是指数、后半段是原始值」的半截曲线；
- **不能把只有假期总量的来源展开成逐日曲线**：C 在来源不支持日粒度时抛
  ``UnsupportedGrain``，D 把它记成「该组合不可用」而不是拿总量除以天数编日曲线。

13 的响应只回 03 §13 列出的 8 项：C 的 ``holidays`` 表另有 ``scope_key`` /
``definition_asset_id`` / ``region_code``，属定义内部口径，不外泄。
但从 ``definition_source_id`` 可以追溯到定义原件（07 号接口），
**「假期定义可追溯」的链条是完整的**。
"""
from __future__ import annotations

from datetime import date as _date
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.core.errors import (CODE_BAD_REQUEST, CODE_DEPENDENCY_NOT_AVAILABLE,
                             AppError)
from app.core.permissions import require_authenticated, require_csrf
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.common import Envelope, envelope
from app.schemas.holidays import (HolidayCompareRequest, HolidayCompareResponse,
                                  HolidayOut, HolidaySeriesResult)
from app.services.c_enums import holiday_kinds

router = APIRouter(prefix="/holidays", tags=["节假日专题"])

#: 假期种类。C 021a2 收口后**按 C 导出的字典校验**（9 项，含 new_year /
#: qingming / dragon_boat / mid_autumn），不再照抄 03 §13 只写了四项的窄枚举。
HOLIDAY_KINDS = tuple(sorted(holiday_kinds()))


@router.get("", response_model=Envelope[list[HolidayOut]])
async def list_holidays(
    request: Request,
    year: int | None = None,
    kind: str | None = None,
    region_code: str | None = None,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """13 GET /holidays：假期列表；``definition_source_id`` 保证定义可追溯。"""
    if year is not None and not 1000 <= year <= 9999:
        raise AppError(400, CODE_BAD_REQUEST, "year 必须是四位年份",
                       {"detail": f"year={year}"})
    if kind is not None and kind not in HOLIDAY_KINDS:
        # 以前这里完全不校验——C 的-kind 过滤本身就是精确匹配，拼错的参数会
        # 安静地返回「空列表」，看起来像「这一年没有假期」。现在按 C 的字典拦。
        raise AppError(400, CODE_BAD_REQUEST,
                       f"kind 取值不在 C 的假期字典内：{kind!r}"
                       f"（合法值：{', '.join(HOLIDAY_KINDS)}）",
                       {"detail": f"kind={kind!r}"})

    cstore = get_c_store()
    if cstore is None:  # 依赖注入被摘除（如测试替身缺失）：明确 503，不静默返回空列表
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE,
                       "数据基座不可用：假期列表需要 C 的 holidays 仓储")
    try:
        rows = cstore.list_holidays(year=year, kind=kind, region_code=region_code)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "假期列表")
        raise
    items = [HolidayOut.model_validate(_holiday_out(row)) for row in rows]
    return envelope(request, items, total=len(items))


def _holiday_out(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "kind": row.get("kind"),
        "year": row.get("year"),
        "start": _day(row.get("start_date")),
        "end": _day(row.get("end_date")),
        # 03 §13：假期定义可追溯——指向 07 号可查到的来源原件
        "definition_source_id": row.get("definition_source_id"),
        "days_count": row.get("days_count"),
    }


def _day(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, _date) and not hasattr(value, "hour"):
        return value.isoformat()
    return str(value)[:10]


@router.get("/statistics")
async def holiday_statistics(
    request: Request, entity_id: str, holiday_ids: str,
    mode: str = "daily_average", _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """Read one or more holidays; preserve the existing two-period comparison API."""
    _require_uuid(entity_id, "entity_id")
    ids = list(dict.fromkeys(holiday_ids.split(",")))
    if not 1 <= len(ids) <= 40 or mode not in {"daily_average", "period_total", "day_index"}:
        raise AppError(400, CODE_BAD_REQUEST, "请选择1至40个假期及有效统计方式")
    store = get_c_store()
    if store is None:
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "数据基座不可用")
    definitions = {row["id"]: row for row in store.list_holidays()}
    items = []
    for holiday_id in ids:
        _require_uuid(holiday_id, "holiday_id")
        if holiday_id not in definitions:
            raise AppError(400, CODE_BAD_REQUEST, "假期定义不存在")
        try:
            query = getattr(store, 'query_project_holiday_values', store.query_holiday_values)
            row = query(entity_id=entity_id, metric="scenic_visits", holiday_id=holiday_id)
        except Exception as exc:
            raise_mapped_c_error(exc, "假期统计")
            raise
        holiday = definitions[holiday_id]
        values = _apply_mode(mode, row)
        if mode == "daily_average" and not values and row.get("daily_sum") is not None:
            values = [round(float(row["daily_sum"]) / holiday["days_count"], 2)]
        # A regional school calendar must not silently become a general visitor calendar.
        if row.get("scope_confirmation_required"):
            values = []
        unit = row.get("metric", {}).get("unit", "人次")
        point = row.get("exact_total") or {}
        provenance = point.get("provenance") or {}
        items.append({"holiday_id": holiday_id, "label": holiday["name"], "metric": "scenic_visits",
                      "value": values[0] if values and mode != "day_index" else None,
                      "unit": unit + "/日" if mode == "daily_average" else unit,
                      "period_start": _day(holiday["start_date"]), "period_end": _day(holiday["end_date"]),
                      "grain": "holiday", "missing_days": len(row.get("missing_days") or []),
                      "source_ids": _collect_source_ids(row), "source_url": provenance.get("source_url"),
                      "published_at": str(point["published_at"]) if point.get("published_at") else None,
                      "quality_status": point.get('quality_status', 'verified') if values else "missing"})
    complete = all(item["value"] is not None for item in items)
    unequal = len({definitions[id]["days_count"] for id in ids}) > 1
    reason = None if complete else "部分所选假期没有该景区的已核验客流统计；有日期定义或知识库资料不等于有客流数值。"
    if mode == "day_index":
        reason = "逐日变化需要完整的逐日统计；假期总量及日均值不能展开成每日客流曲线。"
    elif mode == "period_total" and unequal:
        reason = "所选假期天数不同，总量不宜直接比较，请切换日均值。"
    return envelope(request, {"items": items, "comparable": complete and not (mode == "period_total" and unequal), "reason": reason})


@router.post("/compare", response_model=HolidayCompareResponse)
async def compare_holidays(
    body: HolidayCompareRequest,
    _session=Depends(require_csrf),
    _dep=Depends(require_data_foundation),
) -> HolidayCompareResponse:
    """14 POST /holidays/compare：恰好 2 个假期的同口径比较。

    day_index 要求完整日数据；不能把假期总量展开成逐日曲线。
    """
    cstore = get_c_store()
    for holiday_id in body.holiday_ids:
        _require_uuid(holiday_id, "holiday_ids")
    for item in body.series:
        _require_uuid(item.get("entity_id"), "series[].entity_id")
        if not item.get("metric"):
            raise AppError(400, CODE_BAD_REQUEST, "series[] 每项都必须给 metric",
                           {"detail": f"entity_id={item.get('entity_id')!r}"})

    results: list[HolidaySeriesResult] = []
    notes: list[str] = []
    for holiday_id in body.holiday_ids:
        for item in body.series:
            entity_id, metric = item["entity_id"], item["metric"]
            try:
                row = cstore.query_holiday_values(entity_id=entity_id, metric=metric,
                                                  holiday_id=holiday_id)
            except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
                raise_mapped_c_error(exc, "假期比较")
                raise
            holiday = row.get("holiday") or {}
            name = holiday.get("name") or holiday_id
            missing = len(row.get("missing_days") or [])
            if missing:
                notes.append(f"{name} / {metric}：缺 {missing} 天已核验日数据")
            if not row.get("daily_grain_supported"):
                # 03 §14 红线：来源只有假期总量，不展开成逐日曲线。
                notes.append(
                    f"{name} / {metric}：来源不支持日粒度，未展开为逐日值"
                    f"（契约禁止用假期总量摊成日曲线）")
            if row.get("scope_confirmation_required"):
                notes.append(f"{name}：地区性假期，覆盖范围需确认（scope_key="
                             f"{row.get('scope_key')}）")

            if body.mode == "day_index" and not row.get("day_index_supported"):
                # C 判的「不支持指数化」：日数据不完整，或单位/口径版本不唯一
                # （跨了口径就指数化，等于把不同统计范围的数字放在一条曲线上）。
                notes.append(f"{name} / {metric}：C 判定不支持 day_index"
                             f"（日数据不完整或口径不唯一）")
            values = _apply_mode(body.mode, row)
            # C 021a2《27-契约收口》：来源必须包含 exact_total 的来源，
            # 不能只拿 daily_points 的来源——总量观测才是 period_total /
            # daily_average 的真实依据，逐日点会给不完整的溯源。
            results.append(HolidaySeriesResult(
                holiday_id=holiday_id, entity_id=entity_id, metric=metric,
                values=values, missing_days=missing,
                source_ids=_collect_source_ids(row)))

    comparable, reason = _verdict(body.mode, results, notes)
    return HolidayCompareResponse(mode=body.mode, results=results,
                                  comparable=comparable, reason=reason)


# ---------- 14 的内部 ----------


def _require_uuid(value, field: str) -> None:
    """UUID 先校验再交给 C：非 UUID 会走数据库层的 ValueError，不在 C 异常族里。"""
    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise AppError(400, CODE_BAD_REQUEST, f"{field} 不是合法的 UUID：{value}") from None


def _as_date(value) -> _date:
    """C 给的是 ``date``；保险起见兼容 datetime / 字符串。"""
    if isinstance(value, _date) and not hasattr(value, "hour"):
        return value
    if hasattr(value, "date"):
        return value.date()
    return _date.fromisoformat(str(value)[:10])


def _source_ids(points) -> list[str]:
    seen: list[str] = []
    for point in points or []:
        for source_id in point.get("source_ids") or []:
            if source_id not in seen:
                seen.append(source_id)
    return seen


def _merge_unique(*groups) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for source_id in group or []:
            if source_id not in seen:
                seen.append(str(source_id))
    return seen


def _collect_source_ids(row: dict) -> list[str]:
    """``daily_points`` 的来源 **并上** ``exact_total`` 的来源。

    C 021a2《27-契约收口》明确要求：period_total / daily_average 的依据是
    假期总量观测，只从 daily_points 取来源会漏掉真正的取证对象。
    """
    total = row.get("exact_total") or {}
    return _merge_unique(total.get("source_ids"),
                         _source_ids(row.get("daily_points")))


def _apply_mode(mode: str, row: dict) -> list[float]:
    """按 mode 折算，**优先用 C 算好的口径值**，C 没有才退到日点自算。

    没有可用数据时一律 ``[]``，绝不返回 0 或历史均值顶替（03 §12/§14 同一红线）。
    """
    if mode == "period_total":
        total = row.get("exact_total") or {}
        if total.get("value") is not None:
            return [float(total["value"])]
        # 没有假期总量观测时，C 只在「日数据完整且同口径」下给 daily_sum
        if row.get("daily_sum") is not None:
            return [float(row["daily_sum"])]
        return []

    if mode == "daily_average":
        # C 021a2《27-契约收口》：日均**只能**是「精确总量 ÷ 假期天数」或
        # 「完整同口径 daily_sum ÷ 假期天数」。C 的 query_holiday_values 给的
        # ``daily_average`` 就是这个口径；C 没给就说明总量不可得 → 返回空。
        # 2026-10-17 之前这里有个「用现有 daily_points 求均值」的兜底——
        # 缺天时分母变小，等于**高估日均**，必须删掉。
        average = row.get("daily_average")
        if average is not None:
            return [round(float(average), 2)]
        return []

    # day_index
    if not row.get("day_index_supported"):
        return []
    values = _daily_values(row.get("daily_points"))
    if not values or values[0] == 0:
        return []
    base = values[0]
    return [round(v / base * 100.0, 2) for v in values]


def _daily_values(points) -> list[float]:
    """日粒度点的取值，按日期升序；``value`` 为 None 跳过（缺失不补零）。"""
    daily: dict[_date, float] = {}
    for point in points or []:
        period_start = point.get("period_start")
        value = point.get("value")
        if period_start is None or value is None:
            continue
        daily[_as_date(period_start)] = float(value)
    return [daily[day] for day in sorted(daily)]


def _verdict(mode: str, results: list[HolidaySeriesResult],
             notes: list[str]) -> tuple[bool, str]:
    """可比性判定。不可比必须说清是哪一段、缺多少。"""
    empty = [r for r in results if not r.values]
    if not results:
        return False, "没有可比较的序列"
    if empty and len(empty) == len(results):
        return False, "；".join(notes) if notes else "两个假期都没有可用数据"
    if mode == "day_index":
        incomplete = [r for r in results if r.missing_days > 0]
        if incomplete or empty:
            return False, ("；".join(notes) if notes else "数据不完整") + \
                "——day_index 要求两段时间均有完整日数据，故不给出指数曲线"
    if empty:
        return False, "；".join(notes) if notes else "部分组合无可用数据"
    return True, "同口径可比" if not notes else "可比，但存在缺失：" + "；".join(notes)
