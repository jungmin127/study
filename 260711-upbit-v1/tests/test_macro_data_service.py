"""
tests/test_macro_data_service.py

macro_data_service의 FRED(미국 기준금리/장단기금리차/한국 콜금리) fetch/parse/cache/merge를
검증한다. 캐싱/재시도 패턴은 tests/test_external_data_service.py를 그대로 따른다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

import macro_data_service as mds
from macro_data_service import (
    _fetch_fred_csv,
    _parse_fred_csv,
    get_fed_funds_rate,
    get_kr_call_rate,
    get_us_yield_curve_spread,
    merge_fred_series,
)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fail_fetch(*args, **kwargs):
    raise AssertionError("캐시가 fresh하면 API를 호출하면 안 됨")


def test_fetch_fred_csv_sends_correct_params_and_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "FEDFUNDS"
        assert request.url.params["cosd"] == "2024-01-01"
        assert request.url.params["coed"] == "2024-03-01"
        return httpx.Response(200, text="observation_date,FEDFUNDS\n2024-01-01,5.33\n2024-02-01,5.33\n")

    with _mock_client(handler) as client:
        text = _fetch_fred_csv(
            client, "FEDFUNDS",
            datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 1, tzinfo=timezone.utc),
        )

    assert "FEDFUNDS" in text


def test_fetch_fred_csv_retries_on_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(mds, "RETRY_BASE_DELAY_SECONDS", 0.0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, text="observation_date,FEDFUNDS\n2024-01-01,5.33\n")

    with _mock_client(handler) as client:
        text = _fetch_fred_csv(client, "FEDFUNDS", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))

    assert calls["count"] == 2
    assert "5.33" in text


def test_fetch_fred_csv_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(mds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_fred_csv(client, "FEDFUNDS", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))


def test_parse_fred_csv_renames_value_column_and_normalizes_date():
    text = "observation_date,FEDFUNDS\n2024-01-01,5.33\n2024-02-01,5.33\n"

    df = _parse_fred_csv(text, "fed_funds_rate_value")

    assert list(df.columns) == ["date", "fed_funds_rate_value"]
    assert df.iloc[0]["fed_funds_rate_value"] == 5.33
    assert df.iloc[0]["date"] == pd.Timestamp("2024-01-01", tz="UTC")
    assert df["date"].dt.unit == "us"


def test_parse_fred_csv_drops_non_numeric_rows():
    text = "observation_date,T10Y2Y\n2024-01-01,.\n2024-01-02,-0.38\n"

    df = _parse_fred_csv(text, "treasury_yield_spread_value")

    assert len(df) == 1
    assert df.iloc[0]["treasury_yield_spread_value"] == -0.38


def test_get_fed_funds_rate_skips_fetch_when_cache_is_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({"date": pd.to_datetime([today.date()], utc=True), "fed_funds_rate_value": [5.33]})
    cached.to_parquet(tmp_path / "fred_fedfunds.parquet", index=False)
    monkeypatch.setattr(mds, "_fetch_fred_csv", _fail_fetch)

    result = get_fed_funds_rate(today - timedelta(days=1), today)

    assert result.iloc[-1]["fed_funds_rate_value"] == 5.33


def test_get_fed_funds_rate_refetches_when_cache_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    stale_date = datetime.now(timezone.utc) - timedelta(days=40)
    cached = pd.DataFrame({"date": pd.to_datetime([stale_date.date()], utc=True), "fed_funds_rate_value": [1.0]})
    cached.to_parquet(tmp_path / "fred_fedfunds.parquet", index=False)

    def fake_fetch(client, series_id, start, end):
        return "observation_date,FEDFUNDS\n" + f"{datetime.now(timezone.utc).date().isoformat()},9.99\n"

    monkeypatch.setattr(mds, "_fetch_fred_csv", fake_fetch)

    result = get_fed_funds_rate(datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc))

    assert result.iloc[-1]["fed_funds_rate_value"] == 9.99


def test_get_fed_funds_rate_filters_to_requested_range(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-02-01", today.date()], utc=True),
        "fed_funds_rate_value": [5.33, 5.25, 4.50],
    })
    cached.to_parquet(tmp_path / "fred_fedfunds.parquet", index=False)
    monkeypatch.setattr(mds, "_fetch_fred_csv", _fail_fetch)

    result = get_fed_funds_rate(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 2, 1, tzinfo=timezone.utc))

    assert result["fed_funds_rate_value"].tolist() == [5.33, 5.25]


def test_get_us_yield_curve_spread_and_kr_call_rate_use_distinct_cache_files(monkeypatch, tmp_path):
    """세 시리즈가 같은 헬퍼(_get_fred_series)를 공유하므로, 캐시 파일이 서로 다른 이름을
    써서 충돌하지 않는지 확인한다."""
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    captured_ids = []

    def fake_fetch(client, series_id, start, end):
        captured_ids.append(series_id)
        col = "TEMP"
        return f"observation_date,{col}\n2024-01-01,1.0\n"

    monkeypatch.setattr(mds, "_fetch_fred_csv", fake_fetch)

    get_us_yield_curve_spread(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
    get_kr_call_rate(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))

    assert captured_ids == ["T10Y2Y", "IRSTCI01KRM156N"]
    assert (tmp_path / "fred_t10y2y.parquet").exists()
    assert (tmp_path / "fred_kr_call_rate.parquet").exists()


def test_merge_fred_series_backward_fills():
    df = pd.DataFrame({
        "candle_time": pd.to_datetime(["2024-01-01 00:00", "2024-01-15 00:00", "2024-02-05 00:00"], utc=True),
        "close": [100.0, 101.0, 102.0],
    })
    series_df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-02-01"], utc=True),
        "fed_funds_rate_value": [5.33, 5.25],
    })

    merged = merge_fred_series(df, series_df, "fed_funds_rate_value")

    assert merged["fed_funds_rate_value"].tolist() == [5.33, 5.33, 5.25]


def test_merge_fred_series_returns_nan_when_series_df_is_empty():
    df = pd.DataFrame({"candle_time": pd.to_datetime(["2024-01-01 00:00"], utc=True), "close": [100.0]})
    series_df = pd.DataFrame(columns=["date", "fed_funds_rate_value"])

    merged = merge_fred_series(df, series_df, "fed_funds_rate_value")

    assert merged["fed_funds_rate_value"].isna().all()
