"""目录接口（03-接口总览 §4：05 / 06）。

2026-10-16 接线：C 0.2.0a2 的 ``uow.catalog`` 早已提供 ``list_entities`` /
``list_metrics``，D 之前把这两条一律做成 503「等 C」是**过时判断**（与 23/25
同一次误判的同类问题），现已改为直接走 C 的仓储。

分工与 23/25 一致：**口径归 C，形状归 D**。

- 「哪些指标/粒度真的可用」由 C 按已核验观测算出，D 不补、不猜；
- D 只负责把 C 的公开列收敛成 03 §05/§06 规定的字段集合，并统一时间表示。
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._paging import PageQ, PageSizeQ
from app.api.v1._pending import pending
from app.core.permissions import require_authenticated
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.catalog import EntityOut, MetricOut
from app.schemas.common import Envelope, envelope

router = APIRouter(prefix="/catalog", tags=["目录"])

# 03 §05：type 只接受这三种；摄像头不进目录（只在 23/26/27 的录像链路出现）。
ENTITY_TYPES = ("scenic", "rail_station", "airport")


@router.get("/entities", response_model=Envelope[list[EntityOut]])
async def list_entities(
    request: Request,
    type: str | None = None,
    region_code: str | None = None,
    q: str | None = None,
    page: PageQ = 1,
    page_size: PageSizeQ = 20,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """05 GET /catalog/entities：实体目录。

    03 §05 关键约束：**不在目录中暗示尚未取得的数据可用**——
    ``available_metrics`` / ``available_grains`` 由 C 只统计已核验
    （``quality_status == "verified"``）的观测得出，没有就是空列表。
    """
    if type is not None and type not in ENTITY_TYPES:
        from app.core.errors import CODE_BAD_REQUEST, AppError

        raise AppError(400, CODE_BAD_REQUEST,
                       f"实体类型不被接受：只支持 {' / '.join(ENTITY_TYPES)}",
                       {"detail": f"type={type!r}"})
    if q is not None and len(q) > 50:
        # C 内部会截断到 50 字符（``q[:50]``）；与其静默截断，不如显式拒收，
        # 避免调用方以为自己搜的是完整关键词。
        from app.core.errors import CODE_BAD_REQUEST, AppError

        raise AppError(400, CODE_BAD_REQUEST, "搜索关键词过长：最长 50 个字符")

    cstore = get_c_store()
    if cstore is None:
        pending("实体目录", "C", "data_foundation")
    try:
        result = cstore.list_entities(
            type=type, region_code=region_code, q=q, page=page, page_size=page_size,
        )
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "实体目录")
        raise
    items = [EntityOut.model_validate(_entity_out(item)) for item in result["items"]]
    return envelope(
        request,
        items,
        page=result["page"],
        page_size=result["page_size"],
        total=result["total"],
    )


def _entity_out(row: dict) -> dict:
    """C 的实体公开列 → 03 §05 规定的字段集合（不多不少）。"""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "region_code": row.get("region_code"),
        # C 用 Numeric(10,7) 存经纬度 → Decimal，JSON 里必须落回 float
        "longitude": _num(row.get("longitude")),
        "latitude": _num(row.get("latitude")),
        "available_metrics": list(row.get("available_metrics") or []),
        "available_grains": list(row.get("available_grains") or []),
        "status": row.get("status"),
    }


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


@router.get("/metrics", response_model=Envelope[list[MetricOut]])
async def list_metrics(
    request: Request,
    entity_type: str | None = None,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """06 GET /catalog/metrics：指标字典与可比性规则。

    03 §06：「例如 ``airport_throughput`` 不可作为铁路发送量直接相加」——
    可比性口径由 C 的指标字典维护，D 原样透传 ``aggregation_rule`` 与
    ``comparability_note``，不改写、不省略。
    """
    cstore = get_c_store()
    if cstore is None:
        pending("指标字典", "C", "data_foundation")
    try:
        rows = cstore.list_metrics(entity_type)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "指标字典")
        raise
    items = [MetricOut.model_validate(_metric_out(row)) for row in rows]
    return envelope(request, items, total=len(items))


def _metric_out(row: dict) -> dict:
    return {
        "code": row.get("code"),
        "display_name": row.get("display_name"),
        "definition": row.get("definition"),
        "unit": row.get("unit"),
        "supported_entity_types": list(row.get("supported_entity_types") or []),
        "aggregation_rule": row.get("aggregation_rule"),
        "comparability_note": row.get("comparability_note"),
    }
