"""
tests/test_scan_candle_gaps.py

scripts.scan_candle_gaps.scan_market_gaps()를 검증한다.
"""
from __future__ import annotations

import pandas as pd

from scripts.scan_candle_gaps import scan_market_gaps


def test_scan_market_gaps_detects_single_gap():
    candle_time = pd.to_datetime([
        "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z",
        "2024-01-01T05:00:00Z",  # 1시간 간격이어야 하는데 4시간 결측
        "2024-01-01T06:00:00Z",
    ])
    df = pd.DataFrame({"candle_time": candle_time, "close": [1.0, 1.0, 1.0, 1.0]})

    gaps = scan_market_gaps(df, expected_interval_hours=1.0)

    assert len(gaps) == 1
    assert gaps[0]["gap_hours"] == 4.0


def test_scan_market_gaps_no_gaps_when_regular_interval():
    candle_time = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    df = pd.DataFrame({"candle_time": candle_time, "close": [1.0] * 5})

    gaps = scan_market_gaps(df, expected_interval_hours=1.0)

    assert gaps == []


def test_scan_market_gaps_detects_multiple_gaps():
    candle_time = pd.to_datetime([
        "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z",
        "2024-01-01T04:00:00Z",  # 3시간 결측
        "2024-01-01T05:00:00Z",
        "2024-01-01T10:00:00Z",  # 5시간 결측
    ])
    df = pd.DataFrame({"candle_time": candle_time, "close": [1.0] * 5})

    gaps = scan_market_gaps(df, expected_interval_hours=1.0)

    assert [g["gap_hours"] for g in gaps] == [3.0, 5.0]
