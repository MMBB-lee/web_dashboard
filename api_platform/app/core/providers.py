"""外部依赖接入点：C 的 data_foundation 与 E 的 intelligent_analysis。

设计原则：本目录（api_platform）不 import C/E 的包也能启动——
- 登录等不依赖 C/E 的功能正常运行；
- 需要 C/E 的路由通过依赖检查返回 503 dependency_not_available；
- C 的包到位后，顶层导出 new_auth_store() 或 create_store() 即自动接通认证
  （实现 AuthStore 协议，详见 get_auth_store）。

这也是发给 C/E 的函数契约（详见仓库 README「集成契约」一节）。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class UserRecord:
    """C 的 users 表记录（经仓储返回）。"""

    id: str
    username: str
    display_name: str
    role: str  # viewer | admin
    status: str  # active | disabled
    password_hash: str


@dataclass(frozen=True)
class SessionInfo:
    """C 的 sessions 表会话（经仓储返回）。"""

    user_id: str
    username: str
    display_name: str
    role: str
    csrf_token: str
    expires_at: datetime


class AuthStore(Protocol):
    """认证仓储契约：C 在 data_foundation 顶层导出 new_auth_store() 返回本接口实现。"""

    def get_user_by_username(self, username: str) -> UserRecord | None:
        """按用户名取用户；不存在返回 None。"""

    def create_session(self, user_id: str, session_token_hash: str, csrf_token: str, expires_at: datetime) -> None:
        """创建服务端会话；session_token_hash 是会话令牌的 SHA-256 哈希。"""

    def get_session(self, session_token_hash: str) -> SessionInfo | None:
        """取有效会话；过期或已撤销返回 None。"""

    def revoke_session(self, session_token_hash: str) -> None:
        """撤销会话（退出登录）。"""


def _try_import(package_name: str) -> object | None:
    try:
        return __import__(package_name)
    except ImportError:
        return None


def get_auth_store() -> AuthStore:
    """获取认证仓储。

    优先级：
    1. C 直接导出的 `new_auth_store()`（若未来提供）；
    2. C 的 `create_store(database_url, ...)` → 由 D 的适配层
       `app.services.c_auth_store.DataFoundationAuthStore` 接 `uow.auth`
       （C 0.1.9 的实际形态，见该模块文档）；
    3. 都没有则 503。

    测试可通过 fastapi.dependency_overrides 或直接包装本函数注入假实现。
    """
    from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE, AppError

    pkg = _try_import("data_foundation")
    if pkg is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "认证依赖未接入：需要 C 的 data_foundation 包",
        )
    factory = getattr(pkg, "new_auth_store", None)
    if factory is not None:
        return factory()
    if getattr(pkg, "create_store", None) is not None:
        from app.services.c_auth_store import get_data_foundation_auth_store

        return get_data_foundation_auth_store()
    raise AppError(
        503,
        CODE_DEPENDENCY_NOT_AVAILABLE,
        "认证依赖未接入：需要 C 的 data_foundation 包"
        "（顶层导出 new_auth_store() 或 create_store()）",
    )


def require_data_foundation() -> None:
    """依赖检查：路由需要 C 的数据包；缺失时返回 503。"""
    from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE, AppError

    if _try_import("data_foundation") is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "数据依赖未接入：需要 C 的 data_foundation 包",
        )


def require_intelligent_analysis() -> None:
    """依赖检查：路由需要 E 的智能分析包；缺失时返回 503。"""
    from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE, AppError

    if _try_import("intelligent_analysis") is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "智能分析依赖未接入：需要 E 的 intelligent_analysis 包",
        )


def get_e_service():
    """获取 E 的服务入口模块（intelligent_analysis.service）。

    E 契约 v0.4.0 顶层导出：health / answer_message / index_document /
    process_document_index / reindex_documents / analyze_video /
    get_camera_counts / forecast_short_term / forecast_from_count_history /
    save_knowledge_index / load_knowledge_index。
    缺失时抛 503（与 require_intelligent_analysis 同口径）。
    """
    from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE, AppError

    pkg = _try_import("intelligent_analysis")
    if pkg is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "智能分析依赖未接入：需要 E 的 intelligent_analysis 包",
        )
    from intelligent_analysis import service

    return service


def get_job_store():
    """获取作业仓储。

    C 0.2.0a2 上线后：默认走 ``CStore``（基于 ``data_foundation.create_store``），
    由 ``app.services.c_store.CStore`` 适配 E 0.5.1/E 051 的
    ``submit_job / load_counts / validate_analysis / knowledge_index_path``
    与 C 的 ``provenance/knowledge/jobs`` 仓储。

    通过环境变量 ``D_JOB_STORE_BACKEND=sqlite`` 可退回 ``SqliteJobStore``
    （仅供测试与冒烟脚本的快速回环）。

    单例缓存避免每次新建 connection。
    """
    from app.core.config import get_settings
    from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE, AppError

    backend = __import__("os").environ.get("D_JOB_STORE_BACKEND", "c_store").lower()
    if backend == "sqlite":
        from app.services.job_store import SqliteJobStore

        settings = get_settings()
        path = settings.job_store_path or f"{settings.data_dir}/jobs.db"
        return SqliteJobStore(path)

    pkg = _try_import("data_foundation")
    if pkg is None or not hasattr(pkg, "create_store"):
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "作业依赖未接入：需要 C 的 data_foundation 包导出 create_store()",
        )
    settings = get_settings()
    return _get_or_create_c_store(settings, pkg)


_c_store_singleton: dict[str, object] = {}


def _get_or_create_c_store(settings, pkg):
    key = f"{settings.database_url}|{settings.data_dir}"
    cached = _c_store_singleton.get(key)
    if cached is not None:
        return cached
    store = pkg.create_store(
        settings.database_url,
        data_root=settings.data_dir,
    )
    adapter = _build_c_store(store)
    _c_store_singleton[key] = adapter
    return adapter


def _build_c_store(store):
    from app.services.c_store import CStore
    return CStore(store)


def get_c_store():
    """独立入口：让 e_bridge 在 ``backend=c_store`` 时直接拿 CStore 调 C 三段登记。

    生产环境（``backend=c_store``）与 ``get_job_store()`` 返回同一对象。
    ``backend=sqlite`` 时返回 ``None``——20 上传跳过 C 三段登记，只走 D 桥接
    （不入知识库原始资产、不进 C jobs 表，由 SqliteJobStore 兜底）。
    """
    backend = __import__("os").environ.get("D_JOB_STORE_BACKEND", "c_store").lower()
    if backend == "c_store":
        return get_job_store()
    return None
