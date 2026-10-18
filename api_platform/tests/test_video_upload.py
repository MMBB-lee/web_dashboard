"""24 POST /videos 录像上传（接 C 0.2.0a2 的 ``persist_file`` + ``register_asset`` + ``register_video``）。

守的是五条：

① **「能不能传」由 C 判定**，D 只判形状。授权无效 / 景区与摄像头不是父子 /
   原件·授权·录像三者来源不一致，全部是 C 抛的 ``InvalidPermission`` /
   ``InvalidArgument``，D 映射成 403 / 400，**不自己实现一套判断**。
② **时间顺序必须由 D 提前校验**：C 的 ``videos`` 表有
   ``CHECK (recorded_end_at > recorded_start_at)``，违反走的是数据库
   IntegrityError（不在 C 的异常族里，会漏成 500）。
③ **原件登记与录像登记在同一个 C 写事务内**，不留没有录像指向的孤儿原件。
④ **授权在落盘之前取**：授权无效时不产生垃圾文件。
⑤ 内部存储路径不出现在响应里；``file_digest`` 只给内容摘要。

⚠️ 契约 §24 说「限定格式、大小和时长」，但**没给具体数值**，格式与大小由 D 定；
**时长 D 不做校验**（无 ffmpeg，判不了），这一点在 README 里如实标注。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings

from data_foundation.errors import (
    InvalidArgument,
    InvalidPermission,
    NotFound,
)


VIDEO_BYTES = b"FAKE-VIDEO-BYTES" * 64
VIDEO_SHA256 = hashlib.sha256(VIDEO_BYTES).hexdigest()


class _FakeProvenanceUow:
    """模拟 ``uow.provenance.register_asset``。"""

    def __init__(self) -> None:
        self.asset_calls: list[dict] = []
        self.next_asset_id = str(uuid4())

    def register_asset(self, data):
        self.asset_calls.append(data)
        return {"id": self.next_asset_id, "source_id": data.source_id,
                "sha256": data.sha256, "byte_size": data.byte_size}


class _FakeVideoUow:
    """模拟 ``uow.video`` 的 ``get_permission`` / ``register_video``。"""

    def __init__(self, *, permission: dict | None = None,
                 video_error: Exception | None = None) -> None:
        self._permission = permission
        self._video_error = video_error
        self.video_calls: list[tuple[dict, object]] = []
        self.next_video_id = str(uuid4())

    def get_permission(self, permission_id: str) -> dict:
        if self._permission is None:
            raise NotFound("授权", id=permission_id)
        return self._permission

    def register_video(self, data: dict, now):
        self.video_calls.append((data, now))
        if self._video_error is not None:
            raise self._video_error
        return {"id": self.next_video_id, "status": "uploaded", **data}


class _FakeCStore:
    """只暴露 24 号用到的三个能力，接口与 ``CStore`` 保持一致。"""

    def __init__(self, video_uow: _FakeVideoUow, provenance_uow: _FakeProvenanceUow) -> None:
        self.video_uow = video_uow
        self.provenance_uow = provenance_uow
        self.persist_calls: list[dict] = []

    def get_permission(self, permission_id: str) -> dict:
        return self.video_uow.get_permission(permission_id)

    def persist_file(self, *, source_id: str, original_name: str, stream) -> dict:
        self.persist_calls.append({"source_id": source_id, "original_name": original_name})
        return {
            "relative_path": f"raw/{source_id}/{VIDEO_SHA256}/original.mp4",
            "sha256": VIDEO_SHA256,
            "byte_size": len(VIDEO_BYTES),
        }

    def register_uploaded_video(self, *, source_id, scenic_id, camera_id,
                                permission_record_id, recorded_start_at,
                                recorded_end_at, camera_location_note,
                                relative_path, sha256, byte_size, media_type,
                                original_name) -> dict:
        asset = self.provenance_uow.register_asset(_AssetCreate(
            source_id=source_id, url=f"internal://video-upload/{sha256}",
            title=original_name, retrieved_at=None, relative_path=relative_path,
            sha256=sha256, media_type=media_type, byte_size=byte_size,
            usage_note="管理员经 24 号接口上传的录像原件", published_at=None,
        ))
        return self.video_uow.register_video(
            {
                "source_id": source_id,
                "asset_id": asset["id"],
                "scenic_id": scenic_id,
                "camera_id": camera_id,
                "permission_record_id": permission_record_id,
                "recorded_start_at": recorded_start_at,
                "recorded_end_at": recorded_end_at,
                "camera_location_note": camera_location_note,
            },
            None,
        )


class _AssetCreate:
    """只需要 dataclass 的字段访问（真正的校验在 C 侧，测试里不重复实现）。"""

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _login(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login", json={"username": "admin1", "password": "admin-pass-123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["csrf_token"]


@pytest.fixture
def upload_env(tmp_path, monkeypatch, fake_store):
    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    client = TestClient(pytest.importorskip("app.main").app)

    def _install(*, permission: dict | None = None,
                 video_error: Exception | None = None) -> _FakeCStore:
        store = _FakeCStore(_FakeVideoUow(permission=permission, video_error=video_error),
                            _FakeProvenanceUow())
        monkeypatch.setattr("app.api.v1.videos.get_c_store", lambda: store)
        return store

    return {"client": client, "install": _install}


def _form(**overrides) -> dict:
    """03 §24 表单必填项；``scenic_id`` / ``camera_id`` / ``permission_record_id`` 用 UUID。"""
    base = {
        "scenic_id": str(uuid4()),
        "camera_id": str(uuid4()),
        "recorded_start_at": "2026-05-01T08:00:00+08:00",
        "recorded_end_at": "2026-05-01T08:30:00+08:00",
        "permission_record_id": str(uuid4()),
    }
    base.update(overrides)
    return base


def _post(client: TestClient, csrf: str, *, filename: str = "clip.mp4",
          content: bytes = VIDEO_BYTES, **form_overrides):
    return client.post(
        "/api/v1/videos",
        files={"file": (filename, content, "video/mp4")},
        data=_form(**form_overrides),
        headers={"X-CSRF-Token": csrf},
    )


def _approved_permission() -> dict:
    return {"id": str(uuid4()), "source_id": "src-video-001", "status": "approved",
            "allowed_uses": ["analysis", "replay"], "playback_allowed": True}


# ---------- 权限与 CSRF ----------


class TestUploadAccess:
    def test_requires_login(self, upload_env):
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = upload_env["client"].post(
            "/api/v1/videos",
            files={"file": ("clip.mp4", VIDEO_BYTES, "video/mp4")},
            data=_form(),
        )
        assert resp.status_code in (401, 403), resp.text
        assert not store.persist_calls

    def test_requires_csrf(self, upload_env):
        upload_env["install"](permission=_approved_permission())
        _login(upload_env["client"])
        resp = upload_env["client"].post(
            "/api/v1/videos",
            files={"file": ("clip.mp4", VIDEO_BYTES, "video/mp4")},
            data=_form(),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_required"

    def test_viewer_cannot_upload(self, upload_env):
        upload_env["install"](permission=_approved_permission())
        client = upload_env["client"]
        resp = client.post("/api/v1/auth/login",
                           json={"username": "viewer1", "password": "viewer-pass-123"})
        csrf = resp.json()["data"]["csrf_token"]
        resp = _post(client, csrf)
        assert resp.status_code == 403


# ---------- 形状校验（D 的职责） ----------


class TestUploadShapeValidation:
    def test_rejects_unknown_suffix(self, upload_env):
        """格式白名单：契约没给具体清单，由 D 定；不在白名单 → 400。"""
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf, filename="clip.exe")
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_request"
        # 格式不合法时不该取授权、更不该落盘
        assert not store.persist_calls

    def test_rejects_oversized_file(self, upload_env, monkeypatch):
        from app.api.v1 import videos as videos_route

        monkeypatch.setattr(videos_route, "VIDEO_MAX_BYTES", 16)
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf)
        assert resp.status_code == 400, resp.text
        assert "大小上限" in resp.json()["error"]["message"]
        assert not store.persist_calls

    def test_rejects_end_not_after_start(self, upload_env):
        """② C 的 CHECK 违约会漏成 500，所以 D 必须先拦。"""
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf,
                     recorded_end_at="2026-05-01T08:00:00+08:00")
        assert resp.status_code == 400, resp.text
        assert not store.persist_calls

    def test_rejects_unparsable_time(self, upload_env):
        upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf, recorded_start_at="昨天早上")
        assert resp.status_code == 400
        assert "ISO 时间" in resp.json()["error"]["message"]

    def test_naive_time_is_read_as_cn_timezone(self, upload_env):
        """naive 时间按 +08:00 解析——业务在中国，不能默认成 UTC。"""
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf,
                     recorded_start_at="2026-05-01T08:00:00",
                     recorded_end_at="2026-05-01T08:30:00")
        assert resp.status_code == 201, resp.text
        data = store.video_uow.video_calls[0][0]
        assert data["recorded_start_at"].utcoffset().total_seconds() == 8 * 3600


# ---------- 「能不能传」由 C 判定 ----------


class TestUploadDefersPermissionToC:
    def test_unknown_permission_record_is_404_before_persist(self, upload_env):
        """④ 授权无效时要**在落盘之前**就拒绝，不留垃圾文件。"""
        store = upload_env["install"](permission=None)
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"
        assert not store.persist_calls
        assert not store.video_uow.video_calls

    def test_invalid_permission_maps_to_403(self, upload_env):
        """C 的 ``InvalidPermission``（授权失效/不允许分析）→ 403，不是 400。"""
        store = upload_env["install"](
            permission=_approved_permission(),
            video_error=InvalidPermission("录像授权无效或不允许分析", use="analysis"),
        )
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf)
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "forbidden"
        # 授权是合法的，所以落过盘；但登记失败 → 没有 video 记录
        assert store.persist_calls
        assert store.video_uow.video_calls

    def test_broken_scenic_camera_relation_maps_to_400(self, upload_env):
        """C 的 ``InvalidArgument``（关系无效 / 来源不一致）→ 400。"""
        upload_env["install"](
            permission=_approved_permission(),
            video_error=InvalidArgument("景区与摄像头关系无效"),
        )
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf)
        assert resp.status_code == 400, resp.text
        # message 是 D 的统一文案，C 的原文落在 detail 里（不篡改、也不外泄内部细节到 message）
        assert resp.json()["error"]["message"] == "录像登记：请求参数不被接受"
        assert resp.json()["error"]["details"]["detail"] == "景区与摄像头关系无效"


# ---------- 成功路径 ----------


class TestUploadSuccess:
    def test_returns_201_with_digest_and_status(self, upload_env):
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "uploaded"
        assert body["file_digest"] == VIDEO_SHA256
        assert body["video_id"]

    def test_response_leaks_no_internal_path(self, upload_env):
        """⑤ 内部存储路径（relative_path / 绝对路径）绝不出现在响应里。"""
        upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        body = _post(upload_env["client"], csrf).json()
        assert "raw/" not in str(body)
        assert "relative_path" not in body
        assert "path" not in body

    def test_asset_and_video_share_one_transaction(self, upload_env):
        """③ 原件登记与录像登记必须同事务：asset_id 来自刚登记的原件。"""
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        assert _post(upload_env["client"], csrf).status_code == 201
        assert len(store.provenance_uow.asset_calls) == 1
        assert len(store.video_uow.video_calls) == 1
        asset = store.provenance_uow.asset_calls[0]
        video_data = store.video_uow.video_calls[0][0]
        assert video_data["asset_id"] == store.provenance_uow.next_asset_id
        assert asset.sha256 == VIDEO_SHA256
        assert asset.byte_size == len(VIDEO_BYTES)
        assert asset.media_type == "video/mp4"
        assert not asset.relative_path.startswith("/")  # 相对路径，不落绝对路径

    def test_source_id_comes_from_permission(self, upload_env):
        """录像 / 原件 / 授权三者来源必须一致——source_id 从授权记录派生。"""
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        assert _post(upload_env["client"], csrf).status_code == 201
        assert store.persist_calls[0]["source_id"] == "src-video-001"
        assert store.provenance_uow.asset_calls[0].source_id == "src-video-001"
        assert store.video_uow.video_calls[0][0]["source_id"] == "src-video-001"

    def test_camera_location_note_is_optional(self, upload_env):
        store = upload_env["install"](permission=_approved_permission())
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf, camera_location_note="东门闸机上方")
        assert resp.status_code == 201, resp.text
        assert store.video_uow.video_calls[0][0]["camera_location_note"] == "东门闸机上方"


# ---------- C 不可用 ----------


class TestUploadWithoutC:
    def test_pending_when_store_missing(self, upload_env, monkeypatch):
        monkeypatch.setattr("app.api.v1.videos.get_c_store", lambda: None)
        csrf = _login(upload_env["client"])
        resp = _post(upload_env["client"], csrf)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "dependency_not_available"
