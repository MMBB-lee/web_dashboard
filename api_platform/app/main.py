"""FastAPI 应用入口（唯一后端入口）。

- 基础路径 /api/v1；除 /health/live 与 /auth/login 外均要求会话。
- 统一错误格式：{"error": {"code","message","details"}, "request_id"}。
- 每个请求注入 request_id（响应头 X-Request-ID）。
"""

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import (
    CODE_BAD_REQUEST,
    CODE_INTERNAL_ERROR,
    CODE_NOT_FOUND,
    AppError,
)

logger = logging.getLogger("api_platform")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Media is read/written by the backend; a fresh checkout may have no folders.
    video_root = Path(settings.video_storage_dir)
    video_root.mkdir(parents=True, exist_ok=True)
    (video_root.parent / "cap_images").mkdir(parents=True, exist_ok=True)
    # E 0.5.0：注入 D 拥有的队列/读取绑定（幂等；E 缺失不阻断启动，路由 503 兜底）
    from app.services.e_bindings import configure_e_services

    configure_e_services()
    yield


app = FastAPI(
    title=get_settings().app_name,
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex[:16]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _error_response(request: Request, status_code: int, code: str, message: str, details: dict) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, "details": details},
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _error_response(request, exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 只取可序列化字段（errors 里的 input/ctx 可能携带不可 JSON 化的对象）
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()[:10]
    ]
    return _error_response(request, 400, CODE_BAD_REQUEST, "请求参数错误", {"errors": errors})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: CODE_NOT_FOUND}.get(exc.status_code, f"http_{exc.status_code}")
    return _error_response(request, exc.status_code, code, str(exc.detail), {})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常", exc_info=exc)
    return _error_response(request, 500, CODE_INTERNAL_ERROR, "服务器内部错误", {})


# 01 GET /health/live：公开；只返回 {"status":"ok"}，不泄露内部信息
@app.get("/api/v1/health/live", tags=["系统"])
async def health_live() -> dict:
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")
