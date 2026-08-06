from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_atr,
    create_atr_pct,
    create_bb_lower,
    create_bb_middle,
    create_bb_percent_b,
    create_bb_upper,
)


def test_atr_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("ATR", {"period": 14}, create_atr(df, period=14))


def test_atr_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("ATR_PCT", {"period": 14}, create_atr_pct(df, period=14))


def test_bb_upper_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_upper", {"period": 20}, create_bb_upper(df, period=20))


def test_bb_lower_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_lower", {"period": 20}, create_bb_lower(df, period=20))


def test_bb_middle_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_middle", {"period": 20}, create_bb_middle(df, period=20))


def test_bb_percent_b_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("BB_PERCENT_B", {"period": 20}, create_bb_percent_b(df, period=20))


def test_atr_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_atr(df, period=14)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].notna().all()


def test_bb_upper_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_bb_upper(df, period=20)
    assert result.iloc[:19].isna().all()
    assert result.iloc[19:].notna().all()


def test_live_indicator_factory_registers_volatility():
    assert LIVE_INDICATOR_FACTORY["ATR"] is create_atr
    assert LIVE_INDICATOR_FACTORY["ATR_PCT"] is create_atr_pct
    assert LIVE_INDICATOR_FACTORY["BB_upper"] is create_bb_upper
    assert LIVE_INDICATOR_FACTORY["BB_lower"] is create_bb_lower
    assert LIVE_INDICATOR_FACTORY["BB_middle"] is create_bb_middle
    assert LIVE_INDICATOR_FACTORY["BB_PERCENT_B"] is create_bb_percent_b
