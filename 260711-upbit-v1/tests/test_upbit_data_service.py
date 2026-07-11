from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest

import upbit_data_service as uds
from upbit_data_service import _endpoint_for_timeframe, _parse_candles, _fetch_page, _fetch_range, _compute_gaps, get_candles


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


def _candle(iso_time: str, price: float) -> dict:
    return {
        "market": "KRW-BTC",
        "candle_date_time_utc": iso_time,
        "opening_price": price,
        "high_price": price,
        "low_price": price,
        "trade_price": price,
        "candle_acc_trade_volume": 1.0,
    }


def test_fetch_range_single_page_when_within_count(monkeypatch):
    monkeypatch.setattr(uds, "REQUEST_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _candle("2026-07-10T00:00:00", 103),
                _candle("2026-07-09T00:00:00", 102),
                _candle("2026-07-08T00:00:00", 101),
            ],
        )

    with _mock_client(handler) as client:
        df = _fetch_range(
            "KRW-BTC", "days",
            datetime(2026, 7, 8, tzinfo=timezone.utc),
            datetime(2026, 7, 10, tzinfo=timezone.utc),
            client=client,
        )

    assert len(df) == 3
    assert df["candle_time"].is_monotonic_increasing


def test_fetch_range_pages_backward_until_start_reached(monkeypatch):
    monkeypatch.setattr(uds, "REQUEST_DELAY_SECONDS", 0.0)
    calls = {"count": 0}
    page1_end = datetime(2026, 7, 10, tzinfo=timezone.utc)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            # 200개 꽉 채운 페이지(가장 오래된 캔들도 여전히 start보다 나중)를 반환해
            # "더 오래된 데이터가 있으니 한 페이지 더 가져해야 함"을 흉내낸다.
            page = [
                _candle(
                    (page1_end - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S"),
                    100 + i,
                )
                for i in range(200)
            ]
            return httpx.Response(200, json=page)
        # 두 번째 페이지에서 start 이전 캔들을 반환해 페이지네이션 종료 조건을 만족시킨다.
        page = [_candle("2025-01-01T00:00:00", 50)]
        return httpx.Response(200, json=page)

    with _mock_client(handler) as client:
        df = _fetch_range("KRW-BTC", "days", start, page1_end, client=client)

    assert calls["count"] == 2
    assert df["candle_time"].min() <= start


def _df_with_range(start_iso: str, end_iso: str) -> pd.DataFrame:
    idx = pd.date_range(start_iso, end_iso, freq="D", tz="UTC")
    return pd.DataFrame({"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1})


def test_compute_gaps_no_cache_returns_full_range():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(pd.DataFrame(columns=uds._CANDLE_COLUMNS), start, end)
    assert gaps == [(start, end)]


def test_compute_gaps_missing_front_only():
    cached = _df_with_range("2026-01-05", "2026-01-10")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(cached, start, end)
    assert len(gaps) == 1
    assert gaps[0][0] == start
    assert gaps[0][1] < datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_compute_gaps_missing_back_only():
    cached = _df_with_range("2026-01-01", "2026-01-05")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(cached, start, end)
    assert len(gaps) == 1
    assert gaps[0][1] == end
    assert gaps[0][0] > datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_compute_gaps_fully_covered_returns_empty():
    cached = _df_with_range("2026-01-01", "2026-01-10")
    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    end = datetime(2026, 1, 9, tzinfo=timezone.utc)
    assert _compute_gaps(cached, start, end) == []


def test_get_candles_fetches_full_range_when_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(uds, "CACHE_DIR", tmp_path)
    calls: list[tuple] = []

    def fake_fetch_range(market, timeframe, start, end, client=None):
        calls.append((market, timeframe, start, end))
        idx = pd.date_range(start, end, freq="D", tz="UTC")
        return pd.DataFrame(
            {"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        )

    monkeypatch.setattr(uds, "_fetch_range", fake_fetch_range)
    monkeypatch.setattr(
        uds, "datetime",
        type("_FixedDatetime", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 1, 20, tzinfo=timezone.utc))
        }),
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    df = get_candles("KRW-BTC", "days", start, end)

    assert len(calls) == 1
    assert len(df) == 10
    assert (tmp_path / "KRW-BTC_days.parquet").exists()


def test_get_candles_skips_fetch_when_fully_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(uds, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        uds, "datetime",
        type("_FixedDatetime", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 1, 20, tzinfo=timezone.utc))
        }),
    )

    idx = pd.date_range("2026-01-01", "2026-01-10", freq="D", tz="UTC")
    existing = pd.DataFrame(
        {"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    existing.to_parquet(tmp_path / "KRW-BTC_days.parquet", index=False)

    def fail_fetch_range(*args, **kwargs):
        raise AssertionError("캐시가 이미 구간을 커버하므로 호출되면 안 됨")

    monkeypatch.setattr(uds, "_fetch_range", fail_fetch_range)

    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    end = datetime(2026, 1, 9, tzinfo=timezone.utc)
    df = get_candles("KRW-BTC", "days", start, end)

    assert len(df) == 8
