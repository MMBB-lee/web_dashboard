"""10 驾驶舱总览 / 11 序列比较（接 C 0.2.0a2 的 catalog / observations / events）。

10 守的四条：

① **不同指标分卡，绝不求和成「总客流」**（03 §10 红线）：每个指标一张卡，
   一次合计只碰同一个指标；
② **同一指标跨实体求和只加周期完全一致的点**（C 指标字典
   ``aggregation_rule = complete_non_overlapping_periods_only``）——周期对不上
   的实体不计入合计，但体现在 ``status=partial`` / ``covered_entity_count`` 上，
   不静默当它不存在；
③ **没数据就 null**：``status=no_data``，不补零、不取均值、不用上一周期；
④ **单个实体不支持该粒度不该毁掉整张看板**：``UnsupportedGrain`` 在总览里
   降级为「该实体无数据」，不是 422。

11 守的四条：

① **混合指标不能同轴原始值比较**：``index_100`` 下点位值换成无量纲指数、
   ``unit`` 置 ``index``；
② **基期取不到有效值就整条 400**，绝不返回一半指数一半原始值；
③ **口径错误由 C 判、D 翻译**：指标-实体不匹配 → 422 ``metric_entity_mismatch``、
   不支持粒度 → 422 ``unsupported_grain``；
④ ``comparison_note`` 必须写清涉及哪些指标、C 的可比性口径原文、每条序列
   覆盖了多少期。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings

from data_foundation.errors import MetricEntityMismatch, NotFound, UnsupportedGrain

SOURCE_A = str(uuid4())
SOURCE_B = str(uuid4())

SCENIC_A = str(uuid4())
SCENIC_B = str(uuid4())
STATION = str(uuid4())

M1_START, M1_END = date(2026, 8, 1), date(2026, 8, 31)
M2_START, M2_END = date(2026, 9, 1), date(2026, 9, 30)


# ---------- 假 C 仓储 ----------


def _entity(entity_id: str, name: str, type_: str,
            lon=118.17, lat=30.13) -> dict:
    return {"id": entity_id, "name": name, "type": type_, "region_code": "341000",
            # C 用 Numeric(10,7) 存经纬度 → Decimal，必须能转 float
            "longitude": Decimal(str(lon)), "latitude": Decimal(str(lat)),
            "available_metrics": [], "available_grains": [], "status": "active",
            "supported_metrics": [], "supported_grains": []}


def _metric(code: str, display: str, types: list[str], note: str) -> dict:
    return {"code": code, "display_name": display, "unit": "人次",
            "supported_entity_types": types, "definition": "定义",
            "aggregation_rule": "complete_non_overlapping_periods_only",
            "comparability_note": note, "dictionary_version": "v0.1.0"}


def _point(value, start: date, end: date, *, metric: str = "scenic_visits",
           source_id: str = SOURCE_A, grain: str = "month") -> dict:
    return {
        "id": str(uuid4()), "version": 1, "metric": metric,
        "value": Decimal(str(value)), "unit": "人次",
        "period_start": start, "period_end": end, "grain": grain,
        "source_ids": [source_id],
        "published_at": datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
        "ingested_at": datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc),
        "quality_status": "verified", "methodology_version": "ingest-1.0",
        # 取数细节：不进响应
        "provenance": {"source_id": source_id, "asset_id": str(uuid4()),
                       "source_url": "https://example.gov.cn/x",
                       "asset_url": "https://example.gov.cn/x.csv",
                       "source_locator": "row 12", "run_id": str(uuid4())},
    }


class _FakeCStore:
    """按 (entity_id, metric) 预置结果，让每条用例都能精确控制数据形态。"""

    def __init__(self, *, entities, metrics, latest=None, unsupported=(),
                 events=None, series=None, error=None, total=None):
        self.entities = entities
        self.metrics = metrics
        self.latest = latest or {}
        self.unsupported = set(unsupported)
        self.events = events or []
        self.series = series or {}
        self.error = error
        self.total = len(entities) if total is None else total

    def list_entities(self, *, type=None, region_code=None, q=None,
                      page=1, page_size=20):
        # 与 C 的 contracts.Page 一致：page_size 超过 100 直接 ValueError
        # （不在 C 异常族里，会漏成 500）。2026-10-17 真机就是这么炸的。
        if not 1 <= page_size <= 100:
            raise ValueError("page 从 1 开始，page_size 必须在 1—100 之间")
        return {"items": self.entities, "page": page,
                "page_size": page_size, "total": self.total}

    def list_metrics(self, entity_type=None):
        return self.metrics

    def get_latest_verified(self, *, entity_id, metric, grain, as_of):
        if self.error is not None:
            raise self.error
        if (entity_id, metric) in self.unsupported:
            raise UnsupportedGrain("该序列不支持该粒度")
        return self.latest.get((entity_id, metric))

    def list_events(self, *, region_code=None, status=None):
        return self.events

    def query_series(self, *, entity_id, metric, grain, start, end,
                     quality="verified"):
        if self.error is not None:
            raise self.error
        preset = self.series.get((entity_id, metric))
        if preset is None:
            preset = {"points": [], "missing_periods": []}
        entity = next((e for e in self.entities if e["id"] == entity_id), None)
        metric_row = next((m for m in self.metrics if m["code"] == metric), None)
        return {
            "entity": {"id": entity_id,
                       "name": (entity or {}).get("name", "?"),
                       "type": (entity or {}).get("type", "?")},
            "metric": metric_row or {"code": metric},
            "grain": grain,
            "points": preset["points"],
            "missing_periods": preset["missing_periods"],
            "coverage_ratio": Decimal("0.5"),
            "diagnostics": [{"code": "debug-only"}],
            "selection_policy_version": "policy-1",
        }


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
        monkeypatch.setattr("app.api.v1.dashboard.get_c_store", lambda: store)
        monkeypatch.setattr("app.api.v1.analytics.get_c_store", lambda: store)
        return store

    return {"client": client, "install": install}


def _default_store(**kwargs) -> _FakeCStore:
    """两个景区 + 一个车站，8 月各 100/200 人次、铁路 5000 人次。"""
    entities = [_entity(SCENIC_A, "黄山风景区", "scenic"),
                _entity(SCENIC_B, "宏村", "scenic"),
                _entity(STATION, "黄山北站", "rail_station")]
    metrics = [
        _metric("scenic_visits", "景区接待人次", ["scenic"],
                "不同景区和时期须核对统计范围；不得与交通客流直接相加"),
        _metric("rail_departures", "铁路发送人次", ["rail_station"],
                "发送量不是到达量或双向吞吐量"),
        _metric("airport_throughput", "机场旅客吞吐量", ["airport"], "不可与铁路相加"),
    ]
    latest = {
        (SCENIC_A, "scenic_visits"): _point(100, M1_START, M1_END),
        (SCENIC_B, "scenic_visits"): _point(200, M1_START, M1_END),
        (STATION, "rail_departures"): _point(5000, M1_START, M1_END,
                                             metric="rail_departures",
                                             source_id=SOURCE_B),
    }
    return _FakeCStore(entities=entities, metrics=metrics, latest=latest, **kwargs)


# ---------- 10 驾驶舱总览 ----------


class TestOverview:
    def test_requires_login(self, env):
        env["install"](_default_store())
        resp = env["client"].get("/api/v1/dashboard/overview",
                                 params={"region_code": "341000", "grain": "month"})
        assert resp.status_code == 401

    def test_rejects_bad_grain(self, env):
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "day"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_request"

    def test_rejects_empty_region(self, env):
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "   ", "grain": "month"})
        assert resp.status_code == 400, resp.text

    def test_rejects_bad_as_of(self, env):
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month",
                                  "as_of": "2026/08/01"})
        assert resp.status_code == 400, resp.text

    def test_cards_are_per_metric_and_never_summed_across_metrics(self, env):
        """红线①：景区 300、铁路 5000 各一张卡，绝不会出现 5300 这种总数。"""
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        cards = {c["metric"]: c for c in data["cards"]}
        assert set(cards) == {"scenic_visits", "rail_departures"}
        assert cards["scenic_visits"]["value"] == 300  # 100 + 200，同一指标同周期
        assert cards["rail_departures"]["value"] == 5000
        assert cards["scenic_visits"]["status"] == "ok"
        assert cards["scenic_visits"]["covered_entity_count"] == 2
        # 机场指标在该地区没有适用实体 → 不出卡
        assert "airport_throughput" not in cards

    def test_different_periods_are_not_summed(self, env):
        """红线②：卡片取**最新**周期，周期对不上的实体不计入合计。

        宏村已经更新到 9 月、黄山还停在 8 月：9 月的 999 才是「最新周期」，
        黄山的 100 属于不同统计范围，不能混进来——所以合计是 999 而不是 1099。
        缺了谁由 ``status=partial`` 与两个计数说清楚。
        """
        store = _default_store()
        store.latest[(SCENIC_B, "scenic_visits")] = _point(999, M2_START, M2_END)
        env["install"](store)
        client = env["client"]
        _login(client)
        data = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}
                          ).json()["data"]
        card = next(c for c in data["cards"] if c["metric"] == "scenic_visits")
        assert card["value"] == 999
        assert card["status"] == "partial"
        assert card["entity_count"] == 2
        assert card["covered_entity_count"] == 1
        assert card["period_start"] == "2026-09-01"

    def test_no_data_card_is_null_not_zero(self, env):
        """红线③：没有数据就是 null，不补零。"""
        env["install"](_FakeCStore(
            entities=[_entity(SCENIC_A, "黄山风景区", "scenic")],
            metrics=[_metric("scenic_visits", "景区接待人次", ["scenic"], "n")],
            latest={}))
        client = env["client"]
        _login(client)
        data = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}
                          ).json()["data"]
        card = data["cards"][0]
        assert card["value"] is None
        assert card["status"] == "no_data"
        assert card["period_start"] is None and card["period_end"] is None
        assert card["source_ids"] == []

    def test_unsupported_grain_degrades_not_422(self, env):
        """红线④：一个实体缺 month 不该毁掉整张看板。"""
        env["install"](_default_store(unsupported=[(SCENIC_B, "scenic_visits")]))
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"})
        assert resp.status_code == 200, resp.text
        card = next(c for c in resp.json()["data"]["cards"]
                    if c["metric"] == "scenic_visits")
        assert card["value"] == 100
        assert card["status"] == "partial"

    def test_map_entities_tolerates_null_coordinates(self, env):
        store = _default_store()
        store.entities[1] = _entity(SCENIC_B, "宏村", "scenic", ) | {
            "longitude": None, "latitude": None}
        env["install"](store)
        client = env["client"]
        _login(client)
        data = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}
                          ).json()["data"]
        macun = next(m for m in data["map_entities"] if m["name"] == "宏村")
        assert macun["longitude"] is None and macun["latitude"] is None
        # 坐标可以没有，但最新值照给
        assert macun["latest_value"] == 200
        assert macun["metric"] == "scenic_visits"
        # 取不到点的实体也要在地图上，且 metric/value 是 null，不是 0
        assert all(m["latest_value"] is None or m["metric"] is not None
                   for m in data["map_entities"])

    def test_active_events_count_and_freshness(self, env):
        env["install"](_default_store(events=[{"id": str(uuid4())},
                                              {"id": str(uuid4())}]))
        client = env["client"]
        _login(client)
        data = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}
                          ).json()["data"]
        assert data["active_events_count"] == 2
        # 新鲜度只列真正取到点的实体（3 个实体都有数据 → 3 条）
        assert len(data["data_freshness"]) == 3
        assert all(f["ingested_at"].endswith("+08:00")
                   for f in data["data_freshness"])

    def test_as_of_defaults_to_latest_data_period(self, env):
        """03 §10：不传 as_of 时回显**数据实际截至日**，不是今天。"""
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        data = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}
                          ).json()["data"]
        assert data["as_of"] == "2026-08-31"

    def test_as_of_echoes_when_given(self, env):
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        data = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month",
                                  "as_of": "2026-09-30"}).json()["data"]
        assert data["as_of"] == "2026-09-30"

    def test_rejects_too_many_entities(self, env):
        env["install"](_default_store(total=5000))
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "340000", "grain": "month"})
        assert resp.status_code == 400, resp.text
        assert "更精确的 region_code" in resp.json()["error"]["message"]

    def test_page_size_never_exceeds_c_limit(self, env):
        """回归：C 的 Page 只允许 1—100；传 200 会 ValueError→500（真机踩过）。"""
        store = _default_store()
        seen: list[int] = []
        original = store.list_entities

        def spy(**kwargs):
            seen.append(kwargs["page_size"])
            return original(**kwargs)

        store.list_entities = spy
        env["install"](store)
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"})
        assert resp.status_code == 200, resp.text
        assert seen and max(seen) <= 100, seen

    def test_c_error_is_translated(self, env):
        env["install"](_default_store(error=NotFound("实体不存在")))
        client = env["client"]
        _login(client)
        resp = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"})
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"

    def test_envelope_and_request_id(self, env):
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        body = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}).json()
        assert body["meta"]["request_id"]

    def test_provenance_details_not_leaked(self, env):
        env["install"](_default_store())
        client = env["client"]
        _login(client)
        text = client.get("/api/v1/dashboard/overview",
                          params={"region_code": "341000", "grain": "month"}).text
        for leaked in ("asset_url", "source_locator", "diagnostics",
                       "selection_policy_version"):
            assert leaked not in text, leaked


# ---------- 11 序列比较 ----------


def _series_store(**kwargs) -> _FakeCStore:
    store = _default_store(**kwargs)
    store.series = {
        (SCENIC_A, "scenic_visits"): {
            "points": [_point(100, M1_START, M1_END), _point(150, M2_START, M2_END)],
            "missing_periods": [],
        },
        (SCENIC_B, "scenic_visits"): {
            "points": [_point(200, M1_START, M1_END), _point(300, M2_START, M2_END)],
            "missing_periods": [],
        },
        (STATION, "rail_departures"): {
            "points": [_point(5000, M1_START, M1_END, metric="rail_departures",
                              source_id=SOURCE_B),
                       _point(6000, M2_START, M2_END, metric="rail_departures",
                              source_id=SOURCE_B)],
            "missing_periods": [],
        },
    }
    return store


def _post(client: TestClient, csrf: str, payload: dict):
    return client.post("/api/v1/analytics/compare", json=payload,
                       headers={"X-CSRF-Token": csrf})


BASE_BODY = {
    "series": [{"entity_id": SCENIC_A, "metric": "scenic_visits"},
               {"entity_id": SCENIC_B, "metric": "scenic_visits"}],
    "grain": "month", "start": "2026-08-01", "end": "2026-09-30",
    "display_mode": "separate_axes",
}


class TestCompare:
    def test_requires_csrf(self, env):
        env["install"](_series_store())
        client = env["client"]
        _login(client)  # 登录但不带 CSRF
        assert _post(client, "", BASE_BODY).status_code == 403

    def test_rejects_less_than_two_series(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, series=[{"entity_id": SCENIC_A,
                                        "metric": "scenic_visits"}])
        assert _post(client, csrf, body).status_code == 400

    def test_rejects_more_than_four_series(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, series=[{"entity_id": SCENIC_A,
                                        "metric": "scenic_visits"}] * 5)
        assert _post(client, csrf, body).status_code == 400

    def test_rejects_bad_grain(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        assert _post(client, csrf, dict(BASE_BODY, grain="frame")).status_code == 400

    def test_rejects_start_after_end(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, start="2026-09-30", end="2026-08-01")
        assert _post(client, csrf, body).status_code == 400

    def test_rejects_bad_entity_id(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, series=[{"entity_id": "not-a-uuid",
                                        "metric": "scenic_visits"},
                                       {"entity_id": SCENIC_B,
                                        "metric": "scenic_visits"}])
        assert _post(client, csrf, body).status_code == 400

    def test_index_100_requires_base_period(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, display_mode="index_100")
        assert _post(client, csrf, body).status_code == 400

    def test_separate_axes_keeps_raw_values(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        resp = _post(client, csrf, BASE_BODY)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["display_mode"] == "separate_axes"
        first = data["series"][0]
        assert [p["value"] for p in first["points"]] == [100.0, 150.0]
        assert first["unit"] == "人次"
        assert first["base_value"] is None
        assert set(first["source_ids"]) == {SOURCE_A}

    def test_index_100_normalises_to_base(self, env):
        """红线①：指数无量纲，unit 置 index，原始单位移到序列级。"""
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, display_mode="index_100", base_period="2026-08-01")
        resp = _post(client, csrf, body)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        first, second = data["series"][0], data["series"][1]
        assert [p["value"] for p in first["points"]] == [100.0, 150.0]  # 100→100, 150→150
        assert [p["value"] for p in second["points"]] == [100.0, 150.0]  # 200→100, 300→150
        assert all(p["unit"] == "index" for p in first["points"])
        assert first["unit"] == "人次"  # 原始单位保留在序列级
        assert first["base_value"] == 100.0
        assert second["base_value"] == 200.0

    def test_index_100_rejects_missing_base_value(self, env):
        """红线②：基期取不到值就整条 400，不返回半指数半原始值。"""
        store = _series_store()
        store.series[(SCENIC_B, "scenic_visits")] = {
            "points": [_point(200, M2_START, M2_END)],  # 8 月没有点
            "missing_periods": [],
        }
        env["install"](store)
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, display_mode="index_100", base_period="2026-08-01")
        resp = _post(client, csrf, body)
        assert resp.status_code == 400, resp.text
        assert "无法指数化" in resp.json()["error"]["message"]

    def test_metric_entity_mismatch_is_422(self, env):
        """红线③：口径由 C 判，D 只翻译。"""
        env["install"](_series_store(error=MetricEntityMismatch("指标不适用于该实体")))
        client = env["client"]
        csrf = _login(client)
        resp = _post(client, csrf, BASE_BODY)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "metric_entity_mismatch"

    def test_unsupported_grain_is_422(self, env):
        env["install"](_series_store(error=UnsupportedGrain("来源不支持该粒度")))
        client = env["client"]
        csrf = _login(client)
        resp = _post(client, csrf, BASE_BODY)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "unsupported_grain"

    def test_comparison_note_explains_comparability(self, env):
        """红线④：note 要写清指标、C 的可比性口径原文、各序列覆盖度。"""
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = dict(BASE_BODY, series=[
            {"entity_id": SCENIC_A, "metric": "scenic_visits"},
            {"entity_id": STATION, "metric": "rail_departures"}])
        data = _post(client, csrf, body).json()["data"]
        note = data["comparison_note"]
        assert "不同指标" in note
        assert "不得把原始值画到同一数值轴" in note
        # C 指标字典的 comparability_note 原文要透传
        assert "不得与交通客流直接相加" in note
        assert "发送量不是到达量或双向吞吐量" in note
        assert "2/2 期有数据" in note

    def test_missing_periods_are_reported(self, env):
        store = _series_store()
        store.series[(SCENIC_A, "scenic_visits")] = {
            "points": [_point(100, M1_START, M1_END)],
            "missing_periods": [{"period_start": M2_START, "period_end": M2_END}],
        }
        env["install"](store)
        client = env["client"]
        csrf = _login(client)
        data = _post(client, csrf, BASE_BODY).json()["data"]
        first = data["series"][0]
        assert first["missing_periods"] == [{"period_start": "2026-09-01",
                                             "period_end": "2026-09-30"}]

    def test_source_ids_are_union_of_points(self, env):
        store = _series_store()
        store.series[(SCENIC_A, "scenic_visits")] = {
            "points": [_point(100, M1_START, M1_END, source_id=SOURCE_A),
                       _point(150, M2_START, M2_END, source_id=SOURCE_B)],
            "missing_periods": [],
        }
        env["install"](store)
        client = env["client"]
        csrf = _login(client)
        data = _post(client, csrf, BASE_BODY).json()["data"]
        assert set(data["series"][0]["source_ids"]) == {SOURCE_A, SOURCE_B}

    def test_envelope_and_request_id(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        body = _post(client, csrf, BASE_BODY).json()
        assert body["meta"]["request_id"]

    def test_provenance_details_not_leaked(self, env):
        env["install"](_series_store())
        client = env["client"]
        csrf = _login(client)
        text = _post(client, csrf, BASE_BODY).text
        for leaked in ("provenance", "asset_url", "source_locator",
                       "diagnostics", "selection_policy_version"):
            assert leaked not in text, leaked
