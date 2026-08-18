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
    "minutes5": "5m",
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


def timeframe_duration(timeframe: str) -> timedelta:
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
    cached: pd.DataFrame, start: datetime, end: datetime, time_col: str = "candle_time"
) -> list[tuple[datetime, datetime]]:
    if cached.empty:
        return [(start, end)]

    cache_start = cached[time_col].min()
    cache_end = cached[time_col].max()

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

    duration = timeframe_duration(timeframe)
    now = datetime.now(timezone.utc)
    closed = cached[cached["candle_time"] + duration <= now].reset_index(drop=True)

    if gaps:
        _save_cache(symbol, timeframe, closed)

    result = closed[(closed["candle_time"] >= start) & (closed["candle_time"] <= end)]
    return result.reset_index(drop=True)


FUNDING_BASE_URL = "https://fapi.binance.com/fapi/v1"

_FUNDING_COLUMNS = ["funding_time", "funding_rate"]

FUNDING_CACHE_DIR = Path(__file__).parent / "data" / "cache" / "binance_funding"


def _fetch_funding_page(
    client: httpx.Client,
    symbol: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
) -> list[dict]:
    params = {
        "symbol": symbol,
        "startTime": int(start_time.timestamp() * 1000),
        "endTime": int(end_time.timestamp() * 1000),
        "limit": limit,
    }
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(f"{FUNDING_BASE_URL}/fundingRate", params=params)
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"바이낸스 펀딩비 API 호출 실패 (symbol={symbol}): {last_exc}")


def _parse_funding(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_FUNDING_COLUMNS)
    df = pd.DataFrame(raw)
    df["funding_time"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df["funding_rate"] = df["fundingRate"].astype(float) * 100
    return (
        df[_FUNDING_COLUMNS]
        .drop_duplicates(subset="funding_time")
        .sort_values("funding_time")
        .reset_index(drop=True)
    )


def _fetch_funding_range(
    symbol: str, start: datetime, end: datetime, client: httpx.Client | None = None
) -> pd.DataFrame:
    close_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        frames: list[pd.DataFrame] = []
        cursor = start

        while cursor <= end:
            raw = _fetch_funding_page(client, symbol, cursor, end)
            if not raw:
                break
            page_df = _parse_funding(raw)
            frames.append(page_df)

            newest = page_df["funding_time"].max()
            if len(raw) < 1000 or newest >= end:
                break
            cursor = newest + timedelta(milliseconds=1)
            time.sleep(REQUEST_DELAY_SECONDS)

        if not frames:
            return pd.DataFrame(columns=_FUNDING_COLUMNS)

        merged = (
            pd.concat(frames)
            .drop_duplicates(subset="funding_time")
            .sort_values("funding_time")
            .reset_index(drop=True)
        )
        return merged[
            (merged["funding_time"] >= start) & (merged["funding_time"] <= end)
        ].reset_index(drop=True)
    finally:
        if close_client:
            client.close()


def _funding_cache_path(symbol: str) -> Path:
    return FUNDING_CACHE_DIR / f"{symbol}.parquet"


def _load_funding_cache(symbol: str) -> pd.DataFrame:
    path = _funding_cache_path(symbol)
    if not path.exists():
        return pd.DataFrame(columns=_FUNDING_COLUMNS)
    return pd.read_parquet(path)


def _save_funding_cache(symbol: str, df: pd.DataFrame) -> None:
    FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_funding_cache_path(symbol), index=False)


def get_binance_funding_rate(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """바이낸스 무기한 선물 펀딩비 히스토리를 조회한다(퍼센트 단위, 원시값×100). 심볼이
    선물에 없거나 이 구간에 데이터가 없으면 빈 DataFrame을 반환한다 — futures fundingRate
    엔드포인트는 spot klines와 달리 잘못된 심볼도 200+빈 배열을 반환하므로(실측 확인),
    "심볼 없음"과 "데이터 없음"을 구분하지 않는다."""
    cached = _load_funding_cache(symbol)
    gaps = _compute_gaps(cached, start, end, time_col="funding_time")

    if gaps:
        fetched = [_fetch_funding_range(symbol, g_start, g_end) for g_start, g_end in gaps]
        to_concat = [df for df in [cached, *fetched] if not df.empty]
        cached = (
            pd.concat(to_concat)
            .drop_duplicates(subset="funding_time")
            .sort_values("funding_time")
            .reset_index(drop=True)
            if to_concat
            else pd.DataFrame(columns=_FUNDING_COLUMNS)
        )
        _save_funding_cache(symbol, cached)

    result = cached[(cached["funding_time"] >= start) & (cached["funding_time"] <= end)]
    return result.reset_index(drop=True)


def merge_funding_rate(df: pd.DataFrame, funding_df: pd.DataFrame) -> pd.DataFrame:
    """대상 코인 캔들(df, candle_time 컬럼)에 펀딩비(funding_df, funding_time 컬럼)를
    merge_asof(direction="backward")로 병합한다 — 각 캔들 시각 기준 그 시각 이전(또는 동시)
    가장 최근 펀딩비를 채운다(look-ahead bias 방지). funding_df가 비어있으면 전체 NaN —
    호출부(backend/main.py)가 이 NaN을 보고 400 에러를 낸다."""
    if funding_df.empty:
        return df.assign(funding_rate_value=float("nan"))

    merged = pd.merge_asof(
        df.sort_values("candle_time").reset_index(drop=True),
        funding_df.sort_values("funding_time").reset_index(drop=True).rename(
            columns={"funding_rate": "funding_rate_value"}
        ),
        left_on="candle_time",
        right_on="funding_time",
        direction="backward",
        tolerance=pd.Timedelta(hours=16),
    )
    return merged.drop(columns="funding_time")


__all__ = ["get_binance_close", "binance_symbol", "BinanceSymbolNotFoundError", "get_binance_funding_rate", "merge_funding_rate", "timeframe_duration"]
