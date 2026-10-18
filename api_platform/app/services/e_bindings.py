"""E ``ServiceBindings`` 协议适配层（**0.6.0 口径**）。

E 0.6.0 真实接口（基于 ``intelligent_analysis.integration.ServiceBindings``）：

- ``submit_job(kind: str, payload: dict) -> dict`` — 经 ``intelligent_analysis.submit``
  入队（E 强制键校验：knowledge_index 要求 ``document_id+document_version``）。
- ``load_counts(metric: str, *, bucket, entity_ids=None)`` — 直接读 SQLite 桥
  内部计数（E 不再做 D-Bridge 的窗口过滤）；D 这里只暴露一个影子返回
  ``{"series": []}``，真实拉数经由 C stores 在 worker 完成后写入。
- ``validate_analysis(config: dict) -> None`` — **P2PNet** 校验（0.6.0 起
  ``count_model`` 只接受 ``p2pnet``，``inference_engine`` 只接受 pytorch，
  ``device`` 只接受 CPU，阈值固定 0.5）。
- ``knowledge_index_path: str | None`` 与 ``knowledge_snapshot_loader`` 二选一；
  ``configure_services(bindings)`` 强制互斥校验，否则抛 ``ValueError``。

D 改造要点（依 E 060 执行单）：
- **优先 loader 模式**（``knowledge_snapshot_loader`` → ``CStore.snapshot_for_e_publisher``），
  仅在 C store 不可用（sqlite 桥接/测试）时退回 ``knowledge_index_path``；E 强制两者互斥；
- ``configure_e_services()`` 幂等，重复调用安全；
- main.py lifespan 内注入一次；get_e_service()/configure_e_services() 都是
  顶层可调用，方便测试 monkeypatch；
- **计数模型**：``get_p2p_counter()`` 从服务端配置的 ``P2PNET_BUNDLE_DIR``
  构造 ``P2PNetCounter.from_team_bundle(...)``；DM-Count 通道已整体移除。
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from app.core.config import get_settings

logger = logging.getLogger("api_platform.e_bindings")

_e_configured = False


def _import_e():
    """惰性导入 E 包；缺包时全部 shortcut 安全失败。

    不在模块顶层 import，是因为：
    ① 测试 conftest 在 fixtures 里 monkeypatch config / e_bindings，
       顶层 import 会让 config 调用提前；
    ② E 包偶尔依赖 torch / numpy，未装环境希望 D 仍可起（短答 503）。
    """
    return importlib.import_module("intelligent_analysis")


def submit_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """D 注入给 E ``integration.submit`` 的回调——直接入队，禁止再调回 E。

    E 0.5.1 ``intelligent_analysis.integration.submit`` 校验本函数返回
    ``result["status"] == "queued" and result["job_id"]``；E ``JobAccepted``
    （在 analyze_video 路径构造）还要求 ``result["analysis_id"]``。

    入参约定：
    - ``payload["document_id"]`` → knowledge_index 作业的 ``analysis_id`` 入库；
    - ``payload["analysis_id"]`` → video_analysis 作业的 ``analysis_id`` 入库；
      空串/缺失则 D 自动生成 UUID（E 0.5.1 JobAccepted 必填）。
    """
    from uuid import uuid4 as _uuid4

    from app.core.providers import get_job_store

    payload_kind = kind
    raw_analysis = payload.get("analysis_id") or payload.get("document_id")
    analysis_id = raw_analysis if (raw_analysis and str(raw_analysis).strip()) else str(_uuid4())

    store = get_job_store()
    enriched_payload = dict(payload)
    enriched_payload["analysis_id"] = analysis_id
    # analysis_id 同时作为显式参数传给桥接后端：SqliteJobStore 用它做 21 号
    # find_document / latest_knowledge_payload 的查询键；CStore 的 jobs 表没有
    # 该列（身份已随 payload 落库），接受但忽略。
    job_id = store.create_job(
        payload_kind,
        enriched_payload,
        analysis_id=analysis_id,
    )
    row = store.get_job(job_id) or {"id": job_id}
    return {
        "status": "queued",
        "job_id": job_id,
        "analysis_id": analysis_id,
        **row,
    }


def load_counts(video_id: str, analysis_id: str) -> dict[str, Any]:
    """D 注入给 E 的 ``load_counts``——从 ``job_store.load_analysis_counts`` 读
    CountsResponse 形状 dict（含 ``recorded_start_at/recorded_end_at/roi/analysis``，
    E 0.5.1 ``CountsResponse`` 强校验）。未完成时返回 ``{"status": "analysis_not_ready",
    "points": [...]}``，由 E service 进一步映射到 409；归属校验失败返回 ``None`` →
    E 抛 ``DependencyUnavailable`` → D 转 404。
    """
    from app.core.providers import get_job_store

    store = get_job_store()
    loader = getattr(store, "load_analysis_counts", None)
    if loader is None:
        return {"status": "analysis_not_ready", "video_id": video_id,
                "analysis_id": analysis_id, "points": []}
    return loader(video_id=video_id, analysis_id=analysis_id)


def validate_analysis(config: dict[str, Any]) -> None:
    """绑定的 ``validate_analysis`` — 存在 P2PNet 计数器时调其 ``validate_config``。

    E 0.6.0 ``P2PNetCounter.validate_config`` 必须（全部经
    ``VideoAnalysisConfig.model_validate`` 解析后逐项判定）：
    - ``inference_engine == "pytorch"`` 且 ``device == "CPU"``（GPU/OpenVINO 未验证）；
    - ``weights_version`` 等于登记的 ``leader-SHTechA-50604773``；
    - ``count_model == "p2pnet"``（由 ``VideoAnalysisConfig`` 的字面量约束兜底）；
    - 权重文件与 7 个源码文件的 SHA256 全部命中，并能真正 ``_load()`` 成功。

    任一不满足即抛 ``UnsupportedAnalysis`` → D 路由层映射 422，**诚实失败**。
    """
    counter = get_p2p_counter()
    if counter is None:
        raise e_errors().UnsupportedAnalysis(
            "P2PNet 模型包未登记，请配置 P2PNET_BUNDLE_DIR 指向受信任目录"
        )
    counter.validate_config(config)


def get_p2p_counter():
    """构造 ``P2PNetCounter``（受信任模型包可用时）；否则返 None。

    E 0.6.0 起计数模型只有 P2PNet，不再有 DM-Count 分支；旧的
    ``get_dm_counter`` 已删除——任何残留调用都会立刻 ``AttributeError``，
    避免某个路径静默把 DM 密度图喂给点计数处理器。

    「可用」的判定 = 目录存在 **且** ``SHTechA.pth`` 在里面。E 的
    ``from_team_bundle`` 只记录路径不读盘（校验推迟到第一次 validate），
    所以这里先做一次**便宜的存在性检查**——否则被可写探针建出来的空目录
    会被误判为「已登记」，26 号就会假通过到 E 那一步才 422。
    真正的 SHA256 全量校验仍由 E 的 ``validate_config`` / ``_load`` 执行。

    测试可通过 ``monkeypatch.setattr("app.services.e_bindings.get_p2p_counter", ...)``
    注入假实现。
    """
    from pathlib import Path

    from app.core.config import get_settings

    settings = get_settings()
    try:
        from intelligent_analysis.vision.p2p_counter import P2PNetCounter
    except Exception:
        return None
    bundle = settings.p2pnet_bundle_dir
    if not bundle:
        return None
    weights = Path(bundle) / "SHTechA.pth"
    if not weights.is_file():
        logger.info("P2PNet 模型包未登记：%s 下缺 SHTechA.pth", bundle)
        return None
    try:
        return P2PNetCounter.from_team_bundle(bundle)
    except Exception as exc:
        logger.info("P2PNet 模型包不可用（%s）：%s", bundle, exc)
        return None


def get_p2p_counter_provenance() -> dict[str, Any]:
    """取计数器溯源字典（含 count_model / 权重版本 / 摘要 / 源修订 / 阈值 / 运行时）。

    拿不到计数器时返回空 dict，调用方按「无溯源可写」处理（不伪造）。
    """
    counter = get_p2p_counter()
    if counter is None:
        return {}
    try:
        return dict(counter.provenance)
    except Exception:
        return {}


def knowledge_index_path() -> str | None:
    """路径加载器（默认）：若 settings.rag_index_path 配了就用，否则返回 None。"""
    return get_settings().rag_index_path


def knowledge_snapshot_loader() -> Callable[[], Any] | None:
    """快照加载器：D 把它注入 E 的 ``ServiceBindings.knowledge_snapshot_loader``。

    E ``service.answer_message`` 无参调用它，拿到的 dict 交给
    ``InMemoryKnowledgeIndex.from_payload()``。

    **主路径走 E 的 ``load_verified_knowledge_snapshot``**（E 0.5.1 起提供，
    ``knowledge/artifacts.py``）。它会逐项做语义对账，而不是只校验文件摘要：

    - schema_version / parser_version / embedding_model_version；
    - 文档身份（产物里的 documents 键集合必须 == 请求的 document_id）；
    - **index_version**、**document_version** 与 C 库登记值一致；
    - metadata 逐字段比对（``applicability_note`` 除外，它属 manifest 溯源，不是 C 列）；
    - **chunk 逐条比对**：order / chunk_id / ``locator.order`` / **text** 必须与
      C 库的 KnowledgeChunk 行一致。

    这正是 C §六 场景⑧（「摘要正确但 JSON 文本或 locator 与数据库不同 →
    E 拒绝加载」）要的校验。**D 之前只喂自己拼的
    ``snapshot_for_e_publisher()``，E 的 ``from_payload`` 只校验 chunk 的文档
    归属，拦不住「产物被改写但摘要同步改对」——2026-10-15 取证实测该项返回 200
    而非 503，就是因为这个。**

    退回宽松版的**唯一条件**是 E 没有 artifacts 模块（< 0.5.1）。校验失败
    **绝不退回**——那等于绕开 C §六 的 all-or-nothing 口径，用脏数据继续回答。

    **只有 C store 可用时才返回 loader**（否则返回 None，让调用方退回
    ``knowledge_index_path`` 路径模式）：loader 内部要读 C 的 knowledge 仓储，
    sqlite 桥接后端（测试 / 快速回环）没有 C 库可读，塞给 E 只会在问答时炸成 500。
    """
    from app.core.providers import get_c_store

    if get_c_store() is None:
        return None

    def _loader():
        cstore = get_c_store()
        try:
            from intelligent_analysis.knowledge.artifacts import (
                load_verified_knowledge_snapshot,
            )
        except ImportError as exc:
            # C 021a2 收口明确：**失败不能以宽松 snapshot 继续报成功**。
            # 2026-10-17 之前这里会退回 ``snapshot_for_e_publisher()``——那等于
            # 绕开 C §六 的 all-or-nothing 口径，在 E 缺严格校验器时拿未经逐条
            # 比对的数据继续回答。现在如实报依赖不可用。
            raise e_errors().DependencyUnavailable(
                "E 未提供严格知识校验器（intelligent_analysis.knowledge.artifacts），"
                "拒绝退回宽松快照"
            ) from exc

        # 授权边界由 D 定：只把 C 侧活动索引已就绪的文档交给 E。
        # load_verified_knowledge_snapshot 内部已把 DataFoundationError /
        # OSError / ValueError / KeyError / TypeError 统一转成
        # DependencyUnavailable（「全部或无」，任一文档不通过即整体失败），
        # D 不再额外捕获——捕获就等于放行脏数据。
        return load_verified_knowledge_snapshot(
            cstore, cstore.ready_document_ids(), cstore.data_root,
        )

    return _loader


def configure_e_services() -> bool:
    """main.py lifespan 调一次；幂等。

    行为：
    ① 若 E 包导入失败 → 跳过，调用方按 503 处理（短答）；
    ② 注入 ``ServiceBindings`` 四项（**不能用互斥的 path+loader**）：
       **优先 loader**（E 清单 §五「配置正式 knowledge_snapshot_loader，
       移除互斥的 knowledge_index_path」），只有 loader 拿不到时才退化到 path；
    ③ 同一进程第二次调直接返回。
    """
    global _e_configured
    if _e_configured:
        return True
    try:
        e = _import_e()
    except Exception as exc:
        logger.warning("intelligent_analysis 未安装或导入失败：%s", exc)
        return False

    bindings_cls = getattr(e, "integration", None) and getattr(e.integration, "ServiceBindings", None)
    if bindings_cls is None:
        logger.warning("intelligent_analysis.integration.ServiceBindings 不可用")
        return False

    loader = knowledge_snapshot_loader()
    path = knowledge_index_path()

    kwargs: dict[str, Any] = {
        "submit_job": submit_job,
        "load_counts": load_counts,
        "validate_analysis": validate_analysis,
    }
    if loader is not None:
        kwargs["knowledge_snapshot_loader"] = loader
        mode = "loader"
    else:
        kwargs["knowledge_index_path"] = path or ""
        mode = "path"

    try:
        e.configure_services(bindings_cls(**kwargs))
        _e_configured = True
        logger.info(
            "E ServiceBindings 已注入（E 0.6.0 口径，mode=%s，loader=%s，path=%s）",
            mode, bool(loader), bool(path),
        )
        return True
    except Exception as exc:
        logger.warning("E ServiceBindings 配置失败：%s", exc)
        return False


def reset_for_tests() -> None:
    """测试 helper：把 _e_configured 重置回 False，强制下次重新注入。"""
    global _e_configured
    _e_configured = False


def e_errors():
    """惰性拿 E 的异常类（E 0.5.1：异常类在 ``intelligent_analysis.integration``）。
    E 完全不可用时 fallback 到本地 Stub。
    """
    try:
        from intelligent_analysis.integration import (
            DependencyUnavailable as _DU,
            UnsupportedAnalysis as _UA,
        )
        class _E:
            DependencyUnavailable = _DU
            UnsupportedAnalysis = _UA
        return _E()
    except Exception:
        import traceback
        traceback.print_exc()
        class _Stub:
            class DependencyUnavailable(Exception):
                pass

            class UnsupportedAnalysis(Exception):
                pass
        return _Stub()
