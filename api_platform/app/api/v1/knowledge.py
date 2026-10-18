"""知识库接口（03-接口总览 §7：20 / 21 / 22）。"""

import re
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.errors import CODE_BAD_REQUEST, CODE_NOT_FOUND, AppError
from app.core.permissions import require_admin, require_authenticated, require_csrf
from app.core.providers import get_job_store, require_intelligent_analysis
from app.schemas.common import Envelope, envelope
from app.schemas.knowledge import DocumentAccepted, DocumentOut, ReindexAccepted, ReindexRequest
from app.services import e_bridge

router = APIRouter(prefix="/knowledge", tags=["知识库"])

# 03-接口总览 §20：file 限制大小和类型；知识文档上限 50MB
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

@router.get('/database/status')
def database_status(request: Request, _session=Depends(require_authenticated)):
    from app.core.providers import get_c_store
    from app.services.database_knowledge import database_status as status
    return envelope(request, status(get_c_store()))

@router.post('/database/sync')
async def sync_database(request: Request, background: BackgroundTasks, admin=Depends(require_admin), _csrf=Depends(require_csrf),
                        _dep=Depends(require_intelligent_analysis)):
    from app.core.providers import get_c_store
    from app.services.database_knowledge import sync_database as sync, index_pending_database_documents
    store=get_c_store()
    result=await run_in_threadpool(sync, store, admin.user_id)
    background.add_task(index_pending_database_documents,store)
    return envelope(request,result)

# E 契约的暂存 ID 格式：doc-<12位hex>（见 e_bridge.stage_and_accept_document）
_DOC_ID_RE = re.compile(r"^doc-[0-9a-f]{12}$")


@router.get("/documents")
def list_documents(request: Request, page: int = 1, page_size: int = 100,
                   _session=Depends(require_authenticated)):
    from data_foundation.contracts import Page
    from app.core.providers import get_c_store
    from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE
    if page < 1 or not 1 <= page_size <= 100:
        raise AppError(400, CODE_BAD_REQUEST, "分页参数超出范围")
    store = get_c_store()
    if store is None:
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "知识库持久化服务未连接")
    with store.read() as uow:
        result = uow.knowledge.list_documents(page=Page(page=page, page_size=page_size))
    # Only public metadata, never raw file references or full document content.
    fields = ("id", "title", "publisher", "source_url", "published_at", "valid_from", "valid_until",
              "permission_note", "index_status", "active_index_version")
    return envelope(request, [{key: row.get(key) for key in fields} for row in result["items"]],
                    page=page, page_size=page_size, total=result["total"])


def _is_valid_document_id(value: str) -> bool:
    """UUID（03 契约 §21；C store 文档）或 doc-<hex12>（E 暂存 ID）均合法。"""
    if _DOC_ID_RE.match(value):
        return True
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def _uuids_or_400(values: list[str] | None, field: str) -> list[str] | None:
    """03 契约 §20：entity_ids 为可选的 UUID 数组。"""
    if not values:
        return None
    for v in values:
        try:
            UUID(v)
        except ValueError:
            raise AppError(400, CODE_BAD_REQUEST, f"{field} 含非 UUID 值：{v}") from None
    return values


@router.post("/documents", status_code=202, response_model=Envelope[DocumentAccepted])
async def upload_document(
    request: Request,
    file: UploadFile = File(description="PDF / TXT / Markdown 文档"),
    title: str = Form(min_length=1, max_length=200),
    publisher: str = Form(min_length=1, max_length=200),
    source_url: str = Form(min_length=1, max_length=500, description="资料来源链接（03 契约必填）"),
    published_at: str = Form(min_length=1, max_length=40, description="发布日期（03 契约必填）"),
    permission_note: str = Form(min_length=1, max_length=500, description="授权说明（权限红线必填）"),
    valid_from: str | None = Form(default=None),
    valid_until: str | None = Form(default=None),
    entity_ids: list[str] | None = Form(default=None, description="关联实体 UUID（03 契约可选）"),
    _admin=Depends(require_admin),
    _csrf=Depends(require_csrf),
    _dep=Depends(require_intelligent_analysis),
) -> dict:
    """20 POST /knowledge/documents：管理员；multipart 上传，解析与索引异步执行。

    E 0.5.0 契约：document_id 由 D 生成 UUID（不再由文件名推导），
    以 metadata.document_id / document_version 构造身份；E.index_document
    内部经 D 的 submit 绑定**真实入队一次**（返回的 job_id 即队列作业），
    路由层不再重复入队。
    """
    content = await file.read()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise AppError(400, CODE_BAD_REQUEST, "文档超过大小上限（50MB）")
    metadata = {
        "title": title,
        "publisher": publisher,
        "source_url": source_url,
        "published_at": published_at,
        "permission_note": permission_note,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "entity_ids": _uuids_or_400(entity_ids, "entity_ids"),
    }
    accepted = e_bridge.stage_and_accept_document(
        content,
        file.filename or "untitled",
        metadata,
    )
    return envelope(
        request,
        DocumentAccepted(
            document_id=str(accepted["document_id"]),
            index_status=accepted["index_status"],
            job_id=str(accepted["job_id"]),
        ).model_dump(mode="json"),
    )


@router.get("/documents/{document_id}", response_model=Envelope[DocumentOut])
async def get_document(
    document_id: str,
    request: Request,
    _session=Depends(require_authenticated),
) -> dict:
    """21 GET /knowledge/documents/{document_id}：元数据与获准片段，不公开受限原件。

    数据来源：D 的桥接作业库（20 号上传时留痕的元数据 + 索引状态 +
    获准分块）。H01 接 C 的 store 后切 ``uow.knowledge.get_document``。

    ID 格式：2026-10-14 起 20 号返回的 document_id 为 UUID（E 0.5.0 契约）；
    ``doc-<hex12>`` 是旧版（文件名推导时代）已登记数据的兼容格式，两种都接受；
    其他格式一律 400（03 契约参数错误口径）。
    """
    if not _is_valid_document_id(document_id):
        raise AppError(400, CODE_BAD_REQUEST, f"document_id 格式不合法：{document_id}")

    view = get_job_store().find_document(document_id)
    if view is None:
        raise AppError(404, CODE_NOT_FOUND, "知识文档不存在")
    return envelope(request, DocumentOut(**view).model_dump(mode="json"))


@router.post("/reindex", status_code=202, response_model=Envelope[ReindexAccepted])
async def reindex(
    body: ReindexRequest,
    request: Request,
    _admin=Depends(require_admin),
    _csrf=Depends(require_csrf),
    _dep=Depends(require_intelligent_analysis),
) -> dict:
    """22 POST /knowledge/reindex：管理员；新旧索引版本切换期间旧索引继续服务。"""
    result = e_bridge.reindex_documents(body.document_ids, body.reason)
    return envelope(request, ReindexAccepted.model_validate(result).model_dump(mode="json"))
