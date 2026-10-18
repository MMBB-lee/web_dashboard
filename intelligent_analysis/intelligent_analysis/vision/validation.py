"""Compare automatic counts with manual annotation samples."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CountWindow:
    window_start: datetime
    entries: int
    exits: int


@dataclass(frozen=True)
class CountValidationResult:
    sample_count: int
    entries_mae: float
    exits_mae: float

    @property
    def mae(self) -> float:
        return (self.entries_mae + self.exits_mae) / 2


def validate_counts(
    manual_windows: Iterable[CountWindow], automatic_windows: Iterable[CountWindow]
) -> CountValidationResult:
    automatic = {window.window_start: window for window in automatic_windows}
    entry_errors: list[float] = []
    exit_errors: list[float] = []

    for manual in manual_windows:
        automatic_window = automatic.get(manual.window_start)
        if automatic_window is None:
            continue
        entry_errors.append(abs(manual.entries - automatic_window.entries))
        exit_errors.append(abs(manual.exits - automatic_window.exits))

    if not entry_errors:
        raise ValueError("no matching windows were found")

    return CountValidationResult(
        sample_count=len(entry_errors),
        entries_mae=sum(entry_errors) / len(entry_errors),
        exits_mae=sum(exit_errors) / len(exit_errors),
    )


def count_window_from_dict(item: dict[str, Any]) -> CountWindow:
    return CountWindow(
        window_start=item["window_start"],
        entries=int(item["entries"]),
        exits=int(item["exits"]),
    )
