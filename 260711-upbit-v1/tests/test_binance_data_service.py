from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

import binance_data_service as bds
from binance_data_service import (
    BinanceSymbolNotFoundError,
    _compute_gaps,
    _fetch_page,
    _fetch_range,
    _interval_for_timeframe,
    _parse_klines,
    binance_symbol,
    get_binance_close,
)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _kline(open_time_ms: int, close: float) -> list:
    return [open_time_ms, "0", "0", "0", str(close), "1", open_time_ms + 3599999, "0", 1, "0", "0", "0"]


def test_binance_symbol_strips_krw_prefix_and_appends_usdt():
    assert binance_symbol("KRW-ETH") == "ETHUSDT"
    assert binance_symbol("KRW-BTC") == "BTCUSDT"


def test_interval_for_timeframe_maps_all_supported_timeframes():
    assert _interval_for_timeframe("minutes5") == "5m"
    assert _interval_for_timeframe("minutes15") == "15m"
    assert _interval_for_timeframe("minutes30") == "30m"
    assert _interval_for_timeframe("minutes60") == "1h"
    assert _interval_for_timeframe("days") == "1d"


def test_interval_for_timeframe_raises_for_unsupported_timeframe():
    with pytest.raises(ValueError):
        _interval_for_timeframe("weeks")


def test_fetch_page_returns_klines_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "ETHUSDT"
        assert request.url.params["interval"] == "1h"
        return httpx.Response(200, json=[_kline(1785110400000, 1900.0)])

    with _mock_client(handler) as client:
        raw = _fetch_page(
            client, "ETHUSDT", "1h",
            datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

    assert raw == [_kline(1785110400000, 1900.0)]


def test_fetch_page_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(bds, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[_kline(1785110400000, 1900.0)])

    with _mock_client(handler) as client:
        raw = _fetch_page(
            client, "ETHUSDT", "1h",
            datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

    assert calls["count"] == 2
    assert len(raw) == 1


def test_fetch_page_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(bds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_page(
                client, "ETHUSDT", "1h",
                datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 16, tzinfo=timezone.utc),
            )


def test_fetch_page_raises_symbol_not_found_immediately_without_retry():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    with _mock_client(handler) as client:
        with pytest.raises(BinanceSymbolNotFoundError):
            _fetch_page(
                client, "NOTAREALCOINUSDT", "1h",
                datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 16, tzinfo=timezone.utc),
            )

    # 재시도 대상이 아니므로 정확히 한 번만 호출돼야 한다(RETRY_ATTEMPTS=3만큼 반복되면 안 됨)
    assert calls["count"] == 1


def test_parse_klines_maps_open_time_and_close():
    # 1767225600000ms = 2026-01-01T00:00:00Z (이 저장소의 다른 테스트들과 동일한 기준일)
    raw = [_kline(1767225600000, 1922.23)]

    df = _parse_klines(raw)

    assert list(df.columns) == ["candle_time", "close"]
    assert df.iloc[0]["close"] == 1922.23
    assert df.iloc[0]["candle_time"] == pd.Timestamp("2026-01-01 00:00:00", tz="UTC")


def test_parse_klines_empty_input():
    df = _parse_klines([])
    assert list(df.columns) == ["candle_time", "close"]
    assert df.empty


def test_fetch_range_single_page_when_within_limit(monkeypatch):
    monkeypatch.setattr(bds, "REQUEST_DELAY_SECONDS", 0.0)

    # 1784073600000ms = 2026-07-15T00:00:00Z (아래 start=datetime(2026,7,15)와 일치시켜야
    # _fetch_range의 [start, end] 필터를 통과한다)
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _kline(1784073600000, 1900.0),
                _kline(1784077200000, 1901.0),
                _kline(1784080800000, 1902.0),
            ],
        )

    with _mock_client(handler) as client:
        df = _fetch_range(
            "ETHUSDT", "minutes60",
            datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 15, 3, tzinfo=timezone.utc),
            client=client,
        )

    assert len(df) == 3
    assert df["candle_time"].is_monotonic_increasing


def test_compute_gaps_no_cache_returns_full_range():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(pd.DataFrame(columns=["candle_time", "close"]), start, end)
    assert gaps == [(start, end)]


def test_get_binance_close_raises_when_symbol_not_found_without_caching(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "CACHE_DIR", tmp_path)

    def _raise_not_found(*args, **kwargs):
        raise BinanceSymbolNotFoundError("NOTAREALCOINUSDT")

    monkeypatch.setattr(bds, "_fetch_range", _raise_not_found)

    with pytest.raises(BinanceSymbolNotFoundError):
        get_binance_close(
            "NOTAREALCOINUSDT", "days",
            datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
        )

    assert not (tmp_path / "NOTAREALCOINUSDT_days.parquet").exists()


def test_get_binance_close_skips_fetch_when_fully_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        bds, "datetime",
        type("_FixedDatetime", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 1, 20, tzinfo=timezone.utc))
        }),
    )

    idx = pd.date_range("2026-01-01", "2026-01-10", freq="D", tz="UTC")
    existing = pd.DataFrame({"candle_time": idx, "close": 1000.0})
    tmp_path.mkdir(parents=True, exist_ok=True)
    existing.to_parquet(tmp_path / "ETHUSDT_days.parquet", index=False)

    def _fail_fetch_range(*args, **kwargs):
        raise AssertionError("캐시가 이미 구간을 커버하므로 호출되면 안 됨")

    monkeypatch.setattr(bds, "_fetch_range", _fail_fetch_range)

    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    end = datetime(2026, 1, 9, tzinfo=timezone.utc)
    df = get_binance_close("ETHUSDT", "days", start, end)

    assert len(df) == 8


def test_get_binance_close_excludes_unclosed_candle(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "CACHE_DIR", tmp_path)
    fixed_now = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        bds, "datetime",
        type("_FixedDatetime", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
    )

    def fake_fetch_range(symbol, timeframe, start, end, client=None):
        idx = pd.date_range(start, end, freq="D", tz="UTC")
        return pd.DataFrame({"candle_time": idx, "close": 1000.0})

    monkeypatch.setattr(bds, "_fetch_range", fake_fetch_range)

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, tzinfo=timezone.utc)
    df = get_binance_close("ETHUSDT", "days", start, end)

    # 1/5 일봉은 00:00에 열려 1/6 00:00에 마감되는데, now=1/5 12:00이므로 아직 마감 전 → 제외돼야 함
    assert df["candle_time"].max() < datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_compute_gaps_uses_custom_time_column():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    end = datetime(2026, 1, 15, tzinfo=timezone.utc)
    cached = pd.DataFrame({
        "funding_time": pd.date_range("2026-01-01", "2026-01-10", freq="D", tz="UTC"),
        "funding_rate": 0.01,
    })
    gaps = _compute_gaps(cached, start, end, time_col="funding_time")
    # cache covers 1/1~1/10, requested 1/5~1/15 -> gap after cache end only
    assert gaps == [(datetime(2026, 1, 10, 0, 0, 1, tzinfo=timezone.utc), end)]


def test_compute_gaps_default_time_col_still_candle_time():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    cached = pd.DataFrame({"candle_time": pd.date_range("2026-01-01", "2026-01-10", freq="D", tz="UTC"), "close": 1.0})
    gaps = _compute_gaps(cached, start, end)
    assert gaps == []


def _funding_event(funding_time_ms: int, rate: float) -> dict:
    return {
        "symbol": "ETHUSDT", "fundingTime": funding_time_ms, "fundingRate": str(rate),
        "markPrice": "0", "rateType": "Regular",
    }


def test_parse_funding_converts_to_percentage():
    df = bds._parse_funding([_funding_event(1784073600000, 0.0005)])
    assert df.iloc[0]["funding_rate"] == pytest.approx(0.05)


def test_parse_funding_empty_input():
    df = bds._parse_funding([])
    assert list(df.columns) == ["funding_time", "funding_rate"]
    assert df.empty


def test_fetch_funding_page_returns_raw_events():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "ETHUSDT"
        return httpx.Response(200, json=[_funding_event(1784073600000, 0.0001)])

    with _mock_client(handler) as client:
        raw = bds._fetch_funding_page(
            client, "ETHUSDT",
            datetime(2026, 7, 15, tzinfo=timezone.utc), datetime(2026, 7, 16, tzinfo=timezone.utc),
        )

    assert raw == [_funding_event(1784073600000, 0.0001)]


def test_fetch_funding_range_paginates_when_first_page_is_full(monkeypatch):
    monkeypatch.setattr(bds, "REQUEST_DELAY_SECONDS", 0.0)
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        start_time_ms = int(request.url.params["startTime"])
        if call_count["n"] == 1:
            events = [_funding_event(start_time_ms + i * 28_800_000, 0.0001) for i in range(1000)]
            return httpx.Response(200, json=events)
        events = [_funding_event(start_time_ms, 0.0002)]
        return httpx.Response(200, json=events)

    with _mock_client(handler) as client:
        df = bds._fetch_funding_range(
            "ETHUSDT",
            datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc),
            client=client,
        )

    assert call_count["n"] == 2
    assert len(df) == 1001
    assert df["funding_time"].is_monotonic_increasing


def test_get_binance_funding_rate_returns_empty_for_unlisted_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "FUNDING_CACHE_DIR", tmp_path)

    def fake_fetch_funding_range(symbol, start, end, client=None):
        return pd.DataFrame(columns=bds._FUNDING_COLUMNS)

    monkeypatch.setattr(bds, "_fetch_funding_range", fake_fetch_funding_range)

    df = bds.get_binance_funding_rate(
        "NOTREALUSDT",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    assert df.empty


def test_get_binance_funding_rate_skips_fetch_when_fully_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "FUNDING_CACHE_DIR", tmp_path)

    idx = pd.date_range("2026-01-01", "2026-01-10", freq="8h", tz="UTC")
    existing = pd.DataFrame({"funding_time": idx, "funding_rate": 0.01})
    tmp_path.mkdir(parents=True, exist_ok=True)
    existing.to_parquet(tmp_path / "ETHUSDT.parquet", index=False)

    def _fail_fetch(*args, **kwargs):
        raise AssertionError("캐시가 이미 구간을 커버하므로 호출되면 안 됨")

    monkeypatch.setattr(bds, "_fetch_funding_range", _fail_fetch)

    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    end = datetime(2026, 1, 9, tzinfo=timezone.utc)
    df = bds.get_binance_funding_rate("ETHUSDT", start, end)

    assert len(df) > 0
    assert df["funding_time"].min() >= start
    assert df["funding_time"].max() <= end


def test_merge_funding_rate_backward_fills_from_most_recent_event():
    df = pd.DataFrame({"candle_time": pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")})
    funding_df = pd.DataFrame({
        "funding_time": [pd.Timestamp("2026-01-01 00:30", tz="UTC"), pd.Timestamp("2026-01-01 02:30", tz="UTC")],
        "funding_rate": [0.01, 0.02],
    })

    merged = bds.merge_funding_rate(df, funding_df)

    assert pd.isna(merged.iloc[0]["funding_rate_value"])  # 00:00, 첫 이벤트(00:30) 이전
    assert merged.iloc[1]["funding_rate_value"] == pytest.approx(0.01)  # 01:00
    assert merged.iloc[2]["funding_rate_value"] == pytest.approx(0.01)  # 02:00
    assert merged.iloc[3]["funding_rate_value"] == pytest.approx(0.02)  # 03:00
    assert merged.iloc[4]["funding_rate_value"] == pytest.approx(0.02)  # 04:00


def test_merge_funding_rate_treats_stale_event_beyond_tolerance_as_nan():
    df = pd.DataFrame({"candle_time": [pd.Timestamp("2026-01-05 00:00", tz="UTC")]})
    funding_df = pd.DataFrame({
        "funding_time": [pd.Timestamp("2026-01-01 00:00", tz="UTC")],  # 4일 전 — tolerance(16시간) 초과
        "funding_rate": [0.01],
    })

    merged = bds.merge_funding_rate(df, funding_df)

    assert pd.isna(merged.iloc[0]["funding_rate_value"])


def test_merge_funding_rate_all_nan_when_funding_df_empty():
    df = pd.DataFrame({"candle_time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")})
    merged = bds.merge_funding_rate(df, pd.DataFrame(columns=bds._FUNDING_COLUMNS))
    assert merged["funding_rate_value"].isna().all()


def test_timeframe_duration_is_public():
    from binance_data_service import timeframe_duration
    assert timeframe_duration("minutes60") == timedelta(minutes=60)
    assert timeframe_duration("days") == timedelta(days=1)
