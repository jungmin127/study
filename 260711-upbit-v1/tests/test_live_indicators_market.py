import pandas as pd

from tests.live_indicator_fixtures import assert_matches_backtrader_with_aux
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_market_trend,
    create_market_trend_pct,
    create_btc_correlation,
    create_usdt_correlation,
)


def test_market_trend_matches_backtrader():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000  # 대상 마켓과 스케일이 다른 별도 시세임을 검증
    df["btc_close"] = btc_close
    assert_matches_backtrader_with_aux(
        "MARKET_TREND", {"period": 5}, "btc_close", btc_close,
        create_market_trend(df, period=5),
    )


def test_market_trend_uses_default_period_10_when_omitted():
    df = make_oscillating_df()
    df["btc_close"] = df["close"] * 2 + 1000
    default = create_market_trend(df)
    explicit = create_market_trend(df, period=10)
    assert default.equals(explicit)


def test_live_indicator_factory_registers_market_trend():
    assert LIVE_INDICATOR_FACTORY["MARKET_TREND"] is create_market_trend


def test_market_trend_pct_matches_backtrader():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000
    df["btc_close"] = btc_close
    assert_matches_backtrader_with_aux(
        "MARKET_TREND_PCT", {"period": 5}, "btc_close", btc_close,
        create_market_trend_pct(df, period=5),
    )


def test_market_trend_pct_uses_default_period_10_when_omitted():
    df = make_oscillating_df()
    df["btc_close"] = df["close"] * 2 + 1000
    default = create_market_trend_pct(df)
    explicit = create_market_trend_pct(df, period=10)
    assert default.equals(explicit)


def test_live_indicator_factory_registers_market_trend_pct():
    assert LIVE_INDICATOR_FACTORY["MARKET_TREND_PCT"] is create_market_trend_pct


def test_market_trend_pct_handles_zero_level_without_crashing():
    # BTC 이동평균이 0인 극단 케이스(합성 데이터) — inf/NaN 없이 0.0을 반환해야 함.
    df = make_oscillating_df()
    df["btc_close"] = 0.0
    result = create_market_trend_pct(df, period=3)
    assert result.iloc[-1] == 0.0


def test_market_trend_pct_preserves_warmup_nan_distinct_from_zero_level_guard():
    df = make_oscillating_df()
    df["btc_close"] = df["close"] * 2 + 1000
    period = 5
    result = create_market_trend_pct(df, period=period)
    assert result.iloc[:period - 1].isna().all()
    assert result.iloc[period - 1:].notna().all()


def test_btc_correlation_matches_backtrader():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    df["btc_close"] = btc_df["close"]
    assert_matches_backtrader_with_aux(
        "BTC_CORRELATION", {"period": 10}, "btc_close", btc_df["close"],
        create_btc_correlation(df, period=10),
    )


def test_usdt_correlation_matches_backtrader():
    df = make_oscillating_df()
    usdt_df = make_oscillating_df(base=1300.0, amplitude=40.0, period=30, ripple_period=4)
    df["usdt_close"] = usdt_df["close"]
    assert_matches_backtrader_with_aux(
        "USDT_CORRELATION", {"period": 10}, "usdt_close", usdt_df["close"],
        create_usdt_correlation(df, period=10),
    )


def test_usdt_correlation_returns_zero_when_aux_series_is_constant():
    # KRW-USDT 등 페그/스테이블코인 마켓이 완전히 flat(무변동)한 구간에서는 피어슨
    # 상관계수가 수학적으로 정의되지 않는다 — 크래시/NaN 대신 "상관 신호 없음"으로
    # 0.0을 반환해야 한다(engine/indicators/market.py의 RollingCorrelation과 동일 정책).
    df = make_oscillating_df()
    flat_usdt = pd.Series([1300.0] * len(df))
    df["usdt_close"] = flat_usdt
    result = create_usdt_correlation(df, period=10)
    assert result.iloc[-1] == 0.0


def test_btc_correlation_uses_default_period_20_when_omitted():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    df["btc_close"] = btc_df["close"]
    default = create_btc_correlation(df)
    explicit = create_btc_correlation(df, period=20)
    assert default.equals(explicit)


def test_live_indicator_factory_registers_correlations():
    assert LIVE_INDICATOR_FACTORY["BTC_CORRELATION"] is create_btc_correlation
    assert LIVE_INDICATOR_FACTORY["USDT_CORRELATION"] is create_usdt_correlation


def test_market_trend_propagates_nan_when_btc_close_has_gap_at_end():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000
    btc_close = btc_close.copy()
    btc_close.iloc[-1] = float("nan")
    df["btc_close"] = btc_close
    result = create_market_trend(df, period=5)
    assert pd.isna(result.iloc[-1])


def test_btc_correlation_propagates_nan_when_btc_close_has_gap_at_end():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    btc_close = btc_df["close"].copy()
    btc_close.iloc[-1] = float("nan")
    df["btc_close"] = btc_close
    result = create_btc_correlation(df, period=10)
    assert pd.isna(result.iloc[-1])


def test_usdt_correlation_propagates_nan_when_usdt_close_has_gap_mid_window():
    df = make_oscillating_df()
    usdt_df = make_oscillating_df(base=1300.0, amplitude=40.0, period=30, ripple_period=4)
    usdt_close = usdt_df["close"].copy()
    usdt_close.iloc[-5] = float("nan")  # a gap inside the rolling window, not at the very edge
    df["usdt_close"] = usdt_close
    result = create_usdt_correlation(df, period=10)
    assert pd.isna(result.iloc[-1])
