"""来源接口（03-接口总览 §4：07）。

2026-10-16 接线：C 0.2.0a2 的 ``uow.catalog.get_source`` 早已提供，D 之前
一律 503「等 C」是过时判断。找不到时 C 抛 ``NotFound`` → 404（03 §07）。

只回 03 §07 列出的 11 个字段：C 的 ``sources`` 表另有 ``adapter_key`` /
``adapter_config`` / ``enabled`` / ``update_frequency`` 等采集侧列，
**属于采集实现细节，不对外暴露**。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._pending import pending
from app.core.errors import CODE_BAD_REQUEST, AppError
from app.core.permissions import require_authenticated
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.catalog import SourceOut
from app.schemas.common import Envelope, envelope, iso_cn

router = APIRouter(prefix="/sources", tags=["来源"])


@router.get("/{source_id}", response_model=Envelope[SourceOut])
async def get_source(
    source_id: str,
    request: Request,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """07 GET /sources/{source_id}：来源详情；找不到返回 404。

    ``native_grain`` 可能为 ``None``（来源未声明原生粒度）——03 §07 要求
    「接口不提供其不支持的粒度」，空值如实回传，不替来源猜一个粒度。
    """
    if source_id.startswith('public-'):
        from app.services.native_series import fact_source
        return envelope(request,SourceOut.model_validate(_source_out(fact_source(source_id))).model_dump(mode='json'))
    try:
        UUID(source_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, "source_id 不是合法 UUID") from None

    cstore = get_c_store()
    if cstore is None:
        pending("来源详情", "C", "data_foundation")
    try:
        row = cstore.get_source(source_id)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "来源详情")
        raise
    return envelope(request, SourceOut.model_validate(_source_out(row)).model_dump(mode="json"))


def _source_out(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "publisher": row.get("publisher"),
        "title": row.get("title"),
        "url": row.get("url"),
        "published_at": iso_cn(row.get("published_at")),
        "retrieved_at": iso_cn(row.get("retrieved_at")),
        "coverage_start": iso_cn(row.get("coverage_start")),
        "coverage_end": iso_cn(row.get("coverage_end")),
        "native_grain": row.get("native_grain"),
        "usage_note": row.get("usage_note"),
        "verification_status": row.get("verification_status"),
    }
