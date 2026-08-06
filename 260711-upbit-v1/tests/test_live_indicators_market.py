from tests.live_indicator_fixtures import assert_matches_backtrader_with_aux
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import LIVE_INDICATOR_FACTORY, create_market_trend


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
