# 한국프리미엄(KOREA_PREMIUM) 외부 데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-07-29-korea-premium-external-data-design.md`에서 설계한 대로,
바이낸스(Binance) USDT 페어 종가와 업비트 KRW-USDT 시세를 조합해 코인별 한국프리미엄을 계산하고,
조건 빌더에 `KOREA_PREMIUM` 지표로 등록한다.

**Architecture:** 새 파일 `binance_data_service.py`(바이낸스 klines fetch + parquet 캐시, `upbit_data_service.py`의
캐싱/gap-fill 패턴을 복제)를 만든다. 원/달러 환율(`usdt_close`)은 기존 `AUX_MARKET_INDICATORS`/aux-market
병합 루프(`KRW-USDT`, 이미 `USDT_CORRELATION`이 쓰고 있음)를 그대로 재사용한다. `backend/main.py`에
`FEAR_GREED_CMC`와 나란히 `KOREA_PREMIUM` 전용 병합 분기를 추가해, `korea_premium_value = (df.close /
(binance_close * usdt_close) - 1) * 100`을 pandas에서 계산해 단일 라인으로 병합하고, 지표 팩토리는
`FEAR_GREED_CMC`와 동일하게 pass-through로 끝낸다.

**Tech Stack:** Python 3.11, FastAPI, httpx, pandas, backtrader, pytest / Next.js 14, TypeScript.

## Global Constraints

- 기존 pytest 테스트는 계속 100% 통과해야 한다.
- `npx tsc --noEmit` (frontend)이 항상 깨끗해야 한다.
- 카탈로그(백엔드) ↔ 지표 가이드 탭(프론트) ↔ 조건 빌더 카테고리 상수는 항상 같이 갱신한다. 이번엔 카테고리
  자체가 기존 "시장 심리"를 재사용하므로 `frontend/lib/indicator-categories.ts` 수정은 필요 없다.
- 계산 대상은 **항상 BTC가 아니라 현재 백테스트 중인 코인 기준**이다(`BTC_CORRELATION`/`USDT_CORRELATION`과
  동일한 "대상 코인 기준" 패턴).
- 바이낸스 심볼은 `KRW-XXX → XXXUSDT` 문자열 변환으로 도출한다. 별도 매핑 테이블을 만들지 않는다 — 심볼이
  존재하지 않으면 바이낸스 API 자체가 400(`code -1121, "Invalid symbol."`)을 반환하며, 이를 그대로 "계산
  불가" 신호로 쓴다.
- 미상장 코인/데이터 없는 구간 모두 부분 데이터로 조용히 진행하지 않고 명확히 400 에러를 낸다(기존
  aux-market/fear-greed 패턴과 동일).
- 외부 HTTP 호출의 재시도/백오프 패턴은 `upbit_data_service.py`의 기존 패턴(429 시 지수 백오프, 실패 시
  `RuntimeError`로 통일된 메시지)을 따르되, "심볼 없음"(400 code -1121)만 재시도 없이 즉시 처리한다.
- 커밋은 Task 단위로 작게 나눠서 한다.

---

## Task 1: 바이낸스 종가 수집·캐싱 서비스

**Files:**
- Create: `binance_data_service.py`
- Test: `tests/test_binance_data_service.py` (신규)

**Interfaces:**
- Produces: `binance_symbol(market: str) -> str`. `get_binance_close(symbol: str, timeframe: str, start: datetime,
  end: datetime) -> pd.DataFrame`(컬럼 `[candle_time, close]`). `BinanceSymbolNotFoundError`(내부 재시도 판단용,
  `get_binance_close`는 이 예외를 잡아 빈 DataFrame으로 변환하므로 외부에는 노출되지 않는다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_binance_data_service.py` (신규 파일):
```python
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


def test_get_binance_close_returns_empty_df_when_symbol_not_found_without_caching(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "CACHE_DIR", tmp_path)

    def _raise_not_found(*args, **kwargs):
        raise BinanceSymbolNotFoundError("NOTAREALCOINUSDT")

    monkeypatch.setattr(bds, "_fetch_range", _raise_not_found)

    result = get_binance_close(
        "NOTAREALCOINUSDT", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
    )

    assert result.empty
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_binance_data_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'binance_data_service'`

- [ ] **Step 3: 최소 구현 작성**

`binance_data_service.py` (신규, 저장소 루트 — `upbit_data_service.py`/`external_data_service.py`와 같은 위치):
```python
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
    않으면(BinanceSymbolNotFoundError) 재시도 없이 빈 DataFrame을 즉시 반환하고, 이 경우
    캐시에 아무것도 저장하지 않는다 — "없는 심볼"이라는 사실 자체는 캐싱할 대상이 아니다."""
    cached = _load_cache(symbol, timeframe)
    gaps = _compute_gaps(cached, start, end)

    if gaps:
        try:
            fetched = [_fetch_range(symbol, timeframe, g_start, g_end) for g_start, g_end in gaps]
        except BinanceSymbolNotFoundError:
            return pd.DataFrame(columns=_CLOSE_COLUMNS)
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
        _save_cache(symbol, timeframe, closed)

    result = closed[(closed["candle_time"] >= start) & (closed["candle_time"] <= end)]
    return result.reset_index(drop=True)


__all__ = ["get_binance_close", "binance_symbol", "BinanceSymbolNotFoundError"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_binance_data_service.py -v`
Expected: PASS (16개 테스트)

- [ ] **Step 5: 커밋**

```bash
git add binance_data_service.py tests/test_binance_data_service.py
git commit -m "feat: add Binance close-price fetch/cache service"
```

---

## Task 2: 지표 등록 (`KOREA_PREMIUM`)

**Files:**
- Modify: `engine/condition_tree.py`
- Modify: `engine/indicators/sentiment.py`
- Modify: `engine/runner.py`
- Test: `tests/test_condition_tree.py` (append)
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Consumes: `data.korea_premium_value` 라인(Task 3에서 `build_data_feed_class`가 채움 — 이 Task에서는 테스트가
  직접 `build_data_feed_class(("korea_premium_value",))`로 채워서 검증한다).
- Produces: `create_korea_premium(data, **params) -> bt.LineBuffer`(pass-through, `FEAR_GREED_CMC`와 동일 패턴).
  `INDICATOR_FACTORY["KOREA_PREMIUM"]`. `AUX_MARKET_INDICATORS["KOREA_PREMIUM"] == "KRW-USDT"`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_tree.py` 끝에 추가:
```python
def test_required_aux_markets_returns_usdt_when_korea_premium_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}],
    }
    assert required_aux_markets(tree) == {"KRW-USDT"}
```

`tests/test_indicators.py` 끝에 추가:
```python
def test_korea_premium_matches_raw_korea_premium_value_column():
    df = make_oscillating_df()
    korea_premium = pd.Series([3.0 + (i % 5) * 0.1 for i in range(len(df))])
    values = _run_probe_with_aux("KOREA_PREMIUM", {}, "korea_premium_value", korea_premium)
    assert abs(values[-1] - korea_premium.iloc[-1]) < 1e-6
```

같은 파일의 `_NEEDS_EXTRA_LINE` 집합(현재 `{"MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION",
"FEAR_GREED_CMC"}`)에 `"KOREA_PREMIUM"`을 추가:
```python
_NEEDS_EXTRA_LINE = {"MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION", "FEAR_GREED_CMC", "KOREA_PREMIUM"}
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_condition_tree.py -k korea_premium -v`
Expected: FAIL — `required_aux_markets`가 빈 set을 반환(아직 `AUX_MARKET_INDICATORS`에 없음)

Run: `pytest tests/test_indicators.py -k korea_premium -v`
Expected: FAIL — `KeyError: 'KOREA_PREMIUM'`(아직 `INDICATOR_FACTORY`에 없음)

- [ ] **Step 3: 최소 구현 작성**

`engine/condition_tree.py`의 `AUX_MARKET_INDICATORS`를:
```python
AUX_MARKET_INDICATORS: dict[str, str] = {
    "MARKET_TREND": "KRW-BTC",
    "BTC_CORRELATION": "KRW-BTC",
    "USDT_CORRELATION": "KRW-USDT",
    "KOREA_PREMIUM": "KRW-USDT",
}
```

`engine/indicators/sentiment.py` 전체를:
```python
"""
engine/indicators/sentiment.py

시장 심리 계열 지표 — 코인 자체가 아니라 외부 데이터 소스(공포/탐욕 지수 등)에서 값을 가져오거나,
여러 마켓 데이터를 조합해 계산한 값을 다룬다. engine.runner의 build_data_feed_class가 채워주는
self.data.fear_greed_value / self.data.korea_premium_value 라인(백엔드가 각각
external_data_service.get_fear_greed_cmc, binance_data_service.get_binance_close로 조회·계산한
값을 병합한다)을 그대로 반환한다.
"""
from __future__ import annotations

import backtrader as bt


def create_fear_greed_cmc(data: bt.feeds.PandasData, **params) -> bt.LineBuffer:
    return data.fear_greed_value


def create_korea_premium(data: bt.feeds.PandasData, **params) -> bt.LineBuffer:
    return data.korea_premium_value
```

`engine/indicators/__init__.py` — import 줄을:
```python
from .sentiment import create_fear_greed_cmc, create_korea_premium
```
로 교체. `INDICATOR_FACTORY` dict에 추가(`"FEAR_GREED_CMC": create_fear_greed_cmc,` 다음 줄):
```python
    "KOREA_PREMIUM": create_korea_premium,
```

`engine/runner.py`의 `_OPTIONAL_LINE_CANDIDATES`(현재 `("trade_value", "fear_greed_value",
*AUX_MARKET_LINE_NAME.values())`)를:
```python
_OPTIONAL_LINE_CANDIDATES: tuple[str, ...] = (
    "trade_value", "fear_greed_value", "korea_premium_value", *AUX_MARKET_LINE_NAME.values()
)
```
로 교체 — 이래야 `run_backtest()`가 df에 `korea_premium_value` 컬럼이 있을 때 자동으로 피드에 라인을 붙인다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_condition_tree.py tests/test_indicators.py -v`
Expected: 기존 테스트 전부 PASS + 신규 2개 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/condition_tree.py engine/indicators/sentiment.py engine/indicators/__init__.py engine/runner.py tests/test_condition_tree.py tests/test_indicators.py
git commit -m "feat: register KOREA_PREMIUM indicator (pass-through of korea_premium_value line)"
```

---

## Task 3: 백엔드 병합 로직

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py` (append)

**Interfaces:**
- Consumes: Task 1의 `get_binance_close`/`binance_symbol`, Task 2의 `INDICATOR_FACTORY["KOREA_PREMIUM"]`,
  기존 aux-market 병합 루프가 채우는 `usdt_close` 컬럼.
- Produces: `run_backtest_endpoint()`가 조건 트리에 `KOREA_PREMIUM`이 있으면 자동으로 바이낸스 데이터를
  병합하고 `korea_premium_value` 컬럼을 계산.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가:
```python
def test_run_backtest_computes_korea_premium_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    target_df["close"] = 1_050_000.0
    usdt_df = target_df.copy()
    usdt_df["close"] = 1_000.0

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-USDT":
            return usdt_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    binance_df = pd.DataFrame({"candle_time": target_df["candle_time"], "close": 1_000.0})
    monkeypatch.setattr(
        backend_module, "get_binance_close", lambda symbol, timeframe, start, end: binance_df
    )

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200
    merged = captured["df"]
    # close=1,050,000 / (binance_close=1,000 * usdt_close=1,000) - 1) * 100 = 5.0(%)
    assert merged["korea_premium_value"].round(4).eq(5.0).all()


def test_run_backtest_rejects_korea_premium_when_binance_symbol_not_found(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    monkeypatch.setattr(
        backend_module, "get_binance_close",
        lambda symbol, timeframe, start, end: pd.DataFrame(columns=["candle_time", "close"]),
    )

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(buy_conditions=buy))

    assert resp.status_code == 400
    assert "바이낸스" in resp.json()["detail"]


def test_run_backtest_returns_400_when_binance_candles_have_no_overlapping_candle_time(monkeypatch, tmp_path):
    # 바이낸스 응답에 행은 있지만 target과 candle_time이 전혀 겹치지 않는 경우(merge 후 전부 NaN)
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    target_df = make_oscillating_df()
    disjoint_binance_df = pd.DataFrame(
        {"candle_time": target_df["candle_time"] + pd.Timedelta(days=10000), "close": 1000.0}
    )
    monkeypatch.setattr(
        backend_module, "get_binance_close",
        lambda symbol, timeframe, start, end: disjoint_binance_df,
    )

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "캔들 데이터가 없습니다" in resp.json()["detail"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_backend.py -k korea_premium -v`
Expected: FAIL — `find_unknown_indicators`가 `KOREA_PREMIUM`을 unknown으로 처리하지 않아 (Task 2에서
`INDICATOR_FACTORY`엔 이미 등록됨) 요청 자체는 통과하지만, `backend/main.py`에 병합 로직이 없어
`korea_premium_value` 컬럼 자체가 없거나(첫 테스트는 `KeyError`), 바이낸스 관련 에러가 전혀 나지 않음
(둘째/셋째 테스트는 200으로 잘못 응답).

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 import 줄들을:
```python
from external_data_service import get_fear_greed_cmc, merge_fear_greed
```
다음으로 교체(`binance_data_service` import 추가):
```python
from binance_data_service import binance_symbol, get_binance_close
from external_data_service import get_fear_greed_cmc, merge_fear_greed
```

`FEAR_GREED_CMC` 병합 분기(공포탐욕지수 400 에러를 내는 블록) 바로 다음, `risk_config = {...}` 줄 이전에
추가:
```python
    korea_premium_indicators = {
        b["indicator"] for b in collect_blocks(buy_dict) + collect_blocks(sell_dict)
    }
    if "KOREA_PREMIUM" in korea_premium_indicators:
        symbol = binance_symbol(req.market)
        try:
            binance_df = get_binance_close(symbol, req.timeframe, start_dt, end_dt)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if binance_df.empty:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{req.market}에 대응하는 바이낸스 심볼({symbol})이 없어 "
                    f"한국프리미엄을 계산할 수 없습니다"
                ),
            )
        df = df.merge(
            binance_df.rename(columns={"close": "binance_close"}), on="candle_time", how="left"
        )
        if df["binance_close"].isna().all():
            raise HTTPException(
                status_code=400, detail=f"해당 기간에 {symbol} 캔들 데이터가 없습니다"
            )
        df["binance_close"] = df["binance_close"].ffill().bfill()
        df["korea_premium_value"] = (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100
```

주의: `df["usdt_close"]`는 `KOREA_PREMIUM`이 `AUX_MARKET_INDICATORS`에 `"KRW-USDT"`로 등록돼 있어(Task 2),
이 블록보다 앞선 기존 `aux_markets` 루프가 이미 채워둔다 — 별도 조회 코드 불필요.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전부 PASS

Run: `pytest tests/ -v`
Expected: 전체 스위트 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: merge KOREA_PREMIUM data into backtest feed via Binance close"
```

---

## Task 4: 카탈로그 등록

**Files:**
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)

**Interfaces:**
- Consumes: Task 2의 `INDICATOR_FACTORY["KOREA_PREMIUM"]`.
- Produces: `GET /api/v1/indicators/catalog` 응답에 `KOREA_PREMIUM` 항목 추가(카테고리 `"시장 심리"` 재사용).

- [ ] **Step 1: 실패하는 테스트 확인**

기존 테스트 `test_get_indicator_catalog_covers_all_registered_indicators`(수정 없이 그대로 재사용)는
`catalog_values == set(INDICATOR_FACTORY.keys()) | POSITION_RELATIVE_INDICATORS`를 검증하므로, Task 2에서
`INDICATOR_FACTORY`엔 `KOREA_PREMIUM`이 생겼는데 `INDICATOR_CATALOG`엔 아직 없는 지금 시점에 이 테스트가
저절로 실패한다.

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `catalog_values`에 `KOREA_PREMIUM`이 빠져 있어 set 비교 실패

- [ ] **Step 2: (Step 1에서 이미 실패 확인함 — 별도 실행 불필요)**

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `"FEAR_GREED_CMC"` 항목 바로 뒤에 추가:
```python
    {
        "value": "KOREA_PREMIUM", "label": "한국프리미엄", "category": "시장 심리",
        "params": [],
        "description": "대상 코인의 업비트(KRW) 시세가 바이낸스(USDT, 업비트 KRW-USDT 환율로 환산) 시세보다 몇 % 비싼지를 나타냅니다. 코인별로 계산되며, 해당 코인이 바이낸스에 상장돼 있지 않으면 이 지표를 쓸 수 없습니다.",
        "example": "연산자 <, 임계값 0이면: 역프리미엄(국내가가 더 싼) 구간을 포착합니다. 연산자 >, 임계값 5면: 프리미엄이 +5%를 넘는 과열 구간을 매도 필터로 씁니다.",
    },
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py
git commit -m "feat: register KOREA_PREMIUM in the catalog under 시장 심리 category"
```

---

## Task 5: 조건 빌더 프론트엔드 (threshold 추천값)

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: 백엔드 카탈로그의 `category: "시장 심리"`, `value: "KOREA_PREMIUM"`(Task 4).

- [ ] **Step 1~2: (프론트 로직 테스트는 이 저장소에 별도 단위테스트 인프라가 없음 — 기존 컨벤션대로 Step 3
      구현 후 `tsc`+Playwright 수동 검증으로 대체한다.)**

- [ ] **Step 3: 구현**

`frontend/components/StrategyConditionBuilder.tsx`의 `ZERO_CROSS_INDICATORS`(현재
`new Set(['MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT'])`)에 `'KOREA_PREMIUM'` 추가:
```typescript
const ZERO_CROSS_INDICATORS = new Set(['MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM']);
```
(한국프리미엄은 0~100 범위 오실레이터가 아니라 부호 있는 퍼센트 값이라 `OSCILLATOR_BOUNDS`엔 안 맞는다.
`recommendedThreshold()`가 `ZERO_CROSS_INDICATORS`에 있는 지표는 연산자와 무관하게 `0`을 추천하는 기존
로직을 그대로 타므로, 이 한 줄 추가만으로 충분하다 — 별도 분기 추가 불필요.)

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

브라우저(Playwright)에서 `/`(조건 빌더)의 "시장 심리" 카테고리에 "한국프리미엄"이 뜨는지, 선택 시 threshold가
연산자와 무관하게 `0`으로 자동 채워지는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add KOREA_PREMIUM threshold recommendation to condition builder"
```

---

## Task 6: 지표 가이드 탭 콘텐츠

**Files:**
- Modify: `frontend/lib/guide-sample-data.ts`
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Produces: `guide-sample-data.ts`에 `SAMPLE_KOREA_PREMIUM: number[]`(길이 60, `SAMPLE_BARS`와 같은 `bar`
  인덱스에 대응하는 부호 있는 퍼센트 합성 시계열, 대략 -3%~+8% 범위) 추가.

- [ ] **Step 1~2: (지표 가이드 탭도 별도 단위테스트가 없는 순수 프레젠테이션 레이어 — 기존 컨벤션대로
      `tsc`+Playwright로 검증한다. Step 3 이후로 진행.)**

- [ ] **Step 3: 구현**

`frontend/lib/guide-sample-data.ts`의 `buildFearGreedSeries` 함수 뒤, `const closeSeries = buildCloseSeries();`
줄 앞에 추가:
```typescript
function buildKoreaPremiumSeries(): number[] {
  const values: number[] = [];
  for (let i = 0; i < TOTAL_BARS; i++) {
    const wave = 3 * Math.sin((2 * Math.PI * i) / 18) + 2 * Math.sin((2 * Math.PI * i) / 6);
    values.push(Math.round((2.5 + wave) * 100) / 100);
  }
  return values;
}
```
`const fearGreedSeries = buildFearGreedSeries();` 다음 줄에 추가:
```typescript
const koreaPremiumSeries = buildKoreaPremiumSeries();
```
파일 끝(`SAMPLE_FEAR_GREED` export 다음)에 추가:
```typescript
/** 한국프리미엄은 코인 캔들과 무관한 고정 시계열이라 SAMPLE_BARS의 bar 인덱스에 맞춰 별도 배열로 둔다. */
export const SAMPLE_KOREA_PREMIUM: number[] = koreaPremiumSeries;
```

`frontend/lib/indicator-guide.ts`의 `INDICATOR_GUIDE` 객체에서 `FEAR_GREED_CMC` 항목(파일 마지막 항목) 바로
뒤, 객체를 닫는 `};` 앞에 추가:
```typescript
  KOREA_PREMIUM: {
    meaning: '대상 코인의 업비트(KRW) 시세가, 바이낸스(USDT) 시세를 업비트 KRW-USDT 환율로 원화 환산한 값보다 몇 % 비싼지를 나타내는 퍼센트 값입니다. 양수면 국내가 프리미엄(비쌈), 음수면 역프리미엄(쌈)입니다.',
    params: [],
    formula: '(업비트 대상 코인 종가 / (바이낸스 USDT 종가 × 업비트 KRW-USDT 종가) - 1) × 100. 대상 코인이 바이낸스에 상장돼 있지 않으면 계산할 수 없습니다.',
    thresholdExample: '값은 부호 있는 퍼센트입니다. 예: 임계값 0, 연산자 "<"면 역프리미엄 구간을, 임계값 5, 연산자 ">"면 프리미엄이 +5%를 넘는 과열 구간을 포착합니다.',
    usage: '프리미엄이 과도하게 높아지면 국내 매수 심리가 과열됐다고 보고 매도 필터로, 역프리미엄(음수) 구간에서는 저평가로 보고 매수 필터로 흔히 씁니다. 코인별로 계산되므로 대상 코인을 바꾸면 값도 달라집니다.',
  },
```

`frontend/lib/indicator-example-builder.ts`의 import 줄을:
```typescript
import { SAMPLE_BARS, SAMPLE_BTC, SAMPLE_FEAR_GREED, type SampleBar } from '@/lib/guide-sample-data';
```
다음으로 교체:
```typescript
import { SAMPLE_BARS, SAMPLE_BTC, SAMPLE_FEAR_GREED, SAMPLE_KOREA_PREMIUM, type SampleBar } from '@/lib/guide-sample-data';
```
`buildGuideExample` switch문의 `case 'FEAR_GREED_CMC': { ... }` 블록이 끝나는 닫는 중괄호(`}`) 바로 뒤,
`case 'STOP_LOSS_PCT':` 시작 줄 바로 앞에 추가:
```typescript
    case 'KOREA_PREMIUM': {
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { premium: n(SAMPLE_KOREA_PREMIUM[i]) },
      }));
      const gauge = gaugeExample(
        SAMPLE_KOREA_PREMIUM,
        -10,
        15,
        [
          { from: -10, to: 0, color: '#3b82f6', label: '역프리미엄(<0%)' },
          { from: 0, to: 5, color: '#94a3b8', label: '중립' },
          { from: 5, to: 15, color: '#ef4444', label: '과열(>5%)' },
        ],
        '한국프리미엄'
      );
      return {
        columns: [{ key: 'premium', label: '한국프리미엄(%)' }],
        rows,
        chart: gauge.chart,
      };
    }
```

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Playwright로 `/guide`를 열어 "시장 심리" 중분류에 "한국프리미엄"이 뜨는지, 클릭 시 표 + 게이지 차트
(-10~15%, 0/5% 구간 색상 구분)가 정상 렌더되는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/guide-sample-data.ts frontend/lib/indicator-guide.ts frontend/lib/indicator-example-builder.ts
git commit -m "feat: add KOREA_PREMIUM to the indicator guide tab"
```

---

## 이 플랜에 포함하지 않은 것

`docs/superpowers/specs_v1/2026-07-29-korea-premium-external-data-design.md`의 "이 스펙에 포함하지 않은 것"
절과 동일한 이유로 범위 밖이다.

- **시가총액(코인별 raw market cap), 온체인 데이터**: 사용자가 이번 라운드에서 명시적으로 제외.
- **업비트 데이터랩 공포탐욕지수(`FEAR_GREED_UPBIT`)**: fear-greed 스펙에서 이미 범위 밖.
- **바이낸스 선물 펀딩비(Funding Rate)**: 스펙 작성 중 발견한 새 후보. 별도 스펙(`FUNDING_RATE` 가칭)이
  필요.
- **미상장 코인을 마켓 선택 단계에서 미리 걸러주는 UI**: 요청 시점 400 에러로 충분하다고 사용자가 확인
  (스펙의 "미상장 코인 처리" 절 참고) — 마켓 선택 드롭다운에 바이낸스 상장 정보를 미리 노출하는 건 별도
  요청이 있을 때 다룬다.

## Verification (전체)

- `pytest tests/ -v` — 전체 스위트 그린(기존 스위트 + 이번 플랜 신규 ~21개).
- `cd frontend && npx tsc --noEmit` — 클린.
- Playwright: `/`에서 "시장 심리" 카테고리에 "한국프리미엄"이 뜨고, 실제 조건으로 백테스트 1건을 끝까지
  실행해 결과 화면까지 나오는지(바이낸스에 상장된 코인, 예: KRW-ETH로). `/guide`에서 신규 항목이 표+게이지
  차트와 함께 렌더되는지.
- 백엔드는 코드 수정마다 재시작 필요(`uvicorn --reload`가 이 저장소에서 간헐적으로 안 먹는 이슈가 기존에
  있었음 — 반드시 수동 재시작 후 확인).
