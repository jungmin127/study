from tests.live_indicator_fixtures import assert_matches_backtrader, run_backtrader_probe
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_obv,
    create_trade_value,
    create_trade_value_sma,
    create_volume_sma,
)


def test_obv_matches_backtrader():
    df = make_oscillating_df()
    # OBV는 backtrader의 minperiod=2 때문에 next() 첫 값이 bar1부터 시작한다(bar0은 bt가
    # 아예 안 냄). assert_matches_backtrader는 마지막 값만 비교하므로 이 offset과 무관하게
    # 그대로 재사용 가능하다.
    assert_matches_backtrader("OBV", {}, create_obv(df))


def test_volume_sma_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VOLUME_SMA", {"period": 20}, create_volume_sma(df, period=20))


def test_trade_value_matches_raw_trade_value_column():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value(df)
    assert abs(result.iloc[-1] - df["trade_value"].iloc[-1]) < 1e-6


def test_trade_value_sma_matches_manual_rolling_mean():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value_sma(df, period=5)
    manual = df["trade_value"].rolling(5).mean()
    assert abs(result.iloc[-1] - manual.iloc[-1]) < 1e-6


def test_live_indicator_factory_registers_volume_part1():
    assert LIVE_INDICATOR_FACTORY["OBV"] is create_obv
    assert LIVE_INDICATOR_FACTORY["VOLUME_SMA"] is create_volume_sma
    assert LIVE_INDICATOR_FACTORY["TRADE_VALUE"] is create_trade_value
    assert LIVE_INDICATOR_FACTORY["TRADE_VALUE_SMA"] is create_trade_value_sma


def test_volume_sma_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_volume_sma(df, period=20)
    assert result.iloc[:19].isna().all()
    assert result.iloc[20:].notna().all()


def test_trade_value_sma_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value_sma(df, period=5)
    assert result.iloc[:4].isna().all()
    assert result.iloc[5:].notna().all()
