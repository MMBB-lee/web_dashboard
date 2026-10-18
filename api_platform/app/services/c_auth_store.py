"""C 的 data_foundation 认证适配层（任务 H01）。

背景（2026-10-14 核对 C 0.1.9 wheel）
--------------------------------------
C 的 `data_foundation` 顶层只导出 `create_store(database_url, ...) -> DataStore`，
**没有** `new_auth_store()`，也没有顶层 `create_user()`。用户与会话由
`UnitOfWork.auth`（`AuthRepository`）维护：

    create_user(UserCreate(username, display_name, password_hash, role))
    get_user_by_username(username) -> dict | None
    save_session(SessionCreate(id_hash, user_id, expires_at), now)
    get_active_session(id_hash, now) -> {"session": {...}, "user": {...}} | None
    revoke_session(id_hash, now) -> bool

而 D 的 `AuthStore` 契约（`app/core/providers.py`）要求：

    get_user_by_username(username) -> UserRecord | None
    create_session(user_id, session_token_hash, csrf_token, expires_at) -> None
    get_session(session_token_hash) -> SessionInfo | None
    revoke_session(session_token_hash) -> None

口径差异与处理
--------------
C 的 `sessions` 表只有 `id_hash / user_id / expires_at / revoked_at`，
**不保存 csrf_token**。为不改动 C 的表结构，本适配层用服务端密钥对会话摘要做
HMAC-SHA256，派生 CSRF 令牌：同一会话每次都能算出同一个值，无需额外存储；
客户端在登录/`/auth/me` 取值、写操作通过 `X-CSRF-Token` 回传即可校验。

连接管理
--------
`create_store()` 内部 `create_engine`，每个实例带一个连接池。适配层因此按
DATABASE_URL 缓存一个 `DataStore`，避免每个请求新建引擎导致连接泄漏。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime
from functools import lru_cache

from app.core.providers import SessionInfo, UserRecord

logger = logging.getLogger("api_platform.c_auth_store")

_ephemeral_secret: str | None = None


def _csrf_secret() -> bytes:
    """取 CSRF 派生密钥；未配置时用进程内一次性随机值并告警。"""
    from app.core.config import get_settings

    secret = get_settings().csrf_secret.strip()
    if secret:
        return secret.encode("utf-8")

    global _ephemeral_secret
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(48)
        logger.warning(
            "未配置 CSRF_SECRET，已生成一次性随机密钥；进程重启后既有会话的 CSRF 令牌会失效。"
            "请在 .env 中设置 CSRF_SECRET。"
        )
    return _ephemeral_secret.encode("utf-8")


def derive_csrf_token(session_token_hash: str) -> str:
    """由会话摘要派生稳定的 CSRF 令牌（32 位十六进制）。"""
    return hmac.new(
        _csrf_secret(), session_token_hash.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def _as_utc(value: datetime) -> datetime:
    """C 的库内时间是 UTC naive；统一转成带时区的 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@lru_cache(maxsize=4)
def _cached_data_store(database_url: str):
    """按连接串缓存 C 的 DataStore（含连接池）。"""
    from data_foundation import create_store

    return create_store(database_url)


class DataFoundationAuthStore:
    """把 D 的 `AuthStore` 协议转发到 C 的 `uow.auth`。

    `store` 参数仅供测试注入（传一个实现 `read()/write()` 上下文的对象）。
    """

    def __init__(self, store=None, *, database_url: str | None = None) -> None:
        if store is None:
            from app.core.config import get_settings

            store = _cached_data_store(database_url or get_settings().database_url)
        self._store = store

    # ---- AuthStore 协议 -------------------------------------------------

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self._store.read() as uow:
            row = uow.auth.get_user_by_username(username)
        if row is None:
            return None
        return UserRecord(
            id=str(row["id"]),
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            status=row["status"],
            password_hash=row["password_hash"],
        )

    def create_session(
        self, user_id: str, session_token_hash: str, csrf_token: str, expires_at: datetime
    ) -> None:
        """落库会话。

        `csrf_token` 参数不再入库：CSRF 由 `derive_csrf_token()` 从会话摘要重算
        （调用方在登录后用 `get_session()` 取回规范值，见 auth_service.login）。

        注意：C 的 `utc_naive()` 要求入参**必须带时区**（否则 InvalidArgument），
        故这里传 tz-aware 的 UTC 时间，由 C 自己转成库内的 UTC naive。
        """
        from data_foundation import SessionCreate

        with self._store.write() as uow:
            uow.auth.save_session(
                SessionCreate(
                    id_hash=session_token_hash,
                    user_id=user_id,
                    expires_at=_as_utc(expires_at),
                ),
                datetime.now(UTC),
            )

    def get_session(self, session_token_hash: str) -> SessionInfo | None:
        with self._store.read() as uow:
            row = uow.auth.get_active_session(session_token_hash, datetime.now(UTC))
        if row is None:
            return None
        user = row["user"]
        session = row["session"]
        return SessionInfo(
            user_id=str(user["id"]),
            username=user["username"],
            display_name=user["display_name"],
            role=user["role"],
            csrf_token=derive_csrf_token(session_token_hash),
            expires_at=_as_utc(session["expires_at"]),
        )

    def revoke_session(self, session_token_hash: str) -> None:
        with self._store.write() as uow:
            uow.auth.revoke_session(session_token_hash, datetime.now(UTC))


@lru_cache(maxsize=1)
def get_data_foundation_auth_store() -> DataFoundationAuthStore:
    """进程内单例：复用同一个 C 的 DataStore（连接池）。"""
    return DataFoundationAuthStore()


def create_user(username: str, password_hash: str, display_name: str, role: str = "admin") -> dict:
    """离线创建用户（C 0.1.9 没有顶层 create_user，改用 uow.auth.create_user）。"""
    from app.core.config import get_settings
    from data_foundation import UserCreate

    store = _cached_data_store(get_settings().database_url)
    with store.write() as uow:
        return uow.auth.create_user(
            UserCreate(
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                role=role,
            )
        )
