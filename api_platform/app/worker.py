"""后台作业进程入口（D 是唯一队列消费者，E 0.5.1 + C 0.2.0a2 契约）。

用法：
    python -m app.worker            # 常驻轮询
    python -m app.worker --once     # 只跑一轮（联调 / 验证用）

E 0.5.1 + C 0.2.0a2 链路：
- 队列由 C 的 jobs 表统一管理（``store.claim_job`` / ``complete_job`` /
  ``fail_job`` / ``heartbeat``），D 是唯一消费者；
- E 移除 AnalysisWorker，由 D 领取后调
  ``intelligent_analysis.worker.runner.dispatch_analysis_job`` 拿 E 算的
  ``artifact_bytes / artifact_sha256 / chunks / snapshot``；
- D 落盘到 C ``store.persist_file``（返回 DATA_ROOT 下的**规范相对路径**，
  ``activate_index`` 只接受这种形态）；C ``activate_index`` 校验文件存在性 +
  SHA256 + chunk 数一致 + 租约正确后 ``document.index_status = ready``；
- **``activate_index`` 与 ``jobs.complete_job`` 必须在同一个 C 写事务内提交**
  （E 051 清单 §五 / C 003 执行单）：两段分开提交会出现「索引已 active、
  作业仍 running」的中间态。该事务由 ``CStore.activate_and_complete`` 提供，
  worker 外层不再重复 complete。

knowledge_index 的提交顺序（E 051 清单 §五）：
    ① claim_job 短事务退出 + 事务外 dispatch_analysis_job（心跳续租在长任务中）；
    ② C store.persist_file 落盘 artifact（保留 SHA256 + byte_size）；
    ③ 短事务 begin_index（创建 KnowledgeIndex 行，status=building）；
    ④ write_chunks 按批分批（C 限制 ≤1000/批）；
    ⑤ **同一短事务** activate_index + complete_job；
    ⑥ 调 E publish_knowledge_snapshot 把活动快照写入
       ``settings.rag_index_path``（本地留档；问答走 loader 模式读 C store）。

坏摘要（SHA256 不符）不进入活动版本，作业失败；⑤ 内任一异常向传播出写事务
上下文并整体回滚（不捕获后继续提交）；D 在长任务期间用独立短事务续租。

knowledge_reindex 的展开（22 号重建）：
    E ``reindex_documents`` 只入队一个 ``knowledge_reindex`` 信封，**不生成子作业**；
    D 领取后为每个 document_id 重新入队 ``knowledge_index`` 作业（复用上传时的
    staging 源文件），子作业走上面同一条五步序列。staging 缺失的文档进入
    ``result_refs.skipped``，不静默当作已重建。

降级约定（沿用 D回复E.md §四）：worker 不因单功能缺失退出——
- E 包缺失：仍领取作业，逐个标记失败（error_summary 写明原因）；
- P2PNet 模型包未登记或摘要校验失败（``P2PNET_BUNDLE_DIR``）：
  video_analysis 作业按「模型未配置」失败，不回退密度图/越线口径，不写计数；
- 视频文件未接入（等 C 的接口 24 契约）：video_analysis 作业诚实失败。
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.providers import _try_import, get_job_store
from app.services.e_bindings import configure_e_services

logger = logging.getLogger("api_platform.worker")

# C 限制：单批 write_chunks ≤ 1000（E 051 清单）
CHUNK_BATCH_SIZE = 1000

# C 0.2.1a2《27-契约收口》§二明确：**D Worker 必须按职责筛选领取类型**。
# 队列是 C 的 jobs 表，里面同时躺着 C 自己的 ``ingestion`` 作业（由 C 的
# ``data_foundation.ingestion.run_ingestion_once`` 消费）。D 若不带 job_type
# 领取，会把 ingestion 抢过来然后甩一句「不支持的作业类型」标失败——
# 等于**污染 C 的队列**。因此这里白名单式地只领自己能处理的 5 类，
# 且**绝不使用 job_type=None 的兜底领取**。
SUPPORTED_JOB_TYPES: tuple[str, ...] = (
    "knowledge_index",
    "knowledge_reindex",
    "knowledge_invalidate",
    "video_analysis",
    "visible_forecast",
)


def _next_types(cursor: int) -> list[str]:
    """从 cursor 处轮转一次类型序列（避免前几类饿死后面的）。"""
    total = len(SUPPORTED_JOB_TYPES)
    return [SUPPORTED_JOB_TYPES[(cursor + i) % total] for i in range(total)]


class DWorker:
    """D 自己的队列消费者：领取 → 调 E 的 dispatch_analysis_job → C store 落盘 → activate → complete_job。"""

    def __init__(self, store, counter=None) -> None:
        self.store = store
        self.counter = counter
        # C 的 claim_job 一次只能筛一个 job_type，所以按白名单轮转领取
        self._claim_cursor = 0

    # ---------- 主循环 ----------

    def _claim(self) -> dict | None:
        """按 SUPPORTED_JOB_TYPES 轮转领取一个作业，不碰其它类型。"""
        lease_seconds = self._settings().worker_lease_seconds
        for offset, job_type in enumerate(_next_types(self._claim_cursor)):
            try:
                claimed = self.store.claim_job(
                    worker_id="d-worker", now=datetime.now(UTC),
                    lease_seconds=lease_seconds, job_type=job_type,
                )
            except TypeError:  # 旧 store 不支持 job_type 筛选：宁可不领也不裸领
                logger.error("store.claim_job 不支持 job_type 筛选，拒绝无筛选领取")
                return None
            if claimed is not None:
                self._claim_cursor = (self._claim_cursor + offset + 1) % len(SUPPORTED_JOB_TYPES)
                return claimed
        return None

    def run_once(self) -> dict | None:
        return self.run_claimed(self._claim())

    def run_claimed(self, claimed) -> dict | None:
        """Run a previously leased job through the same publication and error handling path."""
        if claimed is None:
            return None
        job = claimed["job"]
        lease = claimed["lease"]
        job_type = job["type"]
        try:
            if job_type == "knowledge_index":
                result = self._run_knowledge_index(job["payload"], lease)
            elif job_type == "video_analysis":
                result = self._run_video_analysis(job, lease)
            elif job_type == "knowledge_reindex":
                result = self._run_knowledge_reindex(job["payload"], lease)
            elif job_type == "knowledge_invalidate":
                result = self._run_knowledge_invalidate(job, lease)
            elif job_type == "visible_forecast":
                result = self._run_visible_forecast(job, lease)
            else:  # pragma: no cover —— 领取侧已按白名单筛过
                raise RuntimeError(f"不支持的作业类型：{job_type}")
        except Exception as exc:  # noqa: BLE001 —— 单作业失败不拖垮 worker
            logger.exception("作业 %s 失败", getattr(lease, "job_id", lease))
            self.store.fail_job(lease, str(exc), now=datetime.now(UTC))
            if job_type == "video_analysis":
                # C 0.2.1a2 起失败也记在 C 的 visible_analyses 上，否则 27 号会
                # 一直停在 analysis_not_ready 而不知所以然。
                self._fail_visible(job, lease, str(exc))
            return {"status": "failed", "error": str(exc)[:200]}
        try:
            # knowledge_index 已在 _run_knowledge_index 内与 activate_index 同事务完成
            # （E 0.5.1 清单 §五），外层不得再 complete 一次——否则第二次 complete
            # 会对着已 finished 的作业再写一次，掩盖「激活成功但完成失败」的回滚证据。
            if isinstance(result, dict) and result.pop("_job_already_completed", False):
                return {"status": "succeeded"}
            self.store.complete_job(lease, result or {}, now=datetime.now(UTC))
            return {"status": "succeeded"}
        except Exception as exc:
            logger.exception("complete_job 失败 %s", getattr(lease, "job_id", lease))
            try:
                self.store.fail_job(
                    lease, f"complete_job failed: {exc}", now=datetime.now(UTC),
                )
            except Exception:  # noqa: BLE001
                pass
            return {"status": "failed", "error": "complete_job failed"}

    # ---------- knowledge_index（E 051 五步提交顺序） ----------

    def _run_knowledge_index(self, payload: dict, lease) -> dict:
        """E 0.5.1 knowledge_index 严格事务序列（清单 §五）。

        顺序（D 唯一领取者）：

        ``领取并退出短事务``（run_once 已做）→``事务外构建 + C 原生 persist_file
        落盘``→``短事务 begin_index``→``分批短事务 write_chunks``→
        ``同一短事务 activate_index + complete_job``。

        要点：
        - artifact 必须通过 **C 的 ``persist_file``** 落到 C 的 DATA_ROOT，
          ``activate_index`` 只接受**规范相对路径**（绝对路径会被 C 判 400 拒收）；
        - ``parser_version`` / ``embedding_model_version`` 用 E ``prepared`` 的返回值，
          不再由 D 硬编码；
        - 索引版本每次递增（重建不能复用已发布版本）。
        """
        from io import BytesIO

        from intelligent_analysis.worker.runner import dispatch_analysis_job

        document_id = payload["document_id"]
        # 原文来源：**C 登记的原件引用优先**（2026-10-17 按 C《致D和E-021a2接收
        # 核对与补充答复》§6 接）。以前只认 payload 的 ``file_path``，而那最终落
        # 到本机 staging——清理一次就再也重建不了；C 库里的登记才是权威。
        # 取不到（文档未登记、或测试用的 sqlite store 没有该入口）才回退 payload。
        file_path = self._resolve_source_file(document_id)
        if file_path is None:
            file_path = payload.get("file_path")

        # 文档身份以 C 登记为准（20 号接口三段登记已写入 source_id / document_version）。
        # 三级回退顺序：C 库登记值 → 作业 payload 顶层 → payload.metadata。
        # **不再兜底 "v1"**：自造版本号会让 E 的 artifacts._validate_document
        # 加载校验失败（与 C 库登记值不一致）。取不到就按缺失处理，让 E 自己
        # 报 "C source document_version is required" 显式失败，不伪造。
        doc_row = None
        getter = getattr(self.store, "get_document", None)
        if getter is not None:
            doc_row = getter(document_id)
        document_version = (
            (doc_row or {}).get("document_version")
            or payload.get("document_version")
            or (payload.get("metadata") or {}).get("document_version")
        )
        if not document_version:
            raise ValueError(
                f"文档 {document_id} 缺少 C 口径 document_version，拒绝构建索引"
            )
        index_version = self._resolve_index_version(document_id)

        # ① 事务外 long-running：E 解析 + 构建（失败直接抛 → fail_job）
        self.store.heartbeat(lease, now=datetime.now(UTC))
        from app.services.knowledge_input import prepared_source
        with prepared_source(file_path, self._settings().data_dir) as input_path:
            prepared = dispatch_analysis_job("knowledge_index", {
                "document_id": document_id,
                "document_version": document_version,
                "version": index_version,
                "file_path": input_path,
                "metadata": payload.get("metadata") or (doc_row or {}),
            })

        artifact_bytes = prepared["artifact_bytes"]
        declared_sha = prepared["artifact_sha256"]
        actual_sha = _sha256(artifact_bytes)
        if actual_sha != declared_sha:
            raise RuntimeError("知识产物 SHA256 校验失败，坏摘要不进入活动版本")

        # ② 事务外落盘：优先走 C 原生 persist_file（返回 DATA_ROOT 下的规范相对路径），
        #    拿不到 C store（sqlite 桥接/测试）时才退回本地绝对路径。
        artifact_path, artifact_sha, artifact_size = self._stage_artifact(
            artifact_bytes, actual_sha, document_id, index_version, doc_row,
        )

        # ③ 短事务 begin_index（KnowledgeIndex status=building）
        index = self.store.begin_index(
            document_id=document_id,
            version=index_version,
            job_id=getattr(lease, "job_id", ""),
            parser_version=prepared["parser_version"],
            embedding_model_version=prepared["embedding_model_version"],
            lease=lease,
        )

        # ④ write_chunks 按批分批（C 限制 ≤1000/批），每批前续租
        chunks = prepared["chunks"]
        total_inserted = 0
        for batch_start in range(0, len(chunks), CHUNK_BATCH_SIZE):
            self.store.heartbeat(lease, now=datetime.now(UTC))
            batch = chunks[batch_start:batch_start + CHUNK_BATCH_SIZE]
            res = self.store.write_chunks(index["id"], batch, lease=lease)
            total_inserted += res.get("inserted", 0)

        result_refs = {
            "index_id": index["id"],
            "index_version": index_version,
            "chunk_count": len(chunks),
            "artifact_sha256": actual_sha,
        }

        # ⑤ 同一短事务：activate_index + complete_job（文件已先于激活存在）
        completed_inside = False
        combined = getattr(self.store, "activate_and_complete", None)
        if combined is not None:
            activated = combined(
                document_id=document_id,
                version=index_version,
                artifact_path=artifact_path,
                artifact_sha256=artifact_sha,
                expected_chunk_count=len(chunks),
                expected_artifact_byte_size=artifact_size,
                lease=lease,
                result_refs=result_refs,
                now=datetime.now(UTC),
            )["index"]
            completed_inside = True
        else:
            # sqlite 桥接后端没有同事务入口：两段分别提交（仅测试/兜底路径）
            activated = self.store.activate_index(
                document_id=document_id,
                version=index_version,
                artifact_path=artifact_path,
                artifact_sha256=artifact_sha,
                expected_chunk_count=len(chunks),
                expected_artifact_byte_size=artifact_size,
                lease=lease,
            )

        # ⑥ 同步全量活动快照到 settings.rag_index_path（E 路径模式 / 本地留档）
        try:
            self._publish_active_snapshot()
        except Exception as exc:
            # 快照持久化失败不能让 activate 阶段回滚（已提交的知识索引独立存在）；
            # 仅记录错误，让 19 问答 loader 模式兜底（从 C store 直接读快照）。
            logger.warning("发布全量活动快照失败：%s", exc)

        result = {
            "document_index_status": activated.get("index_status", "ready"),
            "chunk_count": activated.get("chunk_count", len(chunks)),
            "artifact_sha256": actual_sha,
            "document_id": document_id,
            "index_version": index_version,
        }
        if completed_inside:
            result["_job_already_completed"] = True
        return result

    def _resolve_index_version(self, document_id: str) -> str:
        """本次要发布的索引版本：C 上已发布版本 +1；无已发布版本则 v1。

        重建（22）必须换新版本——C 的 ``begin_index`` 对 ready 索引直接 Conflict。
        """
        import re

        active = None
        getter = getattr(self.store, "get_document", None)
        if getter is not None:
            try:
                active = (getter(document_id) or {}).get("active_index_version")
            except Exception:  # noqa: BLE001 —— 文档不存在时交由后续步骤报错
                active = None
        if active:
            matched = re.match(r"^v(\d+)$", str(active))
            return f"v{int(matched.group(1)) + 1}" if matched else f"{active}-r1"
        fallback = getattr(self.store, "next_index_version", None)
        if fallback is not None:
            try:
                return f"v{int(fallback(document_id))}"
            except Exception:  # noqa: BLE001
                return "v1"
        return "v1"

    def _stage_artifact(self, artifact_bytes: bytes, actual_sha: str,
                        document_id: str, index_version: str,
                        doc_row: dict | None) -> tuple[str, str, int]:
        """落盘知识产物，返回 ``(artifact_path, artifact_sha256, byte_size)``。

        - C 路径（有 ``persist_file`` 且有 source_id）：走 C 原生落盘，
          ``artifact_path`` 是 C DATA_ROOT 下的**规范相对路径**——C 的
          ``verify_artifact`` 只接受这种形态；
        - sqlite 桥接路径：写到 ``data_dir/knowledge_artifacts/``，用绝对路径。
        """
        persist = getattr(self.store, "persist_file", None)
        source_id = (doc_row or {}).get("source_id")
        if persist is not None and source_id:
            from io import BytesIO

            staged = persist(
                source_id=source_id,
                original_name=f"index-{document_id}-{index_version}.json",
                stream=BytesIO(artifact_bytes),
            )
            return staged["relative_path"], staged["sha256"], staged["byte_size"]

        settings = self._settings()
        artifacts_dir = Path(settings.data_dir) / "knowledge_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        local = artifacts_dir / f"{actual_sha[:16]}.json"
        local.write_bytes(artifact_bytes)
        return str(local), actual_sha, len(artifact_bytes)

    def _publish_active_snapshot(self) -> None:
        """合并已提交活动文档 → 写 E 期望的路径快照。

        SqliteJobStore 等 D 桥接后端无 ``snapshot_for_e_publisher`` 方法——
        仅记录警告，不阻塞作业成功（测试场景由 loader 模式兜底）。
        """
        from intelligent_analysis.worker.knowledge_worker import publish_knowledge_snapshot

        if not hasattr(self.store, "snapshot_for_e_publisher"):
            logger.info("store 缺 snapshot_for_e_publisher，跳过发布快照（loader 模式兜底）")
            return
        snapshot = self.store.snapshot_for_e_publisher()
        if not snapshot.get("documents"):
            return
        publish_knowledge_snapshot(snapshot, self._settings().rag_index_path)
        logger.info("知识快照已发布：%d 个文档 → %s",
                    len(snapshot["documents"]), self._settings().rag_index_path)

    def _fail_visible(self, job: dict, lease, error_summary: str) -> None:
        """把 visible 分析标记失败（C 侧）；失败了也不能让 27 号永远停在
        analysis_not_ready 却不给原因。"""
        analysis_id = (job.get("payload") or {}).get("analysis_id")
        failer = getattr(self.store, "fail_visible_analysis", None)
        if not analysis_id or failer is None:
            return
        try:
            failer(analysis_id, error_summary[:2000], lease)
        except Exception:  # noqa: BLE001 —— C 可能已经把这个 lease 判失效
            logger.warning("fail_visible_analysis 未生效（analysis_id=%s）", analysis_id)

    # ---------- video_analysis ----------

    def _run_video_analysis(self, job: dict, lease) -> dict:
        """消费 ``video_analysis`` 作业：**五步序列**全在 C 的 visible 仓储里。

        ``get_visible_input`` → ``begin_analysis`` → ``write_points``（分批 ≤1000）
        → ``complete_analysis``。**最后一步内部已经 complete 了 C 的 job**，所以
        这里返回的结果里带 ``_job_already_completed``，让主循环别再 complete 一次
        （第二次 complete 会对着已结束的作业再写，掩盖回滚证据）。

        真正的帧处理仍然交给 E（``dispatch_analysis_job``），D 不自己解码视频、
        不算人数、也不重算 5 分钟窗口的中位数。
        """
        from app.services.visible_pipeline import run_visible_analysis

        result = run_visible_analysis(
            self.store, job=job, lease=lease, counter=self.counter,
        )
        result["_job_already_completed"] = True
        return result

    # ---------- knowledge_reindex（D 管理操作） ----------

    def _run_knowledge_reindex(self, payload: dict, lease) -> dict:
        """22 重建索引：把 E 入队的 ``knowledge_reindex`` 信封**逐文档展开**。

        E 0.5.1 ``reindex_documents`` 只做 ``submit("knowledge_reindex", {...})``
        （见 ``intelligent_analysis/service.py``），自己**不会**生成子作业；D 是队列
        唯一消费者，必须在领取信封后为每个 document_id 重新入队一个
        ``knowledge_index`` 作业。子作业走与首次上传**完全相同**的五步事务序列
        （见 ``_run_knowledge_index``），因此新版本构建期间旧活动索引继续服务。

        输入源用上传时留下的 staging 原文件（``data_dir/knowledge_staging``，
        sandbox 场景回退 ``%TEMP%/dpart_knowledge_staging``）；找不到源文件时
        该文档记入 ``skipped`` 而不是静默当作已重建——重建结果必须可核对。
        """
        document_ids = [str(d) for d in (payload.get("document_ids") or []) if d]
        enqueued: list[str] = []
        skipped: list[dict] = []
        for document_id in document_ids:
            # C 登记优先，staging 兜底（见 _resolve_source_file 的说明）
            staging = self._resolve_source_file(document_id)
            if staging is None:
                skipped.append({"document_id": document_id,
                                "reason": "找不到 C 登记原件，也没有 staging 源文件，无法重建"})
                logger.warning("重建索引跳过 %s：staging 源文件缺失", document_id)
                continue
            doc_row = self._get_document_row(document_id)
            document_version = doc_row.get("document_version")
            if not document_version:
                # 不兜底 "v1"：版本号必须来自 C 登记（sha256:<hex> 口径），
                # 否则重建产物过不了 E 的加载校验。此处显式跳过并留痕。
                skipped.append({"document_id": document_id,
                                "reason": "C 登记缺少 document_version，无法重建"})
                logger.warning("重建索引跳过 %s：C 登记缺少 document_version", document_id)
                continue
            try:
                job_id = self.store.create_job(
                    "knowledge_index",
                    {
                        "document_id": document_id,
                        "document_version": document_version,
                        "file_path": str(staging),
                        "metadata": _jsonable_metadata(doc_row),
                        "analysis_id": document_id,
                        "reindex_reason": payload.get("reason"),
                    },
                    analysis_id=document_id,
                )
            except Exception as exc:  # noqa: BLE001 —— 单个文档失败不影响其余
                skipped.append({"document_id": document_id,
                                "reason": f"入队失败：{exc}"})
                logger.exception("重建索引入队失败：%s", document_id)
                continue
            enqueued.append(job_id)
        return {
            "reindexed_documents": len(enqueued),
            "job_ids": enqueued,
            "skipped": skipped,
            "note": "已逐文档入队 knowledge_index 子作业，由队列逐个执行",
        }

    # ---------- knowledge_invalidate / visible_forecast（C 0.2.1a2 新增类型） ----------

    def _run_knowledge_invalidate(self, job: dict, lease) -> dict:
        """按文档失效索引（C 0.2.1a2 ``knowledge.invalidate_document``）。

        三个身份必须对齐，否则 C 会抛 ``LeaseLost``：
        - lease 对应的 job 类型是 ``knowledge_invalidate``；
        - job 的 ``requested_by`` 等于 payload 里的 ``operator_id``（不能由 worker
          自己冒充操作者）；
        - job payload 里带同一个 ``document_id``。

        失效**只**把 ``index_status`` 置 failed，**保留最后一版产物与原件**；
        想让它重新可用，走 22 号重建。**内部已 complete_job**，外层不得再碰。
        """
        payload = job.get("payload") or {}
        document_id = payload.get("document_id")
        operator_id = payload.get("operator_id") or job.get("requested_by")
        reason = payload.get("reason") or "文档失效：索引产物不可用"
        if not document_id:
            raise RuntimeError("knowledge_invalidate 作业缺少 payload.document_id")
        if not operator_id:
            raise RuntimeError(
                "knowledge_invalidate 缺少操作者：入队时必须带 requested_by / operator_id，"
                "worker 不允许自己冒充操作者"
            )
        invalider = getattr(self.store, "invalidate_document", None)
        if invalider is None:
            raise RuntimeError(
                "当前 job store 不支持 knowledge.invalidate_document（需要 C 0.2.1a2 的 CStore）"
            )
        result = invalider(
            document_id=document_id, reason=reason, operator_id=operator_id, lease=lease,
        )
        result["_job_already_completed"] = True
        return result

    def _run_visible_forecast(self, job: dict, lease) -> dict:
        """消费 ``visible_forecast`` 作业：C 给历史 → E 算 → C 保存。

        ``publish_forecast`` 内部已 ``complete_job``，同样要告诉主循环别再 complete。
        """
        from app.services.visible_pipeline import run_visible_forecast

        result = run_visible_forecast(self.store, job=job, lease=lease, counter=self.counter)
        result["_job_already_completed"] = True
        return result

    def _resolve_source_file(self, document_id: str) -> Path | None:
        """重建/索引的原文：**C 登记优先**，本机 staging 兜底。

        C 的 ``get_document_file_ref`` 给的是 C DATA_ROOT 下的绝对路径 + 原件
        SHA256 + document_version，这才是「这个文档当时登记的那份原件」。
        本机 staging 只是上传过程中的临时痕迹，**清理一次就丢了**——过去重建
        完全依赖它，是 C《致D和E-021a2接收核对与补充答复》§6 点名要改的。

        两者都不存在时返回 ``None``（调用方据此决定回退 payload 还是报错）。
        """
        ref_getter = getattr(self.store, "get_document_file_ref", None)
        if ref_getter is not None:
            try:
                ref = ref_getter(document_id)
            except Exception as exc:  # noqa: BLE001 —— 存储异常不当真机炸
                logger.warning("取 C 原件引用失败 %s：%s", document_id, exc)
                ref = None
            candidate = (ref or {}).get("file_path")
            if candidate:
                path = Path(candidate)
                if path.is_file():
                    logger.info("索引原文取自 C 登记 %s → %s", document_id, path)
                    return path
                logger.warning("C 登记的原件文件不存在 %s → %s", document_id, candidate)
        return self._locate_staging_file(document_id)

    def _locate_staging_file(self, document_id: str) -> Path | None:
        """定位上传时留下的 staging 原文件（**仅作兜底**，权威来源是 C 登记）。

        与 ``e_bridge.stage_and_accept_document`` 的落盘约定一致：优先
        ``settings.data_dir/knowledge_staging/{document_id}.*``，其次
        ``%TEMP%/dpart_knowledge_staging/{document_id}.*``。
        """
        import tempfile

        candidates: list[Path] = []
        base = Path(self._settings().data_dir) / "knowledge_staging"
        if base.exists():
            candidates.extend(sorted(base.glob(f"{document_id}.*")))
        tmp = Path(tempfile.gettempdir()) / "dpart_knowledge_staging"
        if tmp.exists():
            candidates.extend(sorted(tmp.glob(f"{document_id}.*")))
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _get_document_row(self, document_id: str) -> dict:
        """读 C 登记的文档行（身份 + 元数据）；后端不支持时返回空 dict。"""
        getter = getattr(self.store, "get_document", None)
        if getter is None:
            return {}
        try:
            return getter(document_id) or {}
        except Exception:  # noqa: BLE001 —— 交由后续步骤报错
            return {}

    @staticmethod
    def _settings():
        # 函数内 import：模块顶部 from-import 会让测试的 monkeypatch 失效，
        # 导致运行时目录漂移到默认 ./data（用户文件卫生红线）。
        from app.core.config import get_settings
        return get_settings()


class DegradedWorker:
    """E 包缺失时的降级 worker：领取的作业直接标记失败，不静默空转。"""

    def __init__(self, store) -> None:
        self.store = store

    def run_once(self) -> dict | None:
        # 降级模式同样按类型白名单领取：连作业都领不对，E 缺包时更没有理由
        # 去动 C 的 ingestion 队列。
        for job_type in SUPPORTED_JOB_TYPES:
            try:
                claimed = self.store.claim_job(
                    worker_id="d-degraded-worker", now=datetime.now(UTC), job_type=job_type,
                )
            except TypeError:
                logger.error("store.claim_job 不支持 job_type 筛选，拒绝无筛选领取")
                return None
            if claimed is None:
                continue
            lease = claimed["lease"]
            self.store.fail_job(
                lease,
                "智能分析依赖未接入：需要 E 的 intelligent_analysis 包（pip install -e 后重启 worker）",
                now=datetime.now(UTC),
            )
            logger.error("作业 %s 因 E 包缺失标记失败", getattr(lease, "job_id", lease))
            return {"status": "failed"}
        return None


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _jsonable_metadata(row: Any) -> dict:
    """把 C 文档行转成可入 JSON 列的 dict（datetime → ISO 字符串）。

    C 的 ``jobs.payload`` 是 JSON 列：``datetime`` 直接入队会抛
    ``Object of type datetime is not JSON serializable``。E 的
    ``DocumentMetadata`` 接受 ISO 字符串（首次上传路径就是这么传的），
    因此这里统一转 ISO 即可，无需区分字段。
    """
    out: dict = {}
    for key, value in (row or {}).items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _get_p2p_counter(settings):
    """构造 P2PNetCounter（受信任模型包可用时）。不可用返回 None。

    E 0.6.0 起计数模型只有 P2PNet；``from_team_bundle`` 只记录「目录 +
    登记摘要」，真正校验/加载发生在第一次 ``validate_config`` / ``__call__``。
    因此这里先做一次便宜的存在性检查（目录里有 ``SHTechA.pth``），再把
    构造异常吞成 None —— 由 ``_run_video_analysis`` 报「模型未登记」的诚实失败。
    """
    try:
        from intelligent_analysis.vision.p2p_counter import P2PNetCounter
    except Exception:
        return None
    bundle = settings.p2pnet_bundle_dir
    if not bundle or not (Path(bundle) / "SHTechA.pth").is_file():
        return None
    try:
        return P2PNetCounter.from_team_bundle(bundle)
    except Exception:
        logger.info("P2PNet 模型包不可用：%s", bundle)
        return None


# 公共别名：测试可 monkeypatch ``app.worker.get_p2p_counter`` 注入假实现
get_p2p_counter = _get_p2p_counter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="api_platform 后台作业进程")
    parser.add_argument("--once", action="store_true", help="只处理一轮作业后退出")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    from app.core.config import get_settings

    settings = get_settings()
    store = get_job_store()

    if _try_import("intelligent_analysis") is not None:
        configure_e_services()
        worker = DWorker(store, _get_p2p_counter(settings))
        logger.info("E 0.6.0（P2PNet）已接入，D worker（队列唯一消费者）启动")
    else:
        worker = DegradedWorker(store)
        logger.warning("E 包未接入，worker 以降级模式运行（E 类作业将标记失败）")

    interval = settings.worker_poll_interval_seconds
    try:
        while True:
            worker.run_once()
            if args.once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("收到退出信号，worker 停止")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
