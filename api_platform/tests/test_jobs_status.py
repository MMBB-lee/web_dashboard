"""30 号作业状态：C 后端字段形状的守卫（2026-10-18 真机 500 回归）。

C 的 ``jobs`` 表与 D 的自建作业桥接库形状不同：

- 时间列（``started_at`` / ``finished_at``）经 C 仓储读回是 **datetime 对象**，
  SQLite 替身给的是 ISO 字符串——替身全绿、真机 500；
- C 的 jobs 表**没有 ``analysis_id`` 列**，只有 D 桥接库才有，直接下标取会
  KeyError → 500。

这两个都只在 ``D_JOB_STORE_BACKEND=c_store`` 的真机上出现，所以钉成单测。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.api.v1.jobs import _to_cn_tz


def test_to_cn_tz_accepts_datetime_and_string():
    aware = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
    assert _to_cn_tz(aware) == "2026-10-17T16:00:00+08:00"
    # naive 的按 UTC 补齐，不得当成 +08 直接输出
    assert _to_cn_tz(aware.replace(tzinfo=None)) == "2026-10-17T16:00:00+08:00"
    assert _to_cn_tz("2026-10-17T08:00:00+00:00") == "2026-10-17T16:00:00+08:00"
    assert _to_cn_tz(None) is None
    assert _to_cn_tz("") is None
    # 解析不了的原始值原样透传，不伪造时间
    assert _to_cn_tz("not-a-time") == "not-a-time"


class _CShapeJobStore:
    """只给 C 的 ``uow.jobs.get_job`` 会返回的那些键（无 analysis_id）。"""

    def get_job(self, job_id: str):
        return {
            "id": job_id,
            "type": "ingestion",
            "status": "succeeded",
            "started_at": datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 10, 17, 8, 1, tzinfo=timezone.utc),
            "processed_count": 1,
            "imported_count": 0,
            "error_summary": None,
        }


def test_job_status_200_for_c_shape_ingestion_job(client, monkeypatch):
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin1", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text
    monkeypatch.setattr("app.api.v1.jobs.get_job_store", lambda: _CShapeJobStore())

    job_id = str(uuid4())
    resp = client.get(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] == job_id
    assert data["type"] == "ingestion"
    assert data["status"] == "succeeded"
    # C 的 ingestion 作业没有分析 ID：必须是 null，不能 500
    assert data["analysis_id"] is None
    assert data["started_at"] == "2026-10-17T16:00:00+08:00"
    assert data["finished_at"] == "2026-10-17T16:01:00+08:00"
