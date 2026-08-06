from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import LIVE_INDICATOR_FACTORY, create_ema, create_sma, create_wma


def test_sma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("SMA", {"period": 14}, create_sma(df, period=14))


def test_ema_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("EMA", {"period": 14}, create_ema(df, period=14))


def test_wma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WMA", {"period": 14}, create_wma(df, period=14))


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


def test_live_indicator_factory_registers_trend_indicators():
    assert LIVE_INDICATOR_FACTORY["SMA"] is create_sma
    assert LIVE_INDICATOR_FACTORY["EMA"] is create_ema
    assert LIVE_INDICATOR_FACTORY["WMA"] is create_wma
