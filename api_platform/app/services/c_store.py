"""C 0.2.0a2 ``DataStore`` 的 D 视角包装。

D 0.5.1 版的 job_store 入口默认走 ``CStore``，让 worker / e_bindings / 21 号
接口的内部调用形态与升级前一致。测试可通过 ``D_JOB_STORE_BACKEND=sqlite``
退回 ``SqliteJobStore``。

``CStore`` 的语义：

- ``write()`` / ``read()`` 透传 C 的 ``UnitOfWork``；
- 任务操作（``create_job`` / ``claim_job`` / ``complete_job`` / ``fail_job`` /
  ``heartbeat``）直接调 C 的 jobs 仓储，返回与之前同形状的 dict；
- ``register_source / register_asset / register_document`` 调 C provenance /
  knowledge registration（dict 入参，内部走 ORM session.flush）；
- ``persist_file`` / ``doctor`` / ``data_root`` 透传；
- ``begin_index`` / ``write_chunks`` / ``activate_index`` / ``get_index_artifact_ref``
  / ``get_chunks`` / ``list_documents`` 透传 C knowledge。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterator, BinaryIO

from .job_store import Lease

logger = logging.getLogger("api_platform.c_store")


def _aware_iso(value: Any) -> str | None:
    """ISO 字符串 → **带时区** 的 ISO 字符串（E 0.6.0 强制 tz-aware）。

    与 ``job_store._aware_iso`` 同语义：naive 一律按 UTC 补 ``+00:00``。
    无法解析 → 原样返回字符串，交给 E 报错（不静默吞掉）。
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class CStore:
    """C ``DataStore`` 的 D 视角薄包装。

    所有方法均把存储异常原样向上抛，由调用方按 D 统一错误格式映射为 HTTP。
    """

    def __init__(self, store) -> None:  # store: data_foundation.DataStore
        self._store = store

    # --------- C 原生：事务上下文 ---------

    @contextmanager
    def write(self) -> Iterator[Any]:
        with self._store.write() as uow:
            yield uow

    @contextmanager
    def read(self) -> Iterator[Any]:
        with self._store.read() as uow:
            yield uow

    # --------- C 原生：文件与登记 ---------

    def persist_file(self, *, source_id: str, original_name: str, stream: BinaryIO) -> dict[str, Any]:
        return self._store.persist_file(
            source_id=source_id, original_name=original_name, stream=stream,
        )

    def _video_file_store(self):
        """Same database, dedicated video root; knowledge originals retain their own root."""
        from app.core.config import get_settings
        from data_foundation import create_store
        from pathlib import Path
        root=Path(get_settings().video_storage_dir).resolve()
        if root==Path(self.data_root).resolve(): return self._store
        if not hasattr(self,'_video_store') or self._video_store.data_root!=root:
            self._video_store=create_store(self._store.engine.url.render_as_string(hide_password=False),
                data_root=root,allow_sqlite_for_tests=self._store.engine.dialect.name=='sqlite')
        return self._video_store

    def persist_video_file(self, *, source_id: str, original_name: str, stream: BinaryIO) -> dict[str, Any]:
        return self._video_file_store().persist_file(source_id=source_id,original_name=original_name,stream=stream)

    def list_playback_videos(self, *, scenic_id=None,camera_id=None,status=None,page=1,page_size=20):
        from data_foundation.ingestion.local_videos import sync_directory,list_files
        from app.core.config import get_settings
        sync_directory(self._store,get_settings().video_storage_dir)
        native=[];number=1
        while True:
            batch=self.list_videos(scenic_id=scenic_id,camera_id=camera_id,status=status,page=number,page_size=100)
            native.extend(batch['items'])
            if number*100>=batch['total']: break
            number+=1
        local=[]
        if not scenic_id and not camera_id and status in {None,'ready'}:
            local=[{'id':r['id'],'title':r['title'],'origin':'local_folder','status':'ready','duration_seconds':r['duration_seconds'],
                    'file_digest':r['sha256'],'source_note':r['source_note'],'scenic_id':None,'camera_id':None,
                    'recorded_start_at':None,'recorded_end_at':None,'permission_record_id':None} for r in list_files(self._store)]
        items=local+native
        return {'items':items[(page-1)*page_size:page*page_size],'page':page,'page_size':page_size,'total':len(items)}

    def doctor(self) -> dict[str, str]:
        return self._store.doctor()

    @property
    def data_root(self):
        return self._store.data_root

    # --------- 来源 / 原件登记（20 上传前两站） ---------

    def register_source(self, *, source_id: str, publisher: str, title: str,
                         url: str, usage_note: str, **kwargs) -> dict[str, Any]:
        from data_foundation import SourceCreate

        with self.write() as uow:
            return uow.provenance.register_source(SourceCreate(
                id=source_id,
                publisher=publisher,
                title=title,
                url=url,
                usage_note=usage_note,
                update_frequency=kwargs.get("update_frequency", "irregular"),
                adapter_key=kwargs.get("adapter_key", "intelligent_analysis.knowledge"),
                native_grain=kwargs.get("native_grain"),
                verification_status=kwargs.get("verification_status", "approved"),
                enabled=kwargs.get("enabled", True),
                coverage_start=kwargs.get("coverage_start"),
                coverage_end=kwargs.get("coverage_end"),
                adapter_config=kwargs.get("adapter_config", {}),
            ))

    def register_asset(self, *, source_id: str, url: str, title: str,
                        retrieved_at: datetime, relative_path: str, sha256: str,
                        media_type: str, byte_size: int, usage_note: str = "",
                        published_at: datetime | None = None) -> dict[str, Any]:
        """登记来源原件。C 0.2.0a2 AssetCreate 没有 id 字段，
        SourceAsset 的 id 由数据库生成；返回的 dict 含 ``id`` 字段（D 视角作 asset_id）。
        """
        from data_foundation import AssetCreate

        with self.write() as uow:
            asset = uow.provenance.register_asset(AssetCreate(
                source_id=source_id,
                url=url,
                title=title,
                retrieved_at=retrieved_at,
                relative_path=relative_path,
                sha256=sha256,
                media_type=media_type,
                byte_size=byte_size,
                usage_note=usage_note,
                published_at=published_at,
            ))
        return {"asset_id": asset["id"], **asset}

    def register_document(self, *, document_id: str, source_id: str, asset_id: str,
                           title: str, url: str, publisher: str = "",
                           published_at: datetime | None = None,
                           valid_from: datetime | None = None,
                           valid_until: datetime | None = None,
                           usage_note: str = "",
                           document_version: str | None = None) -> dict[str, Any]:
        """登记知识库文档。

        D 生成的 ``document_id`` 作为 KnowledgeDocument.id 写入（C 0.2.0a2
        KnowledgeDocument.id 有 default ``new_id()``，可被外部 id 覆盖），
        保证与 staging file / E payload / 21 GET 路径中的 document_id 一致。

        D 接口用 ``url / usage_note``，对应到 C 表 ``source_url / permission_note``。

        ``document_version`` 由调用方（``e_bridge.stage_and_accept_document``）
        从原件字节算出，口径为 ``sha256:<32位hex>``（C manifest 同算法）。
        调用方不传时兜底取原件 sha256 前缀——**绝不写 ``"v1"`` 之类的自造
        递增版本号**，否则 E 的产物加载校验会拒绝（详见 e_bridge 的同名 helper）。
        """
        if not document_version:
            # 兜底：本函数在 cstore 侧无法回读原件，只能要求调用方显式传入。
            raise ValueError("register_document 必须显式提供 C 口径的 document_version")
        with self.write() as uow:
            row = uow.knowledge.register_document({
                "id": document_id,
                "source_id": source_id,
                "asset_id": asset_id,
                "title": title,
                "source_url": url,
                "publisher": publisher,
                "published_at": published_at,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "permission_note": usage_note,
                "document_version": document_version,
            })
        return {"document_id": row["id"], **row}

    # --------- 任务入队 / 领取 / 完成 ---------

    def create_job(self, job_type: str, payload: dict[str, Any], *,
                   analysis_id: str | None = None,
                   idempotency_key: str | None = None,
                   reason: str = "intelligent_analysis.binding.submit_job",
                   requested_by: str | None = None) -> str:
        """入队任务。

        ``analysis_id`` 是 **D 桥接后端（``SqliteJobStore``）的列**，用于 21 号
        ``find_document`` / ``latest_knowledge_payload`` 的查询键；C 0.2.0a2 的
        ``jobs`` 表没有这一列——身份已经随 ``payload["analysis_id"]`` 一起入库。
        为保持 ``submit_job`` 对两种后端的同一调用形态，这里接受但忽略它。
        """
        from data_foundation import JobCreate

        if idempotency_key is None:
            import uuid as _uuid
            idempotency_key = f"{job_type}-{_uuid.uuid4().hex}"
        _check_job_column_limits(job_type=job_type, idempotency_key=idempotency_key)
        job_create = JobCreate(
            type=job_type,
            idempotency_key=idempotency_key,
            payload=payload,
            reason=reason,
            requested_by=requested_by,
            payload_version="v1",
        )
        with self.write() as uow:
            row = uow.jobs.enqueue_or_get(job_create, _now())
        return row["id"]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        from data_foundation import NotFound  # noqa: F401

        with self.read() as uow:
            try:
                return uow.jobs.get_job(job_id)
            except NotFound:
                return None
            except Exception:
                return None

    def claim_job(self, worker_id: str, now: datetime,
                   lease_seconds: int = 120, *,
                   job_type: str | None = None,
                   job_id: str | None = None) -> dict[str, Any] | None:
        """领取一个待处理作业。

        ``job_type`` 透传给 C（**精确匹配，不给就是全队列领取**）。worker 侧
        必须给值——否则 D 会把 C 自己的 ingestion 作业抢走并标失败。

        ``job_id`` 用于**领取指定作业**（28 号短时预测：入队后要当场消费掉同一
        条 ``visible_forecast`` 作业，``publish_forecast`` 会拿它的 lease 校验
        type / payload 三要素，领错一条就全对不上）。2026-10-17 之前这一层漏了
        透传，28 号调用直接 TypeError → 500。
        """
        with self.write() as uow:
            result = uow.jobs.claim_job(
                worker_id, now, lease_seconds, job_type=job_type, job_id=job_id,
            )
        if result is None:
            return None
        # C 0.2.0a2 JobLease = (job_id, attempt_no, lease_token)，
        # lease_expires_at 在 JobAttempt 表里由 SQL 控制，D 不在内存里再保留。
        raw = result["lease"]
        if not isinstance(raw, dict):
            from dataclasses import asdict as _asdict
            raw = _asdict(raw)
        lease = Lease(
            job_id=raw["job_id"],
            attempt_no=raw["attempt_no"],
            lease_token=raw["lease_token"],
            expires_at=None,
        )
        return {"job": result["job"], "lease": lease}

    def complete_job(self, lease: Any, result_refs: dict[str, Any],
                     now: datetime) -> dict[str, Any]:
        with self.write() as uow:
            return uow.jobs.complete_job(_to_job_lease(lease), result_refs, now)

    def fail_job(self, lease: Any, error_summary: str,
                 now: datetime) -> dict[str, Any]:
        with self.write() as uow:
            return uow.jobs.fail_job(_to_job_lease(lease), error_summary, now)

    def heartbeat(self, lease: Any, now: datetime,
                  lease_seconds: int = 120) -> datetime:
        with self.write() as uow:
            return uow.jobs.heartbeat(_to_job_lease(lease), now, lease_seconds)

    # --------- 视频（27 GET 归属校验 / 26 入队后登记） ---------

    _VIDEO_ANALYSES_DDL = (
        "CREATE TABLE IF NOT EXISTS d_video_analyses ("
        " analysis_id VARCHAR(64) PRIMARY KEY,"
        " video_id VARCHAR(64) NOT NULL,"
        " status VARCHAR(20) NOT NULL DEFAULT 'processing',"
        " config_json TEXT NOT NULL,"
        " provenance_json TEXT NULL,"
        " recorded_start_at VARCHAR(40) NULL,"
        " recorded_end_at VARCHAR(40) NULL,"
        " roi_json TEXT NULL,"
        " manual_validation_json TEXT NULL,"
        " error_summary TEXT NULL,"
        " created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " INDEX idx_dva_video (video_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )

    #: D 私表计数点（27 号一段序列）。value 允许 NULL（质量 unavailable）。
    _COUNT_POINTS_DDL = (
        "CREATE TABLE IF NOT EXISTS d_video_count_points ("
        " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
        " analysis_id VARCHAR(64) NOT NULL,"
        " metric VARCHAR(40) NOT NULL DEFAULT 'camera_visible_people',"
        " value DOUBLE NULL,"
        " unit VARCHAR(20) NOT NULL DEFAULT '人',"
        " grain VARCHAR(10) NOT NULL,"
        " recorded_at VARCHAR(40) NULL,"
        " period_start VARCHAR(40) NULL,"
        " period_end VARCHAR(40) NULL,"
        " sample_count INT NOT NULL DEFAULT 0,"
        " aggregation_rule VARCHAR(20) NOT NULL DEFAULT 'median',"
        " quality_status VARCHAR(20) NOT NULL DEFAULT 'good',"
        " source_ids TEXT NULL,"
        " INDEX idx_dvcp_analysis (analysis_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )

    #: E ``VideoAnalysisConfig`` 认识的键 → 存 ``config_json``（27 号回传 E）。
    _E_CONFIG_KEYS = (
        "count_model", "weights_version", "roi", "sample_interval_seconds",
        "aggregation_minutes", "inference_engine", "device",
        "openvino_version", "confidence_threshold",
    )
    #: E **不**认识的审计键 → 存 ``provenance_json``（只在 D 的 27 响应顶层暴露）。
    #: E 的 ``VideoAnalysisConfig`` / ``CountsResponse`` 均为 ``extra="forbid"``，
    #: 这两个键塞进 ``analysis`` 会直接 ValidationError（2026-10-15 实测）。
    _AUDIT_KEYS = ("weights_sha256", "model_source_revision")

    def _ensure_d_video_analyses(self) -> None:
        """D 私表 ``d_video_analyses``——C 0.2.0a2 video_analyses 是旧版契约
        （entries/exits + count_line），与 E camera_visible_people 不兼容；
        D 私表承载新契约下的 video/analysis 归属关系。

        2026-10-15（E 0.6.0）新增 ``provenance_json`` 列；对已存在的旧表
        单独补列（MySQL 无 ``ADD COLUMN IF NOT EXISTS``，用 information_schema 探测）。
        同日补齐 ``recorded_start_at / recorded_end_at / roi_json /
        manual_validation_json / error_summary``，以及计数点表
        ``d_video_count_points``（27 号读回所需）。
        """
        from sqlalchemy import text

        # C 的 DataStore 属性名是 ``engine``（旧版曾用 ``_engine``），两者都兼容。
        engine = getattr(self._store, "engine", None) or getattr(self._store, "_engine", None)
        with engine.connect() as conn:
            conn.execute(text(self._VIDEO_ANALYSES_DDL))
            conn.execute(text(self._COUNT_POINTS_DDL))
            existing = {
                r[0] for r in conn.execute(text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'd_video_analyses'"
                ))
            }
            for col, ddl_type in (
                ("provenance_json", "TEXT NULL"),
                ("recorded_start_at", "VARCHAR(40) NULL"),
                ("recorded_end_at", "VARCHAR(40) NULL"),
                ("roi_json", "TEXT NULL"),
                ("manual_validation_json", "TEXT NULL"),
                ("error_summary", "TEXT NULL"),
            ):
                if col not in existing:
                    conn.execute(text(
                        f"ALTER TABLE d_video_analyses ADD COLUMN {col} {ddl_type}"
                    ))
            conn.commit()

    # --------- 录像元数据与回放（23 / 25，接 C 的 video 仓储） ---------

    def list_videos(self, *, scenic_id: str | None = None,
                    camera_id: str | None = None, status: str | None = None,
                    page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """23 GET /videos：走 C 的 ``uow.video.list_videos``。

        C 负责实体类型校验（scenic / camera）与 status 白名单，D 只透传分页
        与筛选结果；返回项已过 C 的 ``public_columns``，不含内部路径。
        """
        from data_foundation.contracts import Page

        with self.read() as uow:
            return uow.video.list_videos(
                scenic_id=scenic_id, camera_id=camera_id, status=status,
                page=Page(page=page, page_size=page_size),
            )

    def get_playback_ref(self, video_id: str) -> dict[str, Any]:
        """25 回放取回：走 C 的 ``uow.video.get_playback_ref``。

        C 内部完成四件事，D 不做二次实现：
        ① ``_permission(video, "replay", now)``——授权核验（未获准/过期 →
        ``InvalidPermission``）；② 录像文件必须落在 DATA_ROOT 内（越界 →
        ``InvalidArgument``）；③ 文件必须存在；④ 重算 SHA256 与 byte_size
        并与登记值比对（不一致 → ``Conflict``）。

        返回值含**服务器本地绝对路径**——只能用于 D 内部打开文件，
        **绝不能出现在任何 HTTP 响应里**（25 号只回字节流）。
        """
        from datetime import UTC, datetime
        from data_foundation import NotFound
        from data_foundation.ingestion.local_videos import get_file,playback_ref
        from app.core.config import get_settings
        if get_file(self._store,video_id):
            return playback_ref(self._store,get_settings().video_storage_dir,video_id)
        video_store=self._video_file_store()
        try:
            with video_store.read() as uow:
                return uow.video.get_playback_ref(video_id, datetime.now(UTC))
        except NotFound:
            if video_store is self._store: raise
            with self.read() as uow:
                return uow.video.get_playback_ref(video_id, datetime.now(UTC))

    def get_permission(self, permission_id: str) -> dict[str, Any]:
        """24 上传前取授权记录。

        用途有两个：① 拿到 ``source_id``（录像、原件、授权三者必须同来源，
        C 的 ``register_video`` 会校验，D 需要提前知道才能落盘到正确目录）；
        ② 让无效授权**在落盘之前**就 404 / 403，不留孤儿文件。
        """
        with self.read() as uow:
            return uow.video.get_permission(permission_id)

    def register_uploaded_video(
        self, *, source_id: str, scenic_id: str, camera_id: str,
        permission_record_id: str, recorded_start_at: datetime,
        recorded_end_at: datetime, camera_location_note: str | None,
        relative_path: str, sha256: str, byte_size: int, media_type: str,
        original_name: str,
    ) -> dict[str, Any]:
        """24 上传的登记段：**原件登记与录像登记在同一个 C 写事务内**。

        必须同一事务：先 ``register_asset`` 拿 ``asset_id`` 再 ``register_video``，
        若分两个事务，中途失败会留下一条没有任何录像指向的 ``source_assets``。

        授权是否有效、景区与摄像头的父子关系、原件/授权/录像三者来源是否一致，
        **全部由 C 的 ``register_video`` 判定**（``InvalidArgument`` /
        ``InvalidPermission``），D 不重新实现「能不能传」。
        """
        from datetime import UTC, datetime as _dt

        from data_foundation import AssetCreate

        now = _dt.now(UTC)
        with self.write() as uow:
            asset = uow.provenance.register_asset(AssetCreate(
                source_id=source_id,
                url=f"internal://video-upload/{sha256}",
                title=original_name,
                retrieved_at=now,
                relative_path=relative_path,
                sha256=sha256,
                media_type=media_type,
                byte_size=byte_size,
                usage_note="管理员经 24 号接口上传的录像原件",
                published_at=None,
            ))
            return uow.video.register_video(
                {
                    "source_id": source_id,
                    "asset_id": asset["id"],
                    "scenic_id": scenic_id,
                    "camera_id": camera_id,
                    "permission_record_id": permission_record_id,
                    "recorded_start_at": recorded_start_at,
                    "recorded_end_at": recorded_end_at,
                    "camera_location_note": camera_location_note,
                },
                now,
            )

    # --------- 目录、指标字典与来源（05 / 06 / 07，接 C 的 catalog 仓储） ---------

    def list_entities(self, *, type: str | None = None,
                      region_code: str | None = None, q: str | None = None,
                      page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """05 GET /catalog/entities：走 C 的 ``uow.catalog.list_entities``。

        ``available_metrics`` / ``available_grains`` 由 C 按**已核验观测**算出：
        只有 ``quality_status == "verified"`` 的观测才计入，这正是 03 §05
        「不在目录中暗示尚未取得的数据可用」的落地方式——D 不自己补能力列表。

        注意：这里的 ``page`` 是 C 的 ``Page`` 对象（与 ``list_videos`` 返回
        普通 int 不同），``include_cameras`` 保持默认 False——摄像头实体不进
        景区/车站目录，只在录像相关链路出现。
        """
        from data_foundation.contracts import Page

        with self.read() as uow:
            result = uow.catalog.list_entities(
                type=type, region_code=region_code, q=q,
                page=Page(page=page, page_size=page_size),
            )
        from .native_series import capabilities
        for item in result['items']:
            native = capabilities(self,item)
            item['available_grains'] = sorted(set(item['available_grains']) | {r['grain'] for r in native})
            item['available_metrics'] = sorted(set(item['available_metrics']) | {r['metric'] for r in native})
        return result

    def list_metrics(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        """06 GET /catalog/metrics：指标字典（含可比性口径）。

        每项带 ``comparability_note``——03 §06 明确要求「例如 airport_throughput
        不可作为铁路发送量直接相加」，这条口径说明由 C 的指标字典维护，
        D 原样透传，不改写、不省略。
        """
        with self.read() as uow:
            return uow.catalog.list_metrics(entity_type)

    def get_source(self, source_id: str) -> dict[str, Any]:
        """07 GET /sources/{source_id}：找不到时 C 抛 ``NotFound``（→ D 404）。"""
        import sqlalchemy as sa
        with self.read() as uow:
            source = uow.catalog.get_source(source_id)
            from data_foundation.ingestion.project_flow import SOURCE_ID, table
            if source_id == SOURCE_ID and sa.inspect(uow.session.bind).has_table(table.name):
                start, end, updated = uow.session.execute(sa.select(
                    sa.func.min(table.c.period_start), sa.func.max(table.c.period_end), sa.func.max(table.c.updated_at)
                ).where(table.c.source_id == source_id)).one()
                source = {**source, 'coverage_start': start, 'coverage_end': end,
                          'retrieved_at': updated.replace(tzinfo=timezone.utc) if updated and updated.tzinfo is None else updated,
                          'usage_note': '本资料汇集公开统计、估算及模拟数据，具体来源和处理方式可在数据清单中查看。'}
            return source

    # --------- 时序（08 / 09 / 11，接 C 的 observations 仓储） ---------

    def query_series(self, *, entity_id: str, metric: str, grain: str,
                     start: date, end: date, quality: str = "verified", include_project: bool = False) -> dict[str, Any]:
        """08 / 09 时序：走 C 的 ``uow.observations.query_series``。

        C 内部完成的口径判断，D **不重复实现**：

        - 实体/指标不存在 → ``NotFound``（→ 404）；
        - 指标不适用于该实体类型 → ``MetricEntityMismatch``（→ 422
          ``metric_entity_mismatch``，正是 03 §09 要求的语义）；
        - 该序列不支持请求粒度 → ``UnsupportedGrain``（→ 422
          ``unsupported_grain``，03 §08/§09 要求）；
        - 查询范围 > 20 年或点数 > 10000 → ``QueryLimitExceeded``（→ 400）。

        返回里 ``metric`` 是 **C 的指标字典整条**（``public_columns``），
        路由层只取 ``code`` 对外；``coverage_ratio`` 是 ``Decimal``，
        同样由路由层转 float。
        """
        from data_foundation.contracts import SeriesQuery
        from data_foundation.ingestion.project_flow import read_rows, point
        from data_foundation.errors import UnsupportedGrain
        project = [r for r in read_rows(self,entity_id,grain,start,end) if r['metric']==metric] if include_project else []
        with self.read() as uow:
            try:
                row = uow.observations.query_series(SeriesQuery(entity_id=entity_id, metric=metric, grain=grain,
                            start=start, end=end, quality=quality))
            except UnsupportedGrain:
                if not project: raise
                from data_foundation.db.models import Entity, Metric
                from data_foundation.repositories import public_columns
                row={'entity':public_columns(uow.session.get(Entity,entity_id)),
                     'metric':public_columns(uow.session.get(Metric,metric)),'grain':grain,'points':[],'missing_periods':[], 'coverage_ratio':0}
        if not project: return row
        merged={(r['period_start'],r['period_end']):point(r) for r in project}
        merged.update({(p['period_start'],p['period_end']):p for p in row['points']})
        row['points']=[v for k,v in sorted(merged.items())]
        row['missing_periods']=[p for p in row.get('missing_periods',[]) if (p['period_start'],p['period_end']) not in merged]
        row['coverage_ratio']=len(merged)/(len(merged)+len(row['missing_periods'])) if merged else 0
        row['data_basis']='project_series_with_original_observations_preferred'
        return row

    def query_project_series(self, **query):
        """Explicit dashboard/forecast opt-in; ordinary verified queries stay strict."""
        return self.query_series(**query, include_project=True)

    def get_latest_verified(self, *, entity_id: str, metric: str, grain: str,
                            as_of: date) -> dict[str, Any] | None:
        """10 号总览用：走 C 的 ``uow.observations.get_latest_verified``。

        语义正是 03 §10 要的「截至 as_of 的最新已核验点」：C 先按
        ``SeriesQuery(entity, metric, grain, as_of, as_of)`` 校验实体/指标/粒度
        （异常由路由层统一映射），再倒序回查 ``period_end <= as_of`` 的已核验
        周期，返回第一个真正选中了观测的点；一条都没有则返回 ``None``。

        ``quality`` 用 C 的默认值 ``verified``——总览只展示已核验数据，
        未核验的观测不进看板。返回的 ``value`` 是 ``Decimal``（C 用 Numeric
        存观测值），求和必须用 ``Decimal``，落 JSON 前再转 float。
        """
        with self.read() as uow:
            return uow.observations.get_latest_verified(entity_id, metric, grain, as_of)

    # --------- 假期与事件（13 / 15 / 16，接 C 的 holidays / events 仓储） ---------

    def list_holidays(self, *, year: int | None = None, kind: str | None = None,
                      region_code: str | None = None) -> list[dict[str, Any]]:
        """13 GET /holidays：走 C 的 ``uow.holidays.list_holidays``。

        假期定义**可追溯**（03 §13）：C 每条都带 ``definition_source_id``，
        指向 ``sources`` 表里那份定义原件，D 原样透传，不改写。
        """
        with self.read() as uow:
            rows = uow.holidays.list_holidays(year=year, kind=kind)
            if not region_code or region_code == "000000":
                return rows
            return [row for row in rows if row["region_code"] == "000000"
                    or region_code.startswith(row["region_code"].rstrip("0"))]

    def query_holiday_values(self, *, entity_id: str, metric: str,
                             holiday_id: str, include_project: bool = False) -> dict[str, Any]:
        """14 假期比较：走 C 的 ``uow.observations.query_holiday_values``。

        **这是 14 的权威口径入口**，C 在里面已经判好了 03 §14 的全部约束，D 不重算：

        - ``day_index_supported``：日数据完整**且**单位与口径版本唯一 →
          正是「day_index 要求两段时间均有完整日数据」；
        - ``daily_grain_supported``：来源不支持日粒度时为 False →
          正是「不能把只有假期总量的来源展开成逐日曲线」；
        - ``exact_total`` / ``daily_sum`` / ``daily_average``：假期总量观测与
          其日均，缺天时 ``daily_sum`` 直接给 None；
        - ``missing_days``：缺哪些天（date 列表）；
        - ``scope_confirmation_required``：地区性假期要求确认覆盖范围。

        ⚠️ 教训：不要自己用 ``query_series`` 拼日粒度再算日均——我第一版就是
        这么做的，日均用「sum / 有数据天数」，与 C 的「假期总量 / 假期天数」
        口径不同，缺天时会高估。口径归 C。
        """
        with self.read() as uow:
            row = uow.observations.query_holiday_values(entity_id, metric, holiday_id)
        if row.get('exact_total') or metric != 'scenic_visits' or not include_project: return row
        from data_foundation.ingestion.project_flow import read_rows,point,SOURCE_ID
        holiday=row['holiday']
        project=read_rows(self,entity_id,'holiday',holiday['start_date'],holiday['end_date'])
        exact=next((p for p in project if p['period_start']==holiday['start_date'] and p['period_end']==holiday['end_date']),None)
        if exact:
            row['exact_total']=point(exact)
            row['daily_average']=exact['value']/holiday['days_count']
            days=read_rows(self,entity_id,'day',holiday['start_date'],holiday['end_date'])
            row['daily_points']=[point(r) for r in days]
            row['missing_days']=[] if len(days)==holiday['days_count'] else row['missing_days']
            row['day_index_supported']=not row['missing_days']
            row['daily_grain_supported']=True
            row['data_basis']='project_series'
            if holiday['definition_source_id']==SOURCE_ID: row['scope_confirmation_required']=False
        return row

    def query_project_holiday_values(self, **query):
        return self.query_holiday_values(**query,include_project=True)

    def create_event(self, data: dict[str, Any], *, operator_id: str | None,
                     now: datetime) -> dict[str, Any]:
        """17 POST /events：走 C 的 ``uow.events.create_event``。

        C 侧的三条硬校验，D **无法满足就不要让它走到数据库**：

        - ``source_id`` 必须是存在的来源（否则 ``NotFound`` → 404）；
        - ``asset_id`` 必须是存在的原件，且 **``asset.source_id == source_id``**；
        - ``authority`` 必须是 ``official``——三者任一不满足，C 抛
          ``InvalidArgument("官方事件必须关联一致且可核验的来源与原件")``（→ 400）。
          这正是 03 §17「没有可核验的来源不得创建官方事件」的落地。

        ⚠️ ``asset_id`` **不在 03 §17 的请求体里**，但 C 的表里是 NOT NULL 且
        有上述一致性校验，而 ``provenance`` 仓储**没有按来源查原件的入口**，
        D 无法从 ``source_id`` 反查 → 只能要求调用方显式给。已回执 C。

        ⚠️ ``events`` 表有 ``CHECK(ends_at IS NULL OR ends_at >= starts_at)``，
        违反会走数据库 ``IntegrityError``，**不在 C 异常族里会漏成 500**
        → 时间顺序由 D 在路由层先拦。
        """
        with self.write() as uow:
            return uow.events.create_event(data, operator_id=operator_id, now=now)

    def update_event(self, event_id: str, patch: dict[str, Any], *,
                     expected_version: int, operator_id: str | None,
                     now: datetime) -> dict[str, Any]:
        """18 PATCH /events/{id}：走 C 的 ``uow.events.update_event``。

        - ``expected_version`` 与 ``row_version`` 不符 → ``Conflict``（→ 409）。
          03 §18 没定义这个字段、响应里也看不到版本号，所以 D 在调用方不传时
          **先读一次取当前版本**（等于放弃并发保护），并已请 C 补进契约；
        - ``change_note`` 为空 → ``InvalidArgument``（→ 400）；
        - 只允许改白名单内的字段（``description`` / ``severity`` / ``ends_at`` /
          ``status`` / ``source_id`` / ``asset_id`` / ``operator_note`` /
          ``last_verified_at``），外加 ``affected_entity_ids``，
          传了白名单外的键 → ``InvalidArgument``（→ 400）。

        ⚠️ C 只在 **``asset_id`` 也一起给**时才校验「来源与原件一致」。所以 D 在
        调用方要换来源时必须要求**同时**给新原件，否则事件会停在
        source 与 asset 不匹配的状态而 C 不拦。
        """
        with self.write() as uow:
            return uow.events.update_event(
                event_id, patch, expected_version=expected_version,
                operator_id=operator_id, now=now,
            )

    def list_events(self, *, region_code: str | None = None,
                    status: str | None = None) -> list[dict[str, Any]]:
        """15 GET /events 的基础取回：走 C 的 ``uow.events.list_events``。

        C 只支持 ``region_code`` / ``status`` 两个筛选，且 ``status=active`` 时
        按 ``now`` 只返回**当前仍生效**的（正是 03 §15「旧公告不自动显示为
        当前有效」）。契约里另外几个筛选项（``entity_id`` / ``kind`` /
        ``authority`` / ``since`` / ``until``）C 不支持，由 D 在路由层补齐。
        """
        from datetime import UTC, datetime

        with self.read() as uow:
            return uow.events.list_events(
                region_code=region_code, status=status, now=datetime.now(UTC),
            )

    def get_event(self, event_id: str) -> dict[str, Any]:
        """16 GET /events/{event_id}：完整事件（含时间线）；不存在 → C 抛 NotFound。"""
        with self.read() as uow:
            return uow.events.get_event(event_id)

    def get_video_analysis(self, video_id: str, analysis_id: str) -> bool:
        """27 GET 归属校验：D 私表 ``d_video_analyses`` 复合 (video_id, analysis_id) 查询。"""
        from sqlalchemy import text

        try:
            self._ensure_d_video_analyses()
        except Exception:
            return False
        with self.read() as uow:
            row = uow.session.execute(
                text("SELECT 1 FROM d_video_analyses"
                     " WHERE analysis_id = :aid AND video_id = :vid LIMIT 1"),
                {"aid": analysis_id, "vid": video_id},
            ).fetchone()
        return row is not None

    def create_video_analysis(self, analysis_id: str, video_id: str,
                               config: dict[str, Any]) -> None:
        """26 入队后同步登记 video analysis（D 私表 d_video_analyses）。

        入参 ``config`` = E 原生 config ∪ ``counter.provenance``；按 E 的
        ``extra="forbid"`` 约束分两桶存：E 认识的键进 ``config_json``，
        审计键（``weights_sha256`` / ``model_source_revision``）进
        ``provenance_json``。**只有摘要与版本，无本地路径。**
        """
        import json as _json
        from sqlalchemy import text

        try:
            self._ensure_d_video_analyses()
        except Exception:
            return None
        analysis_cfg = {k: v for k, v in config.items()
                        if k in self._E_CONFIG_KEYS or k not in self._AUDIT_KEYS}
        provenance = {k: config[k] for k in self._AUDIT_KEYS if k in config}
        with self.write() as uow:
            # MySQL IGNORE；SqliteJobStore 已走自己的 d_video_analyses 表
            uow.session.execute(
                text("INSERT IGNORE INTO d_video_analyses"
                     " (analysis_id, video_id, status, config_json, provenance_json)"
                     " VALUES (:aid, :vid, 'processing', :cfg, :prov)"),
                {"aid": analysis_id, "vid": video_id,
                 "cfg": _json.dumps(analysis_cfg, ensure_ascii=False),
                 "prov": _json.dumps(provenance, ensure_ascii=False) if provenance else None},
            )

    def write_count_points(self, analysis_id: str, points: list[dict[str, Any]],
                            *, recorded_start_at: str | None = None,
                            recorded_end_at: str | None = None,
                            roi: dict[str, Any] | None = None) -> int:
        """写一次分析的计数点（先清同 analysis_id 旧点，幂等重跑）。

        顺带回填录制起止与 ROI（roi 用 **E 形状** 存 ``roi_json``）。
        返回写入点数。
        """
        import json as _json
        from sqlalchemy import text

        if not analysis_id:
            return 0
        try:
            self._ensure_d_video_analyses()
        except Exception:
            return 0
        with self.write() as uow:
            uow.session.execute(
                text("DELETE FROM d_video_count_points WHERE analysis_id = :aid"),
                {"aid": analysis_id},
            )
            for p in points:
                uow.session.execute(
                    text(
                        "INSERT INTO d_video_count_points"
                        " (analysis_id, metric, value, unit, grain, recorded_at,"
                        "  period_start, period_end, sample_count, aggregation_rule,"
                        "  quality_status, source_ids)"
                        " VALUES (:aid, :metric, :value, :unit, :grain, :rec,"
                        "  :ps, :pe, :sc, :ar, :qs, :src)"
                    ),
                    {
                        "aid": analysis_id,
                        "metric": p.get("metric", "camera_visible_people"),
                        "value": p.get("value"),
                        "unit": p.get("unit", "人"),
                        "grain": p.get("grain"),
                        "rec": p.get("recorded_at"),
                        "ps": p.get("period_start"),
                        "pe": p.get("period_end"),
                        "sc": p.get("sample_count", 0),
                        "ar": p.get("aggregation_rule", "median"),
                        "qs": p.get("quality_status", "good"),
                        "src": _json.dumps(p.get("source_ids", []), ensure_ascii=False),
                    },
                )
            if recorded_start_at or recorded_end_at or roi is not None:
                uow.session.execute(
                    text(
                        "UPDATE d_video_analyses SET"
                        " recorded_start_at = COALESCE(:rs, recorded_start_at),"
                        " recorded_end_at = COALESCE(:re, recorded_end_at),"
                        " roi_json = COALESCE(:roi, roi_json)"
                        " WHERE analysis_id = :aid"
                    ),
                    {"rs": recorded_start_at, "re": recorded_end_at,
                     "roi": _json.dumps(roi, ensure_ascii=False) if roi is not None else None,
                     "aid": analysis_id},
                )
        return len(points)

    def complete_video_analysis(self, analysis_id: str, *,
                                 recorded_start_at: str | None = None,
                                 recorded_end_at: str | None = None,
                                 roi: dict[str, Any] | None = None,
                                 manual_validation: dict[str, Any] | None = None) -> None:
        """把分析置 ``succeeded`` 并回填录制起止 / ROI / 人工校验（E 返回值）。"""
        import json as _json
        from sqlalchemy import text

        try:
            self._ensure_d_video_analyses()
        except Exception:
            return None
        with self.write() as uow:
            uow.session.execute(
                text(
                    "UPDATE d_video_analyses SET status = 'succeeded',"
                    " recorded_start_at = COALESCE(:rs, recorded_start_at),"
                    " recorded_end_at = COALESCE(:re, recorded_end_at),"
                    " roi_json = COALESCE(:roi, roi_json),"
                    " manual_validation_json = COALESCE(:mv, manual_validation_json),"
                    " error_summary = NULL"
                    " WHERE analysis_id = :aid"
                ),
                {"rs": recorded_start_at, "re": recorded_end_at,
                 "roi": _json.dumps(roi, ensure_ascii=False) if roi is not None else None,
                 "mv": _json.dumps(manual_validation, ensure_ascii=False) if manual_validation is not None else None,
                 "aid": analysis_id},
            )

    def fail_video_analysis(self, analysis_id: str, error_summary: str) -> None:
        """把分析置 ``failed`` 并写失败摘要（27 号读到 failed → 409）。"""
        from sqlalchemy import text

        try:
            self._ensure_d_video_analyses()
        except Exception:
            return None
        with self.write() as uow:
            uow.session.execute(
                text("UPDATE d_video_analyses SET status = 'failed',"
                     " error_summary = :err WHERE analysis_id = :aid"),
                {"err": error_summary, "aid": analysis_id},
            )

    def get_analysis_audit(self, analysis_id: str) -> dict[str, Any]:
        """审计摘要（weights_sha256 / model_source_revision）——**不经 E**。"""
        import json as _json
        from sqlalchemy import text

        try:
            self._ensure_d_video_analyses()
        except Exception:
            return {}
        with self.read() as uow:
            row = uow.session.execute(
                text("SELECT provenance_json FROM d_video_analyses"
                     " WHERE analysis_id = :aid"),
                {"aid": analysis_id},
            ).fetchone()
        if row is None or not row[0]:
            return {}
        try:
            audit = _json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return {k: audit[k] for k in self._AUDIT_KEYS if k in audit}

    def load_analysis_counts(self, video_id: str, analysis_id: str) -> dict[str, Any] | None:
        """读回分析结果，构造 E 0.6.0 ``CountsResponse`` 的 dict 形状（MySQL 侧）。

        约束与 ``SqliteJobStore.load_analysis_counts`` 完全一致（见其 docstring）：
        - 只能含 E 的 10 个顶层键，否则 E 的 ``extra="forbid"`` 直接 ValidationError；
        - ``weights_sha256`` / ``model_source_revision`` 不在此返回（走 get_analysis_audit）；
        - 时间戳必须 tz-aware（补 UTC）；
        - 未完成 → analysis_not_ready 且不带 analysis/roi/points。
        """
        import json as _json
        from sqlalchemy import text

        try:
            self._ensure_d_video_analyses()
        except Exception:
            return None
        with self.read() as uow:
            row = uow.session.execute(
                text("SELECT * FROM d_video_analyses WHERE analysis_id = :aid"),
                {"aid": analysis_id},
            ).mappings().fetchone()
        if row is None or (row["video_id"] or "") != video_id:
            return None
        base: dict[str, Any] = {
            "video_id": video_id,
            "analysis_id": analysis_id,
            "metric": "camera_visible_people",
            "status": "analysis_not_ready",
            "points": [],
            "manual_validation": None,
            "roi": None,
            "analysis": None,
            "recorded_start_at": _aware_iso(row["recorded_start_at"]),
            "recorded_end_at": _aware_iso(row["recorded_end_at"]),
        }
        if row["status"] != "succeeded":
            return base
        with self.read() as uow:
            point_rows = uow.session.execute(
                text("SELECT * FROM d_video_count_points WHERE analysis_id = :aid"
                     " ORDER BY recorded_at, period_start, id"),
                {"aid": analysis_id},
            ).mappings().fetchall()
        base["status"] = "ready"
        base["roi"] = _json.loads(row["roi_json"]) if row["roi_json"] else None
        base["analysis"] = _json.loads(row["config_json"]) if row["config_json"] else None
        base["manual_validation"] = (
            _json.loads(row["manual_validation_json"]) if row["manual_validation_json"] else None
        )
        base["points"] = [
            {
                "metric": r["metric"], "value": r["value"], "unit": r["unit"],
                "grain": r["grain"],
                "recorded_at": _aware_iso(r["recorded_at"]),
                "period_start": _aware_iso(r["period_start"]),
                "period_end": _aware_iso(r["period_end"]),
                "sample_count": r["sample_count"],
                "aggregation_rule": r["aggregation_rule"],
                "quality_status": r["quality_status"],
                "source_ids": _json.loads(r["source_ids"] or "[]"),
            }
            for r in point_rows
        ]
        return base

    # --------- 画面人数（C 0.2.1a2 的 uow.visible） ---------

    # C 0.2.1a2 把 P2PNet 的存取收进 ``uow.visible``（``visible_analyses`` /
    # ``visible_count_points`` / ``visible_forecasts`` 三张表），旧 DM-Count 的
    # ``video_analyses`` / ``camera_counts`` **保留但不再写入**。D 一律走新仓储，
    # 不再用 ``d_video_analyses`` 私表当主账本——26 号文档 §1 明确「不填虚构
    # 计数线/方向/OpenVINO 版本，也不搬移旧数据」。
    #
    # 五个写方法都要求 **job lease**：``create_analysis`` 之外的方法首先
    # ``ensure_lease``，且 ``analysis.job_id`` 必须等于 ``lease.job_id``。D 因此
    # 不能绕开作业系统同步写库。
    # ⚠️ ``begin_analysis`` / ``write_points`` / ``complete_analysis`` /
    # ``fail_analysis`` **内部已经完成或失败对应的 C job** —— 外层 D worker
    # **绝不能再调一次 ``complete_job`` / ``fail_job``**（会 LeaseLost）。

    def enqueue_visible_analysis(self, *, video_id: str, analysis_id: str,
                                 config: dict[str, Any], provenance: dict[str, Any],
                                 extra_payload: dict[str, Any] | None = None,
                                 requested_by: str | None = None,
                                 reason: str = "画面人数分析",
                                 ) -> dict[str, Any]:
        """**同一个写事务**里入队 ``video_analysis`` 作业 + 登记 queued 分析。

        为什么不能分两步：``visible.create_analysis`` 要求 job 此刻仍是
        ``queued``。若先提交入队事务、再起第二个事务去登记，中间就有一段窗口
        ——worker 可能已经把作业领走（status 变 running），登记随即被 C 拒。
        这里在**一个**事务里先把 job 行 flush 出来、再 ``SELECT ... FOR UPDATE``
        锁住它做登记；期间 worker 的 ``claim_job`` 因为 ``skip_locked`` 会跳过
        这一行，窗口为零。

        幂等键取 ``analysis_id``：同一 analysis_id 重复入队不会开出第二条作业。
        """
        from data_foundation import JobCreate

        payload = {"video_id": video_id, "analysis_id": analysis_id}
        if extra_payload:
            payload.update(extra_payload)
        with self.write() as uow:
            row = uow.jobs.enqueue_or_get(
                JobCreate(
                    type="video_analysis",
                    idempotency_key=f"video-analysis-{analysis_id}",
                    payload=payload,
                    reason=reason,
                    requested_by=requested_by,
                    payload_version="v1",
                ),
                _now(),
            )
            analysis = uow.visible.create_analysis(
                video_id=video_id, job_id=row["id"], config=config,
                provenance=provenance, analysis_id=analysis_id, now=_now(),
            )
        return {"job": row, "analysis": analysis}

    def create_visible_analysis(self, *, video_id: str, job_id: str,
                                config: dict[str, Any], provenance: dict[str, Any],
                                analysis_id: str | None = None) -> dict[str, Any]:
        """为**排队中的** ``video_analysis`` 作业登记分析（status=queued）。

        C 要求 job 此刻仍是 ``queued`` 且 payload 里带同一个 ``video_id`` /
        ``analysis_id``，因此调用方必须**在同一个写事务里入队 + 登记**
        （见 :func:`app.services.visible_pipeline.enqueue_visible_analysis`），
        否则 worker 可能抢在登记之前把作业领走。
        """
        with self.write() as uow:
            return uow.visible.create_analysis(
                video_id=video_id, job_id=job_id, config=config,
                provenance=provenance, analysis_id=analysis_id, now=_now(),
            )

    def get_visible_analysis(self, analysis_id: str) -> dict[str, Any]:
        """读一条画面人数分析（含 C 侧完整性校验 `_integrity`）。"""
        with self.read() as uow:
            return uow.visible.get_analysis(analysis_id, now=_now())

    def get_visible_input(self, analysis_id: str, lease: Any) -> dict[str, Any]:
        """按 26 号文档 §3 取**已授权的**录像输入（含真实 ``file_path``）。

        这是 ``store`` 级入口（不是 uow）：先在事务内校验身份与授权、事务外
        算 SHA256、再回事务内复核登记未变且文件内容未变。**返回的
        ``file_path`` 只给 worker 内部用，不是回放票据。**
        """
        from data_foundation import NotFound
        video_store=self._video_file_store()
        try:
            return video_store.get_visible_input(analysis_id,_to_job_lease(lease),now=_now())
        except NotFound:
            if video_store is self._store: raise
            return self._store.get_visible_input(analysis_id,_to_job_lease(lease),now=_now())

    def begin_visible_analysis(self, analysis_id: str, lease: Any) -> dict[str, Any]:
        """queued/failed → running。已 succeeded 的会被 C 拒（Conflict）。"""
        with self.write() as uow:
            return uow.visible.begin_analysis(
                analysis_id, _to_job_lease(lease), now=_now(),
            )

    def write_visible_points(self, analysis_id: str, points: list[dict[str, Any]],
                             lease: Any) -> dict[str, int]:
        """写一批人数点；**单批 ≤1000**（C 硬限制），返回 {inserted, skipped}。

        同采样槽/窗口重复写且内容一致 → skipped（幂等）；内容不一致 → C 抛
        Conflict（不删旧点）。C 对每个点做字段、单位、来源、采样槽对齐、缺失
        必须是 null/unavailable/0 样本等校验，任一条不满足直接 ``InvalidArgument``。
        """
        with self.write() as uow:
            return uow.visible.write_points(
                analysis_id, points, _to_job_lease(lease), now=_now(),
            )

    def complete_visible_analysis(self, analysis_id: str, expected_point_count: int,
                                  lease: Any,
                                  manual_validation: dict[str, Any] | None = None,
                                  ) -> dict[str, Any]:
        """发布分析（running → succeeded），**内部已 complete_job**。

        C 会逐点复核摘要、校验「完整 5 分钟窗口已全部提交（缺失窗口也要显式
        unavailable）」、以及每个窗口中位数/样本数/质量与本次帧结果一致。
        """
        with self.write() as uow:
            return uow.visible.complete_analysis(
                analysis_id, expected_point_count, _to_job_lease(lease),
                manual_validation, now=_now(),
            )

    def fail_visible_analysis(self, analysis_id: str, error_summary: str,
                              lease: Any) -> None:
        """标记分析失败，**内部已 fail_job**。succeeded 的分析不能再改失败。"""
        with self.write() as uow:
            uow.visible.fail_analysis(
                analysis_id, error_summary, _to_job_lease(lease), now=_now(),
            )

    def get_visible_counts(self, *, video_id: str, analysis_id: str) -> dict[str, Any]:
        """读画面人数计数结果（与 E 0.6.0 ``CountsResponse`` 同源的形状）。

        ⚠️ **C 的 ``get_counts`` 没有 grain / start / end 参数**——它一次给全量
        ``frame`` + ``5min`` 点。03 §27 要求「D 对 C 原生结果执行查询过滤」，
        所以过滤在 **D 侧**做（见 ``app.api.v1.videos``），不是 C 不做。
        未完成 → ``status=analysis_not_ready`` 且 ``points`` 为空 → D 转 409。
        """
        with self.read() as uow:
            return uow.visible.get_counts(video_id, analysis_id, now=_now())

    def get_visible_forecast_input(self, *, analysis_id: str,
                                   cutoff_at: str) -> dict[str, Any]:
        """取预测输入：``input_manifest`` / ``input_sha256`` / ``history``。

        analysis 必须已 succeeded，且 cutoff 落在真实录像区间内。
        """
        with self.read() as uow:
            return uow.visible.forecast_input(analysis_id, cutoff_at, now=_now())

    def publish_visible_forecast(self, *, analysis_id: str, cutoff_at: str,
                                 horizon_minutes: int, input_sha256: str,
                                 result: dict[str, Any],
                                 lease: Any) -> dict[str, Any]:
        """发布预测到 ``visible_forecasts``，**内部已 complete_job**。

        要求 lease 对应的 job 是 ``visible_forecast`` 且 payload 里的
        ``analysis_id`` / ``cutoff_at`` / ``horizon_minutes`` 三者都对得上；
        还会重新算一遍 ``input_sha256`` 校验「预测输入指纹已变化」。
        """
        with self.write() as uow:
            return uow.visible.publish_forecast(
                analysis_id=analysis_id, cutoff_at=cutoff_at,
                horizon_minutes=horizon_minutes, input_sha256=input_sha256,
                result=result, lease=_to_job_lease(lease), now=_now(),
            )

    def get_visible_forecast(self, forecast_id: str) -> dict[str, Any]:
        with self.read() as uow:
            return uow.visible.get_forecast(forecast_id, now=_now())

    def invalidate_document(self, *, document_id: str, reason: str,
                            operator_id: str, lease: Any) -> dict[str, Any]:
        """按文档失效索引（C 0.2.1a2）。

        lease 对应的 job 必须是 ``knowledge_invalidate``、``requested_by`` 等于
        ``operator_id``、payload 含同一个 ``document_id``；操作者必须是 active
        admin。失效**只**把 ``index_status`` 置 failed，**保留最后一版与文件**
        （不删不改已有产物），内部已 complete_job。
        """
        with self.write() as uow:
            return uow.knowledge.invalidate_document(
                document_id=document_id, reason=reason, operator_id=operator_id,
                lease=_to_job_lease(lease), now=_now(),
            )

    # --------- 知识索引 ---------

    def begin_index(self, *, document_id: str, version: str, job_id: str,
                     parser_version: str, embedding_model_version: str,
                     lease: Any) -> dict[str, Any]:
        with self.write() as uow:
            return uow.knowledge.begin_index(
                document_id=document_id, version=version, job_id=job_id,
                parser_version=parser_version,
                embedding_model_version=embedding_model_version,
                lease=_to_job_lease(lease),
            )

    def write_chunks(self, index_id: str, chunks: list[dict[str, Any]],
                     lease: Any) -> dict[str, int]:
        with self.write() as uow:
            return uow.knowledge.write_chunks(index_id, chunks, _to_job_lease(lease))

    def activate_index(self, *, document_id: str, version: str, artifact_path: str,
                        artifact_sha256: str, expected_chunk_count: int,
                        expected_artifact_byte_size: int | None,
                        lease: Any) -> dict[str, Any]:
        with self.write() as uow:
            return uow.knowledge.activate_index(
                document_id=document_id, version=version,
                artifact_path=artifact_path, artifact_sha256=artifact_sha256,
                expected_chunk_count=expected_chunk_count,
                lease=_to_job_lease(lease),
                expected_artifact_byte_size=expected_artifact_byte_size,
            )

    def get_index_artifact_ref(self, document_id: str,
                                version: str | None = None) -> dict[str, Any]:
        with self.read() as uow:
            return uow.knowledge.get_index_artifact_ref(document_id, version)

    def activate_and_complete(self, *, document_id: str, version: str,
                               artifact_path: str, artifact_sha256: str,
                               expected_chunk_count: int,
                               expected_artifact_byte_size: int | None,
                               lease: Any, result_refs: dict[str, Any],
                               now: datetime) -> dict[str, Any]:
        """E 0.5.1 清单 §五：``activate_index`` 与 ``complete_job`` 必须在**同一个**
        C 写事务内提交。

        两个方法各自带 ``with store.write()`` 会开出两个事务，可能出现
        「索引已 active 但作业仍 running」（或反之）的中间态，破坏
        「文件先于激活存在 + 作业与索引同生共死」的验收口径。这里显式共用一个
        ``uow``；任一异常都向外传播，由外层触发整体回滚（不捕获后继续提交）。
        """
        with self.write() as uow:
            index = uow.knowledge.activate_index(
                document_id=document_id, version=version,
                artifact_path=artifact_path, artifact_sha256=artifact_sha256,
                expected_chunk_count=expected_chunk_count,
                lease=_to_job_lease(lease),
                expected_artifact_byte_size=expected_artifact_byte_size,
            )
            completed = uow.jobs.complete_job(
                _to_job_lease(lease), result_refs, now,
            )
        return {"index": index, "job": completed}

    def get_chunks(self, document_id: str, version: str | None = None) -> dict[str, Any]:
        with self.read() as uow:
            return uow.knowledge.get_chunks(document_id, version)

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self.read() as uow:
            return uow.knowledge.get_document(document_id)

    def get_document_file_ref(self, document_id: str) -> dict[str, Any] | None:
        """重建/索引要用的**原件位置**（2026-10-17 新增，接 C §6 的明确要求）。

        真机返回（已探针确认）``document_id`` / ``document_version`` /
        ``asset_id`` / ``source_id`` / ``relative_path`` / ``sha256`` /
        ``byte_size`` / ``media_type`` / ``file_path``，其中 ``file_path`` 是
        C DATA_ROOT 下的**绝对路径**，可直接读。

        为什么必须用它：以前索引/重建的原文来源是作业 payload 的 ``file_path``
        （最终落到本机 staging 目录），那只是上传时的临时痕迹——**清理过一次就
        重建不了**，而 C 库里的登记才是权威。

        文档不存在（C 的 ``NotFound``）返回 ``None``，由调用方决定回退还是报错；
        **不在这里兜底伪造**。
        """
        try:
            native = getattr(self._store, "get_document_file_ref", None)
            if native is not None:
                return native(document_id)
            # C 0.2.0a2 has the registered original, but lacks the 0.2.1 helper.
            # Resolve through its repositories and retain the same integrity checks.
            from pathlib import Path
            from data_foundation.artifacts import verify_artifact
            with self.read() as uow:
                document = uow.knowledge.get_document(document_id)
                asset = uow.provenance.get_asset(document["asset_id"], internal=True)
            if asset["source_id"] != document["source_id"]:
                raise ValueError("知识文档与原件来源不一致")
            verify_artifact(Path(self.data_root), asset["relative_path"], asset["sha256"], asset["byte_size"])
            if document["document_version"] != "sha256:" + asset["sha256"][:32]:
                raise ValueError("知识文档版本与原件摘要不一致")
            return {**asset, "document_version": document["document_version"],
                    "file_path": str(Path(self.data_root) / asset["relative_path"])}
        except Exception as exc:  # noqa: BLE001 —— C 的 NotFound 等
            logger.debug("get_document_file_ref 未取到 %s：%s", document_id, exc)
            return None

    def find_document(self, document_id: str) -> dict[str, Any] | None:
        """21 GET /knowledge/documents/{document_id} 的数据来源（C 0.2.0a2 版）。

        与 ``SqliteJobStore.find_document`` 同形状（``DocumentOut`` 字段）：

        - 登记元数据：``uow.knowledge.get_document``（C 的 knowledge_documents 行，
          published_at / valid_from / valid_until 是 tz-aware datetime，转 ISO 字符串）；
        - ``index_status``：直接取 C 行的 ``index_status``（pending/building/ready/failed）；
        - ``allowed_snippets``：仅当 ``index_status=ready`` 时取活动索引前 3 个分块文本
          的前 200 字符（03 契约 §21：不公开受限原件）。

        文档不存在返回 ``None``（路由层映射 404），其余存储异常一并收敛为 ``None``，
        避免把 C 的异常细节泄进 HTTP。
        """
        from data_foundation import NotFound

        try:
            with self.read() as uow:
                doc = uow.knowledge.get_document(document_id)
                status = doc.get("index_status") or "pending"
                snippets: list[str] = []
                if status == "ready":
                    chunks = uow.knowledge.get_chunks(document_id)
                    for chunk in chunks.get("items", [])[:3]:
                        snippets.append((chunk.get("text") or "")[:200])
        except NotFound:
            return None
        except Exception:
            logger.exception("find_document(%s) 读取 C store 失败", document_id)
            return None
        return {
            "id": document_id,
            "title": doc.get("title") or "",
            "publisher": doc.get("publisher") or "",
            "source_url": doc.get("source_url") or "",
            "published_at": _iso_text(doc.get("published_at")),
            "valid_from": _iso_text(doc.get("valid_from")) or None,
            "valid_until": _iso_text(doc.get("valid_until")) or None,
            "permission_note": doc.get("permission_note") or "",
            "index_status": status,
            "allowed_snippets": snippets,
        }

    # --------- D 适配层工具 ---------

    def ready_document_ids(self) -> list[str]:
        """D 授权给 E 的、可参与问答的文档 ID（``index_status == "ready"``）。

        E 的 ``load_verified_knowledge_snapshot(store, document_ids, data_root)``
        要求调用方显式给出**已授权**的文档集合，并明确「pending 文档必须由 D 排除」。
        所以这个列表就是 D 的授权边界：只放 C 侧活动索引已就绪的文档。
        """
        from data_foundation.contracts import Page
        rows = []
        with self.read() as uow:
            number = 1
            while True:
                page = uow.knowledge.list_documents(page=Page(page=number, page_size=100))
                rows.extend(page["items"])
                if number * 100 >= page["total"]:
                    break
                number += 1
        from app.services.database_knowledge import latest_ready_ids
        return latest_ready_ids(self, [row["id"] for row in rows if row.get("index_status") == "ready"])

    def snapshot_for_e_publisher(self) -> dict[str, Any]:
        """读取所有 index_status=ready 的文档，构造成 E ``publish_knowledge_snapshot``
        要的 ``{"documents": {document_id: {"metadata": ..., "chunks": [...]}}}`` 形态。

        ⚠️ **这是宽松版，只做形状对齐，不做语义对账。** E 的
        ``InMemoryKnowledgeIndex.from_payload`` 只校验 chunk 的文档归属，
        **不比对 C 库里的 chunk 文本 / locator / document_version / metadata**，
        因此「产物被改写但摘要同步改对」的情形它拦不住（C §六 场景⑧ 要求拦住）。

        正式链路请走 ``verified_snapshot_for_e()``（E 的
        ``load_verified_knowledge_snapshot``，全量语义校验）。本方法仅作
        E 无 artifacts 模块（<0.5.1）时的兜底，保留是为了不让老 E 直接 500。
        """
        documents: dict[str, dict[str, Any]] = {}
        with self.read() as uow:
            page = uow.knowledge.list_documents()
            for row in page.get("items", []):
                document_id = row["id"]
                if row.get("index_status") != "ready":
                    continue
                artifact = uow.knowledge.get_index_artifact_ref(document_id)
                if artifact.get("status") != "ready":
                    continue
                chunks_resp = uow.knowledge.get_chunks(document_id)
                chunks = [
                    {
                        "chunk_id": chunk.get("locator", {}).get("chunk_id",
                                  f"{document_id}:{chunk['chunk_no']}"),
                        "document_id": document_id,
                        "order": chunk["chunk_no"],
                        "text": chunk.get("text", ""),
                    }
                    for chunk in chunks_resp.get("items", [])
                ]
                if not chunks:
                    continue
                documents[document_id] = {
                    "metadata": row,
                    "chunks": chunks,
                    "artifact_path": artifact.get("relative_path"),
                    "artifact_sha256": artifact.get("sha256"),
                }
        return {"documents": documents}

    # --------- 12 历史预测（落 C 的 forecast_runs / forecast_points） ---------

    def save_history_forecast(self, data: dict[str, Any],
                              points: list[dict[str, Any]]) -> dict[str, Any]:
        """12：走 C 的 ``uow.forecasts.save_history_forecast``。

        C 侧的两条硬校验，D **必须自己先满足**，否则是 400 而不是 500：

        - ``status=ready`` 时点数必须等于 ``horizon``；
        - ``status=data_insufficient`` 时必须**无预测点且有 reason**。

        ``points`` 的 ``lower_bound`` / ``upper_bound`` 在 C 的表里是 **NOT NULL**
        且 ``CHECK(lower <= value <= upper)``、``CHECK(lower >= 0)``——
        D 的季节基线算法给的是闭区间实数（下界已 ``max(0.0, ...)``），满足约束。
        """
        with self.write() as uow:
            return uow.forecasts.save_history_forecast(data, points)

    # --------- 29 采集作业（入 C 的 jobs 队列） ---------

    def enqueue_ingestion(self, *, idempotency_key: str, payload: dict[str, Any],
                          reason: str, requested_by: str | None,
                          now: datetime) -> dict[str, Any]:
        """29：走 C 的 ``uow.jobs.enqueue_or_get``（同 key 幂等，返回既有作业）。

        只**入队**，不执行——导入由 C 的 worker 领租约后跑，D 不越界代跑。
        """
        from data_foundation import JobCreate

        with self.write() as uow:
            return uow.jobs.enqueue_or_get(JobCreate(
                type="ingestion",
                idempotency_key=idempotency_key,
                payload=payload,
                payload_version="v1",
                requested_by=requested_by,
                reason=reason,
            ), now)


def _now() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


# C 021a2 的 jobs 表里这些字符串列的长度上限（从 information_schema 核对，
# 不是猜的）。**写超了 MySQL 报 DataError 1406，而 sqlite 测试库不检查列宽**
# ——2026-10-17 的 28 号就是这么在真机炸成 500 的。
C_JOB_STRING_LIMITS: dict[str, int] = {
    "type": 48,
    "idempotency_key": 64,
}


def _check_job_column_limits(*, job_type: str, idempotency_key: str) -> None:
    """入队前自查 C ``jobs`` 表的字符串列宽。

    宁可**当场抛错**也不要让 MySQL 抛 ``DataError``：后者只在真机出现，
    而且错误码是「列太长」，看不出是哪儿拼出来的键。
    SQLite 不校验列宽，所以这一层是想让问题在 pytest 里就露头。
    """
    for column, text in (("type", job_type),
                         ("idempotency_key", idempotency_key)):
        limit = C_JOB_STRING_LIMITS[column]
        if len(text) > limit:
            raise ValueError(
                f"C 的 jobs.{column} 只有 varchar({limit})，"
                f"实际给了 {len(text)} 字符（开头 40 字符：{text[:40]!r}）；"
                f"请改用定长摘要（sha256 前 32 位）而不是直拼"
            )


def _iso_text(value: Any) -> str:
    """把 C 的 tz-aware datetime 转成 ISO 字符串；None/空值返回空串。

    21 号 ``DocumentOut`` 的时间字段是字符串（与 B 契约对齐），
    不能把 datetime 直接塞进 pydantic ``str`` 字段。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_job_lease(lease: Any):
    """把 D 侧拿到的 lease（dict 或对象）转成 C 0.2.0a2 的 ``JobLease``。

    C 0.2.0a2 ``JobLease = (job_id, attempt_no, lease_token)``，
    不再在内存里携带 ``expires_at``——过期时间由 ``JobAttempt`` 表 SQL 控制。
    """
    from data_foundation.repositories import JobLease

    if isinstance(lease, JobLease):
        return lease
    if isinstance(lease, dict):
        return JobLease(
            job_id=lease["job_id"],
            attempt_no=lease["attempt_no"],
            lease_token=lease["lease_token"],
        )
    # SqliteJobStore 的 dataclass / 命名空间对象：仅取必需三字段。
    return JobLease(
        job_id=getattr(lease, "job_id"),
        attempt_no=getattr(lease, "attempt_no"),
        lease_token=getattr(lease, "lease_token"),
    )
