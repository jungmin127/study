from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import pandas as pd

UPBIT_BASE_URL = "https://api.upbit.com/v1"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0
REQUEST_DELAY_SECONDS = 0.15

_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume", "trade_value"]


class _SyncTokenBucket:
    """trading.upbit_client.TokenBucket과 동일한 토큰버킷 알고리즘의 동기 버전(설계
    스펙 결정6) — get_candles() 호출 체인이 전부 동기 함수라 asyncio 기반
    TokenBucket을 쓸 수 없다. clock/sleep을 주입할 수 있어 테스트에서 실제 대기 없이
    결정론적으로 검증 가능하다."""

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self._rate = rate_per_sec
        self._capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._clock()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_seconds = (1 - self._tokens) / self._rate
                self._sleep(wait_seconds)


_CANDLE_BUCKET = _SyncTokenBucket(rate_per_sec=10)  # 업비트 candle 그룹 실제 한도(IP 단위)


def _endpoint_for_timeframe(timeframe: str) -> str:
    if timeframe == "days":
        return f"{UPBIT_BASE_URL}/candles/days"
    if timeframe.startswith("minutes"):
        unit = timeframe[len("minutes"):]
        if not unit.isdigit():
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        return f"{UPBIT_BASE_URL}/candles/minutes/{unit}"
    raise ValueError(f"지원하지 않는 timeframe: {timeframe}")


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
        _CANDLE_BUCKET.acquire()
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
            "candle_acc_trade_price": "trade_value",
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
    cached = pd.read_parquet(path)
    if not set(_CANDLE_COLUMNS).issubset(cached.columns):
        # trade_value 컬럼 추가 이전에 저장된 캐시 파일 — 스키마가 안 맞으므로
        # 캐시가 없는 것처럼 취급해 해당 구간을 다시 받아온다(자연스러운 스키마 마이그레이션).
        return pd.DataFrame(columns=_CANDLE_COLUMNS)
    return cached


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

    duration = timeframe_duration(timeframe)
    now = datetime.now(timezone.utc)
    closed = cached[cached["candle_time"] + duration <= now].reset_index(drop=True)

    if gaps:
        _save_cache(market, timeframe, closed)

    result = closed[(closed["candle_time"] >= start) & (closed["candle_time"] <= end)]
    return result.reset_index(drop=True)


def get_server_time_offset_sec() -> float:
    """업비트 서버 응답의 Date 헤더와 로컬 UTC 시각의 차이(초)를 반환한다. 양수면 로컬
    시각이 서버보다 느리다는 뜻. 인증이 필요 없는 공개 엔드포인트(마켓 목록)를 재사용해
    가볍게 확인한다(설계 스펙 결정10)."""
    resp = httpx.get(f"{UPBIT_BASE_URL}/market/all", params={"isDetails": "false"}, timeout=10)
    resp.raise_for_status()
    server_time = parsedate_to_datetime(resp.headers["Date"])
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - server_time).total_seconds()


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


def get_market_cautions() -> dict[str, bool]:
    """마켓별 업비트 공식 유의종목 지정 여부(warning 또는 caution 플래그 중 하나라도 True)를 반환한다.

    "세력의 가격 조종 가능성" 같은 잡주 특성과 가장 가깝게 대응되는 공식 신호라, 세그먼트
    점수 계산에는 넣지 않고(스냅샷 시점에 활성 플래그가 붙는 코인이 적어 전체 분류축으로 쓰기엔
    약함) 화면에 별도 배지로만 보여주는 용도로 쓴다."""
    resp = httpx.get(f"{UPBIT_BASE_URL}/market/all", params={"isDetails": "true"}, timeout=10)
    resp.raise_for_status()
    all_markets = resp.json()

    result: dict[str, bool] = {}
    for m in all_markets:
        if not m["market"].startswith("KRW-"):
            continue
        event = m.get("market_event") or {}
        caution = event.get("caution") or {}
        result[m["market"]] = bool(event.get("warning")) or any(caution.values())
    return result
