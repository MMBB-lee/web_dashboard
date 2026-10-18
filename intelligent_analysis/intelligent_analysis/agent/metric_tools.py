"""Constrained metric tool placeholders.

The API layer will inject a safe metric query callable. Arbitrary SQL must never
be exposed to the assistant.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

MetricQuery = Callable[[dict[str, Any]], dict[str, Any]]


def run_metric_query(query: dict[str, Any], metric_query: MetricQuery | None = None) -> dict[str, Any]:
    if metric_query is None:
        return {"status": "metric_tool_unavailable"}
    return metric_query(query)
