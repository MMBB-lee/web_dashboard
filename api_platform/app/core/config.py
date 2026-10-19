"""配置：从 .env 与环境变量读取；密钥不写入仓库。

所有相对路径在加载时自动解析为以代码所在目录（api_platform/）为基准的绝对路径，
不再依赖进程 cwd——sandbox 派生的子进程（如 uvicorn）即便 cwd 被改写，
仍然写到项目内的 data/ 目录，不会因相对路径解析到 sandbox 内部目录而 500。

如果当前进程对解析后的目录不可写（sandbox 拦截 / 安全软件限制），
自动回退到系统临时目录下的 ``api_platform_data/``，保证任意子进程都能写。
**注意**：回退是最后手段，且必须对 API 与 worker 是同一结果——两个进程落到
不同 data 根会让 C 的 ``persist_file`` 与 ``activate_index`` 互相看不见
（2026-10-15 修复探针竞态；见 ``_writable_or_fallback``）。
"""

import logging
import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path as _Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_PACKAGE_ROOT = _Path(__file__).resolve().parent.parent.parent  # api_platform/


def _resolve_path(value: str) -> str:
    if not value:
        return value
    p = _Path(value)
    if p.is_absolute():
        return str(p)
    return str(_PACKAGE_ROOT / p)


def _fallback_root() -> _Path:
    fallback = _Path(tempfile.gettempdir()) / "api_platform_data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _writable_or_fallback(candidate: _Path) -> _Path:
    """探测当前进程能否在 ``candidate`` 下创建文件；不能（sandbox 拦截 / 权限不足）
    则回退到系统临时目录下的 ``api_platform_data/``。

    2026-10-15 修复两处会导致 **API 与 worker 落盘到不同 data 根** 的缺陷：
    ① 探针文件过去用固定名 ``.write_probe``——两个进程（uvicorn + worker）同时
       探测时，后完成者的 ``unlink()`` 会抛 ``FileNotFoundError``（属 OSError），
       于是一个进程被静默回退到 %TEMP%，C 的 ``persist_file`` 与 ``activate_index``
       就此写进不同根目录，表现为「索引 ready 但 19 问答 500 / 产物不存在」。
       现改为 **PID + 随机后缀** 的探针名，并容忍 unlink 失败。
    ② 回退是「路径无关」的兜底，绝不能因为竞态而误触发；一旦回退会打 WARNING 日志。
    """
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback = _fallback_root()
        logging.getLogger("api_platform.config").warning(
            "数据目录不可写，回退到 %s（原路径 %s）", fallback, candidate,
        )
        return fallback
    probe = candidate / f".write_probe_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError:
        fallback = _fallback_root()
        logging.getLogger("api_platform.config").warning(
            "数据目录不可写，回退到 %s（原路径 %s）", fallback, candidate,
        )
        return fallback
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return candidate


def _resolve_dir_field(value: str) -> str:
    """目录型配置：解析为绝对路径并探测可写（不可写才回退 %TEMP%）。"""
    if not value:
        return value
    p = _Path(value)
    if not p.is_absolute():
        p = _PACKAGE_ROOT / p
    return str(_writable_or_fallback(p))


def _resolve_file_field(value: str) -> str:
    """文件型配置（jobs.db / rag_index.json）：只探测**父目录**可写。

    过去对文件路径也直接 ``mkdir`` + 写探针，而 ``rag_index.json`` 一旦已存在，
    ``mkdir`` 会抛 FileExistsError（OSError）→ 文件配置被错误地解析成一个
    %TEMP% 下的**目录**，快照发布随之写穿。
    """
    if not value:
        return value
    p = _Path(value)
    if not p.is_absolute():
        p = _PACKAGE_ROOT / p
    parent = _writable_or_fallback(p.parent)
    return str(parent / p.name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "安徽文旅交通客流观察与预测驾驶舱 API"
    api_version: str = "v1"
    debug: bool = False

    llm_enabled: bool = False
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-flash"
    llm_timeout_seconds: int = 60
    llm_max_tokens: int = 2048

    # 会话与 Cookie（生产 HTTPS 环境必须 SECURE_COOKIES=true）
    session_cookie_name: str = "sd_session"
    session_ttl_minutes: int = 720
    secure_cookies: bool = False
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # 登录失败限速：窗口期内失败达到上限后返回 429
    login_max_failures: int = 5
    login_failure_window_seconds: int = 300

    # CSRF 令牌派生密钥（H01）：C 的 sessions 表不保存 csrf_token，
    # D 用本密钥对会话摘要做 HMAC，得到稳定可重算的 CSRF 令牌。
    # 生产环境必须在 .env 显式设置；留空时进程启动生成一次性随机值并告警。
    csrf_secret: str = ""

    # 数据库连接串：由 C 的 data_foundation 使用（迁移与后端读同一配置）；D 不直接连库
    database_url: str = "mysql+pymysql://user:password@127.0.0.1:3306/tourism_flow"

    # 本地数据目录：知识库暂存文件、作业队列（SQLite）、RAG 索引持久化
    data_dir: str = "./data"
    job_store_path: str = ""  # 默认 {data_dir}/jobs.db；C 的 jobs 表接入前的 D 桥接实现

    # E 包（intelligent_analysis）环境变量模板同款（E_SERVICE_CONTRACT.md §环境变量）
    rag_index_path: str = "./data/rag_index.json"
    detector_weights_path: str = "./data/weights"
    openvino_device: str = "CPU"
    video_tmp_dir: str = "./data/video_tmp"
    video_storage_dir: str = "../videos"
    capture_storage_dir: str = "../cap_images"
    counts_storage_path: str = "./data/counts"

    # E 0.6.0 P2PNet 计数器（camera_visible_people）：受信任的模型包目录。
    # 该目录下必须含 models/、util/、SHTechA.pth 三件套，且 7 个源码文件 +
    # 权重文件的 SHA256 与 E 登记值一致；校验在
    # ``intelligent_analysis.vision.p2p_counter.build_team_model`` 内完成。
    # 未配置或校验失败时，视频分析作业按「模型未配置」诚实失败（422），
    # 绝不回退到已退役的 DM-Count 密度图口径，也不伪造计数。
    #
    # **安全红线**：这是**服务端**配置，权重与源码路径绝不通过 HTTP 暴露；
    # 对外只给 ``P2PNetCounter.provenance`` 里的摘要与版本号。
    p2pnet_bundle_dir: str = "../intelligent_analysis/model_bundle/p2pnet"

    # worker 轮询间隔（秒）
    worker_poll_interval_seconds: float = 2.0
    # worker 租约时长（秒）；与 ``jobs.claim_job`` 的 lease_seconds 一致，
    # 可被心跳 ``jobs.heartbeat`` 续约。
    worker_lease_seconds: int = 120

    @field_validator(
        "data_dir",
        "detector_weights_path",
        "video_tmp_dir",
        "video_storage_dir",
        "counts_storage_path",
        "p2pnet_bundle_dir",
        mode="after",
    )
    @classmethod
    def _absolutize_dirs(cls, value: str) -> str:
        """目录型配置：解析为基于 api_platform/ 仓库根的绝对路径并探测可写。"""
        return _resolve_dir_field(value)

    @field_validator("capture_storage_dir", mode="after")
    @classmethod
    def _capture_directory(cls, value: str) -> str:
        # Keep captures in their configured location; permission errors must not
        # silently redirect screenshots to the generic temporary data folder.
        return _resolve_path(value)

    @field_validator("job_store_path", "rag_index_path", mode="after")
    @classmethod
    def _absolutize_files(cls, value: str) -> str:
        """文件型配置：解析绝对路径，只探测父目录可写（不把文件路径当目录 mkdir）。"""
        return _resolve_file_field(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
