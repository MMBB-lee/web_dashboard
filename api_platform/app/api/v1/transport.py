"""交通时序接口（03-接口总览 §4：09）。

与 08 同走 C 的 ``uow.observations.query_series``，差别只有两处：

- 实体必须是 ``rail_station`` 或 ``airport``（03 §09）；
- 响应多一个 ``transport_type``（``rail`` / ``airport``）。

指标与实体的匹配（铁路站用 ``rail_departures``、机场用
``airport_throughput``）**由 C 的指标字典判定**：不符时 C 抛
``MetricEntityMismatch`` → 422 ``metric_entity_mismatch``，正是 03 §09 要的
语义，D 不另写一份映射表。
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

router = APIRouter(prefix="/transport", tags=["交通客流"])

#: 03 §09：铁路站 / 机场。``transport_type`` 与之 1:1 对应（对外用短名）。
TRANSPORT_ENTITY_TYPES = {"rail_station": "rail", "airport": "airport"}


@router.get("/{entity_id}/series", response_model=Envelope[SeriesResponse])
async def get_transport_series(
    entity_id: str,
    metric: str,
    grain: str,
    start: str,
    end: str,
    quality: str = "verified",
    request: Request = None,  # noqa: B006 —— FastAPI 按类型注入
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """09 GET /transport/{entity_id}/series：铁路站 / 机场时序。"""
    try:
        UUID(entity_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, "entity_id 不是合法 UUID") from None
    if grain not in SERIES_GRAINS:
        raise AppError(400, CODE_BAD_REQUEST,
                       f"grain 不被接受：只支持 {' / '.join(SERIES_GRAINS)}",
                       {"detail": f"grain={grain!r}"})
    if quality not in SERIES_QUALITIES:
        raise AppError(400, CODE_BAD_REQUEST,
                       f"quality 不被接受：只支持 {' / '.join(SERIES_QUALITIES)}")
    start_date = parse_series_date(start, "start")
    end_date = parse_series_date(end, "end")
    if start_date > end_date:
        raise AppError(400, CODE_BAD_REQUEST, "start 不能晚于 end",
                       {"detail": f"start={start}, end={end}"})

    cstore = get_c_store()
    if cstore is None:
        pending("交通时序", "C", "data_foundation")
    try:
        row = cstore.query_series(
            entity_id=entity_id, metric=metric, grain=grain,
            start=start_date, end=end_date, quality=quality,
        )
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "交通时序")
        raise

    entity = row.get("entity") or {}
    transport_type = TRANSPORT_ENTITY_TYPES.get(entity.get("type"))
    if transport_type is None:
        # 03 §09：entity_id 必须是铁路站或机场
        raise AppError(422, CODE_METRIC_ENTITY_MISMATCH,
                       "该实体不是铁路站或机场，不能走交通时序接口",
                       {"detail": f"entity_type={entity.get('type')}"})
    return envelope(request, series_response(row, transport_type=transport_type))
