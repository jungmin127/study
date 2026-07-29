"""
binance_data_service.py

바이낸스(Binance) 공개 API(klines)에서 종가만 조회하고 parquet으로 캐싱한다. 한국프리미엄
(KOREA_PREMIUM) 지표 계산에 필요한, 대상 코인의 바이낸스 USDT 페어 종가를 제공하는 용도.
캐싱/gap-fill/재시도 패턴은 upbit_data_service.py를 그대로 복제하되, 바이낸스 특유의
"존재하지 않는 심볼"(HTTP 400, code -1121) 케이스만 추가로 처리한다 — 이건 재시도해도
결과가 달라지지 않는 확정적 에러라, 재시도 없이 즉시 "이 코인은 계산 불가"로 취급한다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

BINANCE_BASE_URL = "https://api.binance.com/api/v3"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0
REQUEST_DELAY_SECONDS = 0.15

_CLOSE_COLUMNS = ["candle_time", "close"]

_TIMEFRAME_TO_INTERVAL = {
    "minutes15": "15m",
    "minutes30": "30m",
    "minutes60": "1h",
    "days": "1d",
}

_INVALID_SYMBOL_CODE = -1121


class BinanceSymbolNotFoundError(Exception):
    """바이낸스에 존재하지 않는 심볼(HTTP 400, code -1121)을 나타낸다."""


def binance_symbol(market: str) -> str:
    """업비트 마켓 코드를 바이낸스 USDT 페어 심볼로 변환한다. 예: KRW-ETH -> ETHUSDT."""
    return market.removeprefix("KRW-") + "USDT"


def _interval_for_timeframe(timeframe: str) -> str:
    if timeframe not in _TIMEFRAME_TO_INTERVAL:
        raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
    return _TIMEFRAME_TO_INTERVAL[timeframe]


def _timeframe_duration(timeframe: str) -> timedelta:
    if timeframe == "days":
        return timedelta(days=1)
    if timeframe.startswith("minutes"):
        unit = timeframe[len("minutes"):]
        if not unit.isdigit():
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        return timedelta(minutes=int(unit))
    raise ValueError(f"지원하지 않는 timeframe: {timeframe}")


def _fetch_page(
    client: httpx.Client,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
) -> list[list]:
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_time.timestamp() * 1000),
        "endTime": int(end_time.timestamp() * 1000),
        "limit": limit,
    }

    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(f"{BINANCE_BASE_URL}/klines", params=params)
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            if resp.status_code == 400 and resp.json().get("code") == _INVALID_SYMBOL_CODE:
                raise BinanceSymbolNotFoundError(symbol)
            resp.raise_for_status()
            return resp.json()
        except BinanceSymbolNotFoundError:
            raise
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"바이낸스 API 호출 실패 (symbol={symbol}): {last_exc}")


def _parse_klines(raw: list[list]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_CLOSE_COLUMNS)
    df = pd.DataFrame(
        raw,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "num_trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )
    df["candle_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    return df[_CLOSE_COLUMNS].sort_values("candle_time").reset_index(drop=True)


def _fetch_range(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    client: httpx.Client | None = None,
) -> pd.DataFrame:
    interval = _interval_for_timeframe(timeframe)
    close_client = client is None
    client = client or httpx.Client(timeout=10)

    try:
        frames: list[pd.DataFrame] = []
        cursor = start

        while cursor <= end:
            raw = _fetch_page(client, symbol, interval, cursor, end)
            if not raw:
                break
            page_df = _parse_klines(raw)
            frames.append(page_df)

            newest = page_df["candle_time"].max()
            if len(raw) < 1000 or newest >= end:
                break
            cursor = newest + timedelta(milliseconds=1)
            time.sleep(REQUEST_DELAY_SECONDS)

        if not frames:
            return pd.DataFrame(columns=_CLOSE_COLUMNS)

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


CACHE_DIR = Path(__file__).parent / "data" / "cache" / "binance_ohlcv"


def _cache_path(symbol: str, timeframe: str) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}.parquet"


def _load_cache(symbol: str, timeframe: str) -> pd.DataFrame:
    path = _cache_path(symbol, timeframe)
    if not path.exists():
        return pd.DataFrame(columns=_CLOSE_COLUMNS)
    return pd.read_parquet(path)


def _save_cache(symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(symbol, timeframe), index=False)


def get_binance_close(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    """바이낸스 klines에서 종가만 조회한다. 컬럼: [candle_time, close]. 심볼이 존재하지
    않으면 BinanceSymbolNotFoundError를 재시도 없이 그대로 전파한다(호출부가 "미상장"과
    "심볼은 있지만 이 구간에 데이터 없음"을 구분할 수 있도록) — 이 경우 캐시에 아무것도
    저장하지 않는다."""
    cached = _load_cache(symbol, timeframe)
    gaps = _compute_gaps(cached, start, end)

    if gaps:
        fetched = [_fetch_range(symbol, timeframe, g_start, g_end) for g_start, g_end in gaps]
        to_concat = [df for df in [cached, *fetched] if not df.empty]
        if to_concat:
            cached = (
                pd.concat(to_concat)
                .drop_duplicates(subset="candle_time")
                .sort_values("candle_time")
                .reset_index(drop=True)
            )
        else:
            cached = pd.DataFrame(columns=_CLOSE_COLUMNS)

    duration = _timeframe_duration(timeframe)
    now = datetime.now(timezone.utc)
    closed = cached[cached["candle_time"] + duration <= now].reset_index(drop=True)

    if gaps:
        _save_cache(symbol, timeframe, closed)

    result = closed[(closed["candle_time"] >= start) & (closed["candle_time"] <= end)]
    return result.reset_index(drop=True)


__all__ = ["get_binance_close", "binance_symbol", "BinanceSymbolNotFoundError"]
