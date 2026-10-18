"""统一错误格式与业务异常。

错误响应统一为：
    {"error": {"code": "...", "message": "...", "details": {}}, "request_id": "..."}
状态码约定（03-接口总览 §1）：
    400 参数错误、401 未登录、403 权限不足、404 不存在、409 状态冲突、
    422 不支持的口径或粒度、429 请求过频、503 依赖未接入。
"""

from typing import Any


class AppError(Exception):
    """业务异常：路由与服务层抛出，由全局处理器渲染为统一错误格式。"""

    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


# 错误码常量（与 03-接口总览 的语义对应）
CODE_BAD_REQUEST = "bad_request"
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_CONFLICT = "conflict"
CODE_UNSUPPORTED_GRAIN = "unsupported_grain"
CODE_METRIC_ENTITY_MISMATCH = "metric_entity_mismatch"
CODE_RATE_LIMITED = "rate_limited"
CODE_UNSUPPORTED = "unsupported"
CODE_ANALYSIS_NOT_READY = "analysis_not_ready"
CODE_ORIGIN_NOT_ALLOWED = "origin_not_allowed"
CODE_CSRF_REQUIRED = "csrf_required"
CODE_DEPENDENCY_NOT_AVAILABLE = "dependency_not_available"
CODE_INTERNAL_ERROR = "internal_error"
