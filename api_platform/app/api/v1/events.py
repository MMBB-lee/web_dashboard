"""突发事件与公告接口（03-接口总览 §6：15—18）。

15 / 16 已接线：C 0.2.0a2 的 ``uow.events`` 早已提供 ``list_events`` /
``get_event``，D 之前一律 503「等 C」是过时判断。

**筛选项的分工**（重要）：

- ``region_code`` / ``status`` 由 C 在 SQL 层完成，且 ``status=active`` 时
  C 按当前时间只返回仍生效的——正是 03 §15「旧公告不自动显示为当前有效」；
- ``entity_id`` / ``kind`` / ``authority`` / ``since`` / ``until`` 与分页，
  **C 的仓储不支持**，由 D 在路由层补齐（C 一次取回后在内存过滤）。
  这是权宜实现：事件量级是公告级（百条内），全量取回可控；
  若后续量级上升，应请 C 在仓储层加参数，而不是让 D 一直全表捞。

17 / 18 已接线：C 的 ``create_event`` / ``update_event`` 一直都有。

**两处 03 契约没写、但 C 实现强制要求的字段**（已回执 C）：

- 17 要 ``asset_id``（事件原件）：``events.asset_id`` 是 NOT NULL，且 C 校验
  ``asset.source_id == source_id`` 与 ``authority == "official"``——
  这正是 03 §17「没有可核验的来源不得创建官方事件」的落地。而
  ``provenance`` 仓储**没有按来源查原件的入口**，D 无法从 ``source_id`` 反查，
  只能要求调用方显式给。
- 18 要 ``expected_version``（乐观锁）：契约没定义这个字段、响应里原本也
  看不到版本号，所以 D 把它回显在 ``row_version`` 里。C 021a2 收口第 5 条
  明确 **18 必填 expected_version、缺失 400、冲突 409**，因此 D 不再代填
  （代填等于替调用方关掉并发保护）。

D 侧必须先拦的两件事（否则走数据库层 ``IntegrityError``，**不在 C 异常族里
会漏成 500**）：``ends_at >= starts_at``（表有 CHECK）、UUID 合法性。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.v1._c_errors import raise_mapped_c_error
from app.api.v1._paging import PageQ, PageSizeQ
from app.api.v1._pending import pending
from app.core.errors import CODE_BAD_REQUEST, AppError
from app.core.permissions import require_admin, require_authenticated, require_csrf
from app.core.providers import get_c_store, require_data_foundation
from app.schemas.common import Envelope, TZ_CN, envelope, iso_cn
from app.schemas.events import EventCreate, EventDetail, EventSummary, EventUpdate
from app.services.c_enums import event_kinds, event_statuses

router = APIRouter(prefix="/events", tags=["突发事件与公告"])

# 以下三组常量的取值**一律以 C 导出的字典为准**（含 closure / expired），
# 不再照抄 03 §15/§17 的窄枚举——详见 ``_reject_bad_filters`` 的注释。
EVENT_KIND_VALUES = tuple(sorted(event_kinds()))
AUTHORITIES = ("official", "model_signal")
STATUSES = tuple(sorted(event_statuses()))


@router.get("", response_model=Envelope[list[EventSummary]])
async def list_events(
    request: Request,
    region_code: str | None = None,
    entity_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    authority: str | None = None,
    since: str | None = None,
    until: str | None = None,
    page: PageQ = 1,
    page_size: PageSizeQ = 20,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """15 GET /events：事件摘要列表。

    03 §15：列表项只含摘要——``description`` 与 ``operator_note`` 不出现在
    列表里（C 的 ``get_event`` 会一并返回，由 D 剔除）。
    """
    _reject_bad_filters(kind, status, authority)
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until")

    cstore = get_c_store()
    if cstore is None:
        pending("事件列表", "C", "data_foundation")
    try:
        rows = cstore.list_events(region_code=region_code, status=status)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "事件列表")
        raise

    rows = _apply_extra_filters(rows, entity_id=entity_id, kind=kind,
                                authority=authority, since=since_dt, until=until_dt)
    total = len(rows)
    window = rows[(page - 1) * page_size: page * page_size]
    items = [EventSummary.model_validate(_event_summary(row)) for row in window]
    return envelope(request, items, page=page, page_size=page_size, total=total)


@router.get("/{event_id}", response_model=Envelope[EventDetail])
async def get_event(
    event_id: str,
    request: Request,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """16 GET /events/{event_id}：完整事件（含描述、来源链接与时间线）。

    ``source_url`` 不在 C 的事件列里——D 用事件的 ``source_id`` 走 07 号同一
    个来源仓储补齐（03 §16 要求可回溯到来源）。来源记录缺失时给 ``null``，
    **不因为来源没了就让整条公告 500**。
    """
    try:
        UUID(event_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, "event_id 不是合法 UUID") from None

    cstore = get_c_store()
    if cstore is None:
        pending("事件详情", "C", "data_foundation")
    try:
        row = cstore.get_event(event_id)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "事件详情")
        raise
    return envelope(request, _event_detail(row, cstore))


@router.post("", status_code=201, response_model=Envelope[EventDetail])
async def create_event(
    body: EventCreate,
    request: Request,
    session=Depends(require_admin),
    _csrf=Depends(require_csrf),
    _dep=Depends(require_data_foundation),
) -> dict:
    """17 POST /events：创建官方公告（admin + CSRF，201）。

    03 §17 的两条红线由 C 的 ``create_event`` 落地：**authority 固定 official**
    （C 校验非 official 直接 ``InvalidArgument``），**必须有可核验来源**
    （C 校验 ``asset.source_id == source_id``，不一致 → 400）。

    D 只负责把「会漏成 500」的两件事先拦掉：时间顺序（表有 CHECK）、
    以及 ``asset_id`` 必填（NOT NULL，C 直接下标取，缺了是 KeyError）。
    """
    starts_at = _as_utc(body.starts_at)
    ends_at = _as_utc(body.ends_at)
    if ends_at is not None and ends_at < starts_at:
        raise AppError(400, CODE_BAD_REQUEST, "ends_at 不能早于 starts_at")

    cstore = get_c_store()
    if cstore is None:
        pending("事件创建", "C", "data_foundation")
    now = datetime.now(timezone.utc)
    data = {
        "title": body.title,
        "description": body.description,
        "kind": body.kind,
        "authority": "official",  # 03 §17：本接口只建官方公告
        "severity": body.severity,
        "region_code": body.region_code,
        "status": "active",
        "starts_at": starts_at,
        "ends_at": ends_at,
        "published_at": now,
        "source_id": body.source_id,
        "asset_id": body.asset_id,
        "affected_entity_ids": list(body.affected_entity_ids),
    }
    try:
        row = cstore.create_event(data, operator_id=session.user_id, now=now)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "事件创建")
        raise
    return envelope(request, _event_detail(row, cstore))


@router.patch("/{event_id}", response_model=Envelope[EventDetail])
async def update_event(
    event_id: str,
    body: EventUpdate,
    request: Request,
    session=Depends(require_admin),
    _csrf=Depends(require_csrf),
    _dep=Depends(require_data_foundation),
) -> dict:
    """18 PATCH /events/{event_id}：更新公告；change_note 必填留痕。

    03 §18：重新激活已解除的事件必须提供**新来源**。C 只在 ``asset_id`` 一并
    提交时才校验「来源与原件一致」，所以换来源时 D 要求**同时**给新原件，
    否则事件会停在 source 与 asset 不匹配的状态而 C 不拦。
    """
    try:
        UUID(event_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, "event_id 不是合法 UUID") from None

    cstore = get_c_store()
    if cstore is None:
        pending("事件更新", "C", "data_foundation")
    try:
        current = cstore.get_event(event_id)  # 不存在 → NotFound → 404
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "事件更新")
        raise

    reactivating = (body.status == "active"
                    and (current.get("status") or "") != "active")
    if reactivating and not body.new_source_id:
        raise AppError(400, CODE_BAD_REQUEST,
                       "重新激活已解除的事件必须提供 new_source_id（03 §18）")
    if body.new_source_id and not body.new_asset_id:
        raise AppError(400, CODE_BAD_REQUEST,
                       "换来源时必须同时提供 new_asset_id：C 只在两者一并提交时"
                       "校验来源与原件一致，只换来源会留下不匹配的状态")

    ends_at = _as_utc(body.ends_at) if body.ends_at is not None else None
    if ends_at is not None:
        starts_at = _as_utc(current.get("starts_at"))
        if starts_at is not None and ends_at < starts_at:
            raise AppError(400, CODE_BAD_REQUEST, "ends_at 不能早于 starts_at")

    patch: dict[str, Any] = {"change_note": body.change_note}
    if body.description is not None:
        patch["description"] = body.description
    if body.severity is not None:
        patch["severity"] = body.severity
    if body.affected_entity_ids is not None:
        patch["affected_entity_ids"] = list(body.affected_entity_ids)
    if ends_at is not None:
        patch["ends_at"] = ends_at
    if body.status is not None:
        patch["status"] = body.status
    if body.new_source_id is not None:
        patch["source_id"] = body.new_source_id
    if body.new_asset_id is not None:
        patch["asset_id"] = body.new_asset_id

    # C 021a2《致D和E-今日确认与接收清单》第 5 条：**18 必填 expected_version**，
    # 缺失 400、冲突 409。2026-10-17 之前 D 的做法是「不传就代填当前版本」——
    # 那等于替调用方关掉并发保护，两个人同改一条公告时后者会静默覆盖前者。
    if body.expected_version is None:
        raise AppError(
            400, CODE_BAD_REQUEST,
            "expected_version 必填：请回传 16 号详情里的 row_version"
            "（不传等于放弃乐观锁保护）",
            {"detail": f"当前 row_version={current.get('row_version')!r}"})
    expected = int(body.expected_version)

    now = datetime.now(timezone.utc)
    try:
        row = cstore.update_event(event_id, patch, expected_version=expected,
                                  operator_id=session.user_id, now=now)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        raise_mapped_c_error(exc, "事件更新")
        raise
    return envelope(request, _event_detail(row, cstore))


# ---------- 形状与筛选 ----------


def _check_enum(value: str | None, allowed: frozenset, field: str) -> None:
    if value is not None and value not in allowed:
        preview = ", ".join(sorted(allowed))
        raise AppError(400, CODE_BAD_REQUEST,
                       f"{field} 取值不在 C 的枚举字典内：{value!r}（合法值：{preview}）",
                       {"detail": f"{field}={value!r}"})


def _reject_bad_filters(kind: str | None, status: str | None,
                        authority: str | None) -> None:
    """按 **C 导出的字典**校验 kind / status，不再像 03 §15 那样只认四个值。

    两次修正：

    - 2026-10-17 前：按 03 契约硬校验 → 真机的 ``closure`` / ``expired``
      被挡成 400。随后 D 改成「一律透传」。
    - C 021a2 收口：C 正式导出 ``EVENT_KINDS``（含 closure）与
      ``EVENT_STATUSES``（含 expired），字典 owner 明确是 C。

    所以现在按 **C 的取值集合**校验——既不会把真实数据挡在门外，也不会让
    一个拼错的筛选参数悄悄返回「空列表」冒充「没有事件」。
    """
    _check_enum(kind, event_kinds(), "kind")
    _check_enum(status, event_statuses(), "status")
    _check_enum(authority, frozenset({"official", "model_signal"}), "authority")


def _parse_dt(value: str | None, field: str) -> datetime | None:
    """ISO 8601 → aware datetime；不带时区时按业务时区 +08:00（03 §1）。"""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST,
                       f"{field} 不是合法 ISO 8601 时间",
                       {"detail": f"{field}={value!r}"}) from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ_CN)
    return parsed


def _apply_extra_filters(rows: list[dict], *, entity_id: str | None,
                         kind: str | None, authority: str | None,
                         since: datetime | None, until: datetime | None) -> list[dict]:
    """补齐 C 仓储不支持的五个筛选（03 §15）。"""
    result = rows
    if entity_id:
        result = [r for r in result
                  if entity_id in (r.get("affected_entity_ids") or [])]
    if kind:
        result = [r for r in result if r.get("kind") == kind]
    if authority:
        result = [r for r in result if r.get("authority") == authority]
    if since is not None:
        result = [r for r in result
                  if (start := _aware(r.get("starts_at"))) is not None
                  and start >= since]
    if until is not None:
        result = [r for r in result
                  if (start := _aware(r.get("starts_at"))) is not None
                  and start <= until]
    return result


def _as_utc(value) -> datetime | None:
    """时间统一到 UTC aware 再比较——一个带时区一个不带会直接 TypeError。

    naive 一律按 **+08:00** 解释（业务在中国，与 24 上传的口径一致），
    不默认 UTC：把「2026-10-01 00:00」当 UTC 会凭空差 8 小时。
    """
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ_CN).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _aware(value) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _event_summary(row: dict) -> dict:
    """15 列表项：03 §15 规定的 12 项，**不含** description / operator_note。"""
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "kind": row.get("kind"),
        "status": row.get("status"),
        "authority": row.get("authority"),
        "severity": row.get("severity"),
        "affected_entity_ids": list(row.get("affected_entity_ids") or []),
        "starts_at": iso_cn(row.get("starts_at")),
        "ends_at": iso_cn(row.get("ends_at")),
        "published_at": iso_cn(row.get("published_at")),
        "last_verified_at": iso_cn(row.get("last_verified_at")),
        "source_id": row.get("source_id"),
    }


def _event_detail(row: dict, cstore) -> dict:
    """16 详情：摘要 + row_version / description / source_url / timeline / operator_note。

    ``row_version`` 来自 C 的 ``MutableMixin``（03 §16 没写，但 §18 的乐观锁
    需要它，不回显调用方就无法启用并发保护）。
    """
    detail = dict(_event_summary(row))
    detail["row_version"] = int(row.get("row_version") or 0)
    detail["description"] = row.get("description") or ""
    detail["source_url"] = _source_url(row.get("source_id"), cstore)
    detail["timeline"] = [_timeline_item(item) for item in (row.get("timeline") or [])]
    detail["operator_note"] = row.get("operator_note")
    return EventDetail.model_validate(detail).model_dump(mode="json")


def _timeline_item(item: dict) -> dict:
    """C 的 ``event_history`` 行 → 03 §16 的时间线项。

    ⚠️ C 的历史表**没有** ``kind`` / ``at`` / ``source_id`` / ``note`` 四列，
    只有 ``version`` / ``snapshot`` / ``change_note`` / ``changed_at``。
    所以：

    - ``at`` ← ``changed_at``（真实时间）；
    - ``note`` ← ``change_note``（真实留痕）；
    - ``source_id`` ← ``snapshot['source_id']``——**该版本生效时的来源**，
      不是当前来源，改过来源时能看出是哪一版换的；
    - ``kind`` 由 ``version`` + ``snapshot['status']`` 推导：1 → announce，
      之后该版本快照为 resolved → resolve，其余 → update。

    推导只用 C 自己存的字段，**不编造取值**。
    """
    snapshot = item.get("snapshot") or {}
    version = item.get("version") or 1
    if version <= 1:
        kind = "announce"
    elif snapshot.get("status") == "resolved":
        kind = "resolve"
    else:
        kind = "update"
    return {
        "kind": kind,
        "at": iso_cn(item.get("changed_at")),
        "source_id": snapshot.get("source_id"),
        "note": item.get("change_note"),
    }


def _source_url(source_id: str | None, cstore) -> str | None:
    """用 07 号同一个来源仓储取 url；来源缺失时给 None，不让公告 500。"""
    if not source_id or cstore is None:
        return None
    try:
        return cstore.get_source(source_id).get("url")
    except Exception:  # noqa: BLE001 —— 来源没了不代表公告没了
        return None
