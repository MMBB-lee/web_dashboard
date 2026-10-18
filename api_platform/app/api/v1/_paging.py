"""分页查询参数的统一约束（05 / 15 / 25 等列表类接口共用）。

**为什么必须在 HTTP 入口拦**：C 的 ``contracts.Page`` 硬性要求
``1 <= page_size <= 100``，越界时它抛的是纯 ``ValueError``——
**不在 C 的异常族里**，``_c_errors.raise_mapped_c_error`` 认不出来，
最后会被 D 的兜底处理器吞成 500。

C 021a2《致D和E-今日确认与接收清单》第 10 条明确要求：D 应在 HTTP 入口拦截
非法分页，不让 ValueError 漏为 500。

这里用 FastAPI 的 ``Query`` 约束，命中时走 ``RequestValidationError``
处理器 → **400 bad_request**（与 D「请求体/参数错误统一 400」一致）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Query

#: C contracts.Page 的上限，不是 D 自己定的。
MAX_PAGE_SIZE = 100

PageQ = Annotated[int, Query(ge=1, description="页码，从 1 开始")]
PageSizeQ = Annotated[
    int,
    Query(ge=1, le=MAX_PAGE_SIZE,
          description=f"每页数量，默认 20，最大 {MAX_PAGE_SIZE}（C contracts.Page 上限）"),
]
