"""23 录像列表 / 25 录像回放（接 C 0.2.0a2 的 ``uow.video`` 仓储）。

守的是三条：
① **回放是获准内容**：授权核验由 C 的 ``get_playback_ref`` 内部完成（``InvalidPermission``
   → 403），D 不自己实现「谁能看」；
② **禁止共享缓存** + **支持 Range**（03 §25），且不把服务器路径/资产 ID 泄进响应；
③ C 的异常族**逐类翻译**成 D 的统一错误码（404/403/409/400/503），不吞成 500。

C store 不可用（sqlite 后端 / C 包缺失）时两条路由回到 503 占位，不在测试里假装接通。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


@dataclass
class _Page:
    page: int = 1
    page_size: int = 20


class _FakeVideoUow:
    """模拟 ``uow.video``：记录调用参数，抛出 C 的真实异常族。"""

    def __init__(self, tmp_path: Path, *, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._error = error
        self.video_file = tmp_path / "clip.mp4"
        self.content = b"FAKE-VIDEO-BYTES" * 64
        self.video_file.write_bytes(self.content)

    def list_videos(self, *, scenic_id=None, camera_id=None, status=None, page=_Page()):
        self.calls.append({"scenic_id": scenic_id, "camera_id": camera_id,
                           "status": status, "page": page})
        if self._error is not None:
            raise self._error
        from datetime import datetime, timedelta, timezone

        tz = timezone(timedelta(hours=8))
        start = datetime(2026, 5, 1, 8, 0, tzinfo=tz)
        return {
            "items": [{
                "id": str(uuid4()), "scenic_id": str(uuid4()), "camera_id": str(uuid4()),
                # C 的 MutableMixin 列非空；D 统一转成 +08:00 的 ISO 串
                "recorded_start_at": start,
                "recorded_end_at": start + timedelta(minutes=30),
                "status": "ready", "permission_record_id": str(uuid4()),
            }],
            # 与 C 实测一致：page / page_size 是普通整数，不是 Page 对象
            "page": page.page, "page_size": page.page_size, "total": 1,
        }

    def get_playback_ref(self, video_id: str, now):
        self.calls.append({"video_id": video_id, "now": now})
        if self._error is not None:
            raise self._error
        return {
            "video_id": video_id, "asset_id": str(uuid4()),
            "path": str(self.video_file),
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "byte_size": len(self.content), "media_type": "video/mp4",
        }


class _FakeCStore:
    def __init__(self, uow: _FakeVideoUow) -> None:
        self.uow = uow

    def list_videos(self, *, scenic_id=None, camera_id=None, status=None,
                    page=1, page_size=20):
        # 与 CStore.list_videos 同签名：把 page/page_size 收敛成 C 的 Page 对象
        return self.uow.list_videos(
            scenic_id=scenic_id, camera_id=camera_id, status=status,
            page=_Page(page=page, page_size=page_size),
        )

    def get_playback_ref(self, video_id: str):
        from datetime import UTC, datetime

        return self.uow.get_playback_ref(video_id, datetime.now(UTC))


def _login(client: TestClient, username: str = "admin1", password: str = "admin-pass-123") -> str:
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password},
    ).json()["data"]["csrf_token"]


@pytest.fixture
def video_env(tmp_path, monkeypatch, fake_store):
    from app.core.config import Settings as _S

    settings = _S(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    client = TestClient(pytest.importorskip("app.main").app)

    def _install(uow: _FakeVideoUow):
        monkeypatch.setattr("app.api.v1.videos.get_c_store", lambda: _FakeCStore(uow))
        return uow

    return {"client": client, "tmp_path": tmp_path, "install": _install}


# ---------- 23 列表 ----------


class TestVideoList:
    def test_requires_login(self, video_env):
        assert video_env["client"].get("/api/v1/videos").status_code == 401

    def test_returns_envelope_with_pagination_meta(self, video_env):
        uow = video_env["install"](_FakeVideoUow(video_env["tmp_path"]))
        client = video_env["client"]
        _login(client)
        resp = client.get("/api/v1/videos", params={"page": 2, "page_size": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["request_id"]
        assert body["meta"]["page"] == 2
        assert body["meta"]["page_size"] == 5
        assert body["meta"]["total"] == 1
        assert len(body["data"]) == 1
        item = body["data"][0]
        assert item["status"] == "ready"
        assert item["permission_record_id"]
        # 时间统一带 +08:00（03 §1：ISO 8601 且带时区）
        assert item["recorded_start_at"].endswith("+08:00")
        assert item["recorded_end_at"] > item["recorded_start_at"]
        # 只回公开列：不含内部存储路径
        assert "path" not in item and "relative_path" not in item
        # 筛选参数原样透传给 C（分页收敛成 C 的 Page 对象）
        assert uow.calls[0]["page"].page == 2
        assert uow.calls[0]["page"].page_size == 5

    def test_pending_503_without_c_store(self, video_env, monkeypatch):
        """C store 不可用（sqlite 后端）时仍是 503 占位，不假装接通。"""
        monkeypatch.setattr("app.api.v1.videos.get_c_store", lambda: None)
        client = video_env["client"]
        _login(client)
        resp = client.get("/api/v1/videos")
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "dependency_not_available"

    def test_c_errors_are_translated(self, video_env):
        """C 的异常族逐类翻译（NotFound → 404，InvalidArgument → 400）。"""
        from data_foundation import InvalidArgument, NotFound

        for error, expected in ((NotFound("录像不存在"), 404),
                                (InvalidArgument("录像 status 无效"), 400)):
            video_env["install"](_FakeVideoUow(video_env["tmp_path"], error=error))
            client = video_env["client"]
            _login(client)
            resp = client.get("/api/v1/videos")
            assert resp.status_code == expected, (error, resp.text)


# ---------- 25 回放 ----------


class TestPlayback:
    def _get(self, video_env, video_id=None, headers=None):
        client = video_env["client"]
        _login(client)
        return client.get(
            f"/api/v1/videos/{video_id or uuid4()}/playback", headers=headers or {},
        )

    def test_requires_login(self, video_env):
        assert video_env["client"].get(
            f"/api/v1/videos/{uuid4()}/playback").status_code == 401

    def test_full_stream_and_cache_headers(self, video_env):
        uow = video_env["install"](_FakeVideoUow(video_env["tmp_path"]))
        resp = self._get(video_env)
        assert resp.status_code == 200, resp.text
        assert resp.content == uow.content
        assert resp.headers["accept-ranges"] == "bytes"
        # 03 §25：禁止共享缓存
        assert "no-store" in resp.headers["cache-control"]
        assert "private" in resp.headers["cache-control"]
        assert resp.headers["content-length"] == str(len(uow.content))
        # 不泄露服务器路径
        assert str(uow.video_file) not in resp.text
        assert "path" not in {k.lower() for k in resp.headers}

    def test_range_returns_206_with_content_range(self, video_env):
        uow = video_env["install"](_FakeVideoUow(video_env["tmp_path"]))
        resp = self._get(video_env, headers={"Range": "bytes=0-15"})
        assert resp.status_code == 206
        assert resp.content == uow.content[:16]
        assert resp.headers["content-range"] == f"bytes 0-15/{len(uow.content)}"
        assert resp.headers["content-length"] == "16"

    def test_range_open_ended_clamps_to_size(self, video_env):
        uow = video_env["install"](_FakeVideoUow(video_env["tmp_path"]))
        resp = self._get(video_env, headers={"Range": "bytes=1000-"})
        assert resp.status_code == 206
        assert resp.content == uow.content[1000:]
        assert resp.headers["content-range"] == (
            f"bytes 1000-{len(uow.content) - 1}/{len(uow.content)}")

    def test_range_suffix_length(self, video_env):
        uow = video_env["install"](_FakeVideoUow(video_env["tmp_path"]))
        resp = self._get(video_env, headers={"Range": "bytes=-32"})
        assert resp.status_code == 206
        assert resp.content == uow.content[-32:]

    def test_unsatisfiable_range_returns_416(self, video_env):
        uow = video_env["install"](_FakeVideoUow(video_env["tmp_path"]))
        for header in ("bytes=999999-", "bytes=10-5", "items=0-10"):
            resp = self._get(video_env, headers={"Range": header})
            assert resp.status_code == 416, header
            assert resp.headers["content-range"] == f"bytes */{len(uow.content)}"

    def test_permission_denied_maps_to_403(self, video_env):
        """授权核验由 C 完成；``InvalidPermission`` → 403，D 不自己判权限。"""
        from data_foundation import InvalidPermission

        video_env["install"](_FakeVideoUow(
            video_env["tmp_path"], error=InvalidPermission("录像授权无效或不允许回放")))
        resp = self._get(video_env)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    def test_digest_mismatch_maps_to_409(self, video_env):
        """文件与登记摘要不一致 → C ``Conflict`` → 409（不拿坏文件继续播）。"""
        from data_foundation import Conflict

        video_env["install"](_FakeVideoUow(
            video_env["tmp_path"], error=Conflict("回放文件与登记摘要或大小不一致")))
        resp = self._get(video_env)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "conflict"

    def test_missing_file_maps_to_404(self, video_env):
        from data_foundation import NotFound

        video_env["install"](_FakeVideoUow(
            video_env["tmp_path"], error=NotFound("录像文件未在 DATA_ROOT 留存")))
        resp = self._get(video_env)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
