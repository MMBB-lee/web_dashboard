"""权限与会话依赖：未登录 401、权限不足 403、写操作 CSRF 校验。"""

from typing import Annotated

from fastapi import Header, Request

from app.core.errors import (
    CODE_CSRF_REQUIRED,
    CODE_FORBIDDEN,
    CODE_UNAUTHORIZED,
    AppError,
)
from app.core.providers import AuthStore, SessionInfo
from app.core.security import hash_session_token


def get_current_session(request: Request, store: AuthStore) -> SessionInfo:
    """从 Cookie 取会话令牌，查服务端会话；无效返回 401。"""
    settings = __import__("app.core.config", fromlist=["get_settings"]).get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AppError(401, CODE_UNAUTHORIZED, "未登录或会话已失效")
    session = store.get_session(hash_session_token(token))
    if session is None:
        raise AppError(401, CODE_UNAUTHORIZED, "未登录或会话已失效")
    return session


def require_authenticated(request: Request) -> SessionInfo:
    """依赖：已登录（viewer 或 admin）。"""
    from app.core.providers import get_auth_store

    return get_current_session(request, get_auth_store())


def require_admin(request: Request) -> SessionInfo:
    """依赖：已登录且角色为 admin。"""
    session = require_authenticated(request)
    if session.role != "admin":
        raise AppError(403, CODE_FORBIDDEN, "需要管理员权限")
    return session


def require_csrf(
    request: Request,
    x_csrf_token: Annotated[
        str | None,
        Header(
            alias="X-CSRF-Token",
            description=(
                "写操作必须携带的 CSRF 令牌；可从 02 登录或 03 GET /auth/me 的响应 "
                "data.csrf_token 取得。缺失或错误返回 403 csrf_required。"
            ),
        ),
    ] = None,
) -> SessionInfo:
    """依赖：已登录且 X-CSRF-Token 与当前会话的 CSRF 令牌一致（写操作）。

    该头显式声明在依赖签名里，是为了让它出现在 OpenAPI 与 Swagger UI（/docs），
    方便直接在页面上点测写操作；校验以该参数为准，缺失或错误一律 403。
    """
    from app.core.providers import get_auth_store

    session = get_current_session(request, get_auth_store())
    if not x_csrf_token or x_csrf_token != session.csrf_token:
        raise AppError(403, CODE_CSRF_REQUIRED, "缺少或错误的 CSRF 令牌")
    return session
