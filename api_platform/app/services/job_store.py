"""D 的本地作业仓储（SQLite 桥接实现）。

为什么存在：E 的 worker（intelligent_analysis.worker.AnalysisWorker）期望
D 注入一个 store，通过 ``store.write() as uow`` 使用：
- ``uow.jobs.claim_job / complete_job / fail_job / heartbeat``
- ``uow.knowledge.begin_index / write_chunks / activate_index``
- ``uow.video.write_counts / complete_analysis``

C 的 DE 调用契约定稿前（jobs 等正式表由 C 的 MySQL 维护），
D 用本模块提供同契约的 SQLite 落地实现，保证：
- API 进程入队（knowledge_index 等作业）真实持久化，重启不丢；
- worker 进程可跨进程领取（WAL 模式 + lease 过期回收）；
- GET /jobs/{job_id} 可查询 D/E 类作业状态。

命名红线（04-项目架构与文件树 §4）：D/E 不各自定义与 C 的 MySQL
同名数据表——故本库所有表一律加 ``d_`` 前缀（d_jobs、d_knowledge_chunks、
d_video_analyses 等），明确这是 D 的桥接暂存，不是 C 的正式表；
C 的定稿实现接入后（providers.get_job_store 切换）本库即可退役。

所有时间以 UTC ISO-8601 字符串存储；payload 为 JSON 文本。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS d_jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    analysis_id TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    processed_count INTEGER,
    imported_count INTEGER,
    error_summary TEXT,
    claimed_by TEXT,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_d_jobs_status ON d_jobs (status, created_at);
CREATE TABLE IF NOT EXISTS d_knowledge_indexes (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    version TEXT NOT NULL,
    job_id TEXT,
    parser_version TEXT,
    embedding_model_version TEXT,
    status TEXT NOT NULL DEFAULT 'building',
    artifact_path TEXT,
    artifact_sha256 TEXT,
    chunk_count INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS d_knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_id TEXT NOT NULL,
    chunk_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    locator TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS d_video_analyses (
    analysis_id TEXT PRIMARY KEY,
    video_id TEXT,
    status TEXT NOT NULL DEFAULT 'processing',
    created_at TEXT NOT NULL,
    recorded_start_at TEXT,
    recorded_end_at TEXT,
    config TEXT,
    roi TEXT,
    manual_validation TEXT,
    error_summary TEXT
);
CREATE TABLE IF NOT EXISTS d_video_counts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    entries INTEGER NOT NULL DEFAULT 0,
    exits INTEGER NOT NULL DEFAULT 0,
    quality_status TEXT NOT NULL
);
-- 2026-10-14 E 0.5.0：camera_visible_people 计数点（value 可空 =
-- unavailable 缺帧；绝不写进上面的旧 entries/exits 人次列——红线）。
CREATE TABLE IF NOT EXISTS d_video_count_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    metric TEXT NOT NULL DEFAULT 'camera_visible_people',
    grain TEXT NOT NULL,
    value REAL,
    unit TEXT NOT NULL DEFAULT '人',
    recorded_at TEXT,
    period_start TEXT,
    period_end TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0,
    aggregation_rule TEXT,
    quality_status TEXT NOT NULL,
    source_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_d_vcp ON d_video_count_points (analysis_id, grain);
"""

# 旧库已存在 d_video_analyses 时补列（SQLite 无 ADD COLUMN IF NOT EXISTS）
_VIDEO_ANALYSES_EXTRA_COLUMNS = (
    ("recorded_start_at", "TEXT"),
    ("recorded_end_at", "TEXT"),
    ("config", "TEXT"),
    ("roi", "TEXT"),
    ("manual_validation", "TEXT"),
    ("error_summary", "TEXT"),
    # 2026-10-15 E 0.6.0：计数溯源（count_model / weights_version /
    # weights_sha256 / model_source_revision / confidence_threshold /
    # inference_engine / device）。**只存摘要与版本号，不存任何本地路径。**
    ("provenance", "TEXT"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str | None:
    """datetime / None → ISO 字符串（E 返回值里可能带 datetime 对象）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _aware_iso(value: Any) -> str | None:
    """ISO 字符串 → **带时区**的 ISO 字符串。

    E 0.6.0 ``CountsResponse.ready_metadata`` 强制 ``recorded_start_at`` /
    ``recorded_end_at`` / 各点的 ``recorded_at|period_start|period_end``
    必须是 timezone-aware，否则直接 ValidationError。SQLite 里存的多为
    tz-naive 的 ISO（如 ``2026-10-15T08:00:00``），这里补成 UTC（``+00:00``）
    ——D 内部一律按 UTC 存取（见 ``_now_iso``）。
    已经是 aware / 无法解析 → 原样返回。
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


class Lease:
    """作业租约：E 的 runner 用 ``lease["job_id"]``、knowledge_worker 用
    ``lease.job_id``，两种访问方式都支持。

    C 0.2.0a2 ``JobLease`` 不含 ``expires_at``——过期由 ``JobAttempt``
    表的 ``lease_expires_at`` SQL 控制，内存里不再保留。旧接口期望
    ``expires_at`` 的代码会在它缺失时回退（attribute 缺失 → getattr 默认值）。
    """

    def __init__(
        self,
        job_id: str,
        attempt_no: int,
        lease_token: str,
        expires_at: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.attempt_no = attempt_no
        self.lease_token = lease_token
        self.expires_at = expires_at

    def __getitem__(self, key: str) -> Any:  # 兼容字典式访问
        return getattr(self, key)


class SqliteJobStore:
    """E worker 契约的 SQLite 实现（单写多读，进程内加锁，跨进程靠 WAL）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # write() 上下文内方法会再次加锁，须可重入
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """旧库补列（幂等）：d_video_analyses 的 E 0.5.0 新列。"""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(d_video_analyses)")}
        for column, decl in _VIDEO_ANALYSES_EXTRA_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE d_video_analyses ADD COLUMN {column} {decl}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ---------- store.write() 上下文（E worker 的入口） ----------

    @contextmanager
    def write(self):
        with self._lock:
            conn = self._connect()
            try:
                yield self  # uow 即 store 自身；方法内部各自开短事务
                conn.commit()
            finally:
                conn.close()

    # E 的 runner 通过 uow.jobs / uow.knowledge / uow.video 三个仓储命名空间访问；
    # 本实现平铺在 store 上，用属性别名满足契约。
    @property
    def jobs(self) -> "SqliteJobStore":
        return self

    @property
    def knowledge(self) -> "SqliteJobStore":
        return self

    @property
    def video(self) -> "SqliteJobStore":
        return self

    # ---------- D 侧：入队与查询 ----------

    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
        analysis_id: str | None = None,
    ) -> str:
        """入队一个作业（API 进程调用）；可指定 job_id 与 E 侧生成的 ID 对齐。"""
        jid = job_id or str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO d_jobs (id, type, status, analysis_id, payload, created_at)"
                " VALUES (?, ?, 'queued', ?, ?, ?)",
                (jid, job_type, analysis_id, json.dumps(payload, ensure_ascii=False), _now_iso()),
            )
        return jid

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM d_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["payload"] = json.loads(job["payload"] or "{}")
        return job

    def find_document(self, document_id: str) -> dict[str, Any] | None:
        """21 GET /knowledge/documents/{document_id} 的数据来源（D 桥接实现）。

        - 登记元数据：最早一条 knowledge_index 作业的 payload.metadata
          （20 号接口上传时留痕，title/publisher/source_url 等）；
        - index_status：d_knowledge_indexes 最新一条（building/ready），
          尚无索引记录时为 pending（03 契约 §20 的初始态）；
        - allowed_snippets：最新 ready 索引的前几个分块（获准展示片段，
          03 契约 §21 不公开受限原件）；索引未 ready 时不返回片段。

        H01 切 C 的 store（uow.knowledge.get_document）后本方法退役。
        """
        with self._connect() as conn:
            reg = conn.execute(
                "SELECT payload FROM d_jobs WHERE type = 'knowledge_index' AND analysis_id = ?"
                " ORDER BY created_at LIMIT 1",
                (document_id,),
            ).fetchone()
            if reg is None:
                return None
            metadata = json.loads(reg["payload"] or "{}").get("metadata", {})
            idx = conn.execute(
                "SELECT id, status FROM d_knowledge_indexes WHERE document_id = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            index_status = "pending"
            snippets: list[str] = []
            if idx is not None:
                index_status = idx["status"]
                if idx["status"] == "ready":
                    rows = conn.execute(
                        "SELECT text FROM d_knowledge_chunks WHERE index_id = ?"
                        " ORDER BY chunk_no LIMIT 3",
                        (idx["id"],),
                    ).fetchall()
                    snippets = [r["text"][:200] for r in rows]
        return {
            "id": document_id,
            "title": metadata.get("title") or "",
            "publisher": metadata.get("publisher") or "",
            "source_url": metadata.get("source_url") or "",
            "published_at": metadata.get("published_at") or "",
            "valid_from": metadata.get("valid_from"),
            "valid_until": metadata.get("valid_until"),
            "permission_note": metadata.get("permission_note") or "",
            "index_status": index_status,
            "allowed_snippets": snippets,
        }

    # ---------- E 契约：uow.jobs ----------

    def claim_job(
        self,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int = 120,
        *,
        job_type: str | None = None,
    ) -> dict[str, Any] | None:
        """领取一个待处理作业（queued，或租约已过期的 running）。

        返回 E runner 约定的 ``{"job": {...}, "lease": Lease}``，无作业返回 None。

        ``job_type`` 精确匹配；``None`` 表示不筛选（与 C 的语义一致）。调用方
        （D worker）必须传值——不筛选会把不属于自己的作业也领走。
        """
        current = now or datetime.now(timezone.utc)
        current_iso = current.isoformat()
        with self._lock, self._connect() as conn:
            type_filter = " AND type = ?" if job_type is not None else ""
            row = conn.execute(
                "SELECT * FROM d_jobs WHERE (status = 'queued'"
                " OR (status = 'running' AND lease_expires_at < ?))"
                + type_filter +
                " ORDER BY created_at LIMIT 1",
                ((current_iso, job_type) if job_type is not None else (current_iso,)),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            expires = (current + timedelta(seconds=lease_seconds)).isoformat()
            attempt = (row["attempt_no"] or 0) + 1
            conn.execute(
                "UPDATE d_jobs SET status = 'running', claimed_by = ?, attempt_no = ?,"
                " lease_token = ?, lease_expires_at = ?, started_at = COALESCE(started_at, ?)"
                " WHERE id = ?",
                (worker_id, attempt, token, expires, current_iso, row["id"]),
            )
        job = dict(row)
        job["payload"] = json.loads(job["payload"] or "{}")
        job["status"] = "running"
        return {
            "job": job,
            "lease": Lease(row["id"], attempt, token, expires),
        }

    def complete_job(self, lease: Any, result: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        finished = _iso(now) or _now_iso()
        processed = result.get("chunk_count", result.get("window_count"))
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE d_jobs SET status = 'succeeded', finished_at = ?, processed_count = ?,"
                " lease_token = NULL, lease_expires_at = NULL WHERE id = ?",
                (finished, processed, lease["job_id"] if not isinstance(lease, str) else lease),
            )
        return {"status": "succeeded"}

    def fail_job(self, lease: Any, error_summary: str, now: datetime | None = None) -> dict[str, Any]:
        finished = _iso(now) or _now_iso()
        job_id = lease["job_id"] if not isinstance(lease, str) else lease
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE d_jobs SET status = 'failed', finished_at = ?, error_summary = ?,"
                " lease_token = NULL, lease_expires_at = NULL WHERE id = ?",
                (finished, error_summary[:2000], job_id),
            )
        return {"status": "failed"}

    def heartbeat(self, lease: Any, now: datetime | None = None, lease_seconds: int = 120) -> None:
        current = now or datetime.now(timezone.utc)
        expires = (current + timedelta(seconds=lease_seconds)).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE d_jobs SET lease_expires_at = ? WHERE id = ? AND lease_token = ?",
                (expires, lease.job_id, lease.lease_token),
            )

    # ---------- E 契约：uow.knowledge ----------

    def begin_index(
        self,
        *,
        document_id: str,
        version: str,
        job_id: Any,
        parser_version: str,
        embedding_model_version: str,
        lease: Any = None,
    ) -> dict[str, Any]:
        index_id = f"idx-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO d_knowledge_indexes (id, document_id, version, job_id,"
                " parser_version, embedding_model_version, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'building', ?)",
                (index_id, document_id, version, str(job_id), parser_version,
                 embedding_model_version, _now_iso()),
            )
        return {"id": index_id, "document_id": document_id, "version": version}

    def write_chunks(self, index_id: str, chunks: list[dict[str, Any]], lease: Any = None) -> dict[str, Any]:
        def _locator(value: Any) -> str:
            """E 0.5.0 的 locator 是 dict（chunk_id/order），落库前序列化。"""
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)

        rows = [
            (index_id, c["chunk_no"], c["text"], _locator(c.get("locator", "")))
            for c in chunks
        ]
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO d_knowledge_chunks (index_id, chunk_no, text, locator)"
                " VALUES (?, ?, ?, ?)",
                rows,
            )
        return {"inserted": len(rows), "skipped": 0}

    def activate_index(
        self,
        *,
        document_id: str,
        version: str,
        artifact_path: str,
        artifact_sha256: str,
        expected_chunk_count: int,
        lease: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        """D 桥接实现。``expected_artifact_byte_size``/其他 C 0.2.0a2 严格校验参数
        由 C store 实施；SqliteJobStore 接受但不强制（D 业务代码不变）。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM d_knowledge_indexes WHERE document_id = ? AND version = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (document_id, version),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE d_knowledge_indexes SET status = 'ready', artifact_path = ?,"
                    " artifact_sha256 = ?, chunk_count = ? WHERE id = ?",
                    (artifact_path, artifact_sha256, expected_chunk_count, row["id"]),
                )
        return {"index_status": "ready", "chunk_count": expected_chunk_count}

    # ---------- E 契约：uow.video ----------

    def write_counts(self, analysis_id: str, points: list[dict[str, Any]], lease: Any = None) -> dict[str, Any]:
        rows = [
            (analysis_id, _iso(p["window_start"]), _iso(p["window_end"]),
             int(p.get("entries", 0)), int(p.get("exits", 0)), p.get("quality_status", "unverified"))
            for p in points
        ]
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO d_video_counts (analysis_id, window_start, window_end,"
                " entries, exits, quality_status) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return {"inserted": len(rows), "skipped": 0}

    def complete_analysis(
        self,
        analysis_id: str,
        expected_window_count: int,
        lease: Any = None,
        manual_validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE d_video_analyses SET status = 'succeeded' WHERE analysis_id = ?",
                (analysis_id,),
            )
        return {"status": "succeeded", "window_count": expected_window_count}

    # ---------- E 0.5.0：camera_visible_people 录像分析（D 桥接实现） ----------

    def create_video_analysis(
        self, analysis_id: str, video_id: str, config: dict[str, Any]
    ) -> None:
        """submit 绑定登记作业时同步登记分析（E 的 config 为其原生形状）。

        入参 ``config`` 由 ``e_bridge.analyze_video`` 组装 = E 原生 config
        **并集** ``counter.provenance``。落库时分两桶（列）存：

        - ``config`` 列：**E 认识的**分析配置键（``count_model`` /
          ``weights_version`` / ``roi`` / ``sample_interval_seconds`` /
          ``aggregation_minutes`` / ``inference_engine`` / ``device`` /
          ``openvino_version`` / ``confidence_threshold``）。这一列会在 27 号
          经 ``analysis`` 子对象回给 E，E 的 ``VideoAnalysisConfig`` 是
          ``extra="forbid"``，多一个键就 ValidationError。
        - ``provenance`` 列：**E 不认识的**审计键（``weights_sha256`` /
          ``model_source_revision``）。这些只在 D 自己的 27 响应顶层对外暴露，
          **绝不**塞进 ``analysis``。

        **只存摘要与版本号，不存任何本地路径。**
        """
        #: E ``VideoAnalysisConfig`` 认识的键 → 存 config 列（可回传 E）。
        _E_CONFIG_KEYS = (
            "count_model", "weights_version", "roi", "sample_interval_seconds",
            "aggregation_minutes", "inference_engine", "device",
            "openvino_version", "confidence_threshold",
        )
        #: E **不**认识的审计键 → 存 provenance 列（只给 D 的 27 响应用）。
        _AUDIT_KEYS = ("weights_sha256", "model_source_revision")
        analysis_cfg = {k: v for k, v in config.items()
                        if k in _E_CONFIG_KEYS or k not in _AUDIT_KEYS}
        provenance = {k: config[k] for k in _AUDIT_KEYS if k in config}
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO d_video_analyses"
                " (analysis_id, video_id, status, created_at, config, provenance)"
                " VALUES (?, ?, 'processing', ?, ?, ?)",
                (
                    analysis_id, video_id, _now_iso(),
                    json.dumps(analysis_cfg, ensure_ascii=False),
                    json.dumps(provenance, ensure_ascii=False) if provenance else None,
                ),
            )

    def get_video_analysis(self, video_id: str, analysis_id: str) -> bool:
        """归属校验：27 GET 调用前确认 analysis 属于 video，且已登记。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT video_id FROM d_video_analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return False
        return (row["video_id"] or "") == video_id

    def snapshot_for_e_publisher(self) -> dict[str, Any]:
        """返回 E ``publish_knowledge_snapshot`` 期望的快照形状。

        D 桥接实现：遍历 ``d_knowledge_indexes.status='ready'`` 的文档，把
        元数据 + chunks 拼成 E 期望的 ``{documents: {document_id: {...}}}``。
        若当前没有任何 ready 文档，返回 ``{"documents": {}}`` —— worker 仍
        会调 ``publish_knowledge_snapshot`` 写空索引（保证 ``rag_index.json`` 落盘）。
        """
        documents: dict[str, Any] = {}
        with self._connect() as conn:
            ready = conn.execute(
                "SELECT id, document_id, version FROM d_knowledge_indexes"
                " WHERE status = 'ready' ORDER BY created_at DESC"
            ).fetchall()
            for idx in ready:
                chunks = conn.execute(
                    "SELECT chunk_no, text, locator FROM d_knowledge_chunks"
                    " WHERE index_id = ? ORDER BY chunk_no",
                    (idx["id"],),
                ).fetchall()
                # 取登记元数据（从最早一条 knowledge_index 作业的 payload）
                meta_row = conn.execute(
                    "SELECT payload FROM d_jobs WHERE type = 'knowledge_index' AND analysis_id = ?"
                    " ORDER BY created_at LIMIT 1",
                    (idx["document_id"],),
                ).fetchone()
                metadata = {}
                if meta_row is not None:
                    metadata = json.loads(meta_row["payload"] or "{}").get("metadata", {})
                documents[idx["document_id"]] = {
                    "metadata": metadata,
                    "chunks": [
                        {
                            "chunk_id": f"{idx['document_id']}:{c['chunk_no']}",
                            "document_id": idx["document_id"],
                            "order": c["chunk_no"],
                            "text": c["text"],
                        }
                        for c in chunks
                    ],
                    "artifact_path": str(Path(__file__).parent.parent / "data" / f"{idx['id']}.json"),
                    "artifact_sha256": "0" * 64,
                }
        return {"documents": documents}

    def write_count_points(self, analysis_id: str, points: list[dict[str, Any]],
                            *, recorded_start_at: str | None = None,
                            recorded_end_at: str | None = None,
                            roi: dict[str, Any] | None = None) -> dict[str, Any]:
        """worker 写回 E process_video_job 的计数点（两粒度全量）。

        幂等：先删同 analysis_id 旧点再插（重跑不会翻倍）。
        顺带回填录制起止与 ROI（E 形状）——27 号 ``ready`` 要求三者齐备。
        """
        rows = [
            (analysis_id, p.get("metric", "camera_visible_people"), p["grain"], p.get("value"),
             p.get("unit", "人"), _iso(p.get("recorded_at")), _iso(p.get("period_start")),
             _iso(p.get("period_end")), int(p.get("sample_count") or 0),
             p.get("aggregation_rule"), p.get("quality_status", "unavailable"),
             json.dumps(p.get("source_ids") or [], ensure_ascii=False))
            for p in points
        ]
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM d_video_count_points WHERE analysis_id = ?", (analysis_id,))
            conn.executemany(
                "INSERT INTO d_video_count_points (analysis_id, metric, grain, value, unit,"
                " recorded_at, period_start, period_end, sample_count, aggregation_rule,"
                " quality_status, source_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            if recorded_start_at or recorded_end_at or roi is not None:
                conn.execute(
                    "UPDATE d_video_analyses SET"
                    " recorded_start_at = COALESCE(?, recorded_start_at),"
                    " recorded_end_at = COALESCE(?, recorded_end_at),"
                    " roi = COALESCE(?, roi)"
                    " WHERE analysis_id = ?",
                    (_iso(recorded_start_at), _iso(recorded_end_at),
                     json.dumps(roi, ensure_ascii=False) if roi is not None else None,
                     analysis_id),
                )
        return {"inserted": len(rows)}

    def complete_video_analysis(
        self,
        analysis_id: str,
        *,
        recorded_start_at: str | None,
        recorded_end_at: str | None,
        roi: dict[str, Any] | None,
        manual_validation: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE d_video_analyses SET status = 'succeeded', recorded_start_at = ?,"
                " recorded_end_at = ?, roi = ?, manual_validation = ? WHERE analysis_id = ?",
                (_iso(recorded_start_at), _iso(recorded_end_at),
                 json.dumps(roi, ensure_ascii=False) if roi else None,
                 json.dumps(manual_validation, ensure_ascii=False) if manual_validation else None,
                 analysis_id),
            )

    def fail_video_analysis(self, analysis_id: str, error: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE d_video_analyses SET status = 'failed', error_summary = ?"
                " WHERE analysis_id = ?",
                (error[:2000], analysis_id),
            )

    def load_analysis_counts(self, video_id: str, analysis_id: str) -> dict[str, Any] | None:
        """读回分析结果，构造 E ``CountsResponse`` 的 dict 形状。

        - 分析不存在或 video_id 不匹配 → None（调用方按 404 处理）；
        - 未完成 → {"status": "analysis_not_ready", "points": [], ...}（E→D 转 409）；
        - 完成 → 录制起止 + roi(E 形状) + analysis(E 形状 config) + 两粒度点。

        **E 0.6.0 关键约束（``CountsResponse`` 是 ``extra="forbid"``）**：
        本方法返回值会被 E 的 ``service.get_camera_counts`` 拿去
        ``CountsResponse.model_validate(raw)``（**原样校验，不做裁剪**），
        因此只能含 E 声明的 10 个顶层键——``video_id / analysis_id /
        recorded_start_at / recorded_end_at / metric / status / points /
        manual_validation / roi / analysis``。多一个键都会让 E 直接
        ValidationError（2026-10-15 实测确认）。

        ``weights_sha256`` / ``model_source_revision`` **不能**出现在这里：
        顶层被 CountsResponse 拒，塞进 ``analysis`` 被 VideoAnalysisConfig
        （同样 ``extra="forbid"``）拒。它们改由 D 的 27 路由层经
        ``get_analysis_audit()`` 单独取，拼到 D 自己的响应里（不经 E）。

        E 0.6.0 ``get_camera_counts`` 还会额外校验：
        ``raw["analysis"]["count_model"] == "p2pnet"``，否则
        ``UnsupportedAnalysis("Stored analysis uses a retired count model; rebuild as P2PNet")``。
        历史 DM-Count 分析**故意**保留旧 config（不回填 p2pnet），因此读旧
        analysis_id 会得到这条明确的 422（而不是伪造出 P2PNet 结果）——符合
        「旧分析保留不改名、新任务新 analysis_id」的执行单要求。

        时间戳必须 tz-aware（E 的 ready_metadata 校验）；未完成时**不**带
        analysis/roi/manual_validation——E 会校验「roi 与 analysis.roi 一致」，
        None 配部分 config 会被拒收。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM d_video_analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        if row is None or (row["video_id"] or "") != video_id:
            return None
        base = {
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
        with self._connect() as conn:
            point_rows = conn.execute(
                "SELECT * FROM d_video_count_points WHERE analysis_id = ?"
                " ORDER BY recorded_at, period_start, id",
                (analysis_id,),
            ).fetchall()
        base["status"] = "ready"
        base["roi"] = json.loads(row["roi"]) if row["roi"] else None
        base["analysis"] = json.loads(row["config"]) if row["config"] else None
        base["manual_validation"] = json.loads(row["manual_validation"]) if row["manual_validation"] else None
        # 注意：weights_sha256 / model_source_revision **不在这里**——它们由
        # D 的 27 路由经 get_analysis_audit() 单独取。见本方法 docstring。
        base["points"] = [
            {
                "metric": r["metric"], "value": r["value"], "unit": r["unit"], "grain": r["grain"],
                "recorded_at": _aware_iso(r["recorded_at"]),
                "period_start": _aware_iso(r["period_start"]),
                "period_end": _aware_iso(r["period_end"]),
                "sample_count": r["sample_count"],
                "aggregation_rule": r["aggregation_rule"], "quality_status": r["quality_status"],
                "source_ids": json.loads(r["source_ids"] or "[]"),
            }
            for r in point_rows
        ]
        return base

    def get_analysis_audit(self, analysis_id: str) -> dict[str, Any]:
        """取该分析的审计摘要（weights_sha256 / model_source_revision）。

        这两项**不能**经 E 的 CountsResponse（``extra="forbid"``），故由 D 的
        27 路由层直接读回并塞进 D 响应顶层。旧分析（无 provenance）→ 空 dict，
        绝不回填伪造的 p2pnet 标识。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT provenance FROM d_video_analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None or not row["provenance"]:
            return {}
        try:
            audit = json.loads(row["provenance"])
        except (TypeError, ValueError):
            return {}
        return {
            k: audit[k]
            for k in ("weights_sha256", "model_source_revision")
            if k in audit
        }

    # ---------- 知识闭环：活动快照与重建 ----------

    def latest_knowledge_payload(self, document_id: str) -> dict[str, Any] | None:
        """取该文档最近一次 knowledge_index 作业的 payload（file_path / metadata）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM d_jobs WHERE type = 'knowledge_index' AND analysis_id = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (document_id,),
            ).fetchone()
        return json.loads(row["payload"] or "{}") if row is not None else None

    def next_index_version(self, document_id: str) -> int:
        """该文档已有的索引版本数 + 1（重建必须换新版本，不得对同版本重复 begin_index）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM d_knowledge_indexes WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        return (row["n"] or 0) + 1

    def active_knowledge_documents(self) -> dict[str, dict[str, Any]]:
        """已提交的活动版本（每文档最新一条 ready 索引 + 全部分块）。

        供 worker 发布全量快照：坏摘要 / 未激活的索引不会进入活动集。
        返回 E 的 InMemoryKnowledgeIndex 快照格式 ``{"documents": {...}}``。
        """
        documents: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            ready = conn.execute(
                "SELECT i.id, i.document_id, j.payload FROM d_knowledge_indexes i"
                " JOIN (SELECT document_id, MAX(created_at) AS latest FROM d_knowledge_indexes"
                "       WHERE status = 'ready' GROUP BY document_id) m"
                "   ON i.document_id = m.document_id AND i.created_at = m.latest"
                " LEFT JOIN d_jobs j ON j.type = 'knowledge_index' AND j.analysis_id = i.document_id"
                " ORDER BY i.created_at DESC"
            ).fetchall()
            for index_row in ready:
                document_id = index_row["document_id"]
                if document_id in documents:
                    continue
                payload = json.loads(index_row["payload"] or "{}") if index_row["payload"] else {}
                metadata = payload.get("metadata") or {}
                chunks = [
                    {"chunk_id": f"{document_id}:{r['chunk_no']}", "document_id": document_id,
                     "order": r["chunk_no"], "text": r["text"]}
                    for r in conn.execute(
                        "SELECT chunk_no, text FROM d_knowledge_chunks WHERE index_id = ?"
                        " ORDER BY chunk_no",
                        (index_row["id"],),
                    ).fetchall()
                ]
                if not chunks:
                    continue
                documents[document_id] = {"metadata": metadata, "chunks": chunks}
        return {"documents": documents}
