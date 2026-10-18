"""Time-ordered validation and backtesting for short-term forecasts."""

from __future__ import annotations

from collections.abc import Sequence


def mean_absolute_error(actual: Sequence[float], predicted: Sequence[float]) -> float:
    _validate_equal_length(actual, predicted)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def symmetric_mean_absolute_percentage_error(
    actual: Sequence[float], predicted: Sequence[float]
) -> float:
    _validate_equal_length(actual, predicted)
    denominator = [(abs(a) + abs(p)) / 2 for a, p in zip(actual, predicted)]
    errors = [
        abs(a - p) / denominator_value
        for a, p, denominator_value in zip(actual, predicted, denominator)
        if denominator_value > 0
    ]
    return sum(errors) / len(errors) if errors else 0.0


def walk_forward_persistence(history: Sequence[float], minimum_train: int = 4) -> dict[str, float]:
    if len(history) <= minimum_train:
        raise ValueError("history must contain more points than minimum_train")
    actual: list[float] = []
    predicted: list[float] = []
    for index in range(minimum_train, len(history)):
        actual.append(float(history[index]))
        predicted.append(float(history[index - 1]))
    return {
        "mae": mean_absolute_error(actual, predicted),
        "smape": symmetric_mean_absolute_percentage_error(actual, predicted),
        "sample_count": float(len(actual)),
    }


def _validate_equal_length(actual: Sequence[float], predicted: Sequence[float]):
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted must be non-empty and have equal length")
