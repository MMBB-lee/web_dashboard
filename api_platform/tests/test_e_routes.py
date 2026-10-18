"""E 实调路由与 worker 闭环测试（E 0.5.0：D 是唯一队列消费者）。

前提：E 的 intelligent_analysis 包（0.5.0，ServiceBindings 契约）已
pip install -e 到当前 venv。E 包缺失环境的回退（503）用 monkeypatch
_try_import 模拟验证。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services import e_bindings
from app.services.e_bridge import document_version_from_bytes
from app.services.job_store import SqliteJobStore
from app.worker import DWorker, DegradedWorker
from tests.fakes import FakeVisibleStore

#: 测试用 C 口径版本标识（C manifest 中固定资料的真实值，形态 sha256:<hex32>）
DOC_VERSION = "sha256:29aa803a0fa3e009feed5ce3aa07330e"


@pytest.fixture
def e_env(tmp_path, monkeypatch, fake_store):
    """临时数据目录：作业库、暂存文件、RAG 索引全走 tmp_path，不污染仓库。"""
    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        detector_weights_path=str(tmp_path / "weights"),  # 目录不存在 → 权重未登记
        # P2PNet 模型包未登记：显式指向 tmp 下**不存在**的目录，
        # 否则默认路径会命中仓库 data/p2pnet_bundle（探针建的空目录），
        # 让「未登记 → 422」用例假通过。
        p2pnet_bundle_dir=str(tmp_path / "p2pnet_bundle"),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    # E 0.6.0：注入 D 拥有的队列/读取绑定（指向 tmp 的作业库与快照路径）
    assert e_bindings.configure_e_services() is True
    client = TestClient(pytest.importorskip("app.main").app)

    # 2026-10-17 起 26—28 的成果统一落在 C 的 ``uow.visible``（不再走 D 私表
    # ``d_video_analyses``）。这里注入内存替身，替身**保留 C 的拒绝语义**：
    # 归属不匹配抛 NotFound、未 ready 返回 analysis_not_ready（见 tests/fakes.py）。
    visible = FakeVisibleStore()
    monkeypatch.setattr("app.core.providers.get_c_store", lambda: visible)
    monkeypatch.setattr("app.api.v1.videos.get_c_store", lambda: visible)
    return {"settings": settings, "client": client, "tmp_path": tmp_path,
            "visible": visible}


def _login_admin(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin1", "password": "admin-pass-123"},
    )
    return resp.json()["data"]["csrf_token"]


def _auth(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


def _register_processing_analysis(e_env, video_id: str, analysis_id: str) -> str:
    """登记一条 queued 分析（模拟「26 已受理、worker 还没干完」）。

    2026-10-17 起归属与状态都在 **C 的画面人数仓储**里，不再写 D 私表
    ``d_video_analyses``——26 号登记走的就是这条路，27 号读的也是它。
    """
    return e_env["visible"].seed_queued(video_id=video_id, analysis_id=analysis_id)


class TestAssistantChat:
    def test_chat_returns_structured_answer(self, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/assistant/chat",
            json={"message": "黄山几点开放？"},
            headers=_auth(csrf),
        )
        assert resp.status_code == 200
        body = resp.json()
        # 03 契约 §1：成功响应统一 {"data":..., "meta":{"request_id"}}
        assert body["meta"]["request_id"]
        data = body["data"]
        assert data["status"] in ("answered", "insufficient_evidence")
        assert data["conversation_id"]
        assert isinstance(data["citations"], list)
        assert isinstance(data["limitations"], list)

    def test_chat_with_conversation_id_roundtrip(self, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        conv = str(uuid4())
        resp = client.post(
            "/api/v1/assistant/chat",
            json={"message": "索道运行时间？", "conversation_id": conv},
            headers=_auth(csrf),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["conversation_id"] == conv

    def test_chat_rejects_bad_conversation_id(self, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/assistant/chat",
            json={"message": "你好", "conversation_id": "not-a-uuid"},
            headers=_auth(csrf),
        )
        assert resp.status_code == 400


class TestKnowledgeUpload:
    def _upload(self, client, filename=b"notice.md", content=None, form_extra=None):
        if content is None:
            content = ("# notice\n黄山景区开放时间说明。" * 5).encode("utf-8")
        csrf = _login_admin(client)
        form = {
            "title": "黄山开放公告",
            "publisher": "黄山风景区管理委员会",
            "source_url": "https://example.gov.cn/notice",
            "published_at": "2026-09-01",
            "permission_note": "已获授权用于本系统",
        }
        form.update(form_extra or {})
        return client.post(
            "/api/v1/knowledge/documents",
            files={"file": (filename.decode(), content, "text/markdown")},
            data=form,
            headers=_auth(csrf),
        )

    def test_upload_accepted_202_and_job_enqueued(self, e_env):
        resp = self._upload(e_env["client"])
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["meta"]["request_id"]
        data = body["data"]
        assert data["index_status"] == "pending"
        # E 0.5.0：document_id 为 UUID（不再由文件名推导 doc-xxx）
        assert len(data["document_id"]) == 36 and data["document_id"].count("-") == 4
        assert data["job_id"]
        # 作业已经 D 的 submit 绑定真实入队（路由不再二次入队，全库只有一条）
        store = SqliteJobStore(e_env["settings"].job_store_path)
        job = store.get_job(data["job_id"])
        assert job is not None
        assert job["type"] == "knowledge_index"
        assert job["status"] == "queued"
        assert job["analysis_id"] == data["document_id"]
        # E 的 payload 顶层即身份；metadata 是 E 校验后的元数据 dump。
        # ⚠️ E 0.6.1 起 ``index_version`` 与 ``document_version`` **解耦**（E service.py:71）：
        # 未显式传 ``index_version`` 时 E 自动生成 ``e-index-<uuid4 hex>`` 填到 payload 顶层
        # ``version``；``document_version`` 保持 C 口径 ``sha256:<32位hex>``。
        # 二者不再相等（E 0.6.0 的旧断言 ``document_version == version`` 已失效）。
        # D 的 worker 执行时会以 C 口径的 ``v<n>``（worker._resolve_index_version）覆盖
        # index_version 并写入产物，故入队 payload 的 ``e-index-*`` 只是中转值。
        assert job["payload"]["document_id"] == data["document_id"]
        assert job["payload"]["document_version"].startswith("sha256:")
        assert len(job["payload"]["document_version"]) == 39
        assert job["payload"]["version"].startswith("e-index-")
        assert len(job["payload"]["version"]) == len("e-index-") + 32
        assert job["payload"]["version"] != job["payload"]["document_version"]
        assert job["payload"]["metadata"]["title"] == "黄山开放公告"

    def test_upload_metadata_dates_normalised_to_cn_tz(self, e_env):
        """表单 naive 日期按东八区补全（E 0.5.0 要求 tz-aware），否则 E 拒收。"""
        resp = self._upload(
            e_env["client"],
            form_extra={"valid_from": "2026-09-01", "valid_until": "2026-12-31"},
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        store = SqliteJobStore(e_env["settings"].job_store_path)
        metadata = store.get_job(data["job_id"])["payload"]["metadata"]
        assert metadata["published_at"].endswith("+08:00")
        assert metadata["valid_from"] == "2026-09-01T00:00:00+08:00"
        assert metadata["valid_until"] == "2026-12-31T00:00:00+08:00"

    def test_upload_with_entity_ids_accepted(self, e_env):
        """03 契约 §20：entity_ids 为可选 UUID 数组（D 校验格式；C store 接入后关联实体）。"""
        e1, e2 = str(uuid4()), str(uuid4())
        resp = self._upload(
            e_env["client"],
            form_extra={"entity_ids": [e1, e2]},
        )
        assert resp.status_code == 202, resp.text

    def test_upload_rejects_non_uuid_entity_ids(self, e_env):
        resp = self._upload(
            e_env["client"],
            form_extra={"entity_ids": ["not-a-uuid"]},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_upload_rejects_unsupported_suffix(self, e_env):
        resp = self._upload(e_env["client"], filename=b"virus.exe", content=b"MZ...")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_upload_requires_source_url_and_published_at(self, e_env):
        """03 契约 §20：title/publisher/source_url/published_at 均必填。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("a.md", b"x", "text/markdown")},
            data={"title": "t", "publisher": "p", "permission_note": "n"},  # 缺 source_url/published_at
            headers=_auth(csrf),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_upload_requires_admin(self, e_env):
        client = e_env["client"]
        resp = client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "viewer-pass-123"})
        csrf = resp.json()["data"]["csrf_token"]
        resp = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("a.md", b"x", "text/markdown")},
            data={
                "title": "t", "publisher": "p", "permission_note": "n",
                "source_url": "https://example.gov.cn", "published_at": "2026-09-01",
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 403


class TestKnowledgeDocument:
    """21 GET /knowledge/documents/{document_id}（G04 关闭后实现）。"""

    def test_get_document_pending_after_upload(self, e_env):
        """20 上传 → 21 查询：元数据回读一致，索引未跑时 index_status=pending、无片段。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        upload = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("notice.md", ("# 公告\n黄山每日六点开放。" * 5).encode("utf-8"), "text/markdown")},
            data={
                "title": "黄山开放公告", "publisher": "黄山管委会",
                "source_url": "https://example.gov.cn/notice",
                "published_at": "2026-09-01", "permission_note": "已授权",
                "valid_from": "2026-09-01", "valid_until": "2026-12-31",
            },
            headers=_auth(csrf),
        )
        assert upload.status_code == 202, upload.text
        doc_id = upload.json()["data"]["document_id"]

        resp = client.get(f"/api/v1/knowledge/documents/{doc_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["request_id"]  # 03 §1：Envelope 包裹
        data = body["data"]
        assert data["id"] == doc_id
        assert data["title"] == "黄山开放公告"
        assert data["publisher"] == "黄山管委会"
        assert data["source_url"] == "https://example.gov.cn/notice"  # C 0.1.3：可追溯来源
        assert data["published_at"].startswith("2026-09-01")
        assert data["valid_from"].startswith("2026-09-01")
        assert data["valid_until"].startswith("2026-12-31")
        assert data["permission_note"] == "已授权"
        assert data["index_status"] == "pending"
        assert data["allowed_snippets"] == []

    def test_get_document_ready_with_snippets_after_worker(self, e_env):
        """worker 完成索引后：index_status=ready，返回获准片段（受限原件不公开）。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        upload = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("notice.md", ("# 公告\n黄山索道七点运行。" * 10).encode("utf-8"), "text/markdown")},
            data={
                "title": "索道公告", "publisher": "黄山管委会",
                "source_url": "https://example.gov.cn/cableway",
                "published_at": "2026-09-02", "permission_note": "已授权",
            },
            headers=_auth(csrf),
        )
        doc_id = upload.json()["data"]["document_id"]

        store = SqliteJobStore(e_env["settings"].job_store_path)
        worker = DWorker(store, counter=None)
        assert worker.run_once() is not None
        assert store.get_job(upload.json()["data"]["job_id"])["status"] == "succeeded"

        resp = client.get(f"/api/v1/knowledge/documents/{doc_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["index_status"] == "ready"
        assert data["allowed_snippets"], "索引 ready 后应返回获准片段"
        assert all(len(s) <= 200 for s in data["allowed_snippets"])

    def test_get_document_404_for_unknown_id(self, e_env):
        """格式合法（UUID）但不存在的文档 → 404。"""
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(f"/api/v1/knowledge/documents/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_get_document_rejects_bad_format(self, e_env):
        """非 UUID 亦非 doc-<hex12> → 400（03 契约参数错误口径）。"""
        client = e_env["client"]
        _login_admin(client)
        for bad in ("not-a-valid-id", "doc-XYZ", "doc-0123456789abc"):
            resp = client.get(f"/api/v1/knowledge/documents/{bad}")
            assert resp.status_code == 400, bad

    def test_get_document_requires_login(self, e_env):
        """03 契约 §21：已登录；未登录 → 401。"""
        client = e_env["client"]
        resp = client.get(f"/api/v1/knowledge/documents/doc-0123456789ab")
        assert resp.status_code == 401


class TestReindex:
    def test_reindex_passthrough(self, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/knowledge/reindex",
            json={"document_ids": [str(uuid4()), str(uuid4())], "reason": "修复索引"},
            headers=_auth(csrf),
        )
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["pending_count"] == 2
        assert data["job_id"]

    def test_reindex_rejects_non_uuid_document_ids(self, e_env):
        """03 契约 §22：document_ids 为 1—100 个 UUID。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/knowledge/reindex",
            json={"document_ids": ["not-a-uuid"], "reason": "修复索引"},
            headers=_auth(csrf),
        )
        assert resp.status_code == 400


class TestVideoRoutes:
    """26—28：2026-10-15 版契约（E 0.6.0）改用 P2PNet 的 camera_visible_people 口径。"""

    ROI = {"points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]}

    def _analysis_payload(self, **overrides):
        payload = {
            "count_model": "p2pnet",
            "weights_version": "leader-SHTechA-50604773",
            "roi": self.ROI,
            "sample_interval_seconds": 10,
            "aggregation_minutes": 5,
            "inference_engine": "pytorch",
            "device": "CPU",
        }
        payload.update(overrides)
        return payload

    def test_create_analysis_returns_422_without_p2p_bundle(self, e_env):
        """E 0.6.0 契约已就绪，但 P2PNet 模型包未登记 → 422 unsupported（诚实失败，不伪造）。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/analyses",
            json=self._analysis_payload(),
            headers=_auth(csrf),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "unsupported"

    def test_create_analysis_rejects_legacy_dm_count(self, e_env):
        """E 0.6.0 只接受 p2pnet：旧 dm_count 报文必须 400（schema 拒收，不静默回退密度图）。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/analyses",
            json=self._analysis_payload(count_model="dm_count"),
            headers=_auth(csrf),
        )
        assert resp.status_code == 400, resp.text

    #: C ``create_analysis`` 要求的完整七键溯源；**不能只给两键**——C 拿不到就
    #: 拒，D 也不替它编（26 号宁可 422，不让一条无法追溯权重的分析进队列）。
    FULL_PROVENANCE = {
        "count_model": "p2pnet",
        "weights_version": "leader-SHTechA-50604773",
        "weights_sha256": "506047732b128ff09efef18e94bfacbe35fcfef300e5e9eeeece259b0488c63f",
        "model_source_revision": "team-zip-2981dc75",
        "confidence_threshold": 0.5,
        "inference_engine": "pytorch",
        "device": "CPU",
    }

    def test_create_analysis_202_when_e_implements_new_contract(self, e_env, monkeypatch):
        """P2PNet 校验通过时（假 counter），同一请求应返回 202 且**在 C 侧双双落地**。

        2026-10-17 起「落地」的含义变了：不再是往 D 私表补一行，而是同一个写事务
        里产出 ``video_analysis`` 作业 + ``visible_analyses`` 的 queued 记录，且
        两者互相绑定（worker 领取后才能按同一个 analysis_id 写回结果）。
        """
        from uuid import uuid4 as _uuid4

        class _FakeCounter:
            @staticmethod
            def validate_config(config):
                return None

        # 26 路由不再经 submit_job 入队（改由 CStore 同一事务完成），这里只 mock
        # 「counter 可用 + 溯源齐全」这两个 E 侧前提。
        monkeypatch.setattr("app.services.e_bindings.get_p2p_counter", lambda: _FakeCounter())
        monkeypatch.setattr(
            "app.services.e_bindings.get_p2p_counter_provenance",
            lambda: dict(self.FULL_PROVENANCE),
        )
        assert e_bindings.configure_e_services() is True  # 重新注入带 validate 的绑定

        client = e_env["client"]
        csrf = _login_admin(client)
        video_id = str(_uuid4())
        resp = client.post(
            f"/api/v1/videos/{video_id}/analyses",
            json=self._analysis_payload(),
            headers=_auth(csrf),
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        assert data["status"] == "queued"
        assert data["analysis_id"]
        assert data["video_id"] == video_id
        assert data["count_metric"] == "camera_visible_people"

        # 同一事务的两侧：作业进了队列、分析登记为 queued，且互相指向对方
        visible = e_env["visible"]
        job = visible.get_job(data["job_id"])
        assert job is not None
        assert job["type"] == "video_analysis"
        assert job["payload"]["analysis_id"] == data["analysis_id"]
        analysis = visible.get_visible_analysis(data["analysis_id"])
        assert analysis["job_id"] == data["job_id"]
        assert analysis["status"] == "queued"
        # ROI 归一成 C 的对象数组；阈值由 D 注入（不在请求体里）
        assert analysis["config"]["roi"]["points"][0] == {"x": 0.1, "y": 0.1}
        assert analysis["config"]["confidence_threshold"] == 0.5

    def test_config_and_provenance_must_declare_same_model(self, e_env, monkeypatch):
        """同一份分析不能声明两套模型身份：config 说 openvino、溯源说 pytorch → 422。

        C 会在 ``create_analysis`` 里比对两处重叠键，但报错不带具体键名、且发生在
        异步阶段更难查。D 因此在 HTTP 入口先自查一次。
        """
        class _FakeCounter:
            @staticmethod
            def validate_config(config):
                return None

        monkeypatch.setattr("app.services.e_bindings.get_p2p_counter", lambda: _FakeCounter())
        monkeypatch.setattr(
            "app.services.e_bindings.get_p2p_counter_provenance",
            lambda: dict(self.FULL_PROVENANCE),  # pytorch / CPU
        )
        assert e_bindings.configure_e_services() is True

        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/analyses",
            json=self._analysis_payload(inference_engine="openvino",
                                        openvino_version="2024.4"),
            headers=_auth(csrf),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "unsupported"
        assert "inference_engine" in body["error"]["message"]

    def test_incomplete_provenance_is_rejected_not_fabricated(self, e_env, monkeypatch):
        """溯源不齐全 → 422，**D 不补默认值给 C**。

        伪造 ``weights_sha256`` 会留下一条无法追溯到真实权重的「成功」分析，
        比直接拒绝危险得多。
        """
        class _FakeCounter:
            @staticmethod
            def validate_config(config):
                return None

        monkeypatch.setattr("app.services.e_bindings.get_p2p_counter", lambda: _FakeCounter())
        monkeypatch.setattr(
            "app.services.e_bindings.get_p2p_counter_provenance",
            lambda: {"count_model": "p2pnet", "weights_version": "leader-SHTechA-50604773"},
        )
        assert e_bindings.configure_e_services() is True

        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/analyses",
            json=self._analysis_payload(),
            headers=_auth(csrf),
        )
        assert resp.status_code == 422, resp.text
        assert "weights_sha256" in resp.json()["error"]["message"]
        # 队列里不该留下任何东西
        assert e_env["visible"].jobs == {}

    def test_create_analysis_real_e_enqueues_via_binding(self, e_env, monkeypatch):
        """走真实 E 0.6.0 的校验器：通过后**同一事务**入队 + 在 C 登记分析。

        本机不跑真 P2PNet 权重推理（耗时且非本测试目的），故注入假 counter
        （只验证「E 校验通过 → C 入队登记」这条链路）；模型包未登记的 422 分支
        由 ``test_create_analysis_returns_422_without_p2p_bundle`` 覆盖。
        """

        class _FakeCounter:
            @staticmethod
            def validate_config(config):
                return None

        monkeypatch.setattr("app.services.e_bindings.get_p2p_counter", lambda: _FakeCounter())
        monkeypatch.setattr(
            "app.services.e_bindings.get_p2p_counter_provenance",
            lambda: dict(self.FULL_PROVENANCE),
        )
        assert e_bindings.configure_e_services() is True  # 重新注入带 validate_analysis 的绑定

        client = e_env["client"]
        csrf = _login_admin(client)
        video_id = str(uuid4())
        resp = client.post(
            f"/api/v1/videos/{video_id}/analyses",
            json=self._analysis_payload(),
            headers=_auth(csrf),
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()["data"]
        assert data["status"] == "queued"
        assert data["count_metric"] == "camera_visible_people"

        visible = e_env["visible"]
        job = visible.get_job(data["job_id"])
        assert job["type"] == "video_analysis"
        assert job["payload"]["analysis_id"] == data["analysis_id"]
        # 给 C 的 config 里 ROI 已转成对象数组（公共契约 [[x,y]] 不受影响）
        analysis = visible.get_visible_analysis(data["analysis_id"])
        assert analysis["config"]["roi"]["points"][0] == {"x": 0.1, "y": 0.1}
        # 登记为 queued：27 号此时应答 409（不是 404，归属已在 C 侧确立）
        counts = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": data["analysis_id"], "grain": "5min"},
        )
        assert counts.status_code == 409
        assert counts.json()["error"]["code"] == "analysis_not_ready"

    def test_create_analysis_rejects_bad_video_id(self, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/videos/not-a-uuid/analyses",
            json=self._analysis_payload(),
            headers=_auth(csrf),
        )
        assert resp.status_code == 400

    def test_create_analysis_rejects_non_5min_aggregation(self, e_env):
        """03 §26：aggregation_minutes 首版固定 5。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/analyses",
            json=self._analysis_payload(aggregation_minutes=15),
            headers=_auth(csrf),
        )
        assert resp.status_code == 400

    def test_create_analysis_requires_openvino_version(self, e_env):
        """03 §26：inference_engine=openvino 时必须记录 openvino_version。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/analyses",
            json=self._analysis_payload(inference_engine="openvino"),
            headers=_auth(csrf),
        )
        assert resp.status_code == 400

    def test_counts_not_ready_returns_409(self, e_env):
        """已登记（26 受理）但 worker 未完成 → 409；完全不存在的分析 → 404。"""
        client = e_env["client"]
        _login_admin(client)  # 只需登录，不需要 CSRF（GET）
        video_id, analysis_id = str(uuid4()), str(uuid4())
        _register_processing_analysis(e_env, video_id, analysis_id)
        resp = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": analysis_id, "grain": "5min"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "analysis_not_ready"

    def test_counts_unknown_analysis_returns_404(self, e_env):
        """归属校验（B 联调约定）：未登记的 analysis_id → 404，不冒充未完成。"""
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(
            f"/api/v1/videos/{uuid4()}/counts",
            params={"analysis_id": str(uuid4()), "grain": "5min"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_counts_rejects_bad_analysis_id(self, e_env):
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(
            f"/api/v1/videos/{uuid4()}/counts",
            params={"analysis_id": "junk"},
        )
        assert resp.status_code == 400

    def test_counts_rejects_unsupported_grain(self, e_env):
        """03 §8（27）+ 状态码约定：不支持的粒度返回 422 unsupported_grain，而不是 500。"""
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(
            f"/api/v1/videos/{uuid4()}/counts",
            params={"analysis_id": str(uuid4()), "grain": "hour"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"]["code"] == "unsupported_grain"
        assert body["error"]["details"]["grain"] == "hour"
        assert body["error"]["details"]["supported"] == ["frame", "5min"]

    def test_counts_accepts_frame_and_5min(self, e_env):
        """frame | 5min 是合法粒度：分析未完成时应为 409，不应被粒度校验拦成 422。"""
        client = e_env["client"]
        _login_admin(client)
        for grain in ("frame", "5min"):
            video_id, analysis_id = str(uuid4()), str(uuid4())
            _register_processing_analysis(e_env, video_id, analysis_id)
            resp = client.get(
                f"/api/v1/videos/{video_id}/counts",
                params={"analysis_id": analysis_id, "grain": grain},
            )
            assert resp.status_code == 409, (grain, resp.text)
            assert resp.json()["error"]["code"] == "analysis_not_ready"

    def test_counts_rejects_legacy_entries_exits_payload(self, e_env):
        """结果点若不是画面人数口径（旧越线进出的 entries/exits），D 拒绝而不是换算。

        2026-10-17 数据源换成 C 的 ``visible_count_points`` 之后这条防线**仍在**：
        ``CountPoint`` 的 ``quality_status`` / ``source_ids`` 必填，少了直接 500；
        缺 ``value`` 又会安静返回 ``value: null`` 的半成形数据。两种下场都比
        一个明确的 422 糟。
        """
        client = e_env["client"]
        _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        # grain 在 C 的 visible_count_points 里是 NOT NULL，真实结果点必带
        e_env["visible"].seed_ready(
            video_id=video_id, analysis_id=analysis_id,
            points=[
                {
                    "grain": "5min",
                    "period_start": "2026-05-01T10:00:00+08:00",
                    "period_end": "2026-05-01T10:05:00+08:00",
                    "entries": 12,
                    "exits": 3,
                    "quality_status": "good",
                }
            ],
        )
        resp = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": analysis_id},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "unsupported"

    def test_counts_rejects_point_without_source(self, e_env):
        """带 value 但缺 source_ids 的点同样拒收——无法溯源的结果不算结果。"""
        client = e_env["client"]
        _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        e_env["visible"].seed_ready(
            video_id=video_id, analysis_id=analysis_id,
            points=[
                {
                    "grain": "5min",
                    "period_start": "2026-05-01T10:00:00+08:00",
                    "period_end": "2026-05-01T10:05:00+08:00",
                    "value": 12.0,
                    "quality_status": "good",
                }
            ],
        )
        resp = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": analysis_id, "grain": "5min"},
        )
        assert resp.status_code == 422
        assert "source_ids" in resp.json()["error"]["message"]

    def test_short_forecast_data_insufficient_is_200(self, e_env):
        """分析已登记未完成：E 无可用计数历史 → 200 data_insufficient（合法结果）。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        _register_processing_analysis(e_env, video_id, analysis_id)
        resp = client.post(
            f"/api/v1/videos/{video_id}/short-forecast",
            json={
                "analysis_id": analysis_id,
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 5,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["metric"] == "camera_visible_people"
        assert data["status"] == "data_insufficient"  # E 当前无计数历史，合法结果
        assert data["reason"]

    def test_horizon_60_accepted_by_schema_and_disabled_inside_e(self, e_env):
        """28：60 分钟跨度——D 的 schema 放行，由 E 0.6.0 内部关闭（不自行伪造预测点）。

        E ``short_forecast/baseline.py`` 明确：
        ``if horizon_minutes == 60: status=data_insufficient, reason="60-minute
        forecasting is disabled pending multi-day validation"``。
        D 只负责**原样透传**，不在 D 侧改写成 422，也不补造预测点。
        """
        baseline = pytest.importorskip(
            "intelligent_analysis.short_forecast.baseline",
            reason="E 的短时预测模块不可用",
        )
        from datetime import datetime, timedelta, timezone

        tz = timezone(timedelta(hours=8))
        cutoff = datetime(2026, 5, 1, 12, 0, tzinfo=tz)
        history = [
            (cutoff - timedelta(minutes=5 * (index + 1)), 10.0 + index)
            for index in range(30)
        ]
        result = baseline.forecast_visible_history(history, cutoff, 60, 5)
        assert result["status"] == "data_insufficient"
        assert result["forecast_points"] == []
        assert "60" in result["reason"] and "disabled" in result["reason"]
        # 对照：15/30 分钟在同样历史下应当能出预测点
        assert baseline.forecast_visible_history(history, cutoff, 30, 5)["status"] in (
            "ready", "data_insufficient", "quality_insufficient",
        )

    def test_horizon_60_route_returns_200_with_insufficient_status(self, e_env):
        """28 路由层：horizon_minutes=60 不被 D 判为参数错误（E 侧给出不足理由）。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        _register_processing_analysis(e_env, video_id, analysis_id)
        resp = client.post(
            f"/api/v1/videos/{video_id}/short-forecast",
            json={
                "analysis_id": analysis_id,
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 60,
                "step_minutes": 5,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "data_insufficient"
        assert data["forecast_points"] == []
        assert data["reason"]

    def test_short_forecast_rejects_direction_field(self, e_env):
        """03 §28：不再接收进出方向参数。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/short-forecast",
            json={
                "analysis_id": str(uuid4()),
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 5,
                "direction": "entries",
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 400

    def test_short_forecast_rejects_non_5min_step(self, e_env):
        """03 §28：step_minutes 首版固定 5。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            f"/api/v1/videos/{uuid4()}/short-forecast",
            json={
                "analysis_id": str(uuid4()),
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 15,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 400

    WEIGHTS_SHA256 = "506047732b128ff09efef18e94bfacbe35fcfef300e5e9eeeece259b0488c63f"

    def test_counts_response_meta_on_top_and_roi_as_pairs(self, e_env):
        """27：元数据在顶层、ROI 归一化成 [x,y][]、审计两键来自 C 的 provenance。

        2026-10-17 起元数据的来源是 **C 的 ``visible_analyses.config``**，审计摘要
        来自**同一行**的 ``provenance``——两者同源才有意义：config 说 ``pytorch``
        而溯源说别的模型的分析，26 号就已经拒绝了。
        """
        client = e_env["client"]
        _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        e_env["visible"].seed_ready(
            video_id=video_id, analysis_id=analysis_id,
            provenance={
                "count_model": "p2pnet",
                "weights_version": "leader-SHTechA-50604773",
                "weights_sha256": self.WEIGHTS_SHA256,
                "model_source_revision": "team-zip-2981dc75",
                "confidence_threshold": 0.5,
                "inference_engine": "pytorch",
                "device": "CPU",
            },
            points=[
                {
                    "grain": "5min",
                    "period_start": "2026-05-01T10:00:00+08:00",
                    "period_end": "2026-05-01T10:05:00+08:00",
                    "value": 12.0,
                    "unit": "人",
                    "quality_status": "good",
                    "sample_count": 30,
                    "aggregation_rule": "median",
                    "source_ids": ["11111111-1111-1111-1111-111111111111"],
                }
            ],
        )
        resp = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": analysis_id, "grain": "5min"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["metric"] == "camera_visible_people"
        # 元数据在顶层，且不再有 analysis 子对象
        assert data["count_model"] == "p2pnet"
        assert data["weights_version"] == "leader-SHTechA-50604773"
        assert data["confidence_threshold"] == 0.5
        assert data["inference_engine"] == "pytorch"
        assert data["device"] == "CPU"
        assert data["sample_interval_seconds"] == 10
        # 审计摘要：不经 E（CountsResponse 是 extra="forbid"），由 D 从 C 的
        # provenance 单独读，也放在顶层
        assert data["weights_sha256"] == self.WEIGHTS_SHA256
        assert data["model_source_revision"] == "team-zip-2981dc75"
        assert "analysis" not in data
        # ROI 归一化成 [x, y][]（C 的对象数组 → B 约定的成对数组）
        assert data["roi"]["points"][0] == [0.1, 0.1]
        assert len(data["roi"]["points"]) == 3
        # 点的值与口径一律按 C 给的透传，D 不重算中位数
        point = data["points"][0]
        assert point["value"] == 12.0
        assert point["sample_count"] == 30
        assert point["aggregation_rule"] == "median"

    def test_counts_never_mixes_grains(self, e_env):
        """27：C 一次给全 frame + 5min，D 按 grain 过滤——**不能混着返回**。"""
        client = e_env["client"]
        _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        shared = {"quality_status": "good",
                  "source_ids": ["11111111-1111-1111-1111-111111111111"]}
        e_env["visible"].seed_ready(
            video_id=video_id, analysis_id=analysis_id,
            points=[
                {"grain": "frame", "value": 7.0, "recorded_at": "2026-05-01T10:00:00+08:00", **shared},
                {"grain": "frame", "value": 8.0, "recorded_at": "2026-05-01T10:00:10+08:00", **shared},
                {"grain": "5min", "value": 12.0,
                 "period_start": "2026-05-01T10:00:00+08:00",
                 "period_end": "2026-05-01T10:05:00+08:00", **shared},
            ],
        )
        five_min = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": analysis_id, "grain": "5min"},
        )
        assert five_min.status_code == 200
        points = five_min.json()["data"]["points"]
        assert len(points) == 1
        assert points[0]["grain"] == "5min"
        assert points[0]["value"] == 12.0

        frame = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={"analysis_id": analysis_id, "grain": "frame"},
        )
        assert frame.status_code == 200
        frame_points = frame.json()["data"]["points"]
        assert len(frame_points) == 2
        assert all(p["grain"] == "frame" for p in frame_points)

    def test_counts_time_filter_is_closed_interval(self, e_env):
        """27：frame 点闭区间端点命中；5min **只保留完整落入区间的窗口**。

        C《致D和E-021a2接收核对与补充答复》§4 明确：5min 不能把横跨边界的窗口
        算进来，那等于拿半个窗口的数值冒充完整 5 分钟。所以对查询
        [10:00, 10:05] 而言，09:55—10:00 与 10:05—10:10 都要剔除，只剩
        10:00—10:05 这一个整窗。
        """
        client = e_env["client"]
        _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        shared = {"quality_status": "good",
                  "source_ids": ["11111111-1111-1111-1111-111111111111"]}
        e_env["visible"].seed_ready(
            video_id=video_id, analysis_id=analysis_id,
            points=[
                {"grain": "5min", "value": 10.0,
                 "period_start": "2026-05-01T09:55:00+08:00",
                 "period_end": "2026-05-01T10:00:00+08:00", **shared},
                {"grain": "5min", "value": 12.0,
                 "period_start": "2026-05-01T10:00:00+08:00",
                 "period_end": "2026-05-01T10:05:00+08:00", **shared},
                {"grain": "5min", "value": 14.0,
                 "period_start": "2026-05-01T10:05:00+08:00",
                 "period_end": "2026-05-01T10:10:00+08:00", **shared},
                {"grain": "frame", "value": 1.0,
                 "recorded_at": "2026-05-01T10:00:00+08:00", **shared},
                {"grain": "frame", "value": 2.0,
                 "recorded_at": "2026-05-01T10:05:00+08:00", **shared},
                {"grain": "frame", "value": 3.0,
                 "recorded_at": "2026-05-01T10:06:00+08:00", **shared},
            ],
        )
        resp = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={
                "analysis_id": analysis_id,
                "grain": "5min",
                "start": "2026-05-01T10:00:00+08:00",
                "end": "2026-05-01T10:05:00+08:00",
            },
        )
        assert resp.status_code == 200
        values = sorted(p["value"] for p in resp.json()["data"]["points"])
        assert values == [12.0], "只有完整落入 [10:00, 10:05] 的整窗可以出现"

        resp2 = client.get(
            f"/api/v1/videos/{video_id}/counts",
            params={
                "analysis_id": analysis_id,
                "grain": "frame",
                "start": "2026-05-01T10:00:00+08:00",
                "end": "2026-05-01T10:05:00+08:00",
            },
        )
        assert resp2.status_code == 200
        frame_values = sorted(p["value"] for p in resp2.json()["data"]["points"])
        # 闭区间：10:00 与 10:05 两个端点都命中，10:06 在区间外
        assert frame_values == [1.0, 2.0]

    def test_short_forecast_response_shape(self, e_env, monkeypatch):
        """28：预测点用 period_start/period_end + lower_bound/upper_bound。

        2026-10-17 起 28 号不再「算完直接回前端」：预测必须挂在一条独立的
        ``visible_forecast`` 作业上，由 ``publish_forecast`` 落到 C 的
        ``visible_forecasts``，28 号**再把 C 存的那一条读回来**当响应。
        本用例因此同时验证「入了队」「落了库」「读回的是同一条」。
        """
        #: E 的返回形状（target_time / lower / upper），D 负责整形
        e_result = {
            "status": "ready",
            "forecast_points": [
                {
                    "target_time": "2026-05-01T10:05:00+08:00",
                    "value": 15.0,
                    "lower": 12.0,
                    "upper": 18.0,
                }
            ],
            "training_cutoff": "2026-05-01T10:00:00+08:00",
            "input_window_minutes": 60,
            "model_version": "damped-trend-v1",
            "validation_error": {
                "30": {
                    "method": "rolling_origin",
                    "mae": 1.5,
                    "baseline_mae": 2.0,
                    "sample_count": 12,
                }
            },
        }
        captured: dict = {}

        def _fake_forecast(*, history, cutoff_at, horizon_minutes, step_minutes):
            captured["history"] = list(history)
            captured["cutoff_at"] = cutoff_at
            return dict(e_result)

        monkeypatch.setattr(
            "app.services.e_bridge.forecast_from_input", _fake_forecast,
        )
        client = e_env["client"]
        csrf = _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        visible = e_env["visible"]
        visible.seed_queued(video_id=video_id, analysis_id=analysis_id)
        # 历史点由 C 提供（D 不自己挑窗口），指纹也由 C 绑定
        visible.history[analysis_id] = [
            {"at": "2026-05-01T09:55:00+08:00", "value": 11.0},
            {"at": "2026-05-01T10:00:00+08:00", "value": 12.0},
        ]
        resp = client.post(
            f"/api/v1/videos/{video_id}/short-forecast",
            json={
                "analysis_id": analysis_id,
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 5,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["metric"] == "camera_visible_people"
        assert data["validation_error"]["30"]["mae"] == 1.5
        assert data["validation_error"]["30"]["baseline_mae"] == 2.0
        point = data["forecast_points"][0]
        assert point["period_start"] == point["period_end"] == "2026-05-01T10:05:00+08:00"
        assert point["lower_bound"] == 12.0
        assert point["upper_bound"] == 18.0
        assert point["is_forecast"] is True
        assert point["grain"] == "5min"
        assert "target_time" not in point and "lower" not in point and "upper" not in point

        # 历史点必须是 C 给的那一份（D 不重挑窗口）
        assert captured["cutoff_at"] == "2026-05-01T10:00:00+08:00"
        assert [h["value"] for h in captured["history"]] == [11.0, 12.0]

        # 预测确实落到了 C：有一条 visible_forecast 作业、一条 forecast 记录，
        # 且作业已完成、result_refs 指回这条记录。
        # ⚠️ forecast_id **不进**响应体：03 §28 没这个字段，D 不擅自加，
        # 调用方通过 30 号 /jobs/{job_id} 追溯。
        forecast_jobs = [j for j in visible.jobs.values() if j["type"] == "visible_forecast"]
        assert len(forecast_jobs) == 1
        job = forecast_jobs[0]
        assert job["status"] == "succeeded"
        forecast_id = job["result_refs"]["forecast_id"]
        assert forecast_id
        forecast = visible.get_visible_forecast(forecast_id)
        assert forecast["analysis_id"] == analysis_id
        # 输入指纹是 C 绑的，D 原样透传（重算就等于第二套口径）
        assert forecast["input_sha256"] == visible.input_sha256

    def test_short_forecast_result_is_read_back_from_c_not_reechoed(self, e_env, monkeypatch):
        """28：响应必须来自 **C 落库的那条记录**，而不是把 E 的返回值再加工一遍。

        30 号 ``/jobs/{job_id}`` 之后仍从同一处取数；若这里图省事回显内存里的
        E 返回值，就会出现「这次请求说 ready、事后查作业说没有」的分叉。

        证伪方式：让 C 落库时给结果盖一个自己的 status，响应里必须是**盖过章
        的那一份**。
        """
        monkeypatch.setattr(
            "app.services.e_bridge.forecast_from_input",
            lambda *, history, cutoff_at, horizon_minutes, step_minutes: {
                "status": "ready",
                "forecast_points": [
                    {"target_time": "2026-05-01T10:05:00+08:00", "value": 15.0,
                     "lower": 12.0, "upper": 18.0},
                ],
            },
        )
        visible = e_env["visible"]
        original_publish = visible.publish_visible_forecast

        def _publish_stamped(**kwargs):
            kwargs["result"] = {**kwargs["result"], "status": "ready-as-stored-by-c"}
            return original_publish(**kwargs)

        monkeypatch.setattr(visible, "publish_visible_forecast", _publish_stamped)

        client = e_env["client"]
        csrf = _login_admin(client)
        video_id, analysis_id = str(uuid4()), str(uuid4())
        visible.seed_queued(video_id=video_id, analysis_id=analysis_id)
        visible.history[analysis_id] = [
            {"at": "2026-05-01T10:00:00+08:00", "value": 12.0},
        ]
        resp = client.post(
            f"/api/v1/videos/{video_id}/short-forecast",
            json={
                "analysis_id": analysis_id,
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 5,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 200, resp.text
        # E 返回的是 "ready"；拿到的却是 C 存下来的那一份才算读回成功
        assert resp.json()["data"]["status"] == "ready-as-stored-by-c"

    def test_short_forecast_unknown_analysis_is_404_not_500(self, e_env):
        """28：分析不存在要 **404**，而且**不许先开出一条作业**。

        2026-10-17 真实 MySQL 上踩到两件事，都是只有平台冒烟能发现：

        1. **异常映射写漏**：``_raise_mapped`` 只认 **E** 的
           ``DependencyUnavailable`` / ``UnsupportedAnalysis``，**认不了 C 的
           ``NotFound``** → 未知分析一路穿到全局处理器，返回 **500**。
        2. **顺序错了**：先 ``create_job`` 再让下游去撞 NotFound，于是库里留下
           一条永远跑不出结果的 ``visible_forecast`` 作业。

        修法是「先查存在、再开票」+ 补一层 C 异常族映射。本用例把这两条钉住。
        """
        client = e_env["client"]
        csrf = _login_admin(client)
        video_id, unknown_analysis = str(uuid4()), str(uuid4())
        visible = e_env["visible"]
        jobs_before = len(visible.jobs)

        resp = client.post(
            f"/api/v1/videos/{video_id}/short-forecast",
            json={
                "analysis_id": unknown_analysis,
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 5,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"
        # 没开票：不能为一个不存在的分析留下一条跑不出结果的作业
        assert len(visible.jobs) == jobs_before
        assert not visible.forecasts

    def test_short_forecast_analysis_belonging_to_another_video_is_404(self, e_env):
        """归属不一致同样是 404——不能靠 ``video_id`` 拼出别人的预测结果。"""
        client = e_env["client"]
        csrf = _login_admin(client)
        visible = e_env["visible"]
        owner_video, analysis_id = str(uuid4()), str(uuid4())
        visible.seed_queued(video_id=owner_video, analysis_id=analysis_id)

        resp = client.post(
            f"/api/v1/videos/{uuid4()}/short-forecast",
            json={
                "analysis_id": analysis_id,
                "cutoff_at": "2026-05-01T10:00:00+08:00",
                "horizon_minutes": 30,
                "step_minutes": 5,
            },
            headers=_auth(csrf),
        )
        assert resp.status_code == 404, resp.text


class TestJobsQuery:
    def test_get_job_roundtrip(self, e_env):
        client = e_env["client"]
        # 直接入队一个作业再查
        store = SqliteJobStore(e_env["settings"].job_store_path)
        job_id = store.create_job("knowledge_index", payload={"document_id": "doc-x"}, analysis_id="doc-x")
        _login_admin(client)
        resp = client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == job_id
        assert data["type"] == "knowledge_index"
        assert data["status"] == "queued"
        assert data["analysis_id"] == "doc-x"

    def test_ingestion_requires_csrf(self, e_env):
        """03 契约 §29：POST /jobs/ingestion 要求 X-CSRF-Token。"""
        client = e_env["client"]
        _login_admin(client)  # 已登录但不带 CSRF 头
        resp = client.post(
            "/api/v1/jobs/ingestion",
            json={"source_ids": [str(uuid4())], "reason": "手动触发"},
        )
        assert resp.status_code == 403

    def test_get_job_404(self, e_env):
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(f"/api/v1/jobs/{uuid4()}")
        assert resp.status_code == 404

    def test_get_job_rejects_non_uuid(self, e_env):
        """03 契约 §30：job_id 为 UUID；非 UUID 属参数错误。"""
        client = e_env["client"]
        _login_admin(client)
        resp = client.get("/api/v1/jobs/not-a-uuid")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_request"

    def test_get_job_requires_admin(self, e_env):
        client = e_env["client"]
        resp = client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "viewer-pass-123"})
        resp = client.get("/api/v1/jobs/whatever")
        assert resp.status_code == 403


class TestDocumentVersionConvention:
    """C 口径 document_version（不能自造 "v1"，必须 sha256:<hex32>）。"""

    def test_derives_sha256_prefix_from_bytes(self):
        assert document_version_from_bytes(b"hello") == (
            "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e"
        )

    def test_matches_c_manifest_fixed_document(self):
        """与 C 交接包 real-notice-preparation.json 的固定资料对账。

        该资料正文在 C 的 ``real_data/2026-10-12-v2/browser_snapshots/
        scenic-2025-spring-notice-browser.txt``。此处只验证**派生规则**：
        给定同样的字节，D 算出的版本号必须与 C manifest 登记的完全一致。
        """
        import hashlib

        text = "黄山风景区2025年春节黄金周公告正文"
        expected = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        assert document_version_from_bytes(text.encode("utf-8")) == expected
        assert document_version_from_bytes(text.encode("utf-8")).startswith("sha256:")
        assert len(document_version_from_bytes(text.encode("utf-8"))) == 39

    def test_registered_version_not_invented(self):
        """禁止自造递增版本号——"v1" 形态必须被判为非 C 口径。"""
        assert not document_version_from_bytes(b"x").startswith("v1")
        assert document_version_from_bytes(b"x") != "v1"


class TestWorkerLoop:
    def test_knowledge_index_job_end_to_end(self, e_env):
        """入队 → worker 领取 → E 解析分块 → 落库 → 作业 succeeded → RAG 索引持久化。"""
        tmp = e_env["tmp_path"]
        doc = tmp / "notice.md"
        doc.write_text("# 黄山开放公告\n黄山景区每日六点开放。" * 10, encoding="utf-8")

        store = SqliteJobStore(e_env["settings"].job_store_path)
        job_id = store.create_job(
            "knowledge_index",
            payload={
                "document_id": "doc-e2e",
                # C 口径版本标识（sha256:<hex32>）——E worker 会校验 metadata 与
                # 顶层 document_version 一致，两者必须同值。
                "document_version": DOC_VERSION,
                "version": "v1",
                "file_path": str(doc),
                "metadata": {
                    "title": "黄山开放公告",
                    "publisher": "黄山管委会",
                    "source_url": "https://example.gov.cn/notice",
                    "published_at": "2026-09-01T00:00:00+08:00",
                    "permission_note": "已授权",
                    "document_version": DOC_VERSION,
                },
            },
            analysis_id="doc-e2e",
        )

        worker = DWorker(store, counter=None)
        result = worker.run_once()
        assert result is not None

        job = store.get_job(job_id)
        assert job["status"] == "succeeded"
        assert (job["processed_count"] or 0) >= 1
        # RAG 索引已持久化（API 进程热重载的依据）
        assert (tmp / "rag_index.json").exists()

        # 经 HTTP 查询：30 号接口时间输出为东八区（03 §1 通用约定）
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "succeeded"
        assert data["finished_at"] and data["finished_at"].endswith("+08:00")

    def test_worker_no_job_returns_none(self, e_env):
        store = SqliteJobStore(e_env["settings"].job_store_path)
        worker = DWorker(store, counter=None)
        assert worker.run_once() is None

    def test_degraded_worker_fails_job_when_e_missing(self, e_env, monkeypatch):
        monkeypatch.setattr(
            "app.core.providers._try_import",
            lambda name: None,
        )
        store = SqliteJobStore(e_env["settings"].job_store_path)
        job_id = store.create_job("knowledge_index", payload={"document_id": "doc-z"})
        degraded = DegradedWorker(store)
        assert degraded.run_once() == {"status": "failed"}
        assert store.get_job(job_id)["status"] == "failed"
        assert "intelligent_analysis" in (store.get_job(job_id)["error_summary"] or "")


class _RecordingReindexStore:
    """只记录 ``create_job`` 的假 store，用于验证 22 重建的**逐文档展开**。

    ``create_job`` 会先按 C ``jobs.payload`` 是 JSON 列这一事实做一次
    ``json.dumps``——含 ``datetime`` 的 payload 会当场抛 ``TypeError``，
    从而把「元数据必须转 ISO 字符串」这一回归点钉死在测试里。
    """

    def __init__(self, docs: dict[str, dict]) -> None:
        self._docs = docs
        self.created: list[tuple[str, dict, str | None]] = []

    def get_document(self, document_id: str) -> dict:
        return dict(self._docs.get(document_id, {}))

    def create_job(self, job_type: str, payload: dict, *, analysis_id: str | None = None) -> str:
        import json

        json.dumps(payload)  # 复现 C 的 JSON 列约束
        self.created.append((job_type, payload, analysis_id))
        return f"child-{len(self.created)}"


class TestKnowledgeReindexExpansion:
    """22 重建：E 只入队 knowledge_reindex 信封，**逐文档展开是 D worker 的职责**。

    回归点：曾经的实现只返回一句「子任务已展开」的说明就成功收尾，实际
    一个 knowledge_index 子作业都没入队——重建是静默空操作。
    """

    def test_reindex_expands_one_child_job_per_document(self, e_env):
        from datetime import datetime, timezone

        doc_id = str(uuid4())
        staging = e_env["tmp_path"] / "knowledge_staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / f"{doc_id}.md").write_text("# 黄山公告\n每日六点开放。", encoding="utf-8")

        store = _RecordingReindexStore({
            doc_id: {
                "id": doc_id,
                "title": "黄山公告",
                "publisher": "黄山管委会",
                "source_url": "https://example.gov.cn/n",
                "permission_note": "已授权",
                # C 口径版本标识：原件内容 SHA256 前 32 位（manifest 同算法）
                "document_version": "sha256:29aa803a0fa3e009feed5ce3aa07330e",
                # C 文档行的时间字段是 tz-aware datetime，必须被转成 ISO 才能入 JSON 列
                "published_at": datetime(2026, 1, 20, tzinfo=timezone.utc),
            }
        })

        result = DWorker(store, counter=None)._run_knowledge_reindex(
            {"document_ids": [doc_id], "reason": "回归测试"}, lease=None,
        )

        assert result["reindexed_documents"] == 1
        assert result["skipped"] == []
        assert len(store.created) == 1
        job_type, payload, analysis_id = store.created[0]
        assert job_type == "knowledge_index"
        assert analysis_id == doc_id
        assert payload["document_id"] == doc_id
        assert payload["document_version"] == "sha256:29aa803a0fa3e009feed5ce3aa07330e"
        assert payload["file_path"].endswith(f"{doc_id}.md")
        # datetime → ISO 字符串（否则 C jobs.payload 落库就会 TypeError）
        assert payload["metadata"]["published_at"] == "2026-01-20T00:00:00+00:00"

    def test_reindex_reports_missing_staging_instead_of_faking_success(self, e_env):
        doc_id = str(uuid4())
        store = _RecordingReindexStore({
            doc_id: {"id": doc_id,
                     "document_version": "sha256:29aa803a0fa3e009feed5ce3aa07330e"}
        })

        result = DWorker(store, counter=None)._run_knowledge_reindex(
            {"document_ids": [doc_id], "reason": "源文件已被清理"}, lease=None,
        )

        assert result["reindexed_documents"] == 0
        assert store.created == []
        assert result["skipped"] and result["skipped"][0]["document_id"] == doc_id
        assert "staging" in result["skipped"][0]["reason"]

    def test_reindex_skips_document_without_cconvention_version(self, e_env):
        """C 登记缺 document_version 时跳过并留痕——绝不兜底自造 "v1"。"""
        doc_id = str(uuid4())
        staging = e_env["tmp_path"] / "knowledge_staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / f"{doc_id}.md").write_text("# 无版本公告\n内容。", encoding="utf-8")

        store = _RecordingReindexStore({doc_id: {"id": doc_id}})  # 无 document_version

        result = DWorker(store, counter=None)._run_knowledge_reindex(
            {"document_ids": [doc_id], "reason": "版本缺失回归"}, lease=None,
        )

        assert result["reindexed_documents"] == 0
        assert store.created == []
        assert result["skipped"] and result["skipped"][0]["document_id"] == doc_id
        assert "document_version" in result["skipped"][0]["reason"]


class TestChatRateLimit:
    def test_sliding_window_limiter(self):
        from app.services.rate_limit import SlidingWindowRateLimiter

        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        assert all(limiter.allow("u1") for _ in range(3))
        assert limiter.allow("u1") is False  # 第 4 次被拒
        assert limiter.allow("u2") is True  # 其他用户不受影响

    def test_chat_returns_429_when_exceeded(self, e_env, monkeypatch):
        """03 契约 §19：/assistant/chat 限速。"""
        from app.services.rate_limit import chat_limiter

        monkeypatch.setattr(chat_limiter, "_max_requests", 2)
        chat_limiter._hits.clear()
        client = e_env["client"]
        csrf = _login_admin(client)
        for _ in range(2):
            resp = client.post(
                "/api/v1/assistant/chat",
                json={"message": "你好"},
                headers=_auth(csrf),
            )
            assert resp.status_code == 200
        resp = client.post(
            "/api/v1/assistant/chat",
            json={"message": "你好"},
            headers=_auth(csrf),
        )
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "rate_limited"


class TestDependencyFallback:
    """E 包缺失时六条路由回退 503（与 C 未接入的口径一致）。"""

    @pytest.fixture
    def no_e(self, e_env, monkeypatch):
        monkeypatch.setattr("app.core.providers._try_import", lambda name: None)

    def test_chat_503(self, no_e, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post("/api/v1/assistant/chat", json={"message": "hi"}, headers=_auth(csrf))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "dependency_not_available"

    def test_upload_503(self, no_e, e_env):
        client = e_env["client"]
        csrf = _login_admin(client)
        resp = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("a.md", b"x", "text/markdown")},
            data={"title": "t", "publisher": "p", "permission_note": "n"},
            headers=_auth(csrf),
        )
        assert resp.status_code == 503

    def test_counts_503(self, no_e, e_env):
        client = e_env["client"]
        _login_admin(client)
        resp = client.get(f"/api/v1/videos/{uuid4()}/counts", params={"analysis_id": str(uuid4())})
        assert resp.status_code == 503


class TestOpenApiCsrfHeader:
    """写操作依赖 require_csrf 时必须显式声明 X-CSRF-Token 头。

    否则 OpenAPI（/docs 的 Swagger UI）里没有这个输入框，页面上点写操作必然 403。
    头声明为可选（required=false）以便页面可直接调用；运行时缺失仍返回 403。
    """

    WRITE_ROUTES = (
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/videos/{video_id}/analyses", "post"),
        ("/api/v1/videos/{video_id}/short-forecast", "post"),
        ("/api/v1/forecasts/history", "post"),
    )

    def test_write_routes_declare_csrf_header(self, e_env):
        client = e_env["client"]
        spec = client.get("/openapi.json").json()
        for path, method in self.WRITE_ROUTES:
            op = spec["paths"][path][method]
            names = {p["name"] for p in op.get("parameters", [])}
            assert "X-CSRF-Token" in names, f"{method.upper()} {path} 未声明 CSRF 头：{names}"

    def test_csrf_header_optional_in_schema_but_enforced_at_runtime(self, e_env):
        client = e_env["client"]
        spec = client.get("/openapi.json").json()
        params = spec["paths"]["/api/v1/auth/logout"]["post"]["parameters"]
        csrf_param = next(p for p in params if p["name"] == "X-CSRF-Token")
        assert csrf_param.get("required", False) is False

        _login_admin(client)
        resp = client.post("/api/v1/auth/logout")  # 已登录但不带 CSRF 头
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_required"
