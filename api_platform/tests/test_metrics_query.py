"""19 号问答的受约束数值查询工具（E ``metric_tool`` 注入契约）。

守的是三条硬约束：
① E 的 metric 分支要求返回六个键且 ``source_ids`` 非空，否则 ``ValueError``
   → D 500；所以 D 要么给全，要么明确报不足，**不能给半个成功**；
② 数据源没接通就报不足，绝不用历史资料/旧快照顶替当前数值；
③ 只有请求显式给 ``metric`` 才注入，否则知识库问答不能被数值分支吞掉。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.services import metrics_query as mq
from data_foundation.errors import (InvalidArgument, MetricEntityMismatch, NotFound,
                                    QueryLimitExceeded, UnsupportedGrain)


def _login_admin(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin1", "password": "admin-pass-123"},
    )
    return resp.json()["data"]["csrf_token"]


# ---------- 指标字典 ----------


def test_catalog_matches_interface_spec_metric_dictionary():
    """03-接口总览 §1 的四个指标必须都在（且只有这四个）。"""
    assert set(mq.METRIC_CATALOG) == {
        "scenic_visits",
        "rail_departures",
        "airport_throughput",
        "camera_visible_people",
    }
    assert mq.METRIC_CATALOG["camera_visible_people"]["unit"] == "人"
    for spec in mq.METRIC_CATALOG.values():
        assert spec["unit"] in ("人次", "人")
        assert spec["entity_types"]
        assert spec["grains"]
        assert spec["source"]


def test_every_catalog_entry_declares_unit_and_blocker():
    """每个指标都要写明单位、来源和责任方——回执与验收要逐项核对。"""
    for metric, spec in mq.METRIC_CATALOG.items():
        assert spec["unit"], metric
        assert spec["source"], metric
        assert "blocked_by" in spec, metric


# ---------- normalise_query：什么情况下才注入 ----------


def test_no_metric_means_no_injection():
    """不给 metric 就不注入（否则知识库问答会被 E 的 metric 分支整条掐掉）。"""
    assert mq.normalise_query(None) is None
    assert mq.normalise_query({}) is None
    assert mq.normalise_query({"entity_ids": [str(uuid4())], "grain": "day"}) is None


def test_unknown_metric_is_not_injected():
    assert mq.normalise_query({"metric": "visits"}) is None
    assert mq.normalise_query({"metric": "scenic_visits; DROP TABLE"}) is None


def test_valid_metric_builds_allowlisted_query():
    query = mq.normalise_query({
        "metric": "scenic_visits",
        "entity_ids": [str(uuid4())],
        "grain": "week",
        "period_start": "2026-09-01T00:00:00+08:00",
        "period_end": "2026-09-30T00:00:00+08:00",
    })
    assert query is not None
    assert set(query) == {
        "metric", "entity_type", "entity_ids", "grain", "period_start", "period_end",
    }
    assert query["entity_type"] == "scenic"
    assert query["grain"] == "week"


# ---------- metric_tool：拒绝与「不足」，绝不伪造 ----------


def _valid_query(**overrides):
    query = {
        "metric": "scenic_visits",
        "entity_type": "scenic",
        "entity_ids": [str(uuid4())],
        "grain": "day",
        "period_start": "2026-09-01T00:00:00+08:00",
        "period_end": "2026-09-30T00:00:00+08:00",
    }
    query.update(overrides)
    return query


@pytest.mark.parametrize("query, reason", [
    ({"metric": "visits"}, mq.REASON_UNSUPPORTED_METRIC),
    (_valid_query(metric="nope"), mq.REASON_UNSUPPORTED_METRIC),
    (_valid_query(grain="5min"), mq.REASON_UNSUPPORTED_GRAIN),
    (_valid_query(grain="holiday"), mq.REASON_UNSUPPORTED_GRAIN),
    (_valid_query(entity_ids=[]), mq.REASON_INVALID_ENTITY),
    (_valid_query(entity_ids=["not-a-uuid"]), mq.REASON_INVALID_ENTITY),
    (_valid_query(entity_ids=[str(uuid4()) for _ in range(mq.MAX_ENTITY_IDS + 1)]),
     mq.REASON_INVALID_ENTITY),
    (_valid_query(period_start=None), mq.REASON_INVALID_PERIOD),
    # 无时区的日期串现在是**合法**的（按 +08:00 业务日读，见 ``test_date_only_period_is_read_as_business_day``）；
    # 要证非法只能用完全不合 ISO 8601 的串。
    (_valid_query(period_start="yesterday"), mq.REASON_INVALID_PERIOD),
    (_valid_query(period_start="2026-13-45"), mq.REASON_INVALID_PERIOD),
    (_valid_query(period_start="2026-10-01T00:00:00+08:00"), mq.REASON_INVALID_PERIOD),
])
def test_rejections_never_carry_a_value(query, reason):
    """非法查询一律报不足，**且绝不带 value / source_ids**（防半个成功）。"""
    result = mq.metric_tool(query)
    assert result["status"] == "metric_tool_unavailable"
    assert result["reason"] == reason
    assert "value" not in result
    assert "source_ids" not in result


def test_metric_tool_rejects_non_dict():
    assert mq.metric_tool("scenic_visits")["reason"] == mq.REASON_INVALID_ENTITY


def test_available_metric_still_reports_insufficient_not_a_fabricated_number():
    """E 0.6.2：失败结果不得携带成功响应的六个必填字段。"""
    result = mq.metric_tool(_valid_query())
    assert result["status"] == "metric_tool_unavailable"
    assert result["reason"] == mq.REASON_DATA_SOURCE_UNAVAILABLE
    assert result["unit"] == "人次"
    assert result["grain"] == "day"
    assert not {"metric", "value", "source_ids", "period_start", "period_end", "queried_at"}.intersection(result)


def test_result_never_leaks_internal_details():
    """返回值会经 E 的 ``metric_result`` 回到前端：不许带内部表名 / 路径 / 责任方。"""
    for metric in mq.METRIC_CATALOG:
        result = mq.metric_tool(_valid_query(metric=metric))
        blob = str(result)
        assert "d_video_analyses" not in blob
        assert "blocked_by" not in result
        for leaked in ("SELECT", "DROP", "C:\\\\", "/videos)"):
            assert leaked not in blob, (metric, leaked)
        # 对外只写接口级来源
        assert mq.METRIC_CATALOG[metric]["source_public"] in result["detail"]


# ---------- 路由层（19 号） ----------


@pytest.fixture
def chat_env(tmp_path, monkeypatch, fake_store):
    """与 ``test_e_routes.e_env`` 同构的 E 联调环境（本文件独立维护，不跨文件借 fixture）。

    作业库 / 暂存 / 索引全走 ``tmp_path``，不污染仓库；P2PNet 包指向不存在的
    目录，保证「未登记」状态不被仓库里的 ``data/p2pnet_bundle`` 误命中。
    """
    from app.core.config import Settings
    from app.services import e_bindings

    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        detector_weights_path=str(tmp_path / "weights"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    assert e_bindings.configure_e_services() is True
    client = TestClient(pytest.importorskip("app.main").app)
    return {"settings": settings, "client": client, "tmp_path": tmp_path}


class TestChatMetricInjection:
    def _post(self, client: TestClient, payload: dict, csrf: str):
        return client.post(
            "/api/v1/assistant/chat", json=payload, headers={"X-CSRF-Token": csrf},
        )

    def test_unknown_metric_rejected_at_route(self, chat_env):
        client = chat_env["client"]
        csrf = _login_admin(client)
        resp = self._post(client, {
            "message": "黄山昨天接待了多少人？",
            "context": {"metric": "visits_yesterday"},
        }, csrf)
        # D 的 body 校验失败统一 400（不是 422）
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_metric_query_returns_insufficient_never_invents_history(self, chat_env):
        client = chat_env["client"]
        csrf = _login_admin(client)
        resp = self._post(client, {
            "message": "黄山昨天接待了多少人？",
            "context": {
                "metric": "scenic_visits",
                "entity_ids": [str(uuid4())],
                "grain": "day",
                "period_start": "2026-09-01T00:00:00+08:00",
                "period_end": "2026-09-30T00:00:00+08:00",
            },
        }, csrf)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "metric_tool_unavailable"
        assert data["metric_result"] is None
        assert data["citations"] == []
        # D 补的 limitations：讲清楚「没数」而不是「查到了」
        assert any("已核验数据" in item for item in data["limitations"])
        assert "无法核实" in data["answer"]

    def test_without_metric_keeps_knowledge_path(self, chat_env):
        """不带 metric 不能被数值分支吞掉：仍走 E 的知识库检索。"""
        client = chat_env["client"]
        csrf = _login_admin(client)
        resp = self._post(client, {"message": "黄山几点开放？"}, csrf)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] in ("answered", "insufficient_evidence")
        # 只有 E 自己的知识库限制语，没有 D 补的「数据源未接通」
        assert all("未接通" not in item for item in data["limitations"])

    def test_context_with_range_but_no_metric_is_not_hijacked(self, chat_env):
        """只带 entity_ids / 时间范围、不带 metric —— 仍是文档问答。"""
        client = chat_env["client"]
        csrf = _login_admin(client)
        resp = self._post(client, {
            "message": "黄山开放时间？",
            "context": {
                "entity_ids": [str(uuid4())],
                "period_start": "2026-09-01T00:00:00+08:00",
                "period_end": "2026-09-30T00:00:00+08:00",
                "grain": "day",
            },
        }, csrf)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] in ("answered", "insufficient_evidence")
        assert all("未接通" not in item for item in data["limitations"])


# ---------- 观测类指标：真从 C 的 observations 取值 ----------


def _point(value, start, end, *, unit="人次", source_ids=("src-1",),
           grain="month", quality="verified") -> dict:
    """拼一个 C ``observations._point`` 形状的观测点。"""
    return {
        "id": str(uuid4()), "version": 1, "metric": "airport_throughput",
        "value": None if value is None else Decimal(str(value)), "unit": unit,
        "period_start": start, "period_end": end, "grain": grain,
        "source_ids": list(source_ids),
        "published_at": None, "ingested_at": None,
        "quality_status": quality, "methodology_version": "ingest-1.0",
        "provenance": {"source_id": source_ids[0] if source_ids else None},
    }


class _FakeObservingStore:
    """只实现 resolver 会用到的两个方法（``query_series`` / ``list_metrics``）。"""

    def __init__(self, *, points=(), missing=(), error=None,
                 rule=mq.AGG_RULE_COMPLETE_NON_OVERLAPPING,
                 entity=None, dictionary_error=None):
        self._points = list(points)
        self._missing = list(missing)
        self._error = error
        self._rule = rule
        self._entity = entity or {"id": "e1", "name": "合肥新桥机场",
                                  "type": "airport"}
        self._dictionary_error = dictionary_error
        self.calls: list[dict] = []

    def query_series(self, *, entity_id, metric, grain, start, end, quality="verified"):
        self.calls.append({"entity_id": entity_id, "metric": metric, "grain": grain,
                           "start": start, "end": end, "quality": quality})
        if self._error is not None:
            raise self._error
        return {
            "entity": self._entity,
            "metric": {"code": metric}, "grain": grain,
            "points": self._points, "missing_periods": self._missing,
            "coverage_ratio": Decimal("1"), "diagnostics": [],
            "selection_policy_version": "policy-1.0",
        }

    def list_metrics(self, entity_type=None):
        if self._dictionary_error is not None:
            raise self._dictionary_error
        return [{"code": "airport_throughput", "aggregation_rule": self._rule,
                 "unit": "人次", "supported_entity_types": ["airport"]}]


@pytest.fixture
def observing(monkeypatch):
    def _install(store):
        monkeypatch.setattr("app.core.providers.get_c_store", lambda: store)
        return store
    return _install


JAN = (date(2025, 1, 1), date(2025, 1, 31))
FEB = (date(2025, 2, 1), date(2025, 2, 28))
FULL_MONTHS = "2025-02-28T00:00:00+08:00"


def _month_query(**overrides) -> dict:
    """机场 2025 年 1 月先验值 1,111,644 人次的查询体。"""
    query = {
        "metric": "airport_throughput",
        "entity_type": "airport",
        "entity_ids": [str(uuid4())],
        "grain": "month",
        "period_start": "2025-01-01T00:00:00+08:00",
        "period_end": "2025-01-31T00:00:00+08:00",
    }
    query.update(overrides)
    return query


class TestObservedMetricResolver:
    """C 观测库取值：给得全才给值，给不全就报不足，绝不合成数值。"""

    def test_single_month_returns_the_verified_value(self, observing):
        """先验值：合肥新桥机场 2025-01 = 1,111,644 人次。"""
        store = observing(_FakeObservingStore(
            points=[_point(1111644, *JAN, source_ids=["src-airport"])],
        ))
        result = mq.metric_tool(_month_query())
        assert "status" not in result, result
        for key in ("metric", "value", "source_ids", "period_start",
                    "period_end", "queried_at"):
            assert key in result, key  # E 缺任一键会 ValueError → D 500
        assert result["value"] == 1111644
        assert result["unit"] == "人次"
        assert result["source_ids"] == ["src-airport"]
        assert result["period_start"] == "2025-01-01"
        assert result["period_end"] == "2025-01-31"
        assert result["aggregated"] is False
        assert store.calls[0]["quality"] == "verified", "只核已核验观测"

    def test_date_only_period_is_read_as_business_day(self, observing):
        """19 号 HTTP 传的是**日期串**，不带时区；不能拿「缺时区」当无效。

        2026-10-17 由 E 的独立实测暴露：以前这里要求入参必须是 aware ISO，
        于是真机每条请求都 ``invalid_period`` 变成「不足」，而本文件的用例
        用的是带时区的 datetime —— **测试全绿、真机全废**。
        """
        observing(_FakeObservingStore(
            points=[_point(1111644, *JAN, source_ids=["src-airport"])],
        ))
        for start, end in (("2025-01-01", "2025-01-31"),
                           ("2025-01-01T00:00:00", "2025-01-31T00:00:00"),
                           ("2025-01-01T00:00:00Z", "2025-01-31T00:00:00Z")):
            result = mq.metric_tool(_month_query(period_start=start, period_end=end))
            assert "status" not in result, (start, end, result)
            assert result["value"] == 1111644, (start, end, result)

    def test_utc_instant_is_read_as_beijing_business_day(self, observing):
        """C §6：aware 输入要先 ``astimezone(+08)`` 再 ``.date()``。

        ``2024-12-31T16:00Z`` 的北京业务日是 **2025-01-01**；直接取 ``.date()``
        会得到 2024-12-31，整段错一个周期（跨年时相差一年）。
        """
        store = observing(_FakeObservingStore(
            points=[_point(1111644, *JAN, source_ids=["src-airport"])],
        ))
        result = mq.metric_tool(_month_query(period_start="2024-12-31T16:00:00Z"))
        assert store.calls[0]["start"] == date(2025, 1, 1), store.calls[0]
        assert "status" not in result, result
        assert result["value"] == 1111644

    @pytest.mark.parametrize("label, end", [
        ("腰部 01-01~01-20", "2025-01-20T00:00:00+08:00"),
        ("腰部 01-01~01-15", "2025-01-15T00:00:00+08:00"),
        ("两日 01-01~01-02", "2025-01-02T00:00:00+08:00"),
        ("跨月 01-01~02-15", "2025-02-15T00:00:00+08:00"),
    ])
    def test_period_not_covering_a_whole_cycle_reports_incomplete(
            self, observing, label, end):
        """落在周期腰部的区间：C 连点都不给，**且 missing_periods 也是空的**。

        2026-10-17 真机实测（合肥新桥机场）：腰部 / 两日 / 跨月三种情形 C 一律
        ``points=[]`` + ``missing=[]``。这时候不能报 ``no_verified_data``——观测是
        存在的（整月查得到 1,111,644），真实原因是「这个区间不构成完整周期」。
        （起止同一天会先在入口被 ``start < end`` 拦成 ``invalid_period``，不进到这里。）
        """
        observing(_FakeObservingStore(points=[], missing=[]))
        result = mq.metric_tool(_month_query(period_end=end))
        assert result["status"] == "metric_tool_unavailable", result
        assert result["reason"] == mq.REASON_INCOMPLETE_PERIOD, (label, result)
        assert "value" not in result
        assert "完整" in result["detail"], f"{label}：提示要说清是周期没对齐 {result}"

    def test_whole_cycle_and_partial_window_get_different_reasons(self, observing):
        """同样是没拿到点，「缺观测」和「周期没对齐」必须能区分。"""
        observing(_FakeObservingStore(
            points=[], missing=[{"period_start": JAN[0], "period_end": JAN[1]}],
        ))
        missing_data = mq.metric_tool(_month_query())
        assert missing_data["reason"] == mq.REASON_NO_VERIFIED_DATA

        observing(_FakeObservingStore(points=[], missing=[]))
        misaligned = mq.metric_tool(_month_query(period_end="2025-01-20T00:00:00+08:00"))
        assert misaligned["reason"] == mq.REASON_INCOMPLETE_PERIOD
        assert misaligned["reason"] != missing_data["reason"]

    def test_half_open_and_closed_month_windows_agree(self, observing):
        """C 的参考适配器按 [start, end) 写，D 其它接口按闭区间写。

        end 落在下一个周期起点时两者才有分歧，D 做宽容归一：
        ``2025-01-01~2025-02-01`` 与 ``2025-01-01~2025-01-31`` 必须指同一批周期。
        """
        observing(_FakeObservingStore(
            points=[_point(1111644, *JAN, source_ids=["src-airport"])],
        ))
        closed = mq.metric_tool(_month_query(period_end="2025-01-31T00:00:00+08:00"))
        half_open = mq.metric_tool(_month_query(period_end="2025-02-01T00:00:00+08:00"))
        assert closed["value"] == half_open["value"] == 1111644
        assert half_open["period_end"] == "2025-01-31"

    def test_no_verified_observation_is_unavailable(self, observing):
        observing(_FakeObservingStore(
            points=[], missing=[{"period_start": JAN[0], "period_end": JAN[1]}],
        ))
        result = mq.metric_tool(_month_query())
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == mq.REASON_NO_VERIFIED_DATA
        assert "value" not in result

    def test_incomplete_period_refuses_partial_value(self, observing):
        """区间跨了两期、只得一期的观测 → 缺期不能拿已有周期凑数。"""
        observing(_FakeObservingStore(
            points=[_point(1000, *JAN)],
            missing=[{"period_start": FEB[0], "period_end": FEB[1]}],
        ))
        result = mq.metric_tool(_month_query(period_end=FULL_MONTHS))
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == mq.REASON_INCOMPLETE_PERIOD
        assert "value" not in result

    def test_multi_period_sums_only_when_dictionary_allows(self, observing):
        observing(_FakeObservingStore(points=[
            _point(1000, *JAN, source_ids=["src-a"]),
            _point(2000, *FEB, source_ids=["src-b"]),
        ]))
        result = mq.metric_tool(_month_query(period_end=FULL_MONTHS))
        assert result["value"] == 3000
        assert result["aggregated"] is True
        assert result["points_count"] == 2
        assert set(result["source_ids"]) == {"src-a", "src-b"}
        assert result["period_start"] == "2025-01-01"
        assert result["period_end"] == "2025-02-28"

    def test_multi_period_without_dictionary_rule_is_unavailable(self, observing):
        """字典没说能合计 → 不许合计（读不到口径 ≠ 默认可以加）。"""
        observing(_FakeObservingStore(points=[
            _point(1000, *JAN), _point(2000, *FEB),
        ], rule="sum_never"))
        result = mq.metric_tool(_month_query(period_end=FULL_MONTHS))
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == mq.REASON_MULTI_PERIOD

    def test_dictionary_unreadable_means_no_sum(self, observing):
        observing(_FakeObservingStore(
            points=[_point(1000, *JAN), _point(2000, *FEB)],
            dictionary_error=RuntimeError("字典挂了"),
        ))
        result = mq.metric_tool(_month_query(period_end=FULL_MONTHS))
        assert result["reason"] == mq.REASON_MULTI_PERIOD

    def test_points_without_unit_or_source_are_never_returned(self, observing):
        """单位与字典不符的观测点直接判不可用——不换算、不折算、不给值。"""
        observing(_FakeObservingStore(points=[
            _point(1000, *JAN, unit="人次"), _point(2, *FEB, unit="万人次"),
        ]))
        result = mq.metric_tool(_month_query(period_end=FULL_MONTHS))
        assert result["reason"] == mq.REASON_UNUSABLE_POINTS
        assert "value" not in result

    def test_point_without_source_is_refused_before_e_sees_it(self, observing):
        """E 要求 source_ids 非空，空列表会让 E ValueError → D 500。

        所以这一步必须在 D 侧拦掉：宁可报不足，也不把无来源的数值递出去。
        """
        observing(_FakeObservingStore(points=[_point(1111644, *JAN, source_ids=[])]))
        result = mq.metric_tool(_month_query())
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == mq.REASON_UNUSABLE_POINTS
        assert "source_ids" not in result
        assert "value" not in result

    def test_unverified_point_is_never_returned(self, observing):
        observing(_FakeObservingStore(points=[
            _point(1111644, *JAN, quality="quarantined"),
        ]))
        result = mq.metric_tool(_month_query())
        assert result["reason"] == mq.REASON_UNUSABLE_POINTS

    def test_multi_entity_is_refused_not_summed(self, observing):
        """跨对象合计属于看板 TOP-N 口径，问答里宁可报不足。"""
        observing(_FakeObservingStore(points=[_point(1000, *JAN)]))
        result = mq.metric_tool(_month_query(
            entity_ids=[str(uuid4()), str(uuid4())]))
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == mq.REASON_MULTI_ENTITY

    @pytest.mark.parametrize("error, reason", [
        (NotFound("实体不存在"), mq.REASON_INVALID_ENTITY),
        (MetricEntityMismatch("指标不适用于该实体"), mq.REASON_METRIC_ENTITY_MISMATCH),
        (UnsupportedGrain("不支持该粒度", supported=["month"]), mq.REASON_UNSUPPORTED_GRAIN),
        (QueryLimitExceeded("查询超限"), mq.REASON_INVALID_PERIOD),
        (InvalidArgument("区间非法"), mq.REASON_INVALID_PERIOD),
        (RuntimeError("数据库炸了"), mq.REASON_DATA_SOURCE_UNAVAILABLE),
    ])
    def test_c_errors_become_unavailable_not_500(self, observing, error, reason):
        """C 的异常必须翻译成「报不足」，绝不让它在 E 里炸成 500。"""
        observing(_FakeObservingStore(error=error))
        result = mq.metric_tool(_month_query())
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == reason
        assert "value" not in result

    def test_camera_metric_stays_insufficient(self, observing):
        """录像类指标依赖真实录像分析结果，没有素材就是没有数值。"""
        observing(_FakeObservingStore(points=[_point(42, *JAN)]))
        result = mq.metric_tool(_month_query(metric="camera_visible_people"))
        assert result["status"] == "metric_tool_unavailable"
        assert result["reason"] == mq.REASON_DATA_SOURCE_UNAVAILABLE
        assert result["unit"] == "人"
