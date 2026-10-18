"""P2PNet 模型包真机绑定测试（E 0.6.0 P0「错误模型和设备拒绝」）。

用**仓库里真实登记的模型包** ``data/p2pnet_bundle`` 跑 E 的
``P2PNetCounter.from_team_bundle`` / ``validate_config``，证明：

- API 与 worker 绑定的是**同一个**受信任包（D 只从 ``P2PNET_BUNDLE_DIR``
  一处构造，不存在第二处权重来源）；
- ``pytorch`` + ``CPU`` 是唯一被验证通过的运行时组合，``GPU`` /
  ``openvino`` / 未登记权重一律 ``UnsupportedAnalysis`` → D 路由层 **422**；
- provenance 七键（含 ``weights_sha256`` / ``model_source_revision``）与
  E 0.6.0 执行单登记值一致——27 号审计摘要就是从这里来的。

包缺失（未登记权重）时整文件 skip，不把「未登记 → 422」当成这些用例通过。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeVisibleStore

from app.core.config import get_settings

BUNDLE_DIR = Path(get_settings().p2pnet_bundle_dir)

pytestmark = pytest.mark.skipif(
    not (BUNDLE_DIR / "SHTechA.pth").is_file(),
    reason="配置的 P2PNET_BUNDLE_DIR 中不存在 SHTechA.pth",
)

#: E 0.6.0 执行单登记的溯源值（27 号响应顶层审计字段的来源）
EXPECTED_PROVENANCE = {
    "count_model": "p2pnet",
    "weights_version": "leader-SHTechA-50604773",
    "weights_sha256": "506047732b128ff09efef18e94bfacbe35fcfef300e5e9eeeece259b0488c63f",
    "model_source_revision": "team-zip-2981dc75",
    "confidence_threshold": 0.5,
    "inference_engine": "pytorch",
    "device": "CPU",
}

#: D/B 公共形状（归一化 [x, y][]）
ROI = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]

#: E 内部形状（``VideoAnalysisConfig`` 要求 roi 必填，且为对象数组）
ROI_FOR_E = {"points": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.9, "y": 0.9}]}

#: 26 号请求体（D/B 公共形状：``roi.points`` 是 ``[[x, y], ...]``，**不含** E 内部形状）
CPU_PYTORCH_BODY = {
    "count_model": "p2pnet",
    "weights_version": "leader-SHTechA-50604773",
    "sample_interval_seconds": 10,
    "aggregation_minutes": 5,
    "inference_engine": "pytorch",
    "device": "CPU",
    "roi": {"points": ROI},
}

#: 直接喂 E ``P2PNetCounter.validate_config`` 的配置（E 内部形状）
CPU_PYTORCH_CONFIG = {**CPU_PYTORCH_BODY, "roi": ROI_FOR_E}


@pytest.fixture(scope="module")
def bundle_counter():
    """用真实模型包构造一次（torch 加载较贵，module 级复用）。"""
    from intelligent_analysis.vision.p2p_counter import P2PNetCounter

    return P2PNetCounter.from_team_bundle(str(BUNDLE_DIR))


@pytest.fixture
def bundle_env(tmp_path, monkeypatch, fake_store):
    from app.core.config import Settings
    from app.services import e_bindings

    settings = Settings(
        data_dir=str(tmp_path),
        job_store_path=str(tmp_path / "jobs.db"),
        rag_index_path=str(tmp_path / "rag_index.json"),
        detector_weights_path=str(tmp_path / "weights"),
        # 指向仓库里**真实登记**的模型包
        p2pnet_bundle_dir=str(BUNDLE_DIR),
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    assert e_bindings.configure_e_services() is True
    client = TestClient(pytest.importorskip("app.main").app)

    # 2026-10-17 起 26 号的成果不再落 D 私表（``d_video_analyses``），而是走 C 的
    # ``uow.visible``。这里用内存替身，但替身**保留 C 的拒绝语义**（见 fakes.py），
    # 免得「fake 太宽松 → 单测全绿、真机才炸」再次发生。
    visible = FakeVisibleStore()
    monkeypatch.setattr("app.core.providers.get_c_store", lambda: visible)
    monkeypatch.setattr("app.api.v1.videos.get_c_store", lambda: visible)
    return {"settings": settings, "client": client, "visible": visible}


def _login_admin(client: TestClient) -> str:
    return client.post(
        "/api/v1/auth/login",
        json={"username": "admin1", "password": "admin-pass-123"},
    ).json()["data"]["csrf_token"]


def test_provenance_matches_e_execution_order(bundle_counter):
    """溯源七键必须与 E 0.6.0 执行单登记值逐项一致（27 号审计摘要的源头）。"""
    provenance = dict(bundle_counter.provenance)
    assert provenance == EXPECTED_PROVENANCE


def test_cpu_pytorch_is_the_only_validated_runtime(bundle_counter):
    """唯一被验证的运行时：pytorch + CPU。"""
    bundle_counter.validate_config(dict(CPU_PYTORCH_CONFIG))


@pytest.mark.parametrize("override, extra", [
    ({"device": "GPU"}, {}),
    ({"device": "NPU"}, {}),
    ({"device": "AUTO"}, {}),
    ({"inference_engine": "openvino"}, {"openvino_version": "2024.1"}),
    ({"weights_version": "some-other-weights"}, {}),
])
def test_unvalidated_runtime_or_weights_rejected(bundle_counter, override, extra):
    """未验证的设备 / 引擎 / 未登记权重 → E ``UnsupportedAnalysis``（不是静默通过）。"""
    from intelligent_analysis.integration import UnsupportedAnalysis

    config = dict(CPU_PYTORCH_CONFIG)
    config.update(override)
    config.update(extra)
    with pytest.raises(UnsupportedAnalysis):
        bundle_counter.validate_config(config)


def test_route_rejects_gpu_device_with_422(bundle_env):
    """26 号：device=GPU 经 E 的校验器拒绝 → 422 unsupported（不是 400、不是 200）。"""
    client = bundle_env["client"]
    csrf = _login_admin(client)
    resp = client.post(
        f"/api/v1/videos/{uuid4()}/analyses",
        json={**CPU_PYTORCH_BODY, "device": "GPU"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "unsupported"


def test_route_accepts_cpu_pytorch_and_enqueues_real_job(bundle_env):
    """26 号：pytorch + CPU + 登记权重 → 202 并经 D 的绑定真实入队。"""
    client = bundle_env["client"]
    csrf = _login_admin(client)
    video_id = str(uuid4())
    resp = client.post(
        f"/api/v1/videos/{video_id}/analyses",
        json=CPU_PYTORCH_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data["status"] == "queued"
    assert data["video_id"] == video_id
    assert data["count_metric"] == "camera_visible_people"
    assert data["inference_engine"] == "pytorch"
    assert data["analysis_id"]
    # 同一事务的产物必须成对出现：作业进了队列，分析登记为 queued 且绑同一 job
    visible = bundle_env["visible"]
    job = visible.get_job(data["job_id"])
    assert job is not None
    assert job["type"] == "video_analysis"
    assert job["payload"]["video_id"] == video_id
    assert job["payload"]["analysis_id"] == data["analysis_id"]

    analysis = visible.get_visible_analysis(data["analysis_id"])
    assert analysis["status"] == "queued"
    assert analysis["job_id"] == data["job_id"]
    # C create_analysis 要求的 config：含 D 注入的固定阈值
    assert analysis["config"]["count_model"] == "p2pnet"
    assert analysis["config"]["confidence_threshold"] == 0.5
    # 溯源七键必须来自**真实模型包**（伪造权重会一路穿透到 27 号审计摘要）
    assert analysis["provenance"] == EXPECTED_PROVENANCE
