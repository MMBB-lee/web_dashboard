"""待接入路由的统一占位实现。"""

from app.core.errors import CODE_DEPENDENCY_NOT_AVAILABLE, AppError


def pending(feature: str, member: str, package: str) -> None:
    """C/E 的包接入前，数据类路由统一返回 503。"""
    raise AppError(
        503,
        CODE_DEPENDENCY_NOT_AVAILABLE,
        f"{feature}接口待接入：需要{member}的 {package} 包（本地联调时与 api_platform 同级摆放并 pip install -e）",
    )
