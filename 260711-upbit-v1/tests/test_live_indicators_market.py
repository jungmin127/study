import pandas as pd

from tests.live_indicator_fixtures import assert_matches_backtrader_with_aux
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_market_trend,
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
