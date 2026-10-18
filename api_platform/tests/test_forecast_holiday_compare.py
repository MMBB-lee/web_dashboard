"""12 历史预测 / 14 假期比较（接 C 0.2.0a2 的 observations / holidays / forecasts）。

12 守的四条：

① **数据不足绝不用历史均值伪装预测**（03 §12 红线）：``status=data_insufficient``
   且 ``forecast_points=[]``，并给出 ``reason``；
② **预测结果落 C 的表**，且满足 C 的两条硬校验——``ready`` 时点数 == ``horizon``，
   ``data_insufficient`` 时**无预测点且有 reason**；
③ **口径错误由 C 判、D 翻译**：实体/指标不存在 → 404、指标-实体不匹配 → 422；
④ ``value`` 为 None 的观测点不参与训练（缺失不补零）。

14 守的四条：

① **day_index 要求完整日数据**：缺一天就整条不比，绝不返回半截指数曲线；
② **不能把假期总量展开成逐日曲线**：C 抛 ``UnsupportedGrain`` 时记成
   「该组合不可用」，不是拿总量除以天数编日值；
③ **日均的分母是有数据的天数**，不是假期总天数（按总天数除等于把缺失当 0）；
④ 不可比必须说清是哪一段缺多少，``comparable=False`` + ``reason``。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings

from data_foundation.errors import (Conflict, MetricEntityMismatch, NotFound,
                                    UnsupportedGrain)

SOURCE_A = str(uuid4())
SOURCE_B = str(uuid4())
ENTITY = str(uuid4())
METRIC = "scenic_visits"

H1 = str(uuid4())   # 五一：5 天
H2 = str(uuid4())   # 国庆：7 天


# ---------- 假 C 仓储 ----------


def _point(value, start: date, end: date, *, grain: str = "month",
           source_id: str = SOURCE_A) -> dict:
    return {
        "id": str(uuid4()), "version": 1, "metric": METRIC,
        "value": None if value is None else Decimal(str(value)), "unit": "人次",
        "period_start": start, "period_end": end, "grain": grain,
        "source_ids": [source_id],
        "published_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "ingested_at": datetime(2026, 7, 2, 1, tzinfo=timezone.utc),
        "quality_status": "verified", "methodology_version": "ingest-1.0",
        "provenance": {"source_id": source_id, "asset_id": str(uuid4())},
    }


def _month_points(count: int, *, start_year: int = 2024,
                  start_month: int = 1, with_hole: bool = False) -> list[dict]:
    """连续的 count 个月度点；``with_hole`` 时中间插一个 value=None 的点。"""
    points = []
    for i in range(count):
        month = (start_month - 1 + i) % 12 + 1
        year = start_year + (start_month - 1 + i) // 12
        last_day = (date(year + month // 12, month % 12 + 1, 1) - date.resolution).day \
            if False else _month_last_day(year, month)
        value = 1000 + i * 10
        if with_hole and i == count // 2:
            value = None
        points.append(_point(value, date(year, month, 1), date(year, month, last_day)))
    return points


def _month_last_day(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


def _holiday(holiday_id: str, name: str, start: date, end: date) -> dict:
    return {
        "id": holiday_id, "name": name, "kind": "may_day", "year": start.year,
        "start_date": start, "end_date": end,
        "days_count": (end - start).days + 1,
        "region_code": "000000", "scope_key": "CN:national",
        "definition_source_id": str(uuid4()), "definition_asset_id": str(uuid4()),
    }


class _FakeCStore:
    def __init__(self, *, points=None, holidays=None, unsupported=(),
                 error=None, holiday_values=None):
        self.points = points if points is not None else []
        self.holidays = holidays or {}
        self.unsupported = set(unsupported)
        self.error = error
        # {(entity, metric, holiday_id): 假期值结构}
        self.holiday_values = holiday_values or {}
        self.saved: list[dict] = []
        self.queries: list[dict] = []
        self.jobs: dict[str, dict] = {}   # idempotency_key -> job
        self.enqueued: list[dict] = []

    # --- 12 ---
    def query_series(self, *, entity_id, metric, grain, start, end,
                     quality="verified"):
        self.queries.append({"entity_id": entity_id, "metric": metric,
                             "grain": grain, "start": start, "end": end})
        if self.error is not None:
            raise self.error
        points = self.points
        return {
            "entity": {"id": entity_id, "name": "黄山风景区", "type": "scenic"},
            "metric": {"code": metric}, "grain": grain,
            "points": points, "missing_periods": [],
            "coverage_ratio": Decimal("1.0"),
        }

    def save_history_forecast(self, data: dict, points: list[dict]):
        # 把 C 侧的两条硬校验照搬进替身，D 违反就会被测试抓住（而不是等真机 500）
        if data["status"] == "ready" and len(points) != data["horizon"]:
            raise ValueError("ready 预测点数必须等于 horizon")
        if data["status"] == "data_insufficient" and (points or not data.get("reason")):
            raise ValueError("数据不足时必须无预测点并提供原因")
        self.saved.append({"data": data, "points": points})
        return {"run": dict(data), "points": points}

    # --- 14 ---
    def query_holiday_values(self, *, entity_id, metric, holiday_id):
        if self.error is not None:
            raise self.error
        if holiday_id not in self.holidays:
            raise NotFound("假期不存在")
        key = (entity_id, metric, holiday_id)
        if key not in self.holiday_values:
            raise NotFound("该假期没有可用数据")
        return self.holiday_values[key]

    # --- 29 ---
    def enqueue_ingestion(self, *, idempotency_key, payload, reason,
                          requested_by, now):
        self.enqueued.append({"idempotency_key": idempotency_key, "payload": payload,
                              "reason": reason, "requested_by": requested_by})
        if self.error is not None:
            raise self.error
        # 照搬 C 的 enqueue_or_get：同 key 命中既有作业，且 payload 不同要 Conflict
        existing = self.jobs.get(idempotency_key)
        if existing:
            if existing["payload"] != payload:
                raise Conflict("相同幂等键对应不同任务内容")
            return existing
        job = {"id": str(uuid4()), "type": "ingestion", "status": "queued",
               "idempotency_key": idempotency_key, "payload": payload,
               "reason": reason, "requested_by": requested_by,
               "processed_count": 0, "imported_count": 0}
        self.jobs[idempotency_key] = job
        return job


def _login(client: TestClient) -> str:
    resp = client.post("/api/v1/auth/login",
                       json={"username": "admin1", "password": "admin-pass-123"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["csrf_token"]


@pytest.fixture
def env(tmp_path, monkeypatch, fake_store):
    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    client = TestClient(pytest.importorskip("app.main").app)

    def install(store: _FakeCStore):
        monkeypatch.setattr("app.api.v1.forecasts.get_c_store", lambda: store)
        monkeypatch.setattr("app.api.v1.holidays.get_c_store", lambda: store)
        monkeypatch.setattr("app.api.v1.jobs.get_c_store", lambda: store)
        return store

    return {"client": client, "install": install}


# ---------- 12 历史预测 ----------


class TestHistoryForecast:
    def test_requires_login(self, env):
        store = env["install"](_FakeCStore())
        resp = env["client"].post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 1})
        assert resp.status_code == 401

    def test_bad_entity_id_is_400(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": "not-a-uuid", "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 1},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_request"

    def test_bad_training_end_is_400(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026/06/30", "horizon": 1},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text

    def test_horizon_limit_is_enforced_by_schema(self, env):
        """周最多 8、月最多 6（03 §12）；越界属请求体错误 → 400 bad_request
        （本项目约定：body 校验失败一律 400，422 只留给语义拒收）。"""
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 7},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_request"

    def test_data_insufficient_never_fakes_forecast(self, env):
        """红线①：训练数据不够 → data_insufficient + 空 points + reason，不伪装。"""
        store = env["install"](_FakeCStore(points=_month_points(2)))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 2},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "data_insufficient"
        assert body["forecast_points"] == []
        assert body["reason"], "数据不足必须给原因"
        # 红线②：落库也必须无预测点
        assert store.saved, "预测运行必须落 C 的表"
        assert store.saved[0]["points"] == []
        assert store.saved[0]["data"]["status"] == "data_insufficient"
        assert store.saved[0]["data"]["reason"]

    def test_ready_forecast_has_exactly_horizon_points(self, env):
        """30 个月度点（>24 最短训练 + 留出回测段）→ ready，点数 == horizon。"""
        store = env["install"](_FakeCStore(points=_month_points(30)))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 3},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready", body.get("reason")
        assert len(body["forecast_points"]) == 3
        for p in body["forecast_points"]:
            assert p["lower_bound"] <= p["value"] <= p["upper_bound"]
        assert body["backtest"]["sample_count"] > 0
        assert body["model_version"]

    def test_forecast_is_persisted_with_input_fingerprint(self, env):
        store = env["install"](_FakeCStore(points=_month_points(30)))
        client = env["client"]
        csrf = _login(client)
        client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 2},
            headers={"X-CSRF-Token": csrf})
        data = store.saved[0]["data"]
        assert data["entity_id"] == ENTITY and data["metric_code"] == METRIC
        assert data["grain"] == "month" and data["horizon"] == 2
        assert data["training_start"] <= data["training_end"]  # C 表有 CHECK
        assert len(data["input_sha256"]) == 64
        assert data["input_manifest"]["point_count"] == 30
        assert len(store.saved[0]["points"]) == 2

    def test_null_values_are_not_fed_as_zero(self, env):
        """红线④：value 为 None 的观测点跳过——把它当 0 会把均值拉低。"""
        store = env["install"](_FakeCStore(points=_month_points(30, with_hole=True)))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 1},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        # 30 个点里有 1 个是 None → 只喂 29 个
        assert store.saved[0]["data"]["input_manifest"]["point_count"] == 29

    def test_source_ids_come_from_observations(self, env):
        store = env["install"](_FakeCStore(points=_month_points(3)))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 1},
            headers={"X-CSRF-Token": csrf})
        assert resp.json()["source_ids"] == [SOURCE_A]

    def test_missing_entity_is_404(self, env):
        env["install"](_FakeCStore(error=NotFound("实体不存在")))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 1},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404, resp.text

    def test_metric_entity_mismatch_is_422(self, env):
        env["install"](_FakeCStore(error=MetricEntityMismatch("指标不适用")))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/forecasts/history", json={
            "entity_id": ENTITY, "metric": METRIC, "grain": "month",
            "training_end": "2026-06-30", "horizon": 1},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "metric_entity_mismatch"


# ---------- 14 假期比较 ----------


def _may_day() -> dict:
    return _holiday(H1, "2026 年五一假期", date(2026, 5, 1), date(2026, 5, 5))


def _national_day() -> dict:
    return _holiday(H2, "2026 年国庆假期", date(2026, 10, 1), date(2026, 10, 7))


def _daily(start: date, days: int, *, skip=(), base=1000) -> list[dict]:
    from datetime import timedelta

    points = []
    for i in range(days):
        day = start + timedelta(days=i)
        if i in skip:
            continue
        points.append(_point(base + i * 10, day, day, grain="day"))
    return points


def _hv(holiday: dict, *, start: date | None = None, days: int | None = None,
        skip=(), total=None, base=1000):
    """拼一个 ``query_holiday_values`` 的返回，模拟 C 的口径判断。"""
    from datetime import timedelta

    start = start or holiday["start_date"]
    days = days or holiday["days_count"]
    daily_points = _daily(start, days, skip=skip, base=base)
    missing = [start + timedelta(days=i) for i in skip]
    complete = not missing
    daily_sum = (sum(Decimal(str(base + i * 10)) for i in range(days)
                     if i not in skip) if complete else None)
    return {
        "entity": {"id": ENTITY, "name": "黄山风景区", "type": "scenic"},
        "metric": {"code": METRIC}, "holiday": holiday,
        "exact_total": ({"value": Decimal(str(total)), "unit": "人次",
                         # C 的 exact_total 就是一条 ``_point``，**带 source_ids**
                         "source_ids": [SOURCE_B if total is not None else SOURCE_A]}
                        if total is not None else None),
        "daily_points": daily_points,
        "daily_sum": daily_sum,
        # C 的日均 = 假期总量 / 假期天数（真实总量，不是缺失日补零）
        "daily_average": (Decimal(str(total)) / days) if total is not None else None,
        "day_index_supported": complete,
        "daily_grain_supported": True,
        "missing_days": missing,
        "diagnostics": [],
        "scope_key": "CN:national_general_public",
        "scope_confirmation_required": False,
    }


class TestHolidayCompare:
    def test_requires_login(self, env):
        env["install"](_FakeCStore())
        resp = env["client"].post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"})
        assert resp.status_code == 401

    def test_bad_holiday_id_is_400(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": ["nope", H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_request"

    def test_missing_holiday_is_404(self, env):
        env["install"](_FakeCStore(holidays={H1: _may_day()}))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404, resp.text

    def test_period_total_uses_c_exact_total(self, env):
        """总量优先用 C 给的假期总量观测（grain=holiday），不自己加日点。"""
        may, nat = _may_day(), _national_day()
        env["install"](_FakeCStore(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may, total=51000),
                (ENTITY, METRIC, H2): _hv(nat, total=71400),
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["comparable"] is True
        assert body["results"][0]["values"] == [51000.0]
        assert body["results"][1]["values"] == [71400.0]
        assert body["results"][0]["missing_days"] == 0

    def test_period_total_falls_back_to_daily_sum(self, env):
        """没有假期总量观测时，用 C 的 daily_sum——C 只在日数据完整时给，
        缺天时为 None，此时 D 不给值而不是自己加一遍。"""
        may, nat = _may_day(), _national_day()

        class _NoTotal(_FakeCStore):
            def query_holiday_values(self, *, entity_id, metric, holiday_id):
                row = super().query_holiday_values(entity_id=entity_id, metric=metric,
                                                   holiday_id=holiday_id)
                row["exact_total"] = None
                row["daily_average"] = None
                return row

        env["install"](_NoTotal(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may),
                (ENTITY, METRIC, H2): _hv(nat, base=2000),
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        assert body["results"][0]["values"] == [float(sum(1000 + i * 10 for i in range(5)))]
        # 缺天的假期：C 的 daily_sum 是 None → D 不给值
        env2 = _NoTotal(holidays={H1: may, H2: nat}, holiday_values={
            (ENTITY, METRIC, H1): _hv(may, skip=(2,)),
            (ENTITY, METRIC, H2): _hv(nat, base=2000),
        })
        env["install"](env2)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        assert body["results"][0]["values"] == [], "缺天时没有可信总量，不自己加日点"
        assert body["results"][0]["missing_days"] == 1

    def test_daily_average_uses_c_value_not_own_sum(self, env):
        """口径归 C：日均 = 假期总量 / 假期天数。自己按「sum/有数据天数」算
        会在缺天时高估，所以必须用 C 给的。"""
        may, nat = _may_day(), _national_day()
        env["install"](_FakeCStore(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may, total=51000),   # 51000/5 = 10200
                (ENTITY, METRIC, H2): _hv(nat, total=71400),   # 71400/7 = 10200
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "daily_average"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        assert body["results"][0]["values"] == [10200.0]
        assert body["results"][1]["values"] == [10200.0]

    def test_daily_average_without_exact_total_gives_no_value(self, env):
        """C 021a2《27-契约收口》：日均只能是「精确总量 ÷ 假期天数」。

        之前这里有个「用现有 daily_points 求均值」的兜底——缺天时分母变小，
        等于**高估日均**。现在总量不可得就一条都不给。
        """
        may, nat = _may_day(), _national_day()

        class _NoTotal(_FakeCStore):
            def query_holiday_values(self, *, entity_id, metric, holiday_id):
                row = super().query_holiday_values(entity_id=entity_id, metric=metric,
                                                   holiday_id=holiday_id)
                row["exact_total"] = None
                row["daily_average"] = None
                return row

        env["install"](_NoTotal(holidays={H1: may, H2: nat}, holiday_values={
            (ENTITY, METRIC, H1): _hv(may, skip=(1, 3)),
            (ENTITY, METRIC, H2): _hv(nat, base=2000),
        }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "daily_average"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        first = body["results"][0]
        assert first["missing_days"] == 2
        assert first["values"] == [], "没有精确总量就不许给日均（否则缺天会高估）"
        assert body["comparable"] is False
        assert "缺 2 天" in body["reason"]

    def test_day_index_requires_complete_daily_data(self, env):
        """红线①：C 判 day_index_supported=False → 整条不给指数曲线。"""
        may, nat = _may_day(), _national_day()
        env["install"](_FakeCStore(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may, skip=(2,)),   # 缺 1 天 → 不支持
                (ENTITY, METRIC, H2): _hv(nat, base=2000),
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "day_index"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        assert body["comparable"] is False
        assert body["results"][0]["values"] == [], "数据不完整就不给指数曲线"
        assert "day_index" in body["reason"]

    def test_day_index_when_complete(self, env):
        may, nat = _may_day(), _national_day()
        env["install"](_FakeCStore(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may),
                (ENTITY, METRIC, H2): _hv(nat, base=2000),
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "day_index"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        assert body["comparable"] is True
        assert body["results"][0]["values"][0] == 100.0  # 首日即基期
        assert len(body["results"][0]["values"]) == 5
        assert len(body["results"][1]["values"]) == 7

    def test_total_only_source_is_not_expanded_into_daily(self, env):
        """红线②：C 判 daily_grain_supported=False（来源只有假期总量）→
        不展开成逐日曲线，且 reason 说明原因。"""
        may, nat = _may_day(), _national_day()

        class _TotalOnly(_FakeCStore):
            def query_holiday_values(self, *, entity_id, metric, holiday_id):
                row = super().query_holiday_values(entity_id=entity_id, metric=metric,
                                                   holiday_id=holiday_id)
                row["daily_grain_supported"] = False
                row["daily_points"] = []
                row["day_index_supported"] = False
                row["daily_sum"] = None
                return row

        env["install"](_TotalOnly(holidays={H1: may, H2: nat}, holiday_values={
            (ENTITY, METRIC, H1): _hv(may, total=51000),
            (ENTITY, METRIC, H2): _hv(nat, total=71400),
        }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "day_index"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["comparable"] is False
        assert all(not r["values"] for r in body["results"]), "不得摊出任何日值"
        assert "不支持日粒度" in body["reason"]
        # 但 period_total 仍可用：假期总量是真实观测
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        assert resp.json()["results"][0]["values"] == [51000.0]

    def test_scope_confirmation_is_reported(self, env):
        """地区性假期：C 要求确认覆盖范围，D 必须把这条透到 reason。"""
        may, nat = _may_day(), _national_day()

        class _Scoped(_FakeCStore):
            def query_holiday_values(self, *, entity_id, metric, holiday_id):
                row = super().query_holiday_values(entity_id=entity_id, metric=metric,
                                                   holiday_id=holiday_id)
                row["scope_key"] = "CN:regional:341000"
                row["scope_confirmation_required"] = True
                return row

        env["install"](_Scoped(holidays={H1: may, H2: nat}, holiday_values={
            (ENTITY, METRIC, H1): _hv(may, total=51000),
            (ENTITY, METRIC, H2): _hv(nat, total=71400),
        }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        assert "覆盖范围需确认" in resp.json()["reason"]

    def test_source_ids_and_missing_days_reported(self, env):
        may, nat = _may_day(), _national_day()
        env["install"](_FakeCStore(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may, skip=(4,)),
                (ENTITY, METRIC, H2): _hv(nat, base=2000),
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        body = resp.json()
        first = body["results"][0]
        assert first["source_ids"] == [SOURCE_A]
        assert first["missing_days"] == 1
        assert first["values"] == [], "缺天 → 没有可信总量"

    def test_source_ids_include_exact_total_not_only_daily_points(self, env):
        """C 021a2《27-契约收口》：来源必须包含 exact_total 的来源。

        period_total / daily_average 的依据是**假期总量观测**，只从
        daily_points 取来源会漏掉真正的取证对象。
        """
        may, nat = _may_day(), _national_day()
        env["install"](_FakeCStore(
            holidays={H1: may, H2: nat},
            holiday_values={
                (ENTITY, METRIC, H1): _hv(may, total=55000),
                (ENTITY, METRIC, H2): _hv(nat, total=71400, base=2000),
            }))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY, "metric": METRIC}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        sources = resp.json()["results"][0]["source_ids"]
        assert SOURCE_B in sources, "假期总量的来源必须在列"
        assert SOURCE_A in sources, "日点的来源也不能丢"

    def test_series_must_have_metric(self, env):
        env["install"](_FakeCStore(holidays={H1: _may_day(), H2: _national_day()}))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/holidays/compare", json={
            "holiday_ids": [H1, H2], "series": [{"entity_id": ENTITY}],
            "mode": "period_total"}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text


# ---------- 29 采集作业 ----------


class TestIngestionJob:
    def test_requires_login(self, env):
        env["install"](_FakeCStore())
        resp = env["client"].post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4())], "reason": "手动补采"})
        assert resp.status_code == 401

    def test_requires_csrf(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        _login(client)
        resp = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4())], "reason": "手动补采"})
        assert resp.status_code in (401, 403), resp.text

    def test_enqueue_returns_202_queued(self, env):
        store = env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4())], "reason": "手动补采"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["job_id"]
        # dry_run 默认 true（03 §29）：默认只检查不写库
        assert store.enqueued[0]["payload"]["dry_run"] is True

    def test_bad_source_id_is_400(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": ["nope"], "reason": "手动补采"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_request"

    def test_reason_is_required(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4())]},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text

    def test_source_ids_limited_to_50(self, env):
        env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4()) for _ in range(51)], "reason": "批量"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text

    def test_same_sources_hit_same_job(self, env):
        """幂等：同一批来源 + 同一 dry_run 重复触发命中同一个作业，不重复排队。"""
        store = env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        sid = str(uuid4())
        first = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [sid], "reason": "第一次"},
            headers={"X-CSRF-Token": csrf}).json()["job_id"]
        second = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [sid], "reason": "换个理由再触发"},
            headers={"X-CSRF-Token": csrf}).json()["job_id"]
        assert first == second, "reason 不该影响幂等键"
        assert len(store.jobs) == 1

    def test_dry_run_changes_the_job(self, env):
        """dry_run 不同视为不同作业（一个只检查、一个真写库）。"""
        store = env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        sid = str(uuid4())
        a = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [sid], "reason": "先干跑"},
            headers={"X-CSRF-Token": csrf}).json()["job_id"]
        b = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [sid], "dry_run": False, "reason": "确认后真跑"},
            headers={"X-CSRF-Token": csrf}).json()["job_id"]
        assert a != b
        assert len(store.jobs) == 2

    def test_conflicting_payload_is_409(self, env):
        """C 的 Conflict：同幂等键对应不同任务内容 → 409。"""
        env["install"](_FakeCStore(error=Conflict("相同幂等键对应不同任务内容")))
        client = env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4())], "reason": "手动补采"},
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 409, resp.text

    def test_requested_by_is_recorded(self, env):
        """作业要留痕：谁触发的必须写进去。"""
        store = env["install"](_FakeCStore())
        client = env["client"]
        csrf = _login(client)
        client.post("/api/v1/jobs/ingestion", json={
            "source_ids": [str(uuid4())], "reason": "手动补采"},
            headers={"X-CSRF-Token": csrf})
        assert store.enqueued[0]["requested_by"], "必须记录操作者"
        assert store.enqueued[0]["reason"] == "手动补采"
