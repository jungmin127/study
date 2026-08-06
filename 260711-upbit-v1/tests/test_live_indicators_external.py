import pandas as pd
import pytest

from tests.live_indicator_fixtures import assert_matches_backtrader_with_aux
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    compute_korea_premium_value,
    create_fear_greed_cmc,
    create_funding_rate,
    create_korea_premium,
)


def test_fear_greed_cmc_matches_backtrader():
    df = make_oscillating_df()
    fear_greed = pd.Series([30.0 + (i % 50) for i in range(len(df))])
    df["fear_greed_value"] = fear_greed
    assert_matches_backtrader_with_aux(
        "FEAR_GREED_CMC", {}, "fear_greed_value", fear_greed,
        create_fear_greed_cmc(df),
    )


def test_korea_premium_matches_backtrader():
    df = make_oscillating_df()
    korea_premium = pd.Series([3.0 + (i % 5) * 0.1 for i in range(len(df))])
    df["korea_premium_value"] = korea_premium
    assert_matches_backtrader_with_aux(
        "KOREA_PREMIUM", {}, "korea_premium_value", korea_premium,
        create_korea_premium(df),
    )


def test_funding_rate_matches_backtrader():
    df = make_oscillating_df()
    funding = pd.Series([0.03] * len(df))
    df["funding_rate_value"] = funding
    assert_matches_backtrader_with_aux(
        "FUNDING_RATE", {}, "funding_rate_value", funding,
        create_funding_rate(df),
    )


def test_compute_korea_premium_value_matches_formula():
    df = pd.DataFrame({
        "close": [100_000_000.0, 101_000_000.0],
        "binance_close": [70000.0, 70500.0],
        "usdt_close": [1400.0, 1405.0],
    })
    result = compute_korea_premium_value(df)
    expected = (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100
    assert result.iloc[0] == pytest.approx(expected.iloc[0])
    assert result.iloc[1] == pytest.approx(expected.iloc[1])


def test_compute_korea_premium_value_propagates_nan_when_binance_close_missing():
    df = pd.DataFrame({
        "close": [100_000_000.0],
        "binance_close": [float("nan")],
        "usdt_close": [1400.0],
    })
    result = compute_korea_premium_value(df)
    assert result.iloc[0] != result.iloc[0]  # NaN


def test_live_indicator_factory_registers_external_group():
    assert LIVE_INDICATOR_FACTORY["FEAR_GREED_CMC"] is create_fear_greed_cmc
    assert LIVE_INDICATOR_FACTORY["KOREA_PREMIUM"] is create_korea_premium
    assert LIVE_INDICATOR_FACTORY["FUNDING_RATE"] is create_funding_rate
