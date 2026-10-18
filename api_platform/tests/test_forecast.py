"""历史预测算法测试。

序列为确定性合成数据（正弦季节性 + 固定伪噪声），仅用于验证算法行为，
不代表任何真实客流；生产数据来自 C 的 observations 仓储。
"""

from datetime import date, timedelta

import pytest

from app.services.historical_forecast import (
    DataInsufficient,
    min_training_points,
    run_history_forecast,
    seasonal_median_baseline,
)


def monthly_series(years: int, base: float = 100.0, amplitude: float = 30.0, noise: float = 0.0):
    """合成月度序列：base + 年度正弦 + 交替伪噪声。"""
    points = []
    for i in range(years * 12):
        d = date(2023, 1, 1) + timedelta(days=0)
        d = date(d.year + i // 12, i % 12 + 1, 1)
        import math

        value = base + amplitude * math.sin(2 * math.pi * (i % 12) / 12)
        if noise:
            value += ((i * 7) % 13 - 6) * noise
        points.append((d, round(value, 2)))
    return points


def weekly_series(weeks: int, base: float = 50.0, amplitude: float = 20.0):
    import math

    start = date(2024, 1, 1)  # 周一
    return [
        (start + timedelta(weeks=i), round(base + amplitude * math.sin(2 * math.pi * (i % 52) / 52), 2))
        for i in range(weeks)
    ]


def test_monthly_forecast_ready():
    result = run_history_forecast(monthly_series(3), "month", 6)
    assert result["status"] == "ready"
    assert result["model_version"] == "baseline-adaptive-v2"
    assert len(result["forecast_points"]) == 6
    assert result["training_window"].startswith("2023-01-01")
    assert result.get("reason") is None


def test_monthly_forecast_values_close_to_seasonal_truth():
    """无噪声纯正弦下，预测应接近真值（同期中位数≈真值）。"""
    result = run_history_forecast(monthly_series(3), "month", 6)
    import math

    for p in result["forecast_points"]:
        month = date.fromisoformat(p["period_start"]).month
        truth = 100 + 30 * math.sin(2 * math.pi * (month - 1) / 12)  # 与 monthly_series 的相位一致
        assert abs(p["value"] - truth) < 1.0
        assert p["lower_bound"] <= p["value"] <= p["upper_bound"]  # 下界 <= 点预测 <= 上界


def test_data_insufficient_when_history_too_short():
    result = run_history_forecast(monthly_series(1)[:2], "month", 3)
    assert result["status"] == "data_insufficient"
    assert result["forecast_points"] == []
    assert f"至少需要 {min_training_points('month')}" in result["reason"]


def test_data_insufficient_when_season_slot_missing():
    """只有 1—6 月数据时，预测 7—12 月必须诚实拒绝（无同期样本）。"""
    points = []
    for year in (2022, 2023, 2024, 2025):
        points += [(date(year, m, 1), 100.0 + m) for m in range(1, 7)]  # 24 个点，够最短训练长度
    result = run_history_forecast(points, "month", 6)
    assert result["status"] == "data_insufficient"
    assert "不连续" in result["reason"]


@pytest.mark.parametrize('grain,count,ready',[('month',2,False),('month',3,True),('week',9,False),('week',10,True)])
def test_short_history_threshold_and_no_leakage(grain,count,ready):
    values=monthly_series(1)[:count] if grain=='month' else weekly_series(count)
    result=run_history_forecast(values,grain,3)
    assert (result['status']=='ready') is ready
    if ready:
        assert len(result['forecast_points'])==3
        assert result['backtest']['sample_count']>0
        assert date.fromisoformat(result['forecast_points'][0]['period_start'])>values[-1][0]


def test_backtest_reports_errors_on_noisy_series():
    result = run_history_forecast(monthly_series(4, noise=1.0), "month", 3)
    assert result["status"] == "ready"
    bt = result["backtest"]
    assert bt["method"] == "rolling_origin"
    assert bt["sample_count"] > 0
    assert bt["mae"] is not None and bt["mae"] < 15.0
    assert bt["smape"] is not None and 0 <= bt["smape"] < 0.15


def test_weekly_forecast_ready():
    result = run_history_forecast(weekly_series(130), "week", 8)
    assert result["status"] == "ready"
    assert len(result["forecast_points"]) == 8
    starts = [date.fromisoformat(p["period_start"]) for p in result["forecast_points"]]
    assert all((b - a).days == 7 for a, b in zip(starts, starts[1:]))  # 连续周
    assert all(d.weekday() == 0 for d in starts)  # 周一起始


def test_baseline_raises_for_empty_series():
    with pytest.raises(DataInsufficient):
        seasonal_median_baseline([], "month", 3)


def test_dedup_and_unsorted_input_tolerated():
    pts = monthly_series(3)
    shuffled = list(reversed(pts)) + [pts[0]]  # 逆序 + 重复
    result = run_history_forecast(shuffled, "month", 1)
    assert result["status"] == "ready"
    assert result["training_window"] == "2023-01-01~2025-12-01"
