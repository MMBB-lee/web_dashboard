"""景区时序接口（03-接口总览 §4：08）。

2026-10-16 接线：C 0.2.0a2 的 ``uow.observations.query_series`` 早已提供
（含实体/指标存在性、指标-实体匹配、粒度支持性、查询范围上限的全部判断），
D 之前做成 503「等 C」是过时判断。

D 只做三件事：① 参数形状校验（日期 / 粒度 / quality）；② 确认该实体确实是
景区（03 §08 要求 ``scenic_id`` 必须是景区）；③ 把 C 的返回收敛成 §08 的形状。
**数值、覆盖率、缺失区间、选点口径全部来自 C，D 不重算。**
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._pending import pending
from app.api.v1._series import (
    SERIES_GRAINS,
    SERIES_QUALITIES,
    parse_series_date,
    series_response,
)
from app.core.errors import CODE_BAD_REQUEST, CODE_METRIC_ENTITY_MISMATCH, AppError
from app.core.permissions import require_authenticated
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.common import Envelope, envelope
from app.schemas.series import SeriesResponse

router = APIRouter(prefix="/scenic", tags=["景区客流"])


@router.get("/{scenic_id}/series", response_model=Envelope[SeriesResponse])
async def get_scenic_series(
    scenic_id: str,
    metric: str,
    grain: str,
    start: str,
    end: str,
    quality: str = "verified",
    request: Request = None,  # noqa: B006 —— FastAPI 按类型注入
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """08 GET /scenic/{scenic_id}/series：景区时序。

    - 来源不支持所选粒度 → 422 ``unsupported_grain``（由 C 抛出）；
    - 无数据 → ``points: []`` 且 ``coverage_ratio: 0``（**不伪造数据**）；
    - ``metric`` 与实体对不上 → 422 ``metric_entity_mismatch``。
    """
    _reject_bad_params(scenic_id, grain, quality)
    start_date = parse_series_date(start, "start")
    end_date = parse_series_date(end, "end")
    if start_date > end_date:
        raise AppError(400, CODE_BAD_REQUEST, "start 不能晚于 end",
                       {"detail": f"start={start}, end={end}"})

    cstore = get_c_store()
    if cstore is None:
        pending("景区时序", "C", "data_foundation")
    try:
        row = cstore.query_series(
            entity_id=scenic_id, metric=metric, grain=grain,
            start=start_date, end=end_date, quality=quality,
        )
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "景区时序")
        raise

    entity = row.get("entity") or {}
    if entity.get("type") != "scenic":
        # 03 §08：scenic_id 必须是景区。C 只校验「指标是否适用于该实体」，
        # 不校验路径语义——这一刀只能由 D 补。
        raise AppError(422, CODE_METRIC_ENTITY_MISMATCH,
                       "该实体不是景区，不能走景区时序接口",
                       {"detail": f"entity_type={entity.get('type')}"})
    return envelope(request, series_response(row))


def _reject_bad_params(scenic_id: str, grain: str, quality: str) -> None:
    try:
        UUID(scenic_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, "scenic_id 不是合法 UUID") from None
    if grain not in SERIES_GRAINS:
        raise AppError(400, CODE_BAD_REQUEST,
                       f"grain 不被接受：只支持 {' / '.join(SERIES_GRAINS)}",
                       {"detail": f"grain={grain!r}"})
    if quality not in SERIES_QUALITIES:
        raise AppError(400, CODE_BAD_REQUEST,
                       f"quality 不被接受：只支持 {' / '.join(SERIES_QUALITIES)}")
