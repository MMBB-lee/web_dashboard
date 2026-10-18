"""05 实体目录 / 06 指标字典 / 07 来源详情（接 C 0.2.0a2 的 ``uow.catalog``）。

守的三条：

① **目录不暗示未取得的数据可用**（03 §05）：``available_metrics`` /
   ``available_grains`` 一律来自 C（只统计已核验观测），D 不补、不猜；
② **字段集合严格等于 03 §05/§06/§07**：C 的 ``public_columns`` 会带出
   ``parent_entity_id`` / ``location_note`` / ``dictionary_version`` /
   ``adapter_key`` / ``enabled`` 等列，这些**不能泄到响应里**；
③ C 的异常族**逐类翻译**（404/400/503…），不吞成 500。

C store 不可用（sqlite 后端）时三条路由回到 503 占位，不在测试里假装接通。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


@dataclass
class _Page:
    page: int = 1
    page_size: int = 20


class _FakeCatalogUow:
    """模拟 ``uow.catalog``：记录调用参数，可注入 C 的真实异常。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error

    def list_entities(self, *, type=None, region_code=None, q=None, page=_Page(),
                      include_cameras=False):
        self.calls.append({"type": type, "region_code": region_code, "q": q,
                           "page": page, "include_cameras": include_cameras})
        if self._error is not None:
            raise self._error
        return {
            "items": [_entity_row()],
            "page": page.page, "page_size": page.page_size, "total": 1,
        }

    def list_metrics(self, entity_type=None):
        self.calls.append({"entity_type": entity_type})
        if self._error is not None:
            raise self._error
        return [_metric_row()]

    def get_source(self, source_id):
        self.calls.append({"source_id": source_id})
        if self._error is not None:
            raise self._error
        return _source_row(source_id)


def _entity_row() -> dict:
    return {
        "id": str(uuid4()), "name": "黄山风景区", "type": "scenic",
        "region_code": "341000",
        # C 用 Numeric(10,7) 存经纬度 → Decimal，必须能落回 float
        "longitude": Decimal("118.1670250"), "latitude": Decimal("30.1333330"),
        "parent_entity_id": None, "location_note": "内部备注，不应外泄",
        "status": "active",
        "available_metrics": ["scenic_visits"], "available_grains": ["day"],
        # C 还会返回「已声明但未核验」的能力，03 §05 只要求 available_*，
        # 这两组不能出现在响应里
        "supported_metrics": ["scenic_visits", "rail_departures"],
        "supported_grains": ["day", "week"],
        "updated_at": datetime.now(timezone.utc), "row_version": 1,
    }


def _metric_row() -> dict:
    return {
        "code": "scenic_visits", "display_name": "景区接待人次",
        "definition": "统计期内进入景区的游客人次", "unit": "人次",
        "supported_entity_types": ["scenic"], "aggregation_rule": "sum",
        "comparability_note": "不同景区间不可直接相加",
        "dictionary_version": "2026-10-12",  # 内部版本，不外泄
        "created_at": datetime.now(timezone.utc),
    }


def _source_row(source_id: str) -> dict:
    return {
        "id": source_id, "publisher": "安徽省文化和旅游厅",
        "title": "2026 年五一假期旅游数据", "url": "https://example.gov.cn/x",
        "native_grain": "day", "usage_note": "仅用于日粒度",
        "verification_status": "verified",
        "coverage_start": date(2026, 5, 1), "coverage_end": date(2026, 5, 5),
        # C 的 public_columns 把 datetime 统一成 UTC aware
        "published_at": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        "retrieved_at": datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc),
        # 采集侧实现细节，一律不外泄
        "adapter_key": "http_csv", "adapter_config": {"secret": "x"},
        "enabled": True, "update_frequency": "daily",
    }


class _FakeCStore:
    def __init__(self, uow: _FakeCatalogUow) -> None:
        self.uow = uow

    def list_entities(self, *, type=None, region_code=None, q=None,
                      page=1, page_size=20):
        from data_foundation.contracts import Page

        return self.uow.list_entities(
            type=type, region_code=region_code, q=q,
            page=Page(page=page, page_size=page_size),
        )

    def list_metrics(self, entity_type=None):
        return self.uow.list_metrics(entity_type)

    def get_source(self, source_id):
        return self.uow.get_source(source_id)


def _login(client: TestClient, username: str = "admin1",
           password: str = "admin-pass-123") -> str:
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password},
    ).json()["data"]["csrf_token"]


@pytest.fixture
def catalog_env(tmp_path, monkeypatch, fake_store):
    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    client = TestClient(pytest.importorskip("app.main").app)
    installed: dict[str, object] = {}

    def _install(uow: _FakeCatalogUow):
        store = _FakeCStore(uow)
        monkeypatch.setattr("app.api.v1.catalog.get_c_store", lambda: store)
        monkeypatch.setattr("app.api.v1.sources.get_c_store", lambda: store)
        installed["uow"] = uow
        return uow

    return {"client": client, "install": _install, "uow": installed}


# ---------- 05 实体目录 ----------


class TestListEntities:
    def test_requires_login(self, catalog_env):
        assert catalog_env["client"].get("/api/v1/catalog/entities").status_code == 401

    def test_returns_only_contract_fields(self, catalog_env):
        uow = catalog_env["install"](_FakeCatalogUow())
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/catalog/entities",
                          params={"type": "scenic", "region_code": "341000",
                                  "q": "黄山", "page": 2, "page_size": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["request_id"]
        assert body["meta"]["page"] == 2
        assert body["meta"]["page_size"] == 5
        assert body["meta"]["total"] == 1
        item = body["data"][0]
        # 03 §05 要求的九项全在
        for key in ("id", "name", "type", "region_code", "longitude", "latitude",
                    "available_metrics", "available_grains", "status"):
            assert key in item, key
        # Decimal → float，不是字符串
        assert isinstance(item["longitude"], float)
        assert item["available_metrics"] == ["scenic_visits"]
        # 内部列不外泄
        for leaked in ("parent_entity_id", "location_note", "row_version",
                       "supported_metrics", "supported_grains", "updated_at"):
            assert leaked not in item, leaked
        # 筛选参数原样透传给 C（分页收敛成 C 的 Page 对象）
        assert uow.calls[0]["type"] == "scenic"
        assert uow.calls[0]["region_code"] == "341000"
        assert uow.calls[0]["q"] == "黄山"
        assert uow.calls[0]["page"].page == 2 and uow.calls[0]["page"].page_size == 5
        # 摄像头不进目录（03 §05 只列 scenic / rail_station / airport）
        assert uow.calls[0]["include_cameras"] is False

    def test_unknown_entity_type_is_400(self, catalog_env):
        catalog_env["install"](_FakeCatalogUow())
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/catalog/entities", params={"type": "camera"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_overlong_query_is_400_not_silent_truncation(self, catalog_env):
        """C 内部会截断到 50 字符；D 显式拒收，不让调用方误以为搜的是完整词。"""
        catalog_env["install"](_FakeCatalogUow())
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/catalog/entities", params={"q": "黄" * 51})
        assert resp.status_code == 400

    def test_pending_503_without_c_store(self, catalog_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.catalog.get_c_store", lambda: None)
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/catalog/entities")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "dependency_not_available"


# ---------- 06 指标字典 ----------


class TestListMetrics:
    def test_requires_login(self, catalog_env):
        assert catalog_env["client"].get("/api/v1/catalog/metrics").status_code == 401

    def test_passes_entity_type_and_keeps_comparability_note(self, catalog_env):
        uow = catalog_env["install"](_FakeCatalogUow())
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/catalog/metrics", params={"entity_type": "scenic"})
        assert resp.status_code == 200, resp.text
        item = resp.json()["data"][0]
        # 03 §06：可比性说明是硬要求，原样透传不省略
        assert item["comparability_note"] == "不同景区间不可直接相加"
        assert item["aggregation_rule"] == "sum"
        assert item["supported_entity_types"] == ["scenic"]
        # 内部版本不外泄
        assert "dictionary_version" not in item and "created_at" not in item
        assert uow.calls[0]["entity_type"] == "scenic"

    def test_pending_503_without_c_store(self, catalog_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.catalog.get_c_store", lambda: None)
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/catalog/metrics")
        assert resp.status_code == 503


# ---------- 07 来源详情 ----------


class TestGetSource:
    def test_requires_login(self, catalog_env):
        source_id = str(uuid4())
        assert catalog_env["client"].get(f"/api/v1/sources/{source_id}").status_code == 401

    def test_returns_contract_fields_with_cn_timezone(self, catalog_env):
        source_id = str(uuid4())
        catalog_env["install"](_FakeCatalogUow())
        client = catalog_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/sources/{source_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        # 03 §07 的 11 项
        for key in ("id", "publisher", "title", "url", "published_at", "retrieved_at",
                    "coverage_start", "coverage_end", "native_grain", "usage_note",
                    "verification_status"):
            assert key in body, key
        # C 给的是 UTC aware；对外统一 +08:00（03 §1）
        assert body["published_at"].endswith("+08:00")
        assert body["coverage_start"] == "2026-05-01"
        # 采集侧实现细节不外泄
        for leaked in ("adapter_key", "adapter_config", "enabled", "update_frequency"):
            assert leaked not in body, leaked

    def test_non_uuid_source_id_is_400(self, catalog_env):
        catalog_env["install"](_FakeCatalogUow())
        client = catalog_env["client"]
        _login(client)
        resp = client.get("/api/v1/sources/not-a-uuid")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_pending_503_without_c_store(self, catalog_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.sources.get_c_store", lambda: None)
        client = catalog_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/sources/{uuid4()}")
        assert resp.status_code == 503


# ---------- C 异常族统一翻译 ----------


class TestCErrorsAreTranslated:
    @pytest.mark.parametrize("path", ["/api/v1/catalog/entities",
                                      "/api/v1/catalog/metrics"])
    def test_catalog_errors(self, catalog_env, path):
        from data_foundation import InvalidArgument, NotFound

        for error, expected in ((NotFound("实体不存在"), 404),
                                (InvalidArgument("region_code 无效"), 400)):
            catalog_env["install"](_FakeCatalogUow(error=error))
            client = catalog_env["client"]
            _login(client)
            resp = client.get(path)
            assert resp.status_code == expected, (path, error, resp.text)
            assert resp.json()["error"]["code"] in ("not_found", "bad_request")

    def test_source_not_found_is_404(self, catalog_env):
        from data_foundation import NotFound

        catalog_env["install"](_FakeCatalogUow(error=NotFound("来源不存在")))
        client = catalog_env["client"]
        _login(client)
        resp = client.get(f"/api/v1/sources/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
