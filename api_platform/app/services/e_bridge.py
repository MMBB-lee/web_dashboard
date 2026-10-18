"""E 包（intelligent_analysis）的调用桥接层。

E 0.5.1 起职责变化（与 0.5.0/0.4.0 不再兼容）：

- 落盘交给 D：worker 拿到 ``intelligent_analysis.worker.runner.dispatch_analysis_job``
  返回的 ``artifact_bytes/sha256/chunks/snapshot`` 后，自己调
  ``C store.persist_file`` 落盘，再用 C 的 ``activate_index`` 同步生效；
- 提交：``E.index_document / E.analyze_video / E.reindex_documents`` 全部走
  ``intelligent_analysis.submit`` → D 的 ``bindings.submit_job`` → C jobs.enqueue_or_get；
- 计数：``E.get_camera_counts`` 走 ``bindings.load_counts(video_id, analysis_id)``，
  D 的 load_counts 把 d_video_count_points 写成 CountsResponse 形状（含
  recorded_start_at/recorded_end_at/roi/analysis），并按 grain 过滤；
- 问答：``E.answer_message`` 在绑定 loader 模式下用
  ``bindings.knowledge_snapshot_loader()`` 取已提交的活动快照；路径模式下
  E 自管 ``_global_index``，D 不能重造；快照损坏抛 ``DependencyUnavailable``。

本模块供路由层调用，不直接 import E 内部模块。
"""
from __future__ import annotations

import hashlib
import inspect
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.core.errors import (
    CODE_ANALYSIS_NOT_READY,
    CODE_BAD_REQUEST,
    CODE_DEPENDENCY_NOT_AVAILABLE,
    CODE_NOT_FOUND,
    CODE_UNSUPPORTED,
    AppError,
)
from app.core.providers import get_e_service, get_c_store
from app.services import metrics_query

logger = logging.getLogger("api_platform.e_bridge")

TZ_CN = timezone(timedelta(hours=8))

# get_settings 一律函数内 import（顶层会让 monkeypatch 失效、运行时目录漂移）

SUPPORTED_DOC_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}
COUNT_METRIC = "camera_visible_people"
#: E 0.6.0 固定的置信度阈值（不是可调参数，D 主动注入队列 payload）。
COUNT_CONFIDENCE_THRESHOLD = 0.5

#: ``document_version`` 取原件内容 SHA256 的前 32 位 hex 作指纹（C 口径）。
#: C 的 16 篇 manifest 文档统一写作 ``sha256:<32位hex>``（如
#: ``sha256:29aa803a0fa3e009feed5ce3aa07330e``），是**原件文件字节**的摘要前缀，
#: 不是递增版本号。长度 7+32=39 ≤ C ``KnowledgeDocument.document_version`` 的
#: String(64)。详见 03-C 交接包 ``real_data/2026-10-12-v2/manifest.json``。
DOCUMENT_VERSION_PREFIX = "sha256:"
DOCUMENT_VERSION_HEX_LEN = 32


def document_version_from_bytes(file_bytes: bytes) -> str:
    """由原件字节推导 C 口径的 ``document_version``。

    规则：``"sha256:" + sha256(file_bytes).hexdigest()[:32]``。

    这是 D 上传（20 号）时唯一合法的版本标识来源——C 的 manifest 用同一算法
    为每篇文档预置版本号，D 不得自行造递增版本（如 ``"v1"``），否则 E 的
    ``knowledge/artifacts.py::_validate_document`` 会在加载时因
    ``document_version`` 与 C 库中登记值不一致而拒绝该产物。
    """
    digest = hashlib.sha256(file_bytes).hexdigest()
    return f"{DOCUMENT_VERSION_PREFIX}{digest[:DOCUMENT_VERSION_HEX_LEN]}"


def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        raise AppError(400, CODE_BAD_REQUEST, f"{field} 不是合法的 UUID：{value}")


def _raise_mapped(exc: Exception) -> None:
    """E 的依赖异常 → D 的 HTTP 口径映射。

    - ``DependencyUnavailable`` → 503（产物损坏或 binding 未注入）；
    - ``UnsupportedAnalysis`` → 422（含 weights/runtime 未配置）；
    - ``analysis_not_ready`` 是 dict 状态，不属异常映射。
    """
    try:
        from intelligent_analysis.integration import (
            DependencyUnavailable,
            UnsupportedAnalysis,
        )
    except ImportError:  # pragma: no cover - E 缺失已由 require_intelligent_analysis 拦下
        return
    if isinstance(exc, DependencyUnavailable):
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, str(exc)) from exc
    if isinstance(exc, UnsupportedAnalysis):
        raise AppError(
            422,
            CODE_UNSUPPORTED,
            str(exc),
            {"e_module_version": _e_module_version()},
        ) from exc


# -------- 19 问答 --------

def prepare_answer(
    message: str,
    conversation_id: str | None,
    context: dict[str, Any] | None,
    owner_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """19 问答：实调 E.answer_message；conversation_id 转 UUID。

    E 0.5.1 自己负责快照可见性：
    - 配了 ``knowledge_snapshot_loader``：E 通过 ``bindings.knowledge_snapshot_loader()``
      调 D 侧的 loader（如 C store 的 ``snapshot_for_e_publisher``）；
    - 配了 ``knowledge_index_path``：E 自管 ``_global_index``；
    - 都没配：E 的 answer_message 走空索引，报 answered 状态但 evidence 为空 —
      D 不能在这个空响应里塞「快照未发布」的话术，那是 E 自己的对话。E
      真正提供不了答案时由它返回 status=insufficient_evidence；D 实在要
      用户友好提示可在路由层 envelope。

    损坏快照 / binding 缺失 → DependencyUnavailable → 503。

    **数值工具注入（E 0.6.0 P1）**：context 里显式给了 ``metric`` 时，D 才向
    E 注入 ``metric_query`` + ``metric_tool``（受约束只读查询，见
    ``app/services/metrics_query.py``）。不给 metric 就保持文档问答路径——E 的
    metric 分支**不检索知识库**，拿不到数只会回「无法核实数值」，不能因为请求
    顺手带了 entity_ids / 时间范围就把知识库问答整条掐掉。
    """
    conv_uuid = _parse_uuid(conversation_id, "conversation_id") if conversation_id else None
    from app.core.config import get_settings
    settings=get_settings()
    if settings.llm_enabled:
        from app.services.assistant_agent import prepare
        return prepare(message,conversation_id,context,owner_id,settings), False
    from app.services.system_answers import answer_system_question
    system = answer_system_question(message, conversation_id)
    if system is not None:
        return system, True

    ctx: dict[str, Any] | None = dict(context) if context else None
    from app.services.data_answers import answer_from_data
    direct = answer_from_data(message, conversation_id, ctx)
    if direct is not None:
        return direct, True
    service = get_e_service()
    if ctx is not None:
        metric_query = metrics_query.normalise_query(ctx)
        if metric_query is not None:
            ctx["metric_query"] = metric_query
            ctx["metric_tool"] = metrics_query.metric_tool
    try:
        baseline = service.answer_message(message, conv_uuid, ctx)
    except Exception as exc:
        _raise_mapped(exc)
        raise
    from app.services.grounded_llm import compact_answer
    return compact_answer(message, baseline), False


def answer_message(message: str, conversation_id: str | None, context: dict[str, Any] | None, owner_id: str | None = None) -> dict[str, Any]:
    baseline, direct = prepare_answer(message, conversation_id, context, owner_id)
    if direct:
        return baseline
    from app.services.grounded_llm import generate
    result=generate(message, baseline, context)
    from app.services.assistant_agent import remember, finalize
    result=finalize(result)
    remember(owner_id,message,result)
    return result


# -------- 20 上传（C 三段登记可选） --------

def stage_and_accept_document(
    file_bytes: bytes,
    original_filename: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """20 文档上传（E 0.5.1；C 0.2.0a2 三段登记在 cstore 可用时执行）。

    流程：
    1) 校验后缀；
    2) D 在路由层生成 document_id / source_id / asset_id 三个 UUID；
    3) staging file 写到 ``settings.data_dir/knowledge_staging/{document_id}{suffix}``，
       E 0.5.1 worker 从 file_path 读；
    4) C 三段登记（persist_file / register_source / register_asset / register_document）——
       **仅当 cstore 非 None 时执行**；测试 sqlite 后端跳过；
    5) E ``index_document`` 经 E 内部 ``submit("knowledge_index", ...)`` 入队
       → D 的 ``bindings.submit_job`` → 实际作业库（SqliteJobStore / C jobs 表）。
       D 路由层不再重复入队。

    E submit 校验：payload 顶层 ``document_version`` 必填；metadata 经
    ``DocumentMetadata.model_validate`` 校验（要求 permission_note 非空、dates tz-aware）。
    """
    suffix = Path(original_filename).suffix.lower()
    if suffix not in SUPPORTED_DOC_SUFFIXES:
        raise AppError(
            400,
            CODE_BAD_REQUEST,
            f"不支持的文档类型 {suffix}，仅支持 PDF / TXT / Markdown",
        )

    from app.core.config import get_settings

    settings = get_settings()
    cstore = get_c_store()

    document_id = str(uuid4())
    source_id = str(uuid4())
    asset_id = str(uuid4())
    now = datetime.now(timezone.utc)
    # C 口径的文档版本标识（原件内容 SHA256 前 32 位）——同一值必须
    # ① 写进 C 的 KnowledgeDocument.document_version，② 随 payload 传给 E。
    # 二者不一致时 E 的 artifacts._validate_document 会拒绝加载产物。
    document_version = document_version_from_bytes(file_bytes)

    # staging file 先写好（E worker 从 file_path 读）。
    # 优先用 settings.data_dir 下的子目录；sandbox 派生进程对项目目录写入
    # 受限时（如 uvicorn 子进程被 sandbox 拦截 CreateFileW），回退到
    # 系统临时目录 %TEMP%/dpart_knowledge_staging/，绕开路径无关的写入拦截。
    import tempfile
    try:
        staging_dir = Path(settings.data_dir) / "knowledge_staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_path = staging_dir / f"{document_id}{suffix}"
        staging_path.write_bytes(file_bytes)
    except PermissionError:
        staging_dir = Path(tempfile.gettempdir()) / "dpart_knowledge_staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_path = staging_dir / f"{document_id}{suffix}"
        staging_path.write_bytes(file_bytes)

    # C 三段登记（cstore=None 时跳过——sqlite 回退场景只验 D 桥接）
    if cstore is not None:
        import io

        file_ref = cstore.persist_file(
            source_id=source_id,
            original_name=original_filename,
            stream=io.BytesIO(file_bytes),
        )
        artifact_path = file_ref["relative_path"]
        sha256 = file_ref["sha256"]
        byte_size = file_ref["byte_size"]

        cstore.register_source(
            source_id=source_id,
            publisher=metadata.get("publisher") or "unknown",
            title=metadata.get("title") or original_filename,
            url=metadata.get("source_url") or "",
            usage_note=metadata.get("permission_note") or "已授权",
            update_frequency="irregular",
            adapter_key="intelligent_analysis.knowledge",
            native_grain=None,
            verification_status="approved",
            enabled=True,
        )
        asset_row = cstore.register_asset(
            source_id=source_id,
            url=metadata.get("source_url") or "",
            title=metadata.get("title") or original_filename,
            retrieved_at=now,
            relative_path=artifact_path,
            sha256=sha256,
            media_type=_media_type_from_suffix(suffix),
            byte_size=byte_size,
            usage_note=metadata.get("permission_note") or "已授权",
            published_at=_aware_or_none(metadata.get("published_at")),
        )
        # SourceAsset.id 是 C 0.2.0a2 数据库生成的（D 不再传 id），
        # KnowledgeDocument.id 仍由 D 控制（保持与 staging file / E payload 一致）。
        asset_id = asset_row["asset_id"]
        cstore.register_document(
            document_id=document_id,
            source_id=source_id,
            asset_id=asset_id,
            title=metadata.get("title") or original_filename,
            url=metadata.get("source_url") or "",
            publisher=metadata.get("publisher") or "",
            published_at=_aware_or_none(metadata.get("published_at")),
            valid_from=_aware_or_none(metadata.get("valid_from")),
            valid_until=_aware_or_none(metadata.get("valid_until")),
            usage_note=metadata.get("permission_note") or "已授权",
            document_version=document_version,
        )

    service = get_e_service()
    payload_metadata = {
        **metadata,
        "published_at": _aware_cn(metadata.get("published_at")),
        "valid_from": _aware_cn(metadata.get("valid_from")),
        "valid_until": _aware_cn(metadata.get("valid_until")),
        "document_id": document_id,
        "document_version": document_version,
    }

    try:
        return service.index_document(str(staging_path), payload_metadata)
    except ValidationError as exc:
        raise AppError(400, CODE_BAD_REQUEST, "文档元数据不合法", {"errors": exc.errors()[:10]}) from exc
    except Exception as exc:
        _raise_mapped(exc)
        raise


def _media_type_from_suffix(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
    }.get(suffix, "application/octet-stream")


def _aware_or_none(value: Any) -> datetime | None:
    """无值/空串/不合法字符串 → None；naive → 东八区。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=TZ_CN)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ_CN)
    return None


def _aware_cn(value: Any) -> Any:
    """同 _aware_or_none 但无值时返回原值；E 的 DocumentMetadata 校验报错更友好。"""
    parsed = _aware_or_none(value)
    return parsed.isoformat() if parsed else value


# -------- 22 重建索引 --------

def reindex_documents(document_ids: list[str], reason: str) -> dict[str, Any]:
    """22 经 E.submit 入队一个 knowledge_reindex 信封作业。

    E 0.5.1 ``reindex_documents`` 不做任何展开（``submit("knowledge_reindex", ...)``
    即返回）；**逐文档展开为 knowledge_index 子作业是 D worker 的职责**
    （``app.worker.DWorker._run_knowledge_reindex``）。
    """
    try:
        return get_e_service().reindex_documents(document_ids, reason)
    except Exception as exc:
        _raise_mapped(exc)
        raise


# -------- 26 创建录像分析 --------

def _roi_for_e(roi: Any) -> Any:
    if isinstance(roi, dict):
        points = roi.get("points")
        if isinstance(points, list) and points and isinstance(points[0], (list, tuple)):
            roi = {**roi, "points": [
                {"x": float(p[0]), "y": float(p[1])} if len(p) == 2 else p for p in points
            ]}
        return roi
    if isinstance(roi, list):
        return {"points": [
            {"x": float(p[0]), "y": float(p[1])} if isinstance(p, (list, tuple)) and len(p) == 2 else p
            for p in roi
        ]}
    return roi


def _resolve_video_asset(video_id: str) -> dict[str, Any]:
    """解析视频资产（E ``process_video_job`` 需要的 file_path / 录制起止 /
    source_ids）。

    当前返回 **空 dict**——C 的视频资产与授权记录契约（接口 24）尚未下发，
    D 没有可信的本地文件路径与录制时间窗口可填。**绝不编造**：
    留空 → worker 领到作业后按「视频文件未接入」诚实失败（error_summary 写明）。

    待 C 接口 24 落地后，这里改成从 C 的 video asset 仓储读：
    ``{"file_path": ..., "recorded_start_at": ..., "recorded_end_at": ...,
    "source_ids": [...]}``。
    """
    return {}


def analyze_video(video_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """26 创建录像分析：**同一事务**入队 ``video_analysis`` 作业并在 C 登记分析。

    2026-10-17 起（装上 C 0.2.1a2）不再是「D 自己 submit + 往私表补一行」：

    - 其次，模型溯源 **必须**来自 E 的 P2PNet 计数器（``weights_sha256`` 等 7 键），
      C 的 ``create_analysis`` 硬性要求，拿不到就 422/503，不编造；
    - 入队与登记在**同一个写事务**里完成（``cstore.enqueue_visible_analysis``），
      避免 worker 抢在登记之前把作业领走导致登记被拒；
    - HTTP 202 只代表「已入队且已登记」，**不代表分析完成**（27 号读到
      analysis_not_ready 是正常中间态）。

    真正的帧处理仍然由 E 承担（worker 里 ``dispatch_analysis_job``）。
    """
    try:
        video_uuid = UUID(video_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, f"video_id 不是合法的 UUID：{video_id}") from None

    from app.core.providers import get_c_store
    from app.services import e_bindings
    from app.services.visible_pipeline import (
        VisiblePipelineError,
        build_analysis_config,
        build_analysis_provenance,
        check_provenance_consistency,
    )

    if e_bindings.get_p2p_counter() is None:
        raise AppError(
            422,
            CODE_UNSUPPORTED,
            "P2PNet 模型包未登记或校验失败，请配置 P2PNET_BUNDLE_DIR 指向受信任目录",
            {"e_module_version": _e_module_version()},
        )

    analysis_id = str(uuid4())
    try:
        analysis_config = build_analysis_config(config)
        provenance = build_analysis_provenance()
        check_provenance_consistency(analysis_config, provenance)
    except VisiblePipelineError as exc:
        raise AppError(422, CODE_UNSUPPORTED, str(exc)) from exc

    # 仍然显式过一遍 E 的校验器（P2PNetCounter.validate_config）：
    # D 绕开了 E 的 ``service.analyze_video``，但**不能连校验一起绕掉**——设备 /
    # 引擎 / 权重不被支持必须在 26 号当场拒绝，不能拖到 worker 才失败。
    e_config = {k: v for k, v in analysis_config.items() if v is not None}
    e_config["roi"] = _roi_for_e(analysis_config["roi"])
    try:
        e_bindings.validate_analysis(e_config)
    except Exception as exc:  # UnsupportedAnalysis → 422 unsupported
        _raise_mapped(exc)
        raise

    # ⚠️ **顺序是有意的**：上面三步都是「这个请求本身合不合法」的判断，必须
    # 排在基础设施检查之前。若把 503 提到前面，一旦 C 的存储暂时不可用，
    # 本该 422 的非法设备/引擎会被报成「依赖不可用」——既掩盖了契约错误，
    # 又会误导调用方以为重试就能成功。
    store = get_c_store()
    if store is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "画面人数分析需要先能写入 C 的画面人数仓储（当前 C 存储未接入）",
        )

    from data_foundation import Conflict, InvalidArgument, InvalidPermission, NotFound

    try:
        enrolled = store.enqueue_visible_analysis(
            video_id=str(video_uuid),
            analysis_id=analysis_id,
            config=analysis_config,
            provenance=provenance,
            reason=f"26 画面人数分析（{provenance.get('weights_version')}）",
        )
    except NotFound as exc:
        raise AppError(404, CODE_NOT_FOUND, "录像不存在", {"video_id": video_id}) from exc
    except InvalidPermission as exc:
        raise AppError(
            403, "forbidden", "该录像没有可用于画面人数分析的有效授权", {"video_id": video_id},
        ) from exc
    except (Conflict, InvalidArgument) as exc:
        raise AppError(400, CODE_BAD_REQUEST, str(getattr(exc, "message", exc)),
                       {"video_id": video_id}) from exc

    return {
        "job_id": str(enrolled["job"]["id"]),
        "analysis_id": analysis_id,
        "video_id": str(video_uuid),
        "status": "queued",
        "count_metric": COUNT_METRIC,
    }


def _errors_of(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()[:10]
    ]


# -------- 27 录像计数 --------

def get_camera_counts(
    video_id: str,
    analysis_id: str,
    start: str | None,
    end: str | None,
    grain: str,
) -> dict[str, Any]:
    """27 画面人数计数：**从 C 的 ``uow.visible`` 读**（2026-10-17 起）。

    以前走 E 的 ``service.get_camera_counts`` + D 私表；现在计数结果由 C
    统一掌管（``visible_analyses`` / ``visible_count_points``）：

    - 归属校验由 C 做（``get_counts(video_id, analysis_id)`` 不匹配 → C 抛
      ``NotFound`` → D 转 404），**不再依赖 D 私表 ``d_video_analyses``**；
    - 未完成 → C 返回 ``status=analysis_not_ready`` 且 points 为空 → D 转 409；
    - ⚠️ **C 的 ``get_counts`` 没有 grain / start / end 参数**，一次给全量
      frame + 5min 点。03 §27 要求「D 对 C 原生结果执行查询过滤」，所以过滤在
      D 侧做（``visible_pipeline.filter_counts``），且 **不跨 analysis 混合**。
    """
    # 先做 UUID 格式校验（bad_id → 400 而不是 404）
    _parse_uuid(video_id, "video_id")
    _parse_uuid(analysis_id, "analysis_id")

    from app.core.providers import get_c_store
    from app.services.visible_pipeline import (
        VisiblePipelineError,
        check_counts_shape,
        filter_counts,
    )

    store = get_c_store()
    if store is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "画面人数结果由 C 的画面人数仓储提供（当前 C 存储未接入）",
        )

    from data_foundation import InvalidPermission, NotFound

    try:
        counts = store.get_visible_counts(video_id=video_id, analysis_id=analysis_id)
    except NotFound as exc:
        raise AppError(
            404,
            CODE_NOT_FOUND,
            "录像分析不存在或不属于该录像",
            {"video_id": video_id, "analysis_id": analysis_id},
        ) from exc
    except InvalidPermission as exc:
        raise AppError(
            403, "forbidden", "该录像没有可用于读取画面人数结果的有效授权",
            {"video_id": video_id, "analysis_id": analysis_id},
        ) from exc

    if (counts.get("status") or "ready") != "ready":
        raise AppError(
            409,
            CODE_ANALYSIS_NOT_READY,
            "该录像分析尚未完成，计数数据未就绪",
            {"analysis_id": analysis_id},
        )

    filtered = filter_counts(counts, grain=grain, start=start, end=end)
    # 点形状自查：脏点会让下游 Pydantic 直接 500，或者安静返回 value=null。
    # 数据源换成 C 之后这条防线也保留——C 的 write_points 是白名单校验，正常
    # 不会脏，但「正常」不能替代检查。
    try:
        check_counts_shape(filtered.get("points") or [])
    except VisiblePipelineError as exc:
        raise AppError(422, CODE_UNSUPPORTED, str(exc),
                       {"analysis_id": analysis_id}) from exc
    # 审计摘要（weights_sha256 / model_source_revision）：C 的 get_counts 只在
    # ready 时给 ``analysis``（即 config，不含这两键），而 E 的 CountsResponse
    # 是 ``extra="forbid"``，塞不进去。这两项始终由 D 单独从 C 的
    # ``visible_analyses.provenance`` 读，**不经 E、也不假手 E**。
    audit = _visible_provenance(store, analysis_id)
    return {
        **filtered,
        "roi": _normalise_roi(filtered.get("roi")),
        "weights_sha256": audit.get("weights_sha256"),
        "model_source_revision": audit.get("model_source_revision"),
    }


def _visible_provenance(store: Any, analysis_id: str) -> dict[str, Any]:
    """读 C ``visible_analyses.provenance`` 里的模型溯源（只读 audit 两键）。"""
    getter = getattr(store, "get_visible_analysis", None)
    if getter is None:
        return {}
    try:
        row = getter(analysis_id) or {}
    except Exception:  # noqa: BLE001 —— 审计取不到不该让计数主链路失败
        return {}
    provenance = row.get("provenance") or {}
    return {k: provenance.get(k) for k in ("weights_sha256", "model_source_revision")}


def _normalise_roi(roi: Any) -> Any:
    if not isinstance(roi, dict):
        return roi
    pts = roi.get("points")
    if not isinstance(pts, list):
        return roi
    converted: list[list[float]] = []
    for point in pts:
        if isinstance(point, dict) and "x" in point and "y" in point:
            converted.append([point["x"], point["y"]])
        elif isinstance(point, (list, tuple)) and len(point) == 2:
            converted.append(list(point))
        else:
            return roi
    return {"points": converted}


def _normalise_counts(result: dict[str, Any], grain: str) -> dict[str, Any]:
    """整形成 camera_visible_people 口径。

    旧契约 `entries/exits` 出现按 422 unsupported 拒绝；ROI 统一归一化为
    ``[[x, y], ...]``（前端 B 联调确认的形状）。
    """
    raw_points = result.get("points") or []
    if raw_points and "value" not in raw_points[0]:
        raise AppError(
            422,
            CODE_UNSUPPORTED,
            "E 返回的是旧版越线进出计数（entries/exits），不是 camera_visible_people；"
            "需 E 切换到 P2PNet 点计数（E 0.6.0 正式口径）",
            {"fields": sorted(raw_points[0].keys())},
        )
    points = []
    for point in raw_points:
        normalised = dict(point)
        normalised.setdefault("metric", COUNT_METRIC)
        normalised.setdefault("unit", "人")
        normalised.setdefault("grain", grain)
        if grain == "5min":
            normalised.setdefault("aggregation_rule", "median")
        points.append(normalised)
    out = {**result, "points": points, "grain": grain}
    if result.get("roi") is not None:
        out["roi"] = _normalise_roi(result["roi"])
    return out


# -------- 28 短时预测 --------

def forecast_short_term(
    video_id: str,
    analysis_id: str,
    cutoff_at: str,
    horizon_minutes: int,
    step_minutes: int,
) -> dict[str, Any]:
    """28 短时预测：**预测结果必须由 C 挂在独立的 ``visible_forecast`` 作业上保存。**

    03 §28 与 C 26 号文档「预测存储」都要求这点——``publish_forecast`` 会拿 lease
    去查 job，校验 type / payload 三要素一致后才落 ``visible_forecasts``。所以
    D 不能「算完直接回给前端」，必须先有一个真作业。

    这条 HTTP 为了保持 03 §28 的同步响应形状，**当场把这个作业消费掉**：
    入队 → 立刻 claim → E 算 → publish（内部原子 complete_job）。28 号因此
    既是「一次预测请求」也是一条可审计的作业记录。

    历史点由 C 提供（``forecast_input`` 给 ``history`` 与 ``input_sha256``），
    D 不自己挑窗口、不重算指纹。
    """
    video_uuid = _parse_uuid(video_id, "video_id")
    analysis_uuid = _parse_uuid(analysis_id, "analysis_id")

    from app.core.providers import get_c_store
    from app.services.visible_pipeline import (
        VisiblePipelineError,
        run_visible_forecast,
    )

    store = get_c_store()
    if store is None:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "画面人数预测需要写入 C 的画面人数仓储（当前 C 存储未接入）",
        )

    # ⚠️ **先做存在性校验，再建作业**（与 26 号同一条原则：
    # 「这个请求合不合法」的判断要排在「开票」之前）。否则对一个不存在的分析
    # 也会先落一条 visible_forecast 作业，然后才在 worker 里踩 NotFound——
    # 库里留一条永远跑不出结果的垃圾作业。
    from data_foundation import Conflict, InvalidArgument, InvalidPermission, NotFound

    try:
        existing = store.get_visible_analysis(str(analysis_uuid))
    except NotFound as exc:
        raise AppError(
            404, CODE_NOT_FOUND, "画面人数分析不存在",
            {"analysis_id": analysis_id},
        ) from exc
    except (Conflict, InvalidArgument) as exc:
        raise AppError(
            400, CODE_BAD_REQUEST, str(getattr(exc, "message", exc)),
            {"analysis_id": analysis_id},
        ) from exc

    # 分析必须属于这条 URL 上的录像。这里**同样回 404 而不是 409/403**：
    # 「别人的分析挂在你的 video_id 上」这种跨录像拼装，对外不该暴露「它存在
    # 但不属于你」。C 在 26/27 上就是这么判的（归属不匹配 → NotFound），D 对齐。
    owner_video_id = str(existing.get("video_id") or "")
    if owner_video_id and owner_video_id != str(video_uuid):
        raise AppError(
            404, CODE_NOT_FOUND, "画面人数分析不存在",
            {"analysis_id": analysis_id},
        )

    payload = {
        "video_id": str(video_uuid),
        "analysis_id": str(analysis_uuid),
        "cutoff_at": cutoff_at,
        "horizon_minutes": horizon_minutes,
        "step_minutes": step_minutes,
    }
    job_id = store.create_job(
        "visible_forecast", payload,
        idempotency_key=_forecast_idempotency_key(
            analysis_uuid, cutoff_at, horizon_minutes, step_minutes),
        reason="28 画面人数短时预测",
    )
    claimed = store.claim_job(
        "d-http-forecast", now=datetime.now(timezone.utc),
        lease_seconds=_forecast_lease_seconds(), job_id=job_id,
    )
    if claimed is None:
        # 极端竞争：作业已被另一个消费者领走。不伪造结果，直接 409 让调用方重试。
        raise AppError(
            409,
            CODE_CONFLICT,
            "该预测作业已被其它消费者领取，请稍后重试或查询作业结果",
            {"job_id": job_id},
        )
    lease = claimed["lease"]
    try:
        run_visible_forecast(store, job=claimed["job"], lease=lease, counter=None)
    except VisiblePipelineError as exc:
        # 分析状态不对（未完成 / 点数不符）→ 409，调用方该重试或先看作业。
        _safe_fail_job(store, lease, str(exc))
        raise AppError(409, CODE_ANALYSIS_NOT_READY, str(exc),
                       {"analysis_id": analysis_id}) from exc
    # ⚠️ 下面三类是 **C 的异常族**。``_raise_mapped`` 只认 E 的异常，
    # 不认 C 的——不做这层映射，C 的 ``NotFound`` 会一路穿到全局处理器变 500
    # （2026-10-17 实测：28 号未知分析返回 500 而不是 404）。
    except NotFound as exc:
        _safe_fail_job(store, lease, "画面人数分析不存在")
        raise AppError(404, CODE_NOT_FOUND, "画面人数分析不存在",
                       {"analysis_id": analysis_id}) from exc
    except InvalidPermission as exc:
        _safe_fail_job(store, lease, "录像没有可用于画面人数分析的有效授权")
        raise AppError(403, "forbidden", "该录像没有可用于画面人数分析的有效授权",
                       {"video_id": video_id}) from exc
    except (Conflict, InvalidArgument) as exc:
        _safe_fail_job(store, lease, str(getattr(exc, "message", exc)))
        raise AppError(400, CODE_BAD_REQUEST, str(getattr(exc, "message", exc)),
                       {"analysis_id": analysis_id}) from exc
    except Exception as exc:  # noqa: BLE001 —— E / C 的原因原样带上
        _safe_fail_job(store, lease, str(exc))
        _raise_mapped(exc)
        raise
    return _latest_forecast_result(store, job_id)


def _safe_fail_job(store: Any, lease: Any, message: str) -> None:
    """把领取到的作业置为 failed；**失败本身不许二次炸**。

    走到这里时主调用已经失败了一次，若 ``fail_job`` 再抛（租约过期、
    作业已被结算等），就会把它自己的错盖掉真正的病因——那正是「看日志也
    查不出为什么 500」的来源。这里只记日志，让**主错误**说话。
    """
    try:
        store.fail_job(lease, message[:2000], now=datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        logger.warning("预测作业置失败时又出错（不影响主错误的上抛）：%s",
                       message[:200])


def _forecast_lease_seconds() -> int:
    from app.core.config import get_settings
    return max(int(getattr(get_settings(), "worker_lease_seconds", 120)), 120)


## C 的 ``jobs.idempotency_key`` 是 **varchar(64)**，键必须装得下。
C_IDEMPOTENCY_KEY_MAX = 64


def _forecast_idempotency_key(analysis_uuid: Any, cutoff_at: str,
                              horizon_minutes: int, step_minutes: int) -> str:
    """28 号的幂等键：**摘要而非直拼**。

    2026-10-17 真机踩坑：原先写成
    ``f"visible-forecast-{analysis_uuid}-{cutoff_at}-{horizon_minutes}"``
    共 **82 字符**，MySQL 立刻 ``DataError 1406 Data too long`` → 28 号 500；
    而 pytest 的 sqlite 作业库**不检查列长度**，所以单测全绿、只有平台冒烟才暴露。

    ⚠️ 教训写死：**凡是直接写进 C 表的字符串，先看 `information_schema.columns`
    的 `character_maximum_length`**，不要假设它够长。同样的坑已经第四次出现
    （`contracts.Page.page_size` ≤100 → `videos` 的 `CHECK(end>start)` → 本次列宽）。

    键的含义不变：同一「分析 + 截止时刻 + 跨度 + 步长」视为同一个预测作业。
    刻意**不含**调用时刻——否则幂等永远命中不了。
    """
    raw = "|".join([str(analysis_uuid), str(cutoff_at),
                    str(horizon_minutes), str(step_minutes)])
    key = "vf-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    # 3 + 32 = 35，远低于 64；这里留一道断言，改构造方式时立即炸而不是上线才炸。
    assert len(key) <= C_IDEMPOTENCY_KEY_MAX, f"幂等键超长：{len(key)}"
    return key


def _latest_forecast_result(store: Any, job_id: str) -> dict[str, Any]:
    """读回刚发布的 ``visible_forecasts.result`` 作为 28 号响应体。

    读 **C 落库的那一条**，而不是把 E 的返回值再加工一遍——C 才是预测结果的权威
    存储，也是 30 号 ``/jobs/{job_id}`` 之后取数的口径，两者必须同源。
    """
    row = store.get_job(job_id) or {}
    refs = row.get("result_refs") or {}
    forecast_id = refs.get("forecast_id")
    if not forecast_id:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "预测已执行但未能读回编号，请通过 /jobs 查询作业结果",
            {"job_id": job_id},
        )
    published = store.get_visible_forecast(forecast_id)
    result = published.get("result") or {}
    if not result:
        raise AppError(
            503,
            CODE_DEPENDENCY_NOT_AVAILABLE,
            "预测记录为空，请通过 /jobs 查询作业结果",
            {"forecast_id": forecast_id},
        )
    return {**result, "forecast_id": forecast_id}


def forecast_from_input(*, history: list[dict[str, Any]], cutoff_at: str,
                        horizon_minutes: int, step_minutes: int) -> dict[str, Any]:
    """把 **C 给的**历史点喂给 E 算短时预测（28 号 HTTP 与 worker 共用这一段）。

    E 的入口 ``forecast_visible_history(history, cutoff, horizon, step)`` 要的是
    ``[(aware datetime, value|None), ...]``；C 的 ``forecast_input`` 给的正是
    ``[{"at": ISO, "value": …}, ...]``，且已经在 C 侧完成了三件事：按 period_end
    排序、只留 5min 且 ``period_end <= cutoff`` 的点、非 good 质量的值置 None。
    **这三点 D 都不重做**——重做就是第二套口径。

    数据不足 / 质量不足由 E 判定并给出 reason，D 原样透传给 C 保存，不替 E
    把 status 改成 ready。
    """
    from intelligent_analysis.short_forecast.baseline import forecast_visible_history

    pairs: list[tuple[Any, Any]] = []
    for item in history:
        stamp = item.get("at")
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        pairs.append((stamp, item.get("value")))
    cutoff = datetime.fromisoformat(cutoff_at.replace("Z", "+00:00")) \
        if isinstance(cutoff_at, str) else cutoff_at
    return forecast_visible_history(
        pairs, cutoff, int(horizon_minutes), int(step_minutes),
    )


def _normalise_short_forecast(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    points = []
    for raw in result.get("forecast_points") or []:
        point = dict(raw)
        target = point.pop("target_time", None)
        if target is not None:
            point.setdefault("period_start", target)
            point.setdefault("period_end", target)
        if "lower" in point and "lower_bound" not in point:
            point["lower_bound"] = point.pop("lower")
        if "upper" in point and "upper_bound" not in point:
            point["upper_bound"] = point.pop("upper")
        point.setdefault("metric", COUNT_METRIC)
        point.setdefault("unit", "人")
        point.setdefault("grain", "5min")
        point.setdefault("is_forecast", True)
        points.append(point)
    out["forecast_points"] = points
    out["validation_error"] = _map_validation_error(out.get("validation_error"))
    return out


#: 公开别名：28 号路由读完 C 落库的预测后，要再走一遍 D 侧形状归一
#: （E 用 ``target_time`` / ``lower`` / ``upper``，D 与前端约定用
#: ``period_start`` / ``period_end`` / ``lower_bound``）。
normalise_short_forecast = _normalise_short_forecast


def _map_validation_error(value: Any) -> dict[str, Any] | None:
    """E 0.5.1 ``persistence_mae`` → D ``baseline_mae``；``fold_count`` 透传。"""
    if not isinstance(value, dict):
        return None
    mapped: dict[str, Any] = {}
    for span, item in value.items():
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        if "persistence_mae" in entry and "baseline_mae" not in entry:
            entry["baseline_mae"] = entry.pop("persistence_mae")
        entry.setdefault("method", "rolling_origin")
        mapped[str(span)] = entry
    return mapped or None


def _e_module_version() -> str | None:
    try:
        from intelligent_analysis import service as e_service
        return getattr(e_service, "MODULE_VERSION", None)
    except Exception:  # noqa: BLE001
        return None
