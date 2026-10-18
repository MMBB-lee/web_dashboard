"""08 景区时序 / 09 交通时序（接 C 0.2.0a2 的 ``uow.observations``）。

守的四条：

① **数字点溯源十项齐全**（03 §1）：metric / value / unit / period_start /
   period_end / grain / source_ids / published_at / ingested_at /
   quality_status，且观测值 ``is_forecast=false``；
② **无数据就是无数据**：``points=[]`` + ``coverage_ratio=0``，不补零、不插值；
③ **口径错误走 422 而不是 400**：指标-实体不匹配 → ``metric_entity_mismatch``、
   来源不支持粒度 → ``unsupported_grain``，这两条由 C 判定，D 只翻译；
④ **内部取数细节不外泄**：``provenance``（asset_url / source_locator）与
   ``diagnostics`` / ``selection_policy_version`` 不进响应。

C store 不可用（sqlite 后端）时回到 503 占位，不在测试里假装接通。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


class _FakeQuery:
    """模拟 C 的 ``SeriesQuery``（dataclass）。"""

    def __init__(self, entity_id, metric, grain, start, end, quality="verified"):
        self.entity_id = entity_id
        self.metric = metric
        self.grain = grain
        self.start = start
        self.end = end
        self.quality = quality


class _FakeObservationUow:
    def __init__(self, *, error: Exception | None = None,
                 entity_type: str = "scenic", empty: bool = False) -> None:
        self.calls: list[_FakeQuery] = []
        self._error = error
        self.entity_type = entity_type
        self.empty = empty

    def query_series(self, query):
        self.calls.append(query)
        if self._error is not None:
            raise self._error
        row = _series_row(query, self.entity_type)
        if self.empty:
            row["points"] = []
            row["coverage_ratio"] = Decimal("0")
        return row


def _point(period_start: date, period_end: date, *, value: float = 1234.0) -> dict:
    return {
        "id": str(uuid4()), "version": 1, "metric": "scenic_visits", "value": value,
        "unit": "人次", "period_start": period_start, "period_end": period_end,
        "grain": "day", "source_ids": [str(uuid4())],
        # C 的 public_columns / _point 把 datetime 统一成 UTC aware
        "published_at": datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
        "ingested_at": datetime(2026, 5, 2, 1, 0, tzinfo=timezone.utc),
        "quality_status": "verified", "methodology_version": "ingest-1.0",
        # 取数细节：只留在服务端，不进响应
        "provenance": {"source_id": str(uuid4()), "asset_id": str(uuid4()),
                       "source_url": "https://example.gov.cn/x",
                       "asset_url": "https://example.gov.cn/x.csv",
                       "source_locator": "row 12", "run_id": str(uuid4())},
    }


def _series_row(query, entity_type: str) -> dict:
    start = query.start
    end = query.end
    return {
        "entity": {"id": query.entity_id, "name": "黄山风景区", "type": entity_type},
        # C 返回的是指标字典整条；对外只要 code
        "metric": {"code": query.metric, "display_name": "景区接待人次",
                   "unit": "人次", "supported_entity_types": [entity_type],
                   "aggregation_rule": "sum", "comparability_note": "x",
                   "definition": "y", "dictionary_version": "2026-10-12"},
        "grain": query.grain,
        "points": [_point(start, start)],
        "missing_periods": [{"period_start": end, "period_end": end}],
        "coverage_ratio": Decimal("0.5"),
        "diagnostics": [{"code": "noncanonical_period_excluded"}],
        "selection_policy_version": "policy-1",
    }


class _FakeCStore:
    def __init__(self, uow: _FakeObservationUow) -> None:
        self.uow = uow

    def query_series(self, *, entity_id, metric, grain, start, end, quality="verified"):
        from data_foundation.contracts import SeriesQuery

        return self.uow.query_series(
            SeriesQuery(entity_id=entity_id, metric=metric, grain=grain,
                        start=start, end=end, quality=quality)
        )


def _login(client: TestClient, username: str = "admin1",
           password: str = "admin-pass-123") -> str:
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password},
    ).json()["data"]["csrf_token"]


@pytest.fixture
def series_env(tmp_path, monkeypatch, fake_store):
    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    client = TestClient(pytest.importorskip("app.main").app)

    def _install(uow: _FakeObservationUow):
        store = _FakeCStore(uow)
        monkeypatch.setattr("app.api.v1.scenic.get_c_store", lambda: store)
        monkeypatch.setattr("app.api.v1.transport.get_c_store", lambda: store)
        return uow

    return {"client": client, "install": _install}


BASE = {"metric": "scenic_visits", "grain": "day",
        "start": "2026-05-01", "end": "2026-05-05"}


# ---------- 08 景区时序 ----------


class TestScenicSeries:
    def test_requires_login(self, series_env):
        url = f"/api/v1/scenic/{uuid4()}/series"
        assert series_env["client"].get(url, params=BASE).status_code == 401

    def test_point_carries_full_provenance_fields(self, series_env):
        uow = series_env["install"](_FakeObservationUow())
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=BASE)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["request_id"]
        data = body["data"]
        # metric 是代码，不是 C 给的指标字典整条
        assert data["metric"] == "scenic_visits"
        assert data["grain"] == "day"
        # Decimal → float
        assert data["coverage_ratio"] == 0.5
        point = data["points"][0]
        for key in ("metric", "value", "unit", "period_start", "period_end",
                    "grain", "source_ids", "published_at", "ingested_at",
                    "quality_status"):
            assert key in point, key
        # 时间统一 +08:00；周期是日期
        assert point["published_at"].endswith("+08:00")
        assert point["period_start"] == "2026-05-01"
        assert point["is_forecast"] is False
        assert point["model_version"] is None
        # 取数细节不外泄
        assert "provenance" not in point
        for leaked in ("diagnostics", "selection_policy_version"):
            assert leaked not in data, leaked
        # 日期参数解析成 date 后传给 C
        assert uow.calls[0].start == date(2026, 5, 1)
        assert uow.calls[0].end == date(2026, 5, 5)
        assert uow.calls[0].quality == "verified"

    def test_non_scenic_entity_is_422(self, series_env):
        """03 §08：scenic_id 必须是景区——C 不校验路径语义，这一刀由 D 补。"""
        series_env["install"](_FakeObservationUow(entity_type="airport"))
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=BASE)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "metric_entity_mismatch"

    def test_empty_series_is_empty_not_fabricated(self, series_env):
        """无观测 → 空 points + 覆盖率 0，不补零、不插值。"""
        series_env["install"](_FakeObservationUow(empty=True))
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=BASE)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["points"] == []
        assert data["coverage_ratio"] == 0
        assert data["missing_periods"]

    @pytest.mark.parametrize("params, expected", [
        ({"grain": "5min"}, 400),      # 录像粒度不用于业务客流
        ({"quality": "whatever"}, 400),
        ({"start": "2026/05/01"}, 400),
    ])
    def test_bad_params_are_400(self, series_env, params, expected):
        series_env["install"](_FakeObservationUow())
        client = series_env["client"]
        _login(client)
        query = {**BASE, **params}
        resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=query)
        assert resp.status_code == expected, resp.text
        assert resp.json()["error"]["code"] == "bad_request"

    def test_start_after_end_is_400(self, series_env):
        series_env["install"](_FakeObservationUow())
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/scenic/{uuid4()}/series",
                          params={**BASE, "start": "2026-05-05", "end": "2026-05-01"})
        assert resp.status_code == 400

    def test_non_uuid_entity_is_400(self, series_env):
        series_env["install"](_FakeObservationUow())
        client = series_env["client"]
        _login(client)
        resp = client.get("/api/v1/scenic/not-a-uuid/series", params=BASE)
        assert resp.status_code == 400

    def test_pending_503_without_c_store(self, series_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.scenic.get_c_store", lambda: None)
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=BASE)
        assert resp.status_code == 503


# ---------- 09 交通时序 ----------


class TestTransportSeries:
    @pytest.mark.parametrize("entity_type, expected", [
        ("rail_station", "rail"), ("airport", "airport"),
    ])
    def test_transport_type_is_derived_from_entity(self, series_env,
                                                   entity_type, expected):
        series_env["install"](_FakeObservationUow(entity_type=entity_type))
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/transport/{uuid4()}/series", params=BASE)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["transport_type"] == expected

    def test_non_transport_entity_is_422(self, series_env):
        series_env["install"](_FakeObservationUow(entity_type="scenic"))
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/transport/{uuid4()}/series", params=BASE)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "metric_entity_mismatch"

    def test_pending_503_without_c_store(self, series_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.transport.get_c_store", lambda: None)
        client = series_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/transport/{uuid4()}/series", params=BASE)
        assert resp.status_code == 503


# ---------- C 异常族翻译（422 的两条是重点） ----------


class TestSeriesCErrors:
    def test_422_semantics(self, series_env):
        from data_foundation import MetricEntityMismatch, UnsupportedGrain

        for error, code in ((MetricEntityMismatch("指标不适用于该实体"),
                             "metric_entity_mismatch"),
                            (UnsupportedGrain("该序列不支持请求粒度"),
                             "unsupported_grain")):
            series_env["install"](_FakeObservationUow(error=error))
            client = series_env["client"]
            _login(client)
            resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=BASE)
            assert resp.status_code == 422, resp.text
            assert resp.json()["error"]["code"] == code

    def test_404_and_400_semantics(self, series_env):
        from data_foundation import InvalidArgument, NotFound, QueryLimitExceeded

        for error, expected in ((NotFound("实体不存在"), 404),
                                (InvalidArgument("quality 只能是 verified 或 all"), 400),
                                (QueryLimitExceeded("查询点数超过 10000"), 400)):
            series_env["install"](_FakeObservationUow(error=error))
            client = series_env["client"]
            _login(client)
            resp = client.get(f"/api/v1/scenic/{uuid4()}/series", params=BASE)
            assert resp.status_code == expected, (error, resp.text)
