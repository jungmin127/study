from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

UPBIT_BASE_URL = "https://api.upbit.com/v1"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0
REQUEST_DELAY_SECONDS = 0.15

_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume"]


def _endpoint_for_timeframe(timeframe: str) -> str:
    if timeframe == "days":
        return f"{UPBIT_BASE_URL}/candles/days"
    if timeframe.startswith("minutes"):
        unit = timeframe[len("minutes"):]
        if not unit.isdigit():
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        return f"{UPBIT_BASE_URL}/candles/minutes/{unit}"
    raise ValueError(f"지원하지 않는 timeframe: {timeframe}")


def _fetch_page(
    client: httpx.Client,
    url: str,
    market: str,
    to: datetime | None,
    count: int = 200,
) -> list[dict]:
    params: dict = {"market": market, "count": count}
    if to is not None:
        params["to"] = to.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(url, params=params)
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"Upbit API 호출 실패 (market={market}, url={url}): {last_exc}")


def _parse_candles(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_CANDLE_COLUMNS)

    df = pd.DataFrame(raw)
    df["candle_time"] = pd.to_datetime(df["candle_date_time_utc"], utc=True)
    df = df.rename(
        columns={
            "opening_price": "open",
            "high_price": "high",
            "low_price": "low",
            "trade_price": "close",
            "candle_acc_trade_volume": "volume",
        }
    )
    df = df[_CANDLE_COLUMNS]
    return df.sort_values("candle_time").reset_index(drop=True)


def _fetch_range(
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    client: httpx.Client | None = None,
) -> pd.DataFrame:
    url = _endpoint_for_timeframe(timeframe)
    close_client = client is None
    client = client or httpx.Client(timeout=10)

    try:
        frames: list[pd.DataFrame] = []
        to_cursor: datetime | None = end

        while True:
            raw = _fetch_page(client, url, market, to_cursor)
            if not raw:
                break

            page_df = _parse_candles(raw)
            frames.append(page_df)

            oldest = page_df["candle_time"].min()
            if oldest <= start or len(raw) < 200:
                break

            to_cursor = oldest - timedelta(seconds=1)
            time.sleep(REQUEST_DELAY_SECONDS)

        if not frames:
            return pd.DataFrame(columns=_CANDLE_COLUMNS)

        merged = (
            pd.concat(frames)
            .drop_duplicates(subset="candle_time")
            .sort_values("candle_time")
            .reset_index(drop=True)
        )
        return merged[
            (merged["candle_time"] >= start) & (merged["candle_time"] <= end)
        ].reset_index(drop=True)
    finally:
        if close_client:
            client.close()


def _compute_gaps(
    cached: pd.DataFrame, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    if cached.empty:
        return [(start, end)]

    cache_start = cached["candle_time"].min()
    cache_end = cached["candle_time"].max()

    gaps: list[tuple[datetime, datetime]] = []
    if start < cache_start:
        gaps.append((start, cache_start - timedelta(seconds=1)))
    if end > cache_end:
        gaps.append((max(start, cache_end + timedelta(seconds=1)), end))
    return gaps


CACHE_DIR = Path(__file__).parent / "data" / "cache" / "ohlcv"


def _cache_path(market: str, timeframe: str) -> Path:
    return CACHE_DIR / f"{market}_{timeframe}.parquet"


def _load_cache(market: str, timeframe: str) -> pd.DataFrame:
    path = _cache_path(market, timeframe)
    if not path.exists():
        return pd.DataFrame(columns=_CANDLE_COLUMNS)
    return pd.read_parquet(path)


def _save_cache(market: str, timeframe: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(market, timeframe), index=False)


def get_candles(market: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    cached = _load_cache(market, timeframe)
    gaps = _compute_gaps(cached, start, end)

    if gaps:
        fetched = [_fetch_range(market, timeframe, g_start, g_end) for g_start, g_end in gaps]
        cached = (
            pd.concat([cached, *fetched])
            .drop_duplicates(subset="candle_time")
            .sort_values("candle_time")
            .reset_index(drop=True)
        )
        _save_cache(market, timeframe, cached)

    now = datetime.now(timezone.utc)
    closed = cached[cached["candle_time"] <= now]
    result = closed[(closed["candle_time"] >= start) & (closed["candle_time"] <= end)]
    return result.reset_index(drop=True)
