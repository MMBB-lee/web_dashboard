from datetime import UTC, datetime

from intelligent_analysis.vision.validation import CountWindow, validate_counts


def test_validate_counts_reports_mae():
    timestamp = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
    manual = [CountWindow(timestamp, entries=10, exits=4)]
    automatic = [CountWindow(timestamp, entries=8, exits=5)]

    result = validate_counts(manual, automatic)

    assert result.sample_count == 1
    assert result.entries_mae == 2
    assert result.exits_mae == 1
    assert result.mae == 1.5
