"""13 假期列表 / 15 事件列表 / 16 事件详情（接 C 0.2.0a2 的仓储）。

守的四条：

① **假期定义可追溯**（03 §13）：``definition_source_id`` 必须回传，
   且指向 07 号能查到的来源；同一批内部列（``scope_key`` /
   ``definition_asset_id``）不外泄；
② **列表只给摘要**（03 §15）：``description`` / ``operator_note`` /
   ``timeline`` **不能**出现在 15 的列表项里——C 的 ``get_event`` 会一并返回，
   由 D 剔除；
③ **C 不支持的筛选项由 D 补齐**：``entity_id`` / ``kind`` / ``authority`` /
   ``since`` / ``until`` 与分页在路由层生效（并记在 README 里，量级上升时应
   请 C 下沉到 SQL）；
④ **来源缺失不拖垮公告**：16 的 ``source_url`` 查不到时给 ``null``，不 500。

17 / 18（写操作）仍是 pending 占位，测试里断言它们**没有**被偷偷接上。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from data_foundation import Conflict, InvalidArgument, NotFound


def _utc(*, days_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


class _FakeHolidayEventUow:
    def __init__(self, *, error: Exception | None = None,
                 source_error: Exception | None = None,
                 status: str = "active", row_version: int = 1,
                 asset_matches_source: bool = True) -> None:
        self.calls: list[dict] = []
        self._error = error
        self._source_error = source_error
        self._status = status
        self._row_version = row_version
        # C 校验的是 asset.source_id == source_id。fake 里没有原件表，
        # 用开关直接表达「这对来源与原件是否一致」，比拿两个随机 UUID 比相等靠谱。
        self._asset_matches_source = asset_matches_source
        self.created: list[dict] = []
        self.updated: list[dict] = []

    # --- holidays ---
    def list_holidays(self, *, year=None, kind=None, region_code=None):
        self.calls.append({"kind": "holidays", "year": year, "kind_filter": kind,
                           "region_code": region_code})
        if self._error is not None:
            raise self._error
        return [_holiday_row()]

    # --- events ---
    def list_events(self, *, region_code=None, status=None, now=None):
        self.calls.append({"kind": "events", "region_code": region_code,
                           "status": status, "now": now})
        if self._error is not None:
            raise self._error
        return [_event_row(), _event_row(days_ago=10, kind="rail_adjustment",
                                         authority="model_signal",
                                         entity_ids=[str(uuid4())])]

    def get_event(self, event_id):
        self.calls.append({"kind": "event", "event_id": event_id})
        if self._error is not None:
            raise self._error
        return _event_row(detail=True, status=self._status,
                          row_version=self._row_version)

    def create_event(self, data, *, operator_id, now):
        self.created.append({"data": dict(data), "operator_id": operator_id})
        if self._error is not None:
            raise self._error
        # 复刻 C 的两条硬校验，确保 D 没把该自己拦的事推给数据库
        if data.get("authority") != "official":
            raise InvalidArgument("官方事件必须关联一致且可核验的来源与原件")
        if not self._asset_matches_source:
            raise InvalidArgument("官方事件必须关联一致且可核验的来源与原件")
        if data.get("ends_at") is not None and data["ends_at"] < data["starts_at"]:
            raise ValueError("time_order")  # C 会走数据库 IntegrityError
        return _event_row(detail=True)

    def update_event(self, event_id, patch, *, expected_version, operator_id, now):
        self.updated.append({"event_id": event_id, "patch": dict(patch),
                             "expected_version": expected_version,
                             "operator_id": operator_id})
        if self._error is not None:
            raise self._error
        if expected_version != self._row_version:
            raise Conflict("事件版本已变化，请刷新后重试")
        if not (patch.get("change_note") or "").strip():
            raise InvalidArgument("更新事件必须填写 change_note")
        # C 只在 asset_id 一并提交时校验来源与原件一致
        if "asset_id" in patch and not self._asset_matches_source:
            raise InvalidArgument("事件来源与原件不一致")
        return _event_row(detail=True, status=patch.get("status", self._status),
                          row_version=self._row_version + 1)

    # --- catalog（16 补 source_url 用） ---
    def get_source(self, source_id):
        if self._source_error is not None:
            raise self._source_error
        return {"id": source_id, "url": "https://example.gov.cn/notice/1",
                "publisher": "x", "title": "y", "usage_note": "z",
                "verification_status": "verified"}


def _holiday_row() -> dict:
    return {
        "id": str(uuid4()), "name": "2026 年五一假期", "kind": "may_day",
        "year": 2026, "start_date": date(2026, 5, 1), "end_date": date(2026, 5, 5),
        "days_count": 5, "region_code": "000000",
        "scope_key": "CN:national_general_public",  # 内部口径，不外泄
        "definition_asset_id": str(uuid4()),        # 同上
        "definition_source_id": str(uuid4()),
    }


def _event_row(*, detail: bool = False, days_ago: int = 0, kind: str = "weather",
               authority: str = "official", entity_ids: list[str] | None = None,
               status: str = "active", row_version: int = 1) -> dict:
    source_id = str(uuid4())
    row = {
        "id": str(uuid4()), "title": "暴雨橙色预警", "kind": kind,
        "status": status, "authority": authority, "severity": "warning",
        "region_code": "341000",
        "starts_at": _utc(days_ago=days_ago), "ends_at": _utc(days_ago=days_ago - 1),
        "published_at": _utc(days_ago=days_ago), "last_verified_at": _utc(days_ago=days_ago),
        "source_id": source_id, "asset_id": str(uuid4()),
        "description": "景区临时闭园", "operator_note": "内部备注，不进列表",
        "row_version": row_version,  # C 的 MutableMixin
        "affected_entity_ids": entity_ids if entity_ids is not None else [str(uuid4())],
        # ⚠️ C 的 event_history 只有 version / snapshot / change_note / changed_at，
        # 没有 kind / at / source_id / note——fixture 必须按真实列给，
        # 否则测不到 D 的映射逻辑（曾经就是按假列写才漏的）。
        "timeline": [{"version": 1, "changed_at": _utc(days_ago=days_ago),
                      "change_note": "创建事件",
                      "snapshot": {"status": status, "source_id": source_id}}],
    }
    return row


class _FakeCStore:
    def __init__(self, uow: _FakeHolidayEventUow) -> None:
        self.uow = uow

    def list_holidays(self, *, year=None, kind=None, region_code=None):
        return self.uow.list_holidays(year=year, kind=kind, region_code=region_code)

    def list_events(self, *, region_code=None, status=None):
        from datetime import UTC, datetime as _dt

        return self.uow.list_events(region_code=region_code, status=status,
                                    now=_dt.now(UTC))

    def get_event(self, event_id):
        return self.uow.get_event(event_id)

    def create_event(self, data, *, operator_id, now):
        return self.uow.create_event(data, operator_id=operator_id, now=now)

    def update_event(self, event_id, patch, *, expected_version, operator_id, now):
        return self.uow.update_event(
            event_id, patch, expected_version=expected_version,
            operator_id=operator_id, now=now)

    def get_source(self, source_id):
        return self.uow.get_source(source_id)


def _login(client: TestClient, username: str = "admin1",
           password: str = "admin-pass-123") -> str:
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password},
    ).json()["data"]["csrf_token"]


@pytest.fixture
def he_env(tmp_path, monkeypatch, fake_store):
    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    client = TestClient(pytest.importorskip("app.main").app)

    def _install(uow: _FakeHolidayEventUow):
        store = _FakeCStore(uow)
        monkeypatch.setattr("app.api.v1.holidays.get_c_store", lambda: store)
        monkeypatch.setattr("app.api.v1.events.get_c_store", lambda: store)
        return uow

    return {"client": client, "install": _install}


# ---------- 13 假期列表 ----------


class TestListHolidays:
    def test_requires_login(self, he_env):
        assert he_env["client"].get("/api/v1/holidays").status_code == 401

    def test_definition_is_traceable(self, he_env):
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/holidays", params={"year": 2026, "kind": "may_day"})
        assert resp.status_code == 200, resp.text
        item = resp.json()["data"][0]
        for key in ("id", "name", "kind", "year", "start", "end",
                    "definition_source_id", "days_count"):
            assert key in item, key
        assert item["start"] == "2026-05-01" and item["end"] == "2026-05-05"
        # 内部口径不外泄
        for leaked in ("scope_key", "definition_asset_id", "region_code"):
            assert leaked not in item, leaked
        assert uow.calls[0]["year"] == 2026
        assert uow.calls[0]["kind_filter"] == "may_day"

    def test_bad_year_is_400(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/holidays", params={"year": 26})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_kind_is_passed_through_not_whitelisted(self, he_env):
        """03 §13 只列了四种 kind，但 C 库里有 ``new_year``——按 C 的字典透传，
        D 不能把真实存在的一类假期查询挡成 400。"""
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/holidays", params={"kind": "new_year"})
        assert resp.status_code == 200, resp.text
        assert uow.calls[0]["kind_filter"] == "new_year"

    def test_pending_503_without_c_store(self, he_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.holidays.get_c_store", lambda: None)
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/holidays")
        assert resp.status_code == 503


# ---------- 15 事件列表 ----------


class TestListEvents:
    def test_requires_login(self, he_env):
        assert he_env["client"].get("/api/v1/events").status_code == 401

    def test_summary_only_no_description_or_operator_note(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["total"] == 2
        item = body["data"][0]
        # 03 §15 的 12 项
        assert item["source_id"]
        assert item["starts_at"].endswith("+08:00")   # 统一业务时区
        # 列表不给详情字段
        for leaked in ("description", "operator_note", "timeline"):
            assert leaked not in item, leaked

    def test_extra_filters_are_applied_by_d(self, he_env):
        """C 不支持的 kind 筛选由 D 补齐。"""
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events", params={"kind": "rail_adjustment"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1 and data[0]["kind"] == "rail_adjustment"
        # region_code / status 仍然透传给 C（由 C 在 SQL 层过滤）
        assert uow.calls[0]["kind"] == "events"

    def test_authority_filter(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events", params={"authority": "model_signal"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1 and data[0]["authority"] == "model_signal"

    def test_pagination(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events", params={"page": 2, "page_size": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["page"] == 2 and body["meta"]["total"] == 2
        assert len(body["data"]) == 1

    def test_since_filter_and_bad_time(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = client.get("/api/v1/events", params={"since": since})
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1      # 10 天前那条被筛掉
        bad = client.get("/api/v1/events", params={"since": "昨天"})
        assert bad.status_code == 400

    @pytest.mark.parametrize("params", [
        {"kind": "closure"},        # C 真实取值，不在 03 §15 枚举里
        {"status": "expired"},      # 同上：真机有一条 expired 公告
        {"authority": "official"},
    ])
    def test_filters_are_passed_through_not_whitelisted(self, he_env, params):
        """C 的字典比 03 §15 宽（status=expired / kind=closure）——一律透传，
        D 不能把真实存在的公告查询挡成 400。"""
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events", params=params)
        assert resp.status_code == 200, (params, resp.text)

    def test_pending_503_without_c_store(self, he_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.events.get_c_store", lambda: None)
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events")
        assert resp.status_code == 503


# ---------- 16 事件详情 ----------


class TestGetEvent:
    def test_requires_login(self, he_env):
        assert he_env["client"].get(f"/api/v1/events/{uuid4()}").status_code == 401

    def test_detail_includes_description_timeline_and_source_url(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/events/{uuid4()}")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["description"] == "景区临时闭园"
        assert data["operator_note"] == "内部备注，不进列表"
        assert data["source_url"] == "https://example.gov.cn/notice/1"
        assert len(data["timeline"]) == 1
        assert data["timeline"][0]["kind"] == "announce"

    def test_missing_source_degrades_to_null_not_500(self, he_env):
        """来源记录没了，公告本身还得能查——source_url 给 null。"""
        from data_foundation import NotFound

        he_env["install"](_FakeHolidayEventUow(source_error=NotFound("来源不存在")))
        client = he_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/events/{uuid4()}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["source_url"] is None

    def test_non_uuid_is_400(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.get("/api/v1/events/not-a-uuid")
        assert resp.status_code == 400

    def test_not_found_is_404(self, he_env):
        from data_foundation import NotFound

        he_env["install"](_FakeHolidayEventUow(error=NotFound("事件不存在")))
        client = he_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/events/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


# ---------- 17 / 18 事件写操作 ----------


def _create_body(**over) -> dict:
    body = {"title": "暴雨橙色预警", "description": "景区临时闭园",
            "kind": "weather", "severity": "warning", "region_code": "341000",
            "starts_at": _utc().isoformat(), "source_id": str(uuid4()),
            "asset_id": str(uuid4())}
    body.update(over)
    return body


class TestCreateEvent:
    def test_requires_login(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        resp = he_env["client"].post("/api/v1/events", json=_create_body())
        assert resp.status_code == 401

    def test_requires_csrf(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        _login(client)
        resp = client.post("/api/v1/events", json=_create_body())
        assert resp.status_code in (401, 403), resp.text

    def test_creates_as_official_and_active(self, he_env):
        """03 §17：本接口只建官方公告，且新建即为 active。"""
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/events", json=_create_body(),
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["authority"] == "official"
        assert data["status"] == "active"
        sent = uow.created[0]["data"]
        assert sent["authority"] == "official"
        assert sent["status"] == "active"
        assert sent["published_at"] is not None
        assert uow.created[0]["operator_id"], "必须留痕：谁建的"

    def test_asset_id_is_required(self, he_env):
        """C 的 events.asset_id 是 NOT NULL 且直接下标取——缺了会漏成 500。"""
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        body = _create_body()
        body.pop("asset_id")
        resp = client.post("/api/v1/events", json=body,
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text

    def test_source_id_is_required(self, he_env):
        """03 §17 红线：没有可核验来源不得创建官方事件。"""
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        body = _create_body()
        body.pop("source_id")
        resp = client.post("/api/v1/events", json=body,
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text

    def test_end_before_start_is_400_not_500(self, he_env):
        """表有 CHECK(ends_at >= starts_at)：D 侧先拦，不能漏成数据库 500。"""
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/events", json=_create_body(
            starts_at=_utc().isoformat(), ends_at=_utc(days_ago=1).isoformat()),
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert not uow.created, "不该带着非法时间走到 C"

    def test_naive_time_is_read_as_cn_not_utc(self, he_env):
        """naive 时间按 +08:00 解释，不当 UTC——否则凭空差 8 小时。"""
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/events", json=_create_body(
            starts_at="2026-10-01T00:00:00", ends_at="2026-10-02T00:00:00"),
            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 201, resp.text
        start = uow.created[0]["data"]["starts_at"]
        assert start.utcoffset().total_seconds() == 0
        assert start.hour == 16 and start.day == 30, start.isoformat()

    def test_bad_kind_is_rejected(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/events", json=_create_body(kind="瞎写"),
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text

    def test_missing_source_is_404(self, he_env):
        """来源不存在 → C 的 NotFound → 404（不是 400，也不是 500）。"""
        he_env["install"](_FakeHolidayEventUow(error=NotFound("事件来源不存在")))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/events", json=_create_body(),
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404, resp.text

    def test_source_asset_mismatch_is_400(self, he_env):
        """03 §17 红线：来源与原件对不上 → C 的 InvalidArgument → 400。"""
        he_env["install"](_FakeHolidayEventUow(asset_matches_source=False))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.post("/api/v1/events", json=_create_body(),
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["details"]["detail"] == (
            "官方事件必须关联一致且可核验的来源与原件")


class TestUpdateEvent:
    def test_requires_login(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        resp = he_env["client"].patch(f"/api/v1/events/{uuid4()}",
                                      json={"change_note": "修正"})
        assert resp.status_code == 401

    def test_change_note_is_required(self, he_env):
        """03 §18：变更必须留痕。"""
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"severity": "info", "expected_version": 1},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert "change_note" in resp.json()["error"]["details"]["errors"][0]["loc"][-1]
        assert not uow.updated

    def test_expected_version_is_required(self, he_env):
        """C 021a2 收口第 5 条：18 **必填** expected_version，缺失即 400。

        2026-10-17 之前 D 是「不传就代填当前版本」——那等于替调用方关掉并发
        保护（两个人同改一条公告，后者静默覆盖前者）。现在必须显式拒收。
        """
        uow = he_env["install"](_FakeHolidayEventUow(row_version=3))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"severity": "critical", "change_note": "升级预警"},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert "expected_version" in resp.text
        assert not uow.updated, "没给版本号就不许写库"

    def test_updates_and_bumps_version(self, he_env):
        uow = he_env["install"](_FakeHolidayEventUow(row_version=3))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"severity": "critical", "change_note": "升级预警",
                                  "expected_version": 3},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["row_version"] == 4
        sent = uow.updated[0]
        assert sent["patch"]["change_note"] == "升级预警"
        assert sent["operator_id"], "必须留痕：谁改的"
        # D **原样透传**调用方给的版本号，不再代填
        assert sent["expected_version"] == 3

    def test_expected_version_is_honoured(self, he_env):
        uow = he_env["install"](_FakeHolidayEventUow(row_version=3))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"change_note": "改", "expected_version": 7},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 409, resp.text  # C 的 Conflict
        assert uow.updated[0]["expected_version"] == 7

    def test_reactivating_resolved_requires_new_source(self, he_env):
        """03 §18 红线：重新激活已解除的事件必须提供新来源。"""
        uow = he_env["install"](_FakeHolidayEventUow(status="resolved"))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"status": "active", "change_note": "再次生效",
                                  "expected_version": 1},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert "new_source_id" in resp.json()["error"]["message"]
        assert not uow.updated

    def test_new_source_without_new_asset_is_rejected(self, he_env):
        """C 只在两者一并提交时才校验一致性——只换来源会留下不匹配状态。"""
        uow = he_env["install"](_FakeHolidayEventUow(status="resolved"))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"status": "active", "change_note": "再次生效",
                                  "new_source_id": str(uuid4()),
                                  "expected_version": 1},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert not uow.updated

    def test_reactivation_with_new_source_and_asset_succeeds(self, he_env):
        uow = he_env["install"](_FakeHolidayEventUow(status="resolved"))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"status": "active", "change_note": "再次生效",
                                  "new_source_id": str(uuid4()),
                                  "new_asset_id": str(uuid4()),
                                  "expected_version": 1},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 200, resp.text
        patch = uow.updated[0]["patch"]
        assert patch["source_id"] and patch["asset_id"]

    def test_end_before_start_is_400_not_500(self, he_env):
        uow = he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"change_note": "改时间", "expected_version": 1,
                                  "ends_at": _utc(days_ago=5).isoformat()},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400, resp.text
        assert not uow.updated

    def test_non_uuid_is_400(self, he_env):
        he_env["install"](_FakeHolidayEventUow())
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch("/api/v1/events/not-a-uuid",
                            json={"change_note": "x", "expected_version": 1},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_not_found_is_404(self, he_env):
        he_env["install"](_FakeHolidayEventUow(error=NotFound("事件不存在")))
        client = he_env["client"]
        csrf = _login(client)
        resp = client.patch(f"/api/v1/events/{uuid4()}",
                            json={"change_note": "x", "expected_version": 1},
                            headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 404
