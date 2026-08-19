import pandas as pd
import pytest

from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_ema,
    create_ema_pct,
    create_sma,
    create_sma_pct,
    create_wma,
    create_wma_pct,
)


def test_sma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("SMA", {"period": 14}, create_sma(df, period=14))


def test_ema_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("EMA", {"period": 14}, create_ema(df, period=14))


def test_wma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WMA", {"period": 14}, create_wma(df, period=14))


def test_sma_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("SMA_PCT", {"period": 14}, create_sma_pct(df, period=14))


def test_ema_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("EMA_PCT", {"period": 14}, create_ema_pct(df, period=14))


def test_wma_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WMA_PCT", {"period": 14}, create_wma_pct(df, period=14))


def test_live_indicator_factory_registers_pct_variants():
    assert LIVE_INDICATOR_FACTORY["SMA_PCT"] is create_sma_pct
    assert LIVE_INDICATOR_FACTORY["EMA_PCT"] is create_ema_pct
    assert LIVE_INDICATOR_FACTORY["WMA_PCT"] is create_wma_pct


def test_sma_uses_default_period_14_when_omitted():
    df = make_oscillating_df()
    default = create_sma(df)
    explicit = create_sma(df, period=14)
    assert default.equals(explicit)


def test_sma_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_sma(df, period=14)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].notna().all()


def test_ema_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_ema(df, period=14)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].notna().all()


def test_wma_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_wma(df, period=14)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].notna().all()


def test_live_indicator_factory_registers_trend_indicators():
    assert LIVE_INDICATOR_FACTORY["SMA"] is create_sma
    assert LIVE_INDICATOR_FACTORY["EMA"] is create_ema
    assert LIVE_INDICATOR_FACTORY["WMA"] is create_wma


def _zero_close_df():
    idx = pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC")
    return pd.DataFrame({
        "candle_time": idx, "open": [0.0] * 10, "high": [0.0] * 10,
        "low": [0.0] * 10, "close": [0.0] * 10, "volume": [100.0] * 10,
    })


def test_sma_pct_handles_zero_level_without_crashing():
    df = _zero_close_df()
    result = create_sma_pct(df, period=3)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_ema_pct_handles_zero_level_without_crashing():
    df = _zero_close_df()
    result = create_ema_pct(df, period=3)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_wma_pct_handles_zero_level_without_crashing():
    df = _zero_close_df()
    result = create_wma_pct(df, period=3)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_sma_pct_preserves_warmup_nan_distinct_from_zero_level_guard():
    df = make_oscillating_df()
    period = 5
    result = create_sma_pct(df, period=period)
    assert result.iloc[:period - 1].isna().all()
    assert result.iloc[period - 1:].notna().all()


def test_ema_pct_preserves_warmup_nan_distinct_from_zero_level_guard():
    df = make_oscillating_df()
    period = 5
    result = create_ema_pct(df, period=period)
    assert result.iloc[:period - 1].isna().all()
    assert result.iloc[period - 1:].notna().all()


def test_wma_pct_preserves_warmup_nan_distinct_from_zero_level_guard():
    df = make_oscillating_df()
    period = 5
    result = create_wma_pct(df, period=period)
    assert result.iloc[:period - 1].isna().all()
    assert result.iloc[period - 1:].notna().all()
