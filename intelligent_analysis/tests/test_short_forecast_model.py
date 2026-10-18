from datetime import UTC, datetime, timedelta

import pytest

from intelligent_analysis.service import forecast_from_count_history
from intelligent_analysis.short_forecast.backtest import walk_forward_persistence


def history(n=36):
    cutoff = datetime(2026, 10, 14, 10, tzinfo=UTC)
    return cutoff, [(cutoff - timedelta(minutes=5 * (n - 1 - i)), 20.0) for i in range(n)]


def test_forecast_is_validated_and_has_no_invented_interval():
    cutoff, series = history()
    result = forecast_from_count_history(series, cutoff, 30)
    assert result["status"] == "ready"
    assert len(result["forecast_points"]) == 6
    assert result["validation_error"]["30"]["fold_count"] >= 3
    assert all(p["lower"] is None and p["upper"] is None for p in result["forecast_points"])


def test_cutoff_excludes_future_values_and_no_leakage():
    cutoff, series = history()
    expected = forecast_from_count_history(series, cutoff, 15)
    series.append((cutoff + timedelta(minutes=5), 10000000.0))
    assert forecast_from_count_history(series, cutoff, 15) == expected


@pytest.mark.parametrize("minutes", [15, 30])
def test_minimum_history_alone_does_not_prove_forecast_quality(minutes):
    cutoff, series = history(12 if minutes == 15 else 24)
    result = forecast_from_count_history(series, cutoff, minutes)
    assert result["status"] == "data_insufficient"
    assert result["forecast_points"] == []


def test_sixty_disabled_and_gaps_rejected():
    cutoff, series = history()
    assert forecast_from_count_history(series, cutoff, 60)["status"] == "data_insufficient"
    series.pop(10)
    assert forecast_from_count_history(series, cutoff, 15)["status"] == "quality_insufficient"


def test_invalid_or_stale_history_and_parameters():
    cutoff, series = history()
    with pytest.raises(ValueError):
        forecast_from_count_history(series, cutoff, 20)
    with pytest.raises(ValueError):
        forecast_from_count_history(series + [series[-1]], cutoff, 15)
    series[-1] = (series[-1][0], None)
    assert forecast_from_count_history(series, cutoff, 15)["status"] == "quality_insufficient"


def test_walk_forward_persistence():
    assert walk_forward_persistence([1, 2, 3, 4, 5], minimum_train=3)["mae"] == 1.0
