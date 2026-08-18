import statistics

import pandas as pd
import pytest

from tests.live_indicator_fixtures import assert_matches_backtrader, run_backtrader_probe
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_obv,
    create_trade_value,
    create_trade_value_pct,
    create_trade_value_sma,
    create_volume_sma,
    create_vpin,
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


def test_trade_value_pct_matches_manual_ratio_to_own_sma():
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    result = create_trade_value_pct(df, period=5)
    sma = df["trade_value"].rolling(5).mean()
    manual = (df["trade_value"] - sma) / sma * 100
    assert abs(result.iloc[-1] - manual.iloc[-1]) < 1e-6


def test_live_indicator_factory_registers_trade_value_pct():
    assert LIVE_INDICATOR_FACTORY["TRADE_VALUE_PCT"] is create_trade_value_pct


def test_vpin_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPIN", {"period": 20}, create_vpin(df, period=20))


def test_vpin_matches_hand_traced_bucket_sequence():
    # engine/indicators/volume.py의 VolumeBarVPIN을 검증한 것과 동일한 손 계산 시퀀스
    # (tests/test_indicators.py::test_vpin_matches_hand_traced_bucket_sequence 참고).
    volumes = [10, 10, 2, 2, 2, 1, 1, 10]
    closes = [100, 100, 100, 100, 100, 100, 100, 105]
    idx = pd.date_range("2026-01-01", periods=8, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": volumes,
    })
    values = create_vpin(df, period=2)

    assert values.iloc[:4].isna().all()
    assert values.iloc[4] == pytest.approx(0.0)
    assert values.iloc[5] == values.iloc[4]
    assert values.iloc[6] == pytest.approx(0.0)

    sigma = statistics.stdev([0.0, 5.0])
    z = 5.0 / sigma
    buy_ratio = statistics.NormalDist().cdf(z)
    imbalance_bucket_8 = abs(2 * buy_ratio - 1)
    expected_bar8 = imbalance_bucket_8 / 2
    assert values.iloc[7] == pytest.approx(expected_bar8)


def test_live_indicator_factory_registers_vpin():
    assert LIVE_INDICATOR_FACTORY["VPIN"] is create_vpin
