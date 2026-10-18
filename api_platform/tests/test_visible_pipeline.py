"""画面人数链路（26—28 + worker）的单元测试：C 持久化 + E 计算的分工边界。

这一组覆盖的是**最容易出错、也最晚被发现**的那一层：worker 与 C 的
``uow.visible`` 之间的五步序列。它平时不会被 HTTP 请求触发，一旦写错要到
真录像跑起来才会现形——而真录像目前是 0 行，所以只能靠这里守住。

要守住的几条：
- ``begin → write_points → complete_analysis`` 的顺序与批次（单批 ≤1000）；
- **complete / fail 只由 C 内部做**，D 外层再补一次 ``complete_job`` 会因为
  lease 已失效而冲突（fake 里这条会直接报错）；
- 输入只能来自 ``get_visible_input``，缺 file_path / source_ids 必须失败，
  不拼路径、不伪造来源；
- ``knowledge_invalidate`` 的三个身份必须对齐，worker 不许冒充操作者。
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services import visible_pipeline as vp
from app.services.visible_pipeline import (
    POINT_BATCH_SIZE,
    VisiblePipelineError,
    build_analysis_config,
    build_analysis_provenance,
    check_counts_shape,
    check_provenance_consistency,
    filter_counts,
    normalise_points_for_c,
    run_visible_analysis,
)
from app.worker import DWorker
from tests.fakes import FakeVisibleStore


def _point(value: float = 12.0, **overrides):
    base = {
        "grain": "5min",
        "value": value,
        "unit": "人",
        "metric": "camera_visible_people",
        "period_start": "2026-05-01T10:00:00+08:00",
        "period_end": "2026-05-01T10:05:00+08:00",
        "sample_count": 30,
        "quality_status": "good",
        "source_ids": ["11111111-1111-1111-1111-111111111111"],
    }
    base.update(overrides)
    return base


def _e_point(offset_seconds: int = 0, value: float = 12.0) -> dict:
    """一个帧点；``offset_seconds`` 保证同一分析里时间戳不重复。

    C 的 ``write_points`` 按采样槽去重（``point_key``），所以构造批量用例时
    **每个点必须落在不同槽**，否则会被 skipped 掉、测不到分批逻辑。
    """
    total = 10 * 3600 + offset_seconds  # 基准 10:00:00
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return {
        "grain": "frame",
        "recorded_at": f"2026-05-01T{hh:02d}:{mm:02d}:{ss:02d}+08:00",
        "value": value,
        "unit": "人",
        "metric": "camera_visible_people",
        "sample_count": 1,
        "quality_status": "good",
        "source_ids": ["11111111-1111-1111-1111-111111111111"],
    }


class _FakeCounter:
    def __init__(self, points=None, error: Exception | None = None) -> None:
        self._points = points if points is not None else [_e_point()]
        self._error = error

    def dispatch(self, points=None, error=None):
        self._points = points if points is not None else self._points
        self._error = error
        return self


def _patch_dispatch(monkeypatch, counter: _FakeCounter, error: Exception | None = None):
    def _dispatch(job_type, payload, *, counter=None, frames=None):
        if error is not None:
            raise error
        return {
            "metric": "camera_visible_people",
            "points": counter._points,
            "roi": payload.get("config", {}).get("roi") if isinstance(payload.get("config"), dict) else None,
            "recorded_start_at": payload.get("recorded_start_at"),
            "recorded_end_at": payload.get("recorded_end_at"),
        }

    monkeypatch.setattr(
        "intelligent_analysis.worker.runner.dispatch_analysis_job", _dispatch,
    )


def _enqueue_and_claim(store, *, video_id=None, analysis_id=None, **store_tweaks):
    """走一遍真实的「入队 → 领取」，拿到 job 与 lease。

    不能只 ``seed_queued`` 就想要 lease：C 的写方法都要求 lease 绑定既有作业，
    凭空造一个 lease 会让测试绕过这层约束。
    """
    for key, value in store_tweaks.items():
        setattr(store, key, value)
    video_id = video_id or str(uuid4())
    analysis_id = analysis_id or str(uuid4())
    enrolled = store.enqueue_visible_analysis(
        video_id=video_id, analysis_id=analysis_id,
        config={"count_model": "p2pnet", "roi": {"points": [{"x": 0.1, "y": 0.1},
                                                            {"x": 0.9, "y": 0.1},
                                                            {"x": 0.9, "y": 0.9}]}},
        provenance={},
    )
    claimed = store.claim_job("w", datetime.now(UTC), job_id=enrolled["job"]["id"])
    assert claimed is not None
    # 入队那一步不算在待验证序列里，只留下后面 worker 五步的痕迹
    store.calls.clear()
    return video_id, analysis_id, claimed["job"], claimed["lease"]


# ---------- 26 号的形状组装 ----------


class TestConfigAndProvenanceAssembly:
    def test_config_has_exactly_the_keys_c_requires(self):
        """C 用 ``set(config) != keys`` 判等——多一个少一个都会被拒。"""
        config = build_analysis_config({
            "count_model": "p2pnet",
            "weights_version": "leader-SHTechA-50604773",
            "roi": {"points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]},
            "sample_interval_seconds": 10,
            "aggregation_minutes": 5,
            "inference_engine": "pytorch",
            "device": "CPU",
        })
        assert set(config) == set(vp.C_CONFIG_KEYS)
        # D 请求体里没有、但 C 要求的两项由 D 主动注入
        assert config["confidence_threshold"] == 0.5
        assert config["aggregation_minutes"] == 5
        # ROI 归一成对象数组
        assert config["roi"]["points"][0] == {"x": 0.1, "y": 0.1}

    def test_openvino_field_is_dropped_before_sending_to_c(self):
        """P2PNet 不接受 openvino_version，**带 null 也不行**。"""
        config = build_analysis_config({
            "count_model": "p2pnet",
            "weights_version": "w1",
            "roi": {"points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]},
            "sample_interval_seconds": 10,
            "aggregation_minutes": 5,
            "inference_engine": "pytorch",
            "device": "CPU",
            "openvino_version": None,
        })
        assert "openvino_version" not in config

    def test_missing_required_key_is_an_error_not_a_default(self):
        with pytest.raises(VisiblePipelineError) as exc:
            build_analysis_config({"count_model": "p2pnet"})
        assert "weights_version" in str(exc.value)

    def test_incoming_provenance_is_never_fabricated(self, monkeypatch):
        """溯源拿不全就报错——伪造 ``weights_sha256`` 会留下无法追溯的记录。"""
        monkeypatch.setattr(
            "app.services.e_bindings.get_p2p_counter_provenance",
            lambda: {"count_model": "p2pnet", "weights_version": "w1"},
        )
        with pytest.raises(VisiblePipelineError) as exc:
            build_analysis_provenance()
        assert "weights_sha256" in str(exc.value)

    def test_config_and_provenance_must_agree_on_shared_keys(self):
        with pytest.raises(VisiblePipelineError) as exc:
            check_provenance_consistency(
                {"count_model": "p2pnet", "weights_version": "w1", "device": "GPU"},
                {"count_model": "p2pnet", "weights_version": "w1", "device": "CPU"},
            )
        assert "device" in str(exc.value)


# ---------- worker：五步序列 ----------


class TestVideoAnalysisWorker:
    def test_five_step_sequence_writes_through_c(self, monkeypatch):
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=True,
        )
        counter = _FakeCounter()
        _patch_dispatch(monkeypatch, counter)

        result = run_visible_analysis(store, job=job, lease=lease, counter=counter)
        assert result["point_count"] == 1
        assert result["metric"] == "camera_visible_people"
        # 顺序必须是 取输入 → 开始 → 写点 → 完成
        assert store.calls == [
            "get_visible_input", "begin_visible_analysis",
            "write_visible_points", "complete_visible_analysis",
        ]
        assert store.get_visible_analysis(analysis_id)["status"] == "succeeded"
        assert len(store.points[analysis_id]) == 1

    def test_points_are_written_in_batches_of_at_most_1000(self, monkeypatch):
        """C 的 ``write_points`` 硬性限制单批 1000——超了是 InvalidArgument。"""
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=True,
        )

        # 每秒一个帧点，撒够两批多一点
        many = [
            _e_point(offset, value=float(offset))
            for offset in range(POINT_BATCH_SIZE * 2 + 5)
        ]
        counter = _FakeCounter(points=many)
        _patch_dispatch(monkeypatch, counter)

        result = run_visible_analysis(store, job=job, lease=lease, counter=counter)
        assert result["point_count"] == len(many)
        writes = [c for c in store.calls if c == "write_visible_points"]
        assert len(writes) == 3  # 1000 + 1000 + 5

    def test_worker_does_not_complete_the_job_twice(self, monkeypatch):
        """``complete_analysis`` 内部已完结作业，外层再 complete 会因 lease 失效冲突。

        这是 C 0.2.1a2 的硬性约定，也是最容易写错的一处：worker 主循环看到
        ``_job_already_completed`` 就必须跳过。
        """
        store = FakeVisibleStore()
        store.inputs_allowed = True
        video_id, analysis_id, job, _lease = _enqueue_and_claim(store)
        job_id = job["id"]
        # 放回队列，让 worker 自己领一次
        store.jobs[job_id]["status"] = "queued"

        counter = _FakeCounter()
        _patch_dispatch(monkeypatch, counter)

        worker = DWorker(store, counter=counter)
        outcome = worker.run_once()
        # 没抛 Conflict 就说明外层没有重复 complete
        assert outcome is not None and outcome["status"] == "succeeded"
        assert store.get_job(job_id)["status"] == "succeeded"

    def test_e_failure_is_recorded_on_c_and_reraised(self, monkeypatch):
        """E 失败要把异常继续抛给 worker（由 worker 分支写 fail_analysis）。"""
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=True,
        )
        _patch_dispatch(monkeypatch, None, error=RuntimeError("E 推理崩溃"))

        with pytest.raises(VisiblePipelineError) as exc:
            run_visible_analysis(store, job=job, lease=lease, counter=_FakeCounter())
        assert "E 推理崩溃" in str(exc.value)

    def test_missing_analysis_id_in_payload_is_rejected(self):
        store = FakeVisibleStore()
        _, _, job, lease = _enqueue_and_claim(store)
        job["payload"] = {}
        with pytest.raises(VisiblePipelineError) as exc:
            run_visible_analysis(store, job=job, lease=lease, counter=_FakeCounter())
        assert "analysis_id" in str(exc.value)

    def test_without_p2p_counter_the_job_fails_honestly(self):
        """模型包不可用就明说，不回退旧的 DM-Count 口径。"""
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=True,
        )
        with pytest.raises(VisiblePipelineError) as exc:
            run_visible_analysis(store, job=job, lease=lease, counter=None)
        assert "P2PNet" in str(exc.value)

    def test_input_without_file_path_never_reaches_e(self, monkeypatch):
        """file_path 只能来自 C 的授权回执；拼路径或拿空串蒙混都不行。"""
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=True, input_file_path="",
        )
        _patch_dispatch(monkeypatch, _FakeCounter())
        with pytest.raises(VisiblePipelineError) as exc:
            run_visible_analysis(store, job=job, lease=lease, counter=_FakeCounter())
        assert "file_path" in str(exc.value)

    def test_input_without_source_ids_is_refused(self, monkeypatch):
        """没有来源就无法溯源，宁可失败也不写无源的点。"""
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=True, source_ids=[],
        )
        _patch_dispatch(monkeypatch, _FakeCounter())
        with pytest.raises(VisiblePipelineError) as exc:
            run_visible_analysis(store, job=job, lease=lease, counter=_FakeCounter())
        assert "来源" in str(exc.value)

    def test_unauthorised_input_fails_instead_of_reading_disk(self):
        store = FakeVisibleStore()
        video_id, analysis_id, job, lease = _enqueue_and_claim(
            store, inputs_allowed=False,
        )
        with pytest.raises(VisiblePipelineError) as exc:
            run_visible_analysis(store, job=job, lease=lease, counter=_FakeCounter())
        assert "授权" in str(exc.value)


# ---------- 点形状与 E/C 的边界 ----------


class TestPointNormalisation:
    def test_only_c_whitelisted_keys_survive(self):
        """C 的点键是白名单，多一个键就 InvalidArgument——必须显式挑一遍。"""
        point = _e_point()
        extra = {**point, "authorization_scope": "demo", "notes": "x"}
        out = normalise_points_for_c([extra])[0]
        assert "authorization_scope" not in out
        assert "notes" not in out
        assert set(out) <= {
            "grain", "value", "sample_count", "quality_status", "source_ids",
            "metric", "unit", "recorded_at", "period_start", "period_end",
            "aggregation_rule", "published_at", "ingested_at",
        }

    def test_median_and_sample_count_are_never_recomputed_by_d(self):
        """中位数 / 样本数是 E 的口径，C 在 complete_analysis 里复核；D 动了就分叉。"""
        point = _e_point(value=7.0)
        out = normalise_points_for_c([point])[0]
        assert out["value"] == 7.0
        assert out["sample_count"] == 1

    def test_legacy_entries_exits_point_is_refused(self):
        with pytest.raises(VisiblePipelineError) as exc:
            check_counts_shape([
                {"quality_status": "good", "entries": 3, "exits": 1,
                 "source_ids": ["s1"]},
            ])
        assert "entries" in str(exc.value)

    def test_point_without_value_is_refused(self):
        with pytest.raises(VisiblePipelineError) as exc:
            check_counts_shape([
                {"quality_status": "good", "source_ids": ["s1"]},
            ])
        assert "value" in str(exc.value)

    def test_point_without_source_is_refused(self):
        with pytest.raises(VisiblePipelineError) as exc:
            check_counts_shape([
                {"value": 1.0, "quality_status": "good"},
            ])
        assert "source_ids" in str(exc.value)

    def test_well_formed_points_pass(self):
        check_counts_shape([_point()])


class TestCountsFiltering:
    def test_grains_are_never_mixed(self):
        counts = {"points": [
            _point(grain="frame", recorded_at="2026-05-01T10:00:00+08:00"),
            _point(12.0),
        ]}
        out = filter_counts(counts, grain="5min")
        assert [p["grain"] for p in out["points"]] == ["5min"]

    def test_frame_points_compare_recorded_at(self):
        counts = {"points": [
            _point(grain="frame", recorded_at="2026-05-01T09:00:00+08:00"),
            _point(grain="frame", recorded_at="2026-05-01T10:30:00+08:00"),
            _point(grain="frame", recorded_at="2026-05-01T11:00:00+08:00"),
        ]}
        out = filter_counts(
            counts, grain="frame",
            start="2026-05-01T10:00:00+08:00", end="2026-05-01T11:00:00+08:00",
        )
        stamps = [p["recorded_at"] for p in out["points"]]
        assert stamps == ["2026-05-01T10:30:00+08:00", "2026-05-01T11:00:00+08:00"]

    def test_time_comparison_is_timezone_safe(self):
        """同一个时刻用 +08:00 和 +09:00 写出来字符串不同——必须归一后再比。"""
        base = "2026-05-01T10:00:00"
        counts = {"points": [
            _point(grain="frame", recorded_at=base + "+08:00"),  # 02:00Z
        ]}
        # 查询区间用 +09:00 写，同一个物理时刻
        out = filter_counts(
            counts, grain="frame",
            start="2026-05-01T10:00:00+09:00",  # 01:00Z
            end="2026-05-01T11:00:00+09:00",    # 02:00Z
        )
        assert len(out["points"]) == 1

    def test_five_minute_window_crossing_boundary_is_dropped(self):
        """横跨查询边界的 5 分钟窗口必须丢掉（C §4 明确禁止冒充完整窗口）。

        一个 5 分钟点承载的是它自己窗口的中位数；查询从 10:04 起，而窗口是
        10:00—10:05，把它算进来等于把「前 4 分钟也算进查询区间」。
        """
        counts = {"points": [_point()]}
        out = filter_counts(
            counts, grain="5min",
            start="2026-05-01T10:04:00+08:00", end="2026-05-01T10:30:00+08:00",
        )
        assert out["points"] == []

    def test_five_minute_window_fully_inside_is_kept(self):
        """完整落在区间内的窗口保留——右端点按闭区间算（10:05 是窗口自己的末刻）。"""
        counts = {"points": [
            _point(12.0),
            _point(15.0, period_start="2026-05-01T10:05:00+08:00",
                   period_end="2026-05-01T10:10:00+08:00"),
            _point(18.0, period_start="2026-05-01T10:25:00+08:00",
                   period_end="2026-05-01T10:30:00+08:00"),
            _point(99.0, period_start="2026-05-01T10:30:00+08:00",
                   period_end="2026-05-01T10:35:00+08:00"),
        ]}
        out = filter_counts(
            counts, grain="5min",
            start="2026-05-01T10:00:00+08:00", end="2026-05-01T10:30:00+08:00",
        )
        assert [p["value"] for p in out["points"]] == [12.0, 15.0, 18.0]

    def test_unparseable_timestamps_are_kept_not_dropped(self):
        """宁可多给也不能漏给：解析不了的时间不过滤。"""
        counts = {"points": [_point(start=None, period_start="昨天", period_end=None)]}
        out = filter_counts(counts, grain="5min", start="2026-05-01T11:00:00+08:00")
        assert len(out["points"]) == 1


class TestSourceFileResolution:
    """重建/索引的原文来源：C 登记优先，本机 staging 兜底。

    以前原文只看 payload 里的 staging 路径，而那只是上传时的临时痕迹——清理过
    一次就再也重建不了。C《致D和E-021a2接收核对与补充答复》§6 点名要改。
    """

    def test_c_registered_original_wins_over_staging(self, tmp_path):
        original = tmp_path / "c-original.md"
        original.write_text("C 登记的原件", encoding="utf-8")
        staging = tmp_path / "staging.md"
        staging.write_text("staging", encoding="utf-8")

        class _Store:
            def get_document_file_ref(self, document_id):
                return {"document_id": document_id, "file_path": str(original)}

        worker = DWorker(_Store())
        worker._locate_staging_file = lambda document_id: staging
        assert worker._resolve_source_file("d1") == original

    def test_falls_back_to_staging_when_c_has_no_ref(self, tmp_path):
        staging = tmp_path / "staging.md"
        staging.write_text("staging", encoding="utf-8")

        class _Store:
            def get_document_file_ref(self, document_id):
                return None  # C 未登记（文档不存在）

        worker = DWorker(_Store())
        worker._locate_staging_file = lambda document_id: staging
        assert worker._resolve_source_file("d1") == staging

    def test_c_ref_pointing_to_a_missing_file_falls_back(self, tmp_path):
        """C 登记了但文件被挪走了：退到 staging，并在日志里留痕（不静默撒谎）。"""
        staging = tmp_path / "staging.md"
        staging.write_text("staging", encoding="utf-8")

        class _Store:
            def get_document_file_ref(self, document_id):
                return {"file_path": str(tmp_path / "已经.txt")}

        worker = DWorker(_Store())
        worker._locate_staging_file = lambda document_id: staging
        assert worker._resolve_source_file("d1") == staging

    def test_nothing_found_returns_none(self, tmp_path):
        class _Store:
            def get_document_file_ref(self, document_id):
                raise RuntimeError("C 不可用")

        worker = DWorker(_Store())
        worker._locate_staging_file = lambda document_id: None
        assert worker._resolve_source_file("d1") is None


# ---------- knowledge_invalidate 的身份约定 ----------


class TestKnowledgeInvalidate:
    def test_missing_document_id_is_rejected(self):
        store = FakeVisibleStore()
        worker = DWorker(store)
        with pytest.raises(RuntimeError) as exc:
            worker._run_knowledge_invalidate({"payload": {}}, None)
        assert "document_id" in str(exc.value)

    def test_worker_must_not_impersonate_the_operator(self):
        """操作者必须来自 job.requested_by，worker 不许自己充当。"""
        store = FakeVisibleStore()
        worker = DWorker(store)
        with pytest.raises(RuntimeError) as exc:
            worker._run_knowledge_invalidate(
                {"payload": {"document_id": str(uuid4())}, "requested_by": None}, None,
            )
        assert "操作者" in str(exc.value)

    def test_unsupported_store_reports_missing_entrypoint(self):
        """ fake 之外：store 没有 invalidate_document 时必须明说需要 C 0.2.1a2。"""

        class _Bare:
            pass

        worker = DWorker(_Bare())
        with pytest.raises(RuntimeError) as exc:
            worker._run_knowledge_invalidate(
                {"payload": {"document_id": str(uuid4()), "operator_id": "u1"},
                 "requested_by": "u1"},
                None,
            )
        assert "invalidate_document" in str(exc.value)
