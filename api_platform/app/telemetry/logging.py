"""日志与审计。

约定（团队文档）：密码与会话 ID 不写入日志；
请求日志统一携带 request_id（由 main.py 中间件注入）。
"""

import logging


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"api_platform.{name}")


SENSITIVE_FIELDS = ("password", "csrf_token", "session", "cookie", "authorization")


def sanitize_for_log(payload: dict) -> dict:
    """输出日志前移除敏感字段。"""
    return {
        k: ("***" if any(s in k.lower() for s in SENSITIVE_FIELDS) else v)
        for k, v in payload.items()
    }
