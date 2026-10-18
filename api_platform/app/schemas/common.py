"""通用约定模型：分页、统一响应包裹。

成功响应统一为 {"data": ..., "meta": {"request_id": "..."}}；
列表 meta 另含 page、page_size、total（03-接口总览 §1）。
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

#: 业务时区（03 §1：时间一律带时区；对外呈现统一用 +08:00）。
#: 单独定义在 schemas 层，避免服务层（e_bridge）与模型层循环导入。
TZ_CN = timezone(timedelta(hours=8))


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100")


class ListMeta(BaseModel):
    request_id: str
    page: int
    page_size: int
    total: int


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: dict[str, Any] = Field(default_factory=dict)


def envelope(request: Any, data: Any, **meta: Any) -> dict:
    """构造统一成功响应；request_id 由中间件注入 request.state。"""
    meta.setdefault("request_id", getattr(request.state, "request_id", ""))
    return {"data": data, "meta": meta}


def iso_cn(value: Any) -> str | None:
    """date / datetime → ISO 8601 字符串（03 §1）。

    C 的 ``public_columns`` 把 datetime 统一转成 **UTC aware**；对外一律按
    业务时区 +08:00 呈现，避免同一个字段在不同接口里一会儿 UTC 一会儿本地。
    纯 date（如来源覆盖区间）没有时区概念，原样输出。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            return value.isoformat()
        return value.astimezone(TZ_CN).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
