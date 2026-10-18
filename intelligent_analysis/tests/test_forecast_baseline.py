from datetime import datetime, timedelta, timezone

from intelligent_analysis.short_forecast.backtest import mean_absolute_error
from intelligent_analysis.short_forecast.baseline import naive_baseline


def test_naive_baseline_uses_last_value():
    cutoff = datetime(2026, 10, 11, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    points = naive_baseline([3, 5], horizon_steps=3, step_minutes=5, cutoff=cutoff)

    assert [value for _, value in points] == [5.0, 5.0, 5.0]
    assert points[0][0].minute == 5
    assert points[-1][0].minute == 15


def test_mean_absolute_error():
    assert mean_absolute_error([1, 2, 3], [1, 3, 2]) == 2 / 3
