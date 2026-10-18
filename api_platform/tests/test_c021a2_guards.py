"""C 021a2 收口清单里两条「必须在 D 侧堵住」的约束守卫。

两条都属于**假替身永远抓不到、只有真机会炸**的那一类：

1. ``page_size > 100``：C 的 ``contracts.Page`` 抛的是纯 ``ValueError``，
   **不在 C 的异常族里**，``_c_errors`` 认不出来 → 兜底处理器吞成 500。
   D 必须在 HTTP 入口拦成 400（C 021a2 清单第 10 条）。
2. Worker 不按 ``job_type`` 领取：C 的 jobs 表里躺着 C 自己的 ``ingestion``
   作业，D 一把抓会把它领走再甩一句「不支持的作业类型」标失败——
   **直接污染 C 的队列**（《27-契约收口》§二点名）。
"""
from __future__ import annotations

from app.services.job_store import SqliteJobStore
from app.worker import SUPPORTED_JOB_TYPES, DWorker


def _login(client):
    resp = client.post("/api/v1/auth/login",
                       json={"username": "admin1", "password": "admin-pass-123"})
    assert resp.status_code == 200, resp.text


class TestPageSizeCeilingIsRejectedAtHttpEntry:
    """分页越界要在参数校验层就被挡掉，不允许走到 C 的 Page。"""

    ROUTES = ("/api/v1/catalog/entities", "/api/v1/events", "/api/v1/videos")

    def test_page_size_over_100_is_400(self, client):
        _login(client)
        for route in self.ROUTES:
            resp = client.get(route, params={"page_size": 101})
            assert resp.status_code == 400, (route, resp.status_code, resp.text)
            assert resp.json()["error"]["code"] == "bad_request"

    def test_page_size_zero_is_400(self, client):
        _login(client)
        for route in self.ROUTES:
            resp = client.get(route, params={"page_size": 0})
            assert resp.status_code == 400, (route, resp.status_code)

    def test_page_zero_is_400(self, client):
        _login(client)
        for route in self.ROUTES:
            resp = client.get(route, params={"page": 0})
            assert resp.status_code == 400, (route, resp.status_code)

    def test_legal_page_reaches_business_layer(self, client):
        """反向保证：合法分页只是过不了「C 未接」这一关，不是被参数层吃掉。

        C store 不可用时这些路由回到 503 pending —— 说明它**通过了**参数校验
        才走到业务层。若这里也变 400，说明上面几条约束写宽了。
        """
        _login(client)
        for route in self.ROUTES:
            resp = client.get(route, params={"page": 1, "page_size": 100})
            assert resp.status_code == 503, (route, resp.status_code, resp.text)


class TestWorkerJobTypeFilter:
    """D Worker 只领自己支持的 5 类，ingestion 留给 C 的消费入口。"""

    def test_supported_types_do_not_include_ingestion(self):
        assert "ingestion" not in SUPPORTED_JOB_TYPES
        assert set(SUPPORTED_JOB_TYPES) == {
            "knowledge_index", "knowledge_reindex", "knowledge_invalidate",
            "video_analysis", "visible_forecast",
        }

    def test_claim_always_passes_a_type(self, tmp_path):
        """``claim_job`` 必须带 job_type 调用——不带就是全队列裸领。"""
        seen: list[str | None] = []

        class _RecordingStore(SqliteJobStore):
            def claim_job(self, worker_id, now=None, lease_seconds=120, *,
                          job_type=None):
                seen.append(job_type)
                return None

        store = _RecordingStore(tmp_path / "jobs.sqlite3")
        assert DWorker(store, counter=None).run_once() is None
        assert len(seen) == len(SUPPORTED_JOB_TYPES), "应逐个支持类型试领一遍"
        assert all(t in SUPPORTED_JOB_TYPES for t in seen), "出现了白名单外的类型"

    def test_ingestion_job_is_never_claimed(self, tmp_path):
        """队列里只有 ingestion 时，D 一次都领不到，作业保持 queued。"""
        store = SqliteJobStore(tmp_path / "jobs.sqlite3")
        store.create_job("ingestion", payload={"csv_asset_id": "asset-x"})
        assert DWorker(store, counter=None).run_once() is None
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT status FROM d_jobs WHERE type = 'ingestion'").fetchall()
        assert [r["status"] for r in rows] == ["queued"], "D 抢走了 C 的 ingestion 作业"

    def test_supported_type_is_still_claimed(self, tmp_path):
        """反向保证：筛选不是一刀切，支持的类型照样能领到。"""
        store = SqliteJobStore(tmp_path / "jobs.sqlite3")
        store.create_job("knowledge_index", payload={"document_id": "d1"})
        claimed = DWorker(store, counter=None)._claim()
        assert claimed is not None
        assert claimed["job"]["type"] == "knowledge_index"

    def test_claim_rotates_so_later_types_are_not_starved(self, tmp_path):
        """领到某个类型后，下一轮从**下一个**类型起，避免前几类永远插队。"""
        third = SUPPORTED_JOB_TYPES[2]
        job = {"id": "job-1", "type": third, "payload": {}}

        class _ThirdOnce(SqliteJobStore):
            calls: list[str] = []
            served: bool = False

            def claim_job(self, worker_id, now=None, lease_seconds=120, *,
                          job_type=None):
                cls = type(self)
                if job_type is None:
                    raise AssertionError("绝不能带 None 类型领取")
                cls.calls.append(job_type)
                if job_type == third and not cls.served:
                    cls.served = True
                    return {"job": dict(job), "lease": ("job-1", 1, "tok")}
                return None

        worker = DWorker(_ThirdOnce(tmp_path / "jobs.sqlite3"), counter=None)
        assert worker._claim() is not None
        first_round = list(_ThirdOnce.calls)
        assert first_round[0] == SUPPORTED_JOB_TYPES[0], "第一轮从头开始"
        assert first_round[-1] == third

        _ThirdOnce.calls.clear()
        assert worker._claim() is None
        # 第二轮从被领取类型的**下一个**开始，而不是又插队从第一个开始
        assert _ThirdOnce.calls[0] == SUPPORTED_JOB_TYPES[3]
