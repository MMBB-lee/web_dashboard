"""Damped Holt forecasting for fixed-ROI visible people."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise

from ..contracts import ForecastPoint

MIN_HISTORY_POINTS = 4


def naive_baseline(
    history: Sequence[float], horizon_steps: int, step_minutes: int, cutoff: datetime
) -> list[tuple[datetime, float]]:
    if not history:
        raise ValueError("history must not be empty")
    value = float(history[-1])
    return _future_times(cutoff, horizon_steps, step_minutes, value)


def forecast_from_history(
    history: Sequence[tuple[datetime, float]],
    cutoff: datetime,
    horizon_minutes: int,
    step_minutes: int,
) -> tuple[list[ForecastPoint], dict[str, float]]:
    result = forecast_visible_history(history, cutoff, horizon_minutes, step_minutes)
    if result["status"] != "ready":
        raise ValueError(result["reason"])
    return ([ForecastPoint.model_validate(p) for p in result["forecast_points"]],
            result["validation_error"][str(horizon_minutes)])


def _holt(values: list[float], steps: int) -> list[float]:
    """Fit level/trend/damping by in-sample one-step SSE on this prefix only."""
    best = None
    for alpha in (0.2, 0.5, 0.8):
        for beta in (0.1, 0.3):
            for phi in (0.8, 0.9, 0.98):
                level, trend, error = values[0], values[1] - values[0], 0.0
                for value in values[1:]:
                    previous = level
                    predicted = level + phi * trend
                    error += (value - predicted) ** 2
                    level = alpha * value + (1 - alpha) * predicted
                    trend = beta * (level - previous) + (1 - beta) * phi * trend
                if best is None or error < best[0]:
                    best = (error, level, trend, phi)
    _, level, trend, phi = best
    return [max(0.0, level + sum(phi ** i for i in range(1, h + 1)) * trend)
            for h in range(1, steps + 1)]


def forecast_visible_history(history, cutoff: datetime, horizon_minutes: int,
                             step_minutes: int = 5) -> dict:
    if cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    if type(horizon_minutes) is not int or horizon_minutes not in (15, 30, 60):
        raise ValueError("horizon_minutes must be 15, 30 or 60")
    if type(step_minutes) is not int or step_minutes != 5:
        raise ValueError("step_minutes must be 5")
    result = {"metric": "camera_visible_people", "status": "data_insufficient",
              "forecast_points": [], "training_cutoff": cutoff.isoformat(),
              "input_window_minutes": 0, "model_version": "visible-damped-holt-0.5.0",
              "validation_error": None, "reason": None}
    if horizon_minutes == 60:
        result["reason"] = "60-minute forecasting is disabled pending multi-day validation"
        return result
    for timestamp, _ in history:
        if timestamp.utcoffset() is None:
            raise ValueError("history timestamps must be timezone-aware")
    ordered = sorted((t, v) for t, v in history if t <= cutoff)
    if not ordered:
        result["reason"] = "No completed history before cutoff"
        return result
    if len({t for t, _ in ordered}) != len(ordered):
        raise ValueError("duplicate history timestamps")
    if cutoff - ordered[-1][0] >= timedelta(minutes=5):
        result.update(status="quality_insufficient", reason="Latest count is stale")
        return result
    if any(v is None or not math.isfinite(float(v)) or v < 0 for _, v in ordered):
        result.update(status="quality_insufficient", reason="History contains invalid/missing counts")
        return result
    if any(b[0] - a[0] != timedelta(minutes=5) for a, b in pairwise(ordered)):
        result.update(status="quality_insufficient", reason="History is not continuous at five minutes")
        return result
    values = [float(v) for _, v in ordered]
    minimum = 12 if horizon_minutes == 15 else 24
    steps = horizon_minutes // 5
    result["input_window_minutes"] = len(values) * 5
    if len(values) < minimum + steps + 2:
        result["reason"] = "Insufficient history for at least three completed rolling-origin folds"
        return result
    errors, persistence_errors = [], []
    for origin in range(minimum, len(values) - steps + 1):
        predicted = _holt(values[:origin], steps)
        actual = values[origin:origin + steps]
        errors.extend(abs(a - p) for a, p in zip(actual, predicted))
        persistence_errors.extend(abs(a - values[origin - 1]) for a in actual)
    mae = sum(errors) / len(errors)
    baseline = sum(persistence_errors) / len(persistence_errors)
    result["validation_error"] = {str(horizon_minutes): {
        "mae": mae, "persistence_mae": baseline,
        "fold_count": len(errors) // steps, "sample_count": len(errors)}}
    if mae > baseline + 1e-9:
        result.update(status="quality_insufficient", reason="Damped Holt did not match persistence MAE")
        return result
    predictions = _holt(values, steps)
    result.update(status="ready", reason=None, forecast_points=[{
        "target_time": (cutoff + timedelta(minutes=5 * h)).isoformat(),
        "value": value, "is_forecast": True, "lower": None, "upper": None}
        for h, value in enumerate(predictions, 1)])
    return result


def _future_times(
    cutoff: datetime, horizon_steps: int, step_minutes: int, value: float
) -> list[tuple[datetime, float]]:
    return [
        (cutoff + timedelta(minutes=step_minutes * (index + 1)), value)
        for index in range(horizon_steps)
    ]
