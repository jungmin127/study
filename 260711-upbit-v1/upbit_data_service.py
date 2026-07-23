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

    duration = _timeframe_duration(timeframe)
    now = datetime.now(timezone.utc)
    closed = cached[cached["candle_time"] + duration <= now].reset_index(drop=True)

    if gaps:
        _save_cache(market, timeframe, closed)

    result = closed[(closed["candle_time"] >= start) & (closed["candle_time"] <= end)]
    return result.reset_index(drop=True)


def get_krw_markets() -> list[dict]:
    """업비트 KRW 마켓 전체 목록을 조회한다. 캐싱하지 않는다 — 가볍고 자주 바뀌지
    않는 호출이라, 매 조회마다 최신 상장 코인을 그대로 반영하는 편이 낫다."""
    resp = httpx.get(f"{UPBIT_BASE_URL}/market/all", params={"isDetails": "false"}, timeout=10)
    resp.raise_for_status()
    all_markets = resp.json()
    return [
        {
            "market": m["market"],
            "korean_name": m["korean_name"],
            "english_name": m["english_name"],
        }
        for m in all_markets
        if m["market"].startswith("KRW-")
    ]


def get_krw_markets_with_ticker() -> list[dict]:
    """코인 선택 UI(현재가/전일대비 등락률·등락폭/거래대금)를 위해 시세 정보를 포함한 KRW 마켓 목록을 반환한다.

    signed_change_rate/signed_change_price는 업비트 기준 그대로 prev_closing_price(전일 종가) 대비 값이다."""
    markets = get_krw_markets()
    if not markets:
        return markets

    market_codes = ",".join(m["market"] for m in markets)
    resp = httpx.get(f"{UPBIT_BASE_URL}/ticker", params={"markets": market_codes}, timeout=10)
    resp.raise_for_status()
    tickers = {t["market"]: t for t in resp.json()}

    return [
        {
            **m,
            "price": tickers[m["market"]]["trade_price"] if m["market"] in tickers else None,
            "change_rate": tickers[m["market"]]["signed_change_rate"] if m["market"] in tickers else None,
            "change_price": tickers[m["market"]]["signed_change_price"] if m["market"] in tickers else None,
            "trade_price_24h": tickers[m["market"]]["acc_trade_price_24h"] if m["market"] in tickers else None,
        }
        for m in markets
    ]


def get_current_prices(markets: list[str]) -> dict[str, float]:
    """주어진 마켓들의 현재가(ticker trade_price)를 한 번에 조회한다.

    미청산 포지션이 있는 백테스트 목록의 수익률을 실시간에 준하게 재계산할 때,
    관련된 마켓들을 한 번에 배치 조회하기 위해 쓴다."""
    if not markets:
        return {}
    market_codes = ",".join(markets)
    resp = httpx.get(f"{UPBIT_BASE_URL}/ticker", params={"markets": market_codes}, timeout=10)
    resp.raise_for_status()
    return {t["market"]: t["trade_price"] for t in resp.json()}
