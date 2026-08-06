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


from datetime import datetime, timedelta, timezone

import trading.live_indicators as live_indicators
from trading.live_indicators import fetch_live_fear_greed_value, fetch_live_funding_rate_value


def test_fetch_live_fear_greed_value_returns_latest_when_fresh(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_fear_greed_cmc(start, end):
        return pd.DataFrame({
            "date": [now.replace(hour=0, minute=0, second=0, microsecond=0)],
            "fear_greed_value": [55.0],
        })

    monkeypatch.setattr(live_indicators, "get_fear_greed_cmc", fake_get_fear_greed_cmc)
    assert fetch_live_fear_greed_value(now=now) == pytest.approx(55.0)


def test_fetch_live_fear_greed_value_returns_none_when_stale(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    stale_date = now - timedelta(days=5)

    def fake_get_fear_greed_cmc(start, end):
        return pd.DataFrame({"date": [stale_date], "fear_greed_value": [55.0]})

    monkeypatch.setattr(live_indicators, "get_fear_greed_cmc", fake_get_fear_greed_cmc)
    assert fetch_live_fear_greed_value(now=now) is None


def test_fetch_live_fear_greed_value_returns_none_when_empty(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        live_indicators, "get_fear_greed_cmc",
        lambda start, end: pd.DataFrame(columns=["date", "fear_greed_value"]),
    )
    assert fetch_live_fear_greed_value(now=now) is None


def test_fetch_live_fear_greed_value_returns_none_on_api_failure(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def raise_runtime_error(start, end):
        raise RuntimeError("alternative.me 공포탐욕지수 API 호출 실패")

    monkeypatch.setattr(live_indicators, "get_fear_greed_cmc", raise_runtime_error)
    assert fetch_live_fear_greed_value(now=now) is None


def test_fetch_live_funding_rate_value_returns_latest_when_fresh(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_binance_funding_rate(symbol, start, end):
        assert symbol == "ETHUSDT"
        return pd.DataFrame({
            "funding_time": [now - timedelta(hours=2)],
            "funding_rate": [0.012],
        })

    monkeypatch.setattr(live_indicators, "get_binance_funding_rate", fake_get_binance_funding_rate)
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) == pytest.approx(0.012)


def test_fetch_live_funding_rate_value_returns_none_when_stale(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def fake_get_binance_funding_rate(symbol, start, end):
        return pd.DataFrame({
            "funding_time": [now - timedelta(hours=20)],
            "funding_rate": [0.012],
        })

    monkeypatch.setattr(live_indicators, "get_binance_funding_rate", fake_get_binance_funding_rate)
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) is None


def test_fetch_live_funding_rate_value_returns_none_when_empty(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        live_indicators, "get_binance_funding_rate",
        lambda symbol, start, end: pd.DataFrame(columns=["funding_time", "funding_rate"]),
    )
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) is None


def test_fetch_live_funding_rate_value_returns_none_on_api_failure(monkeypatch):
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    def raise_runtime_error(symbol, start, end):
        raise RuntimeError("바이낸스 펀딩비 API 호출 실패")

    monkeypatch.setattr(live_indicators, "get_binance_funding_rate", raise_runtime_error)
    assert fetch_live_funding_rate_value("KRW-ETH", now=now) is None
