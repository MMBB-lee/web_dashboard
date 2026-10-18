"""请求模型校验测试（口径红线在 schema 层就拦住）。"""

import pytest
from pydantic import ValidationError

from app.schemas.analytics import CompareRequest
from app.schemas.forecast import ForecastRequest
from app.schemas.holidays import HolidayCompareRequest
from app.schemas.videos import AnalysisConfig, ShortForecastRequest


class TestForecastRequest:
    def test_week_horizon_limit_is_8(self):
        with pytest.raises(ValidationError):
            ForecastRequest(entity_id="e1", metric="scenic_visits", grain="week", training_end="2025-12-31", horizon=9)

    def test_month_horizon_limit_is_6(self):
        with pytest.raises(ValidationError):
            ForecastRequest(entity_id="e1", metric="scenic_visits", grain="month", training_end="2025-12-31", horizon=7)

    def test_valid_month_request(self):
        req = ForecastRequest(entity_id="e1", metric="scenic_visits", grain="month", training_end="2025-12-31", horizon=6)
        assert req.model == "baseline"  # 默认值

    def test_grain_rejects_day(self):
        with pytest.raises(ValidationError):
            ForecastRequest(entity_id="e1", metric="scenic_visits", grain="day", training_end="2025-12-31", horizon=1)


class TestCompareRequest:
    def test_index_100_requires_base_period(self):
        with pytest.raises(ValidationError):
            CompareRequest(
                series=[{"entity_id": "a", "metric": "scenic_visits"}, {"entity_id": "b", "metric": "rail_departures"}],
                grain="month",
                start="2025-01-01",
                end="2025-12-31",
                display_mode="index_100",
            )

    def test_series_needs_at_least_two(self):
        with pytest.raises(ValidationError):
            CompareRequest(
                series=[{"entity_id": "a", "metric": "scenic_visits"}],
                grain="month",
                start="2025-01-01",
                end="2025-12-31",
                display_mode="separate_axes",
            )


class TestHolidayCompareRequest:
    def test_needs_exactly_two_distinct_holidays(self):
        with pytest.raises(ValidationError):
            HolidayCompareRequest(holiday_ids=["h1", "h1"], series=[{"entity_id": "a", "metric": "scenic_visits"}], mode="period_total")


class TestAnalysisConfig:
    """26：P2PNet 画面人数分析配置（2026-10-15 版契约，E 0.6.0）。

    ROI 形状与前端（B）联调确认一致：归一化 `[[x, y], ...]`。
    计数模型只接受 `p2pnet`；旧的 `dm_count`（密度图求和）已退役并按 422/400 拒收。
    """

    ROI = {"points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]}

    def _kwargs(self, **overrides):
        kwargs = {
            "count_model": "p2pnet",
            "weights_version": "leader-SHTechA-50604773",
            "roi": self.ROI,
            "sample_interval_seconds": 10,
            "aggregation_minutes": 5,
            "inference_engine": "pytorch",
            "device": "CPU",
        }
        kwargs.update(overrides)
        return kwargs

    def test_count_model_defaults_to_p2pnet(self):
        cfg = AnalysisConfig(**self._kwargs())
        assert cfg.count_model == "p2pnet"
        assert cfg.weights_version == "leader-SHTechA-50604773"

    def test_count_model_fixed_to_p2pnet(self):
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(count_model="yolov8"))

    def test_legacy_dm_count_rejected(self):
        """E 0.6.0 起 DM-Count 密度图口径退役：旧报文必须被拒收，不静默回退。"""
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(count_model="dm_count"))

    def test_aggregation_minutes_fixed_to_5(self):
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(aggregation_minutes=15))

    def test_roi_must_be_normalized(self):
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(roi={"points": [[0.0, 0.0], [1.5, 0.0], [1.0, 1.0]]}))

    def test_roi_needs_at_least_three_points(self):
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(roi={"points": [[0.0, 0.0], [1.0, 1.0]]}))

    def test_roi_point_must_be_xy_pair(self):
        """每个顶点必须是 [x, y] 两元素数组（前端约定，不用像素坐标或对象）。"""
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(roi={"points": [[0.0, 0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]}))

    def test_openvino_requires_version(self):
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(inference_engine="openvino"))

    def test_legacy_yolov8_fields_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisConfig(**self._kwargs(count_line={"x1": 0, "y1": 0, "x2": 1, "y2": 1}, entry_side="left"))


class TestShortForecastRequest:
    """28：画面人数短时预测（不接受 direction）。"""

    def _kwargs(self, **overrides):
        kwargs = {
            "analysis_id": "a1",
            "cutoff_at": "2025-05-01T10:00:00+08:00",
            "horizon_minutes": 30,
            "step_minutes": 5,
        }
        kwargs.update(overrides)
        return kwargs

    def test_horizon_must_be_15_30_60(self):
        with pytest.raises(ValidationError):
            ShortForecastRequest(**self._kwargs(horizon_minutes=45))

    def test_step_fixed_to_5(self):
        with pytest.raises(ValidationError):
            ShortForecastRequest(**self._kwargs(step_minutes=15))

    def test_direction_not_accepted(self):
        with pytest.raises(ValidationError):
            ShortForecastRequest(**self._kwargs(direction="entries"))

    def test_valid_request(self):
        req = ShortForecastRequest(**self._kwargs(horizon_minutes=60, step_minutes=5))
        assert req.horizon_minutes == 60
        assert req.step_minutes == 5
