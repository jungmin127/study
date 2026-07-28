from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

import external_data_service as eds
from external_data_service import _fetch_fear_greed_all, _parse_fear_greed, get_fear_greed_cmc, merge_fear_greed


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fail_fetch(client):
    raise AssertionError("캐시가 fresh하면 API를 호출하면 안 됨")


def test_fetch_fear_greed_all_calls_limit_zero_and_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "0"
        return httpx.Response(
            200,
            json={"data": [{"value": "42", "value_classification": "Fear", "timestamp": "1517443200"}]},
        )

    with _mock_client(handler) as client:
        raw = _fetch_fear_greed_all(client)

    assert raw == [{"value": "42", "value_classification": "Fear", "timestamp": "1517443200"}]


def test_fetch_fear_greed_all_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(eds, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={"data": [{"value": "1", "value_classification": "Extreme Fear", "timestamp": "1517443200"}]},
        )

    with _mock_client(handler) as client:
        raw = _fetch_fear_greed_all(client)

    assert calls["count"] == 2
    assert len(raw) == 1


def test_fetch_fear_greed_all_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(eds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_fear_greed_all(client)


def test_parse_fear_greed_normalizes_timestamp_and_value():
    raw = [{"value": "42", "value_classification": "Fear", "timestamp": "1517443200"}]

    df = _parse_fear_greed(raw)

    assert list(df.columns) == ["date", "fear_greed_value"]
    assert df.iloc[0]["fear_greed_value"] == 42.0
    assert df.iloc[0]["date"] == pd.Timestamp("2018-02-01", tz="UTC")


def test_parse_fear_greed_empty_input():
    df = _parse_fear_greed([])
    assert list(df.columns) == ["date", "fear_greed_value"]
    assert df.empty


def test_get_fear_greed_cmc_skips_fetch_when_cache_is_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(eds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({"date": pd.to_datetime([today.date()], utc=True), "fear_greed_value": [55.0]})
    cached.to_parquet(tmp_path / "fear_greed_cmc.parquet", index=False)
    monkeypatch.setattr(eds, "_fetch_fear_greed_all", _fail_fetch)

    result = get_fear_greed_cmc(today - timedelta(days=1), today)

    assert result.iloc[-1]["fear_greed_value"] == 55.0


def test_get_fear_greed_cmc_refetches_when_cache_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(eds, "CACHE_DIR", tmp_path)
    stale_date = datetime.now(timezone.utc) - timedelta(days=5)
    cached = pd.DataFrame({"date": pd.to_datetime([stale_date.date()], utc=True), "fear_greed_value": [10.0]})
    cached.to_parquet(tmp_path / "fear_greed_cmc.parquet", index=False)

    def fake_fetch(client):
        now_ts = str(int(datetime.now(timezone.utc).timestamp()))
        return [{"value": "99", "value_classification": "Extreme Greed", "timestamp": now_ts}]

    monkeypatch.setattr(eds, "_fetch_fear_greed_all", fake_fetch)

    result = get_fear_greed_cmc(
        datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc)
    )

    assert result.iloc[-1]["fear_greed_value"] == 99.0


def test_get_fear_greed_cmc_filters_to_requested_date_range(monkeypatch, tmp_path):
    monkeypatch.setattr(eds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [(today - timedelta(days=3)).date(), (today - timedelta(days=2)).date(), today.date()], utc=True
            ),
            "fear_greed_value": [10.0, 20.0, 30.0],
        }
    )
    cached.to_parquet(tmp_path / "fear_greed_cmc.parquet", index=False)
    monkeypatch.setattr(eds, "_fetch_fear_greed_all", _fail_fetch)

    result = get_fear_greed_cmc(today - timedelta(days=2), today - timedelta(days=2))

    # 요청 종료일(today-2) 이후 값(today, 30.0)은 제외되지만, 시작일 이전 7일 lookback 마진
    # 덕분에 today-3(10.0)은 포함된다 — merge_fear_greed의 merge_asof(direction="backward")가
    # 요청 시작일 자체가 결측일이어도 fallback 값을 찾을 수 있어야 하기 때문.
    assert result["fear_greed_value"].tolist() == [10.0, 20.0]
    assert result["date"].max().date() == (today - timedelta(days=2)).date()


def test_get_fear_greed_cmc_includes_lookback_margin_so_gap_day_requests_still_get_a_fallback_value(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(eds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame(
        {
            # alternative.me 실제 히스토리에도 2024-10-26처럼 결측일이 존재한다 — 10-26 자체엔
            # 값이 없다. 캐시가 stale하지 않도록(오늘 날짜 포함) 세 번째 행을 추가한다.
            "date": pd.to_datetime(["2024-10-25", "2024-10-27", today.date()], utc=True),
            "fear_greed_value": [40.0, 60.0, 50.0],
        }
    )
    cached.to_parquet(tmp_path / "fear_greed_cmc.parquet", index=False)
    monkeypatch.setattr(eds, "_fetch_fear_greed_all", _fail_fetch)

    result = get_fear_greed_cmc(
        datetime(2024, 10, 26, tzinfo=timezone.utc), datetime(2024, 10, 27, tzinfo=timezone.utc)
    )

    assert result.iloc[0]["fear_greed_value"] == 40.0
    assert result.iloc[-1]["fear_greed_value"] == 60.0


def test_fetch_fear_greed_all_raises_runtime_error_on_malformed_200_response(monkeypatch):
    monkeypatch.setattr(eds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_fear_greed_all(client)


def test_merge_fear_greed_forward_fills_within_day():
    df = pd.DataFrame(
        {
            "candle_time": pd.to_datetime(
                ["2026-01-01 00:00", "2026-01-01 12:00", "2026-01-02 00:00"], utc=True
            ),
            "close": [100.0, 101.0, 102.0],
        }
    )
    fng_df = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True), "fear_greed_value": [30.0, 70.0]}
    )

    merged = merge_fear_greed(df, fng_df)

    assert merged["fear_greed_value"].tolist() == [30.0, 30.0, 70.0]


def test_merge_fear_greed_returns_nan_when_fng_df_is_empty():
    df = pd.DataFrame({"candle_time": pd.to_datetime(["2017-01-01 00:00"], utc=True), "close": [100.0]})
    fng_df = pd.DataFrame(columns=["date", "fear_greed_value"])

    merged = merge_fear_greed(df, fng_df)

    assert merged["fear_greed_value"].isna().all()


def test_merge_fear_greed_leaves_nan_before_earliest_available_date():
    df = pd.DataFrame(
        {"candle_time": pd.to_datetime(["2018-01-30 00:00", "2018-02-01 00:00"], utc=True), "close": [100.0, 101.0]}
    )
    fng_df = pd.DataFrame({"date": pd.to_datetime(["2018-02-01"], utc=True), "fear_greed_value": [50.0]})

    merged = merge_fear_greed(df, fng_df)

    assert pd.isna(merged["fear_greed_value"].iloc[0])
    assert merged["fear_greed_value"].iloc[1] == 50.0
