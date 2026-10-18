"""认证接口（03-接口总览 §3：02 / 03 / 04）。"""

from fastapi import APIRouter, Depends, Request, Response

from app.core.permissions import require_authenticated, require_csrf
from app.core.providers import get_auth_store
from app.schemas.auth import LoginRequest
from app.schemas.common import envelope
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login")
async def login(request: Request, response: Response, body: LoginRequest) -> dict:
    """02 POST /auth/login：公开；限速；成功旋转会话 ID 并写入 Cookie。"""
    data = auth_service.login(request, response, body, get_auth_store())
    return envelope(request, data.model_dump(mode="json"))


@router.get("/me")
async def me(request: Request, _session=Depends(require_authenticated)) -> dict:
    """03 GET /auth/me：已登录；可重新取得当前会话的 CSRF token。"""
    data = auth_service.me(request)
    return envelope(request, data.model_dump(mode="json"))


@router.post("/logout")
async def logout(request: Request, response: Response, _session=Depends(require_csrf)) -> dict:
    """04 POST /auth/logout：已登录 + CSRF；撤销服务端会话并清除 Cookie。"""
    data = auth_service.logout(request, response)
    return envelope(request, data.model_dump(mode="json"))
