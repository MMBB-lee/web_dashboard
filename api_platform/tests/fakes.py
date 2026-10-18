"""测试用的内存版 C 画面人数仓储（``uow.visible`` + 作业队列）。

2026-10-17 起 26/27/28 不再走 D 私表 ``d_video_analyses``，而是走 C 的
``uow.visible``。HTTP 层的测试需要一个替身，但**替身必须保留 C 的拒绝语义**——
本项目已经踩过五次「fake 过于宽松 → 单测全绿、真机 500」的坑，所以这里刻意
保留三条 C 真实会做的拒绝：

1. 分析不存在、或 ``video_id`` 与登记的不一致 → 抛 C 的 ``NotFound``
   （**归属校验真机上也由 C 做**，D 依赖这条异常翻译，所以必须走**真异常类型**）；
2. 分析未 succeeded → 返回 ``status=analysis_not_ready`` 且 points 为空
   （D 转 409，参数错误不能混进来）；
3. ``write_points`` 单批 >1000 → 抛 ``InvalidArgument``，
   ``complete_analysis`` 的 expected_point_count 与实际点数不符 → 抛 ``Conflict``
   ——这两条是 C 的硬校验，D 的分批与计数逻辑靠它兜底。

反过来，凡属 C 内部才能算的东西（SHA256、5 分钟窗口中位数复核）这里**不算**，
测试要用就显式给值。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from data_foundation import Conflict, InvalidArgument, NotFound


class Lease:
    """C 的 JobLease 最小形状（D 的 _to_job_lease 认识这个形状）。"""

    def __init__(self, job_id: str, token: str | None = None) -> None:
        self.job_id = job_id
        self.attempt_no = 1
        self.lease_token = token or uuid.uuid4().hex
        self.expires_at = datetime.now(UTC) + timedelta(minutes=5)


class FakeVisibleStore:
    """26—28 链路的替身。**故意不完整**：只实现新链路真正用到的那些方法。

    缺方法时 Python 会报 AttributeError，那正是我们想要的——比静默返回空 dict
    安全得多。
    """

    def __init__(self, *, video_id: str | None = None) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.analyses: dict[str, dict[str, Any]] = {}
        self.points: dict[str, list[dict[str, Any]]] = {}
        self.forecasts: dict[str, dict[str, Any]] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.input_sha256 = "d" * 64  # 够长、够假，只用来验「原样透传」
        self.seeded_video_id = video_id
        self.calls: list[str] = []

    # ---------- 26：同一事务入队 + 登记 ----------

    def enqueue_visible_analysis(self, *, video_id: str, analysis_id: str,
                                 config: dict[str, Any],
                                 provenance: dict[str, Any],
                                 extra_payload: dict[str, Any] | None = None,
                                 requested_by: str | None = None,
                                 reason: str = "") -> dict[str, Any]:
        self.calls.append("enqueue_visible_analysis")
        payload = {"video_id": video_id, "analysis_id": analysis_id}
        if extra_payload:
            payload.update(extra_payload)
        job_id = self.create_job(
            "video_analysis", payload, idempotency_key=f"video-analysis-{analysis_id}",
            reason=reason, requested_by=requested_by,
        )
        analysis = {
            "id": analysis_id,
            "video_id": video_id,
            "job_id": job_id,
            "status": "queued",
            "config": dict(config),
            "provenance": dict(provenance),
        }
        self.analyses[analysis_id] = analysis
        self.points.setdefault(analysis_id, [])
        return {"job": dict(self.jobs[job_id]), "analysis": dict(analysis)}

    def get_visible_analysis(self, analysis_id: str) -> dict[str, Any]:
        row = self.analyses.get(analysis_id)
        if row is None:
            raise NotFound(f"analysis not found: {analysis_id}")
        return dict(row)

    # ---------- 27：读结果（含 C 的归属校验） ----------

    def get_visible_counts(self, *, video_id: str, analysis_id: str) -> dict[str, Any]:
        self.calls.append("get_visible_counts")
        row = self.analyses.get(analysis_id)
        if row is None or row["video_id"] != video_id:
            # C 的真实行为：归属不匹配按「不存在」处理，不让调用方探测其它录像
            raise NotFound(f"analysis not found: {analysis_id}")
        if row["status"] != "succeeded":
            return {"analysis_id": analysis_id, "status": "analysis_not_ready",
                    "points": []}
        return {
            "analysis_id": analysis_id,
            "status": "ready",
            "roi": row["config"].get("roi"),
            "analysis": dict(row["config"]),
            "points": [dict(p) for p in self.points.get(analysis_id, [])],
        }

    # ---------- 28：预测 ----------

    def get_visible_forecast_input(self, *, analysis_id: str,
                                   cutoff_at: str) -> dict[str, Any]:
        row = self.analyses.get(analysis_id)
        if row is None:
            raise NotFound(f"analysis not found: {analysis_id}")
        return {
            "analysis_id": analysis_id,
            "cutoff_at": cutoff_at,
            "history": list(self.history.get(analysis_id, [])),
            "input_sha256": self.input_sha256,
        }

    def publish_visible_forecast(self, *, analysis_id: str, cutoff_at: str,
                                 horizon_minutes: int, input_sha256: str,
                                 result: dict[str, Any],
                                 lease: Any) -> dict[str, Any]:
        job_id = lease.job_id if isinstance(lease, Lease) else lease["job_id"]
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound(f"job not found: {job_id}")
        # C 会拿 lease 回查 job，校验 type 与 payload 三要素一致
        if job["type"] != "visible_forecast":
            raise Conflict(f"job {job_id} is not a visible_forecast job")
        for key, expected in (("analysis_id", analysis_id),
                              ("cutoff_at", cutoff_at),
                              ("horizon_minutes", horizon_minutes)):
            if job["payload"].get(key) != expected:
                raise Conflict(
                    f"job payload 与发布内容不一致：{key} "
                    f"{job['payload'].get(key)!r} ≠ {expected!r}"
                )
        forecast_id = str(uuid.uuid4())
        self.forecasts[forecast_id] = {
            "id": forecast_id,
            "analysis_id": analysis_id,
            "cutoff_at": cutoff_at,
            "horizon_minutes": horizon_minutes,
            "input_sha256": input_sha256,
            "result": dict(result),
        }
        # C 的 publish_forecast 内部已 complete_job
        self.complete_job(lease, {"forecast_id": forecast_id}, now=datetime.now(UTC))
        return dict(self.forecasts[forecast_id])

    def get_visible_forecast(self, forecast_id: str) -> dict[str, Any]:
        row = self.forecasts.get(forecast_id)
        if row is None:
            raise NotFound(f"forecast not found: {forecast_id}")
        return dict(row)

    # ---------- worker 五步序列 ----------

    def get_visible_input(self, analysis_id: str, lease: Any) -> dict[str, Any]:
        row = self.analyses.get(analysis_id)
        if row is None:
            raise NotFound(f"analysis not found: {analysis_id}")
        if not self.inputs_allowed:
            raise NotFound("未授权：录像还没有可用于画面人数分析的授权记录")
        self.calls.append("get_visible_input")
        return {
            "analysis_id": analysis_id,
            "video_id": row["video_id"],
            "file_path": str(self.input_file_path),
            "recorded_start_at": self.recorded_start_at,
            "recorded_end_at": self.recorded_end_at,
            "source_ids": list(self.source_ids),
            "config": dict(row["config"]),
        }

    def begin_visible_analysis(self, analysis_id: str, lease: Any) -> dict[str, Any]:
        row = self.analyses.get(analysis_id)
        if row is None:
            raise NotFound(f"analysis not found: {analysis_id}")
        if row["status"] == "succeeded":
            raise Conflict(f"analysis already succeeded: {analysis_id}")
        row["status"] = "running"
        self.calls.append("begin_visible_analysis")
        return dict(row)

    def write_visible_points(self, analysis_id: str,
                             points: list[dict[str, Any]],
                             lease: Any) -> dict[str, int]:
        if len(points) > 1000:
            raise InvalidArgument(f"单批点数 {len(points)} 超过 C 的上限 1000")
        bucket = self.points.setdefault(analysis_id, [])
        seen = {(p.get("recorded_at"), p.get("period_start")) for p in bucket}
        inserted = skipped = 0
        for point in points:
            key = (point.get("recorded_at"), point.get("period_start"))
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            bucket.append(dict(point))
            inserted += 1
        self.calls.append("write_visible_points")
        return {"inserted": inserted, "skipped": skipped}

    def complete_visible_analysis(self, analysis_id: str, expected_point_count: int,
                                  lease: Any,
                                  manual_validation: dict[str, Any] | None = None,
                                  ) -> dict[str, Any]:
        row = self.analyses.get(analysis_id)
        if row is None:
            raise NotFound(f"analysis not found: {analysis_id}")
        actual = len(self.points.get(analysis_id, []))
        if actual != int(expected_point_count):
            raise Conflict(
                f"点数不符：声明 {expected_point_count}，实际 {actual}"
            )
        row["status"] = "succeeded"
        row["manual_validation"] = manual_validation
        # C 的 complete_analysis 内部已 complete_job
        self.complete_job(lease, {"analysis_id": analysis_id}, now=datetime.now(UTC))
        self.calls.append("complete_visible_analysis")
        return dict(row)

    def fail_visible_analysis(self, analysis_id: str, error_summary: str,
                              lease: Any) -> None:
        row = self.analyses.get(analysis_id)
        if row is None:
            raise NotFound(f"analysis not found: {analysis_id}")
        row["status"] = "failed"
        row["error_summary"] = error_summary
        self.fail_job(lease, error_summary[:2000], now=datetime.now(UTC))
        self.calls.append("fail_visible_analysis")

    # ---------- 作业队列（内存版） ----------

    def create_job(self, job_type: str, payload: dict[str, Any], *,
                   analysis_id: str | None = None,
                   idempotency_key: str | None = None,
                   reason: str = "",
                   requested_by: str | None = None) -> str:
        if idempotency_key:
            for job in self.jobs.values():
                if job.get("idempotency_key") == idempotency_key:
                    return job["id"]
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "payload": dict(payload),
            "analysis_id": analysis_id,
            "idempotency_key": idempotency_key,
            "reason": reason,
            "requested_by": requested_by,
            "result_refs": {},
            "error_summary": None,
            "attempt_no": 0,
        }
        return job_id

    def claim_job(self, worker_id: str, now: datetime,
                  lease_seconds: int = 120, *,
                  job_type: str | None = None,
                  job_id: str | None = None) -> dict[str, Any] | None:
        for job in self.jobs.values():
            if job["status"] != "queued":
                continue
            if job_id is not None and job["id"] != job_id:
                continue
            if job_type is not None and job["type"] != job_type:
                continue
            job["status"] = "running"
            job["attempt_no"] += 1
            job["claimed_by"] = worker_id
            lease = Lease(job["id"])
            return {"job": dict(job), "lease": lease}
        return None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.jobs.get(job_id)
        return dict(row) if row is not None else None

    def complete_job(self, lease: Any, result_refs: dict[str, Any],
                     now: datetime) -> dict[str, Any]:
        job_id = lease.job_id if isinstance(lease, Lease) else lease["job_id"]
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound(f"job not found: {job_id}")
        # C 的真实行为：已经完结的作业不能再次完结（lease 已失效）。
        # 这条守卫专门用来抓「C 内部已 complete、D 外层又调一次」这类 bug。
        if job["status"] != "running":
            raise Conflict(
                f"job {job_id} 当前是 {job['status']}，不能再次完成"
                "（C 的 complete_analysis / publish_forecast 内部已完成作业，"
                "外层 worker 必须跳过）"
            )
        job["status"] = "succeeded"
        job["result_refs"] = dict(result_refs or {})
        return dict(job)

    def fail_job(self, lease: Any, error_summary: str,
                 now: datetime) -> dict[str, Any]:
        job_id = lease.job_id if isinstance(lease, Lease) else lease["job_id"]
        job = self.jobs.get(job_id)
        if job is None:
            raise NotFound(f"job not found: {job_id}")
        job["status"] = "failed"
        job["error_summary"] = error_summary
        return dict(job)

    def heartbeat(self, lease: Any, now: datetime,
                  lease_seconds: int = 120) -> datetime:
        return now + timedelta(seconds=lease_seconds)

    # ---------- 测试用的播种辅助 ----------

    inputs_allowed = False
    input_file_path = "videos/demo.mp4"
    recorded_start_at = "2026-05-01T10:00:00+08:00"
    recorded_end_at = "2026-05-01T11:00:00+08:00"
    source_ids: list[str] = ["11111111-1111-1111-1111-111111111111"]

    def seed_queued(self, *, video_id: str,
                    analysis_id: str | None = None,
                    provenance: dict[str, Any] | None = None) -> str:
        """登记一条 queued 分析（模拟「26 已受理、worker 还没干完」）。"""
        analysis_id = analysis_id or str(uuid.uuid4())
        config = {
            "count_model": "p2pnet",
            "weights_version": "leader-SHTechA-50604773",
            "roi": {"points": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1},
                               {"x": 0.9, "y": 0.9}]},
            "sample_interval_seconds": 10,
            "aggregation_minutes": 5,
            "confidence_threshold": 0.5,
            "inference_engine": "pytorch",
            "device": "CPU",
        }
        self.analyses[analysis_id] = {
            "id": analysis_id, "video_id": video_id, "job_id": None,
            "status": "queued", "config": config,
            "provenance": provenance or {},
        }
        self.points.setdefault(analysis_id, [])
        return analysis_id

    def seed_ready(self, *, video_id: str, analysis_id: str | None = None,
                   provenance: dict[str, Any] | None = None,
                   points: list[dict[str, Any]] | None = None) -> str:
        """登记一条 succeeded 分析并写入计数点（27 号能直接读到结果）。"""
        analysis_id = self.seed_queued(
            video_id=video_id, analysis_id=analysis_id, provenance=provenance,
        )
        self.analyses[analysis_id]["status"] = "succeeded"
        self.points[analysis_id] = [dict(p) for p in (points or [])]
        return analysis_id
