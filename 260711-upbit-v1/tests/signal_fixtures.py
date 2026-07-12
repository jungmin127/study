"""신호 테스트에서 공유하는 합성 OHLCV 데이터."""
from __future__ import annotations

import math

import pandas as pd


def make_oscillating_df(
    n: int = 300,
    base: float = 20000.0,
    amplitude: float = 600.0,
    period: int = 120,
    ripple_amplitude: float = 50.0,
    ripple_period: int = 6,
) -> pd.DataFrame:
    prices = [
        base
        + amplitude * math.sin(2 * math.pi * i / period)
        + ripple_amplitude * math.sin(2 * math.pi * i / ripple_period)
        for i in range(n)
    ]
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1.0] * n,
        }
    )
