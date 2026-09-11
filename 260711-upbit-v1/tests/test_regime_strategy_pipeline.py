from datetime import datetime, timezone

from scripts.regime_strategy_pipeline import adjust_window, min_trades_for_days


def test_min_trades_for_days_boundary():
    assert min_trades_for_days(1) == 3
    assert min_trades_for_days(5) == 3
    assert min_trades_for_days(15) == 3
    assert min_trades_for_days(20) == 4


def test_adjust_window_keeps_already_long_enough_segment():
    seg = {"start": "2026-06-01T00:00:00+00:00", "end": "2026-06-20T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, tzinfo=timezone.utc)


def test_adjust_window_widens_short_segment_by_pulling_start_back():
    seg = {"start": "2026-06-15T00:00:00+00:00", "end": "2026-06-20T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, tzinfo=timezone.utc)


def test_adjust_window_clamps_to_history_start():
    seg = {"start": "2026-01-05T00:00:00+00:00", "end": "2026-01-08T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == history_start
    assert end == datetime(2026, 1, 8, tzinfo=timezone.utc)
