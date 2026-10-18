"""认证业务：登录（限速 + 同源校验）、会话创建 / 查询、退出（撤销）。

用户与 会话表由 C 的 data_foundation 维护；本服务只通过 AuthStore 契约访问。
"""

from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from app.core.config import get_settings
from app.core.errors import (
    CODE_ORIGIN_NOT_ALLOWED,
    CODE_RATE_LIMITED,
    CODE_UNAUTHORIZED,
    AppError,
)
from app.core.permissions import get_current_session
from app.core.providers import AuthStore, SessionInfo, get_auth_store
from app.core.security import (
    PasswordService,
    hash_session_token,
    new_csrf_token,
    new_session_token,
)
from app.schemas.auth import LoginData, LoginRequest, LogoutData, UserOut
from app.services.rate_limit import LoginRateLimiter

_rate_limiter = LoginRateLimiter(
    max_failures=get_settings().login_max_failures,
    window_seconds=get_settings().login_failure_window_seconds,
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_origin_allowed(request: Request) -> None:
    """登录与写操作的同源校验：带 Origin 头时必须在白名单内。"""
    origin = request.headers.get("Origin")
    if origin is None:
        return  # 非浏览器请求（如 curl、服务间调用）不带 Origin
    if origin not in get_settings().allowed_origins:
        raise AppError(403, CODE_ORIGIN_NOT_ALLOWED, "请求来源不被允许", {"origin": origin})


def login(request: Request, response: Response, body: LoginRequest, store: AuthStore) -> LoginData:
    settings = get_settings()
    check_origin_allowed(request)

    if _rate_limiter.is_blocked(body.username, _client_ip(request)):
        raise AppError(429, CODE_RATE_LIMITED, "登录尝试过于频繁，请稍后再试")

    user = store.get_user_by_username(body.username)
    # 凭据错误统一 401，不区分用户名是否存在（03-接口总览 §3）
    verified = (
        user is not None
        and user.status == "active"
        and PasswordService.verify_password(user.password_hash, body.password)
    )
    if not verified:
        _rate_limiter.record_failure(body.username, _client_ip(request))
        raise AppError(401, CODE_UNAUTHORIZED, "用户名或密码不正确")

    assert user is not None  # 已通过 verified 校验
    _rate_limiter.record_success(body.username, _client_ip(request))

    # 每次登录生成全新会话令牌（会话 ID 旋转）与 CSRF 令牌
    session_token = new_session_token()
    csrf_token = new_csrf_token()
    token_hash = hash_session_token(session_token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.session_ttl_minutes)
    store.create_session(user.id, token_hash, csrf_token, expires_at)

    # 以仓储回读的 CSRF 为准：C 的 sessions 表不存 csrf_token，适配层由会话摘要
    # 派生（H01），此处回读保证与后续 /auth/me、写操作校验用的是同一个值。
    stored = store.get_session(token_hash)
    effective_csrf = stored.csrf_token if stored is not None else csrf_token

    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )
    return LoginData(
        user=UserOut(id=user.id, username=user.username, display_name=user.display_name, role=user.role),
        csrf_token=effective_csrf,
        expires_at=expires_at,
    )


def me(request: Request) -> LoginData:
    session: SessionInfo = get_current_session(request, get_auth_store())
    return LoginData(
        user=UserOut(
            id=session.user_id,
            username=session.username,
            display_name=session.display_name,
            role=session.role,
        ),
        csrf_token=session.csrf_token,
        expires_at=session.expires_at,
    )


def logout(request: Request, response: Response) -> LogoutData:
    """撤销服务端会话并清除 Cookie；CSRF 已在路由层校验。"""
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        get_auth_store().revoke_session(hash_session_token(token))
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return LogoutData()
