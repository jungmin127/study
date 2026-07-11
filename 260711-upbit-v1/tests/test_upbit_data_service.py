from datetime import datetime, timezone

import httpx
import pytest

import upbit_data_service as uds
from upbit_data_service import _endpoint_for_timeframe, _parse_candles, _fetch_page


def test_endpoint_for_days():
    assert _endpoint_for_timeframe("days") == "https://api.upbit.com/v1/candles/days"


def test_endpoint_for_minutes():
    assert _endpoint_for_timeframe("minutes60") == "https://api.upbit.com/v1/candles/minutes/60"


def test_endpoint_for_unsupported_timeframe_raises():
    with pytest.raises(ValueError):
        _endpoint_for_timeframe("weeks")


def test_parse_candles_maps_fields():
    raw = [
        {
            "market": "KRW-BTC",
            "candle_date_time_utc": "2026-07-10T00:00:00",
            "opening_price": 100.0,
            "high_price": 110.0,
            "low_price": 90.0,
            "trade_price": 105.0,
            "candle_acc_trade_volume": 12.5,
        }
    ]

    df = _parse_candles(raw)

    assert list(df.columns) == ["candle_time", "open", "high", "low", "close", "volume"]
    assert df.iloc[0]["open"] == 100.0
    assert df.iloc[0]["close"] == 105.0
    assert df.iloc[0]["volume"] == 12.5
    assert df["candle_time"].dt.tz is not None


def test_parse_candles_empty_input():
    df = _parse_candles([])
    assert list(df.columns) == ["candle_time", "open", "high", "low", "close", "volume"]
    assert len(df) == 0


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_page_returns_json_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["market"] == "KRW-BTC"
        return httpx.Response(200, json=[{"market": "KRW-BTC"}])

    with _mock_client(handler) as client:
        result = _fetch_page(client, "https://api.upbit.com/v1/candles/days", "KRW-BTC", None)

    assert result == [{"market": "KRW-BTC"}]


def test_fetch_page_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(uds, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[{"market": "KRW-BTC"}])

    with _mock_client(handler) as client:
        result = _fetch_page(
            client, "https://api.upbit.com/v1/candles/days", "KRW-BTC",
            datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

    assert calls["count"] == 2
    assert result == [{"market": "KRW-BTC"}]


def test_fetch_page_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(uds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_page(client, "https://api.upbit.com/v1/candles/days", "KRW-BTC", None)
