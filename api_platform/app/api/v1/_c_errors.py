"""C 异常族 → D 统一错误响应的公共翻译层。

凡是「D 调 C 仓储」的路由都走这里，保证同一个 C 异常在任何接口上都是同一个
HTTP 语义，不会某个接口 404、另一个接口 500。

C 的异常类**彼此不继承**（全部直接继承 ``DataFoundationError``），所以按下表
逐类翻译即可；``DataFoundationError`` 兜底放最后。

| C 异常 | HTTP | D 错误码 | 出现处 |
|---|---|---|---|
| ``NotFound`` | 404 | ``not_found`` | 07 / 16 等取单条 |
| ``InvalidPermission`` | 403 | ``forbidden`` | 25 回放授权 |
| ``Conflict`` | 409 | ``conflict`` | 25 摘要不符 |
| ``LeaseLost`` | 409 | ``conflict`` | 作业租约失效 |
| ``AnalysisNotReady`` | 409 | ``analysis_not_ready`` | 27 读计数 |
| ``MetricEntityMismatch`` | 422 | ``metric_entity_mismatch`` | 08 / 09 指标与实体对不上 |
| ``UnsupportedGrain`` | 422 | ``unsupported_grain`` | 08 / 09 来源不支持该粒度 |
| ``QueryLimitExceeded`` | 400 | ``bad_request`` | 查询范围超限 |
| ``InvalidArgument`` | 400 | ``bad_request`` | 参数不被接受 |
| 其余 ``DataFoundationError`` | 503 | ``dependency_not_available`` | — |

**不吞异常**：不认识的异常原样向上抛（最终由全局异常处理成 500），
绝不把 C 的内部失败伪装成「查到了空结果」。
"""

from __future__ import annotations

from app.core.errors import (
    CODE_ANALYSIS_NOT_READY,
    CODE_BAD_REQUEST,
    CODE_CONFLICT,
    CODE_DEPENDENCY_NOT_AVAILABLE,
    CODE_FORBIDDEN,
    CODE_METRIC_ENTITY_MISMATCH,
    CODE_NOT_FOUND,
    CODE_UNSUPPORTED_GRAIN,
    AppError,
)


def _detail(exc: Exception) -> dict:
    """C 的异常把可读原因放在 ``message`` 上；取不到再用 ``str(exc)``。"""
    return {"detail": getattr(exc, "message", None) or str(exc)}


def raise_mapped_c_error(exc: Exception, feature: str) -> None:
    """把 C 的异常翻译成 D 的 AppError 并抛出。

    调用处写法（保持静态可分析，翻译函数不返回时原样向上抛）::

        try:
            result = cstore.list_videos()
        except Exception as exc:  # noqa: BLE001
            raise_mapped_c_error(exc, "视频列表")
            raise

    （示例方法名要用真实存在的：``tests/test_c_store_surface.py`` 会扫源码里
    所有 ``cstore.<名字>`` 并对着真 ``CStore`` 断言，写个占位名会让它失败。）
    """
    from data_foundation import (
        AnalysisNotReady,
        Conflict,
        DataFoundationError,
        InvalidArgument,
        InvalidPermission,
        LeaseLost,
        MetricEntityMismatch,
        NotFound,
        QueryLimitExceeded,
        UnsupportedGrain,
    )

    if isinstance(exc, NotFound):
        raise AppError(404, CODE_NOT_FOUND, f"{feature}：资源不存在", _detail(exc)) from exc
    if isinstance(exc, InvalidPermission):
        raise AppError(403, CODE_FORBIDDEN, f"{feature}：授权无效或不允许该用途",
                       _detail(exc)) from exc
    if isinstance(exc, Conflict):
        raise AppError(409, CODE_CONFLICT, f"{feature}：资源状态冲突", _detail(exc)) from exc
    if isinstance(exc, LeaseLost):
        raise AppError(409, CODE_CONFLICT, f"{feature}：作业租约已失效", _detail(exc)) from exc
    if isinstance(exc, AnalysisNotReady):
        raise AppError(409, CODE_ANALYSIS_NOT_READY, f"{feature}：分析尚未完成",
                       _detail(exc)) from exc
    if isinstance(exc, MetricEntityMismatch):
        raise AppError(422, CODE_METRIC_ENTITY_MISMATCH, f"{feature}：指标与实体类型不匹配",
                       _detail(exc)) from exc
    if isinstance(exc, UnsupportedGrain):
        raise AppError(422, CODE_UNSUPPORTED_GRAIN, f"{feature}：来源不支持该时间粒度",
                       _detail(exc)) from exc
    if isinstance(exc, QueryLimitExceeded):
        raise AppError(400, CODE_BAD_REQUEST, f"{feature}：查询范围超出限制",
                       _detail(exc)) from exc
    if isinstance(exc, InvalidArgument):
        raise AppError(400, CODE_BAD_REQUEST, f"{feature}：请求参数不被接受",
                       _detail(exc)) from exc
    if isinstance(exc, DataFoundationError):
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, f"{feature}：数据层不可用",
                       _detail(exc)) from exc
