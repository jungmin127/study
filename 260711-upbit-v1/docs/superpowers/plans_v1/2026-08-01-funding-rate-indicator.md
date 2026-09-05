# 펀딩비(Funding Rate) 지표 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 바이낸스 무기한 선물 펀딩비를 새 지표 `FUNDING_RATE`(카테고리 "시장 심리")로 추가한다. 조건식에서 원시 펀딩비값(퍼센트)을 threshold와 비교하는 단순 구조이며, 기존 `KOREA_PREMIUM`/`FEAR_GREED_CMC`와 동일한 "외부 API 조회 → parquet 캐싱 → `backend/main.py`에서 캔들에 병합" 패턴을 재사용한다.

**Architecture:** `binance_data_service.py`에 펀딩비 조회(`get_binance_funding_rate`)·병합(`merge_funding_rate`) 함수를 추가하고, `engine/indicators/sentiment.py`에 병합된 라인을 그대로 반환하는 `create_funding_rate`를 등록한다. `backend/main.py`가 조건식에 `FUNDING_RATE`가 쓰였는지 확인해 병합을 수행하고, 프론트(카탈로그/threshold 추천/가이드)에 반영한다.

**Tech Stack:** Python(httpx, pandas — `binance_data_service.py`/`engine/indicators/sentiment.py`/`backend/main.py`), pytest, TypeScript(Next.js 프론트, 자동 테스트 없음).

## Global Constraints

- 스펙 문서: `docs/superpowers/specs_v1/2026-08-01-funding-rate-indicator.md`.
- 조건 구조: 원시 펀딩비값(퍼센트, `원시값 × 100`) vs threshold만 — 파생값(누적/이동평균) 없음.
- 카탈로그 카테고리: "시장 심리"(신규 카테고리 없음).
- 심볼: 기존 `binance_data_service.binance_symbol()` 재사용(선물 심볼 = 현물 심볼 표기).
- 바이낸스 선물 `fundingRate` 엔드포인트는 존재하지 않는 심볼도 HTTP 400이 아니라 200+빈 배열을 반환한다(실측 확인) — "심볼 없음"과 "구간에 데이터 없음"을 구분하지 않고 동일하게 처리.
- `_compute_gaps()`는 기존에 `candle_time` 컬럼명을 하드코딩하고 있어 펀딩비 캐시(`funding_time` 컬럼)에 그대로 못 쓴다 — 컬럼명 파라미터화 필요(Task 1).
- `merge_funding_rate()`는 구간 앞부분(첫 펀딩비 이벤트 이전)에 NaN이 남는 것을 정상으로 취급한다 — `ffill`/`bfill` 없음. `backend/main.py`는 전체가 NaN일 때만(`isna().all()`) 400 에러를 낸다(KOREA_PREMIUM의 `isna().any()`와 다름 — 이유는 Task 4에 명시).
- 프론트에는 테스트 프레임워크가 없다 — 신규 도입 안 함. `npx tsc --noEmit` + 수동 브라우저 확인으로 검증.

---

### Task 1: `_compute_gaps()` 컬럼명 파라미터화

**Files:**
- Modify: `binance_data_service.py`
- Test: `tests/test_binance_data_service.py`

**Interfaces:**
- Produces: `_compute_gaps(cached: pd.DataFrame, start: datetime, end: datetime, time_col: str = "candle_time") -> list[tuple[datetime, datetime]]` — 기존 호출부(`get_binance_close`)는 인자 변경 없이 그대로 동작(기본값 유지), 새 호출부(Task 2의 `get_binance_funding_rate`)는 `time_col="funding_time"`으로 호출.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_binance_data_service.py`에 추가:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_binance_data_service.py -k compute_gaps -v`
Expected: FAIL — `test_compute_gaps_uses_custom_time_column`이 `KeyError: 'candle_time'`로 실패(기존 함수가 `time_col` 인자를 안 받음).

- [ ] **Step 3: `_compute_gaps()` 수정**

현재(`binance_data_service.py`):
```python
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
```
다음으로 교체:
```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_binance_data_service.py -v`
Expected: PASS (전체 통과, 신규 2개 포함, `get_binance_close` 관련 기존 테스트도 그대로 통과)

- [ ] **Step 5: 커밋**

```bash
git add binance_data_service.py tests/test_binance_data_service.py
git commit -m "fix: parameterize _compute_gaps time column for reuse by non-candle caches"
```

---

### Task 2: `binance_data_service.py` — 펀딩비 조회·캐싱·병합

**Files:**
- Modify: `binance_data_service.py`
- Test: `tests/test_binance_data_service.py`

**Interfaces:**
- Consumes: Task 1의 `_compute_gaps(cached, start, end, time_col=...)`. 기존 `RETRY_ATTEMPTS`/`RETRY_BASE_DELAY_SECONDS`/`RATE_LIMIT_BACKOFF_SECONDS`/`REQUEST_DELAY_SECONDS` 상수.
- Produces: `get_binance_funding_rate(symbol: str, start: datetime, end: datetime) -> pd.DataFrame`(컬럼 `[funding_time, funding_rate]`, `funding_rate`는 퍼센트 단위). `merge_funding_rate(df: pd.DataFrame, funding_df: pd.DataFrame) -> pd.DataFrame`(`df`에 `funding_rate_value` 컬럼 추가). 둘 다 Task 4(`backend/main.py`)가 그대로 가져다 씀.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_binance_data_service.py` 상단 import에 추가할 것이 있으면 추가하고(이미 `pd`/`httpx`/`pytest`/`datetime` 등은 임포트돼 있음), 파일 끝에 추가:

```python
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


def test_merge_funding_rate_all_nan_when_funding_df_empty():
    df = pd.DataFrame({"candle_time": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")})
    merged = bds.merge_funding_rate(df, pd.DataFrame(columns=bds._FUNDING_COLUMNS))
    assert merged["funding_rate_value"].isna().all()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_binance_data_service.py -k "funding" -v`
Expected: FAIL — `AttributeError: module 'binance_data_service' has no attribute '_parse_funding'` 등.

- [ ] **Step 3: `binance_data_service.py`에 함수 추가**

파일 끝(`__all__` 선언 바로 앞)에 추가:

```python
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
    )
    return merged.drop(columns="funding_time")
```

`__all__` 리스트에 `"get_binance_funding_rate"`, `"merge_funding_rate"` 추가.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_binance_data_service.py -v`
Expected: PASS (전체 통과)

- [ ] **Step 5: 커밋**

```bash
git add binance_data_service.py tests/test_binance_data_service.py
git commit -m "feat: add Binance funding rate fetch, cache, and merge"
```

---

### Task 3: 엔진 등록 (`FUNDING_RATE` 지표)

**Files:**
- Modify: `engine/indicators/sentiment.py`
- Modify: `engine/indicators/__init__.py`
- Modify: `engine/runner.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: Task 2의 병합 결과가 `funding_rate_value` 컬럼으로 이미 df에 존재한다고 가정(이 Task는 df를 만들지 않고, "이미 병합된 컬럼을 읽는" 부분만 담당).
- Produces: `INDICATOR_FACTORY["FUNDING_RATE"]`(`create_funding_rate`), `engine.runner._OPTIONAL_LINE_CANDIDATES`에 `"funding_rate_value"` 포함.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py`의 `_NEEDS_EXTRA_LINE` 집합을 아래로 교체:

```python
_NEEDS_EXTRA_LINE = {"MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION", "FEAR_GREED_CMC", "KOREA_PREMIUM", "FUNDING_RATE"}  # btc_close/usdt_close 데이터 라인이 필요 — test_market_trend_matches_manual_close_minus_sma_of_btc_close_line 등 참고
```

파일 끝에 추가:

```python
def test_funding_rate_returns_merged_line_value():
    df = make_oscillating_df()
    funding_series = pd.Series([0.03] * len(df))
    values = _run_probe_with_aux("FUNDING_RATE", {}, "funding_rate_value", funding_series)
    assert values[-1] == pytest.approx(0.03)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_indicators.py -k funding_rate -v`
Expected: FAIL — `KeyError: 'FUNDING_RATE'`(아직 `INDICATOR_FACTORY`에 없음). `test_all_registered_indicators_produce_values`는 `_NEEDS_EXTRA_LINE`에 이미 넣어뒀으니 실패하지 않음(등록 전이라 애초에 순회 대상도 아님).

- [ ] **Step 3: `engine/indicators/sentiment.py`에 추가**

파일 끝에 추가:

```python
def create_funding_rate(data: bt.feeds.PandasData, **params) -> bt.LineBuffer:
    return data.funding_rate_value
```

- [ ] **Step 4: `engine/indicators/__init__.py`에 등록**

`from .sentiment import create_fear_greed_cmc, create_korea_premium` 줄을 아래로 교체:

```python
from .sentiment import create_fear_greed_cmc, create_funding_rate, create_korea_premium
```

`INDICATOR_FACTORY` 딕셔너리의 `"KOREA_PREMIUM": create_korea_premium,` 줄 바로 다음에 추가:

```python
    "FUNDING_RATE": create_funding_rate,
```

- [ ] **Step 5: `engine/runner.py`에 라인 등록**

현재:
```python
_OPTIONAL_LINE_CANDIDATES: tuple[str, ...] = (
    "trade_value", "fear_greed_value", "korea_premium_value", *AUX_MARKET_LINE_NAME.values()
```
다음으로 교체:
```python
_OPTIONAL_LINE_CANDIDATES: tuple[str, ...] = (
    "trade_value", "fear_greed_value", "korea_premium_value", "funding_rate_value", *AUX_MARKET_LINE_NAME.values()
```

(이 줄 뒤에 닫는 괄호 등 나머지 원본 내용은 그대로 둔다.)

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS (전체 통과, 신규 1개 포함)

- [ ] **Step 7: 커밋**

```bash
git add engine/indicators/sentiment.py engine/indicators/__init__.py engine/runner.py tests/test_indicators.py
git commit -m "feat: register FUNDING_RATE indicator"
```

---

### Task 4: `backend/main.py` — 카탈로그 + 병합

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 2의 `get_binance_funding_rate`/`merge_funding_rate`, Task 3의 `INDICATOR_FACTORY["FUNDING_RATE"]`(→ `tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators`가 카탈로그와 자동 대조).
- Produces: `POST /api/v1/backtests/run`이 `FUNDING_RATE` 조건을 처리할 수 있게 됨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_run_backtest_computes_funding_rate_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    target_df = make_oscillating_df()
    funding_df = pd.DataFrame({
        "funding_time": target_df["candle_time"] - pd.Timedelta(minutes=1),
        "funding_rate": 0.03,
    })
    monkeypatch.setattr(
        backend_module, "get_binance_funding_rate",
        lambda symbol, start, end: funding_df,
    )

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": -100}]}
    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(market="KRW-ETH", buy_conditions=buy),
    )

    assert resp.status_code == 200
    merged = captured["df"]
    assert merged["funding_rate_value"].round(4).eq(0.03).all()


def test_run_backtest_rejects_funding_rate_when_no_data_in_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    monkeypatch.setattr(
        backend_module, "get_binance_funding_rate",
        lambda symbol, start, end: pd.DataFrame(columns=["funding_time", "funding_rate"]),
    )

    buy = {"type": "AND", "conditions": [{"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "펀딩비" in resp.json()["detail"]


def test_run_backtest_allows_funding_rate_with_partial_leading_nan(monkeypatch, tmp_path):
    # 구간 앞부분(첫 펀딩비 이벤트 이전)에 NaN이 남는 건 정상 — 400 에러가 나면 안 된다.
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    target_df = make_oscillating_df()
    half = len(target_df) // 2
    funding_df = pd.DataFrame({
        "funding_time": target_df["candle_time"].iloc[half:] - pd.Timedelta(minutes=1),
        "funding_rate": 0.02,
    })
    monkeypatch.setattr(
        backend_module, "get_binance_funding_rate",
        lambda symbol, start, end: funding_df,
    )

    buy = {"type": "AND", "conditions": [{"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": -100}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_backend.py -k funding_rate -v`
Expected: FAIL — 카탈로그에 `FUNDING_RATE`가 없어 `find_unknown_indicators`가 걸리거나(400), `AttributeError: module 'backend.main' has no attribute 'get_binance_funding_rate'`.

- [ ] **Step 3: import 추가**

`backend/main.py` 상단의 다음 줄:
```python
from binance_data_service import BinanceSymbolNotFoundError, binance_symbol, get_binance_close
```
을 아래로 교체:
```python
from binance_data_service import (
    BinanceSymbolNotFoundError,
    binance_symbol,
    get_binance_close,
    get_binance_funding_rate,
    merge_funding_rate,
)
```

- [ ] **Step 4: 카탈로그 항목 추가**

`INDICATOR_CATALOG`의 `"value": "KOREA_PREMIUM", ...` 항목(`},`로 끝나는 블록) 바로 다음에 추가:

```python
    {
        "value": "FUNDING_RATE", "label": "펀딩비(바이낸스 선물)", "category": "시장 심리",
        "params": [],
        "description": "대상 코인의 바이낸스 무기한 선물 펀딩비를 퍼센트로 나타냅니다. 양수면 롱이 숏에게 수수료를 지불(롱 우세/과열), 음수면 그 반대(숏 우세)입니다.",
        "example": "펀딩비 > 0.05%면 롱 포지션이 과열된 구간으로, < -0.03%면 숏 포지션이 과열된 구간으로 흔히 해석합니다.",
    },
```

- [ ] **Step 5: `_fetch_backtest_dataframe()`에 병합 블록 추가**

`KOREA_PREMIUM` 병합 블록(`df["korea_premium_value"] = ...` 줄로 끝나는 `if "KOREA_PREMIUM" in used_indicators:` 블록) 바로 다음, `return df` 줄 바로 앞에 추가:

```python
    if "FUNDING_RATE" in used_indicators:
        symbol = binance_symbol(market)
        try:
            funding_df = get_binance_funding_rate(symbol, start_dt, end_dt)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        df = merge_funding_rate(df, funding_df)
        if df["funding_rate_value"].isna().all():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{symbol}의 바이낸스 선물 펀딩비 데이터가 해당 기간에 없습니다"
                    "(선물 미상장 또는 기간 밖일 수 있습니다)"
                ),
            )
```

(`isna().all()`을 쓴다 — KOREA_PREMIUM의 `isna().any()`와 다름. 펀딩비는 8시간마다 한 번씩만
찍히고 `merge_asof`가 "그 이전 가장 최근 값"을 채우는 구조라, 구간 맨 앞부분(첫 펀딩비
이벤트 이전)은 정상적으로 NaN이 남을 수 있다 — 전부 NaN일 때만(그 심볼의 펀딩비 데이터를
하나도 못 찾음) 진짜 에러다.)

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (전체 통과, 신규 3개 포함)

- [ ] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: merge Binance funding rate into backtest dataframe"
```

---

### Task 5: 조건식 빌더 threshold 추천 (`frontend/components/StrategyConditionBuilder.tsx`)

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: Task 4에서 카탈로그에 추가된 `FUNDING_RATE` 지표 이름.

- [ ] **Step 1: `OSCILLATOR_BOUNDS`에 `FUNDING_RATE` 추가**

현재:
```ts
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
  FEAR_GREED_CMC: { low: 20, high: 80 },
  VPIN: { low: 0.35, high: 0.55 },
};
```
다음으로 교체:
```ts
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
  FEAR_GREED_CMC: { low: 20, high: 80 },
  VPIN: { low: 0.35, high: 0.55 },
  FUNDING_RATE: { low: -0.03, high: 0.05 },
};
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add threshold recommendation for FUNDING_RATE"
```

---

### Task 6: 지표 가이드 (`frontend/lib/indicator-guide.ts`)

**Files:**
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Consumes: Task 4의 `FUNDING_RATE` 카탈로그 항목(`params: []`, 빈 파라미터 리스트와 정확히 일치해야 함 — `guide.params`도 빈 배열).

- [ ] **Step 1: `KOREA_PREMIUM` 항목 바로 다음에 신규 항목 추가**

`KOREA_PREMIUM: { ... },` 블록 바로 다음에 추가:

```ts
  FUNDING_RATE: {
    meaning:
      '대상 코인의 바이낸스 무기한 선물(perpetual futures) 펀딩비를 퍼센트로 나타낸 값입니다. 양수면 롱 포지션이 숏 포지션에게 수수료를 지불하는 상태(롱 우세/과열), 음수면 그 반대(숏 우세/과열)입니다. 8시간마다 갱신되며, 각 캔들 시각 기준 가장 최근 값을 그대로 씁니다.',
    params: [],
    formula: '바이낸스 선물 API가 산출하는 값을 그대로 가져와 퍼센트로 변환합니다(원시값 × 100). 이 앱이 직접 계산하지 않습니다. 대상 코인이 바이낸스 선물에 상장돼 있지 않으면 계산할 수 없습니다.',
    thresholdExample: '값은 부호 있는 퍼센트입니다. 예: 임계값 0.05, 연산자 ">"면 롱 과열 구간을, 임계값 -0.03, 연산자 "<"면 숏 과열 구간을 포착합니다.',
    usage: '펀딩비가 과도하게 양수면 롱 포지션이 몰려 과열됐다고 보고 역발상 매도(숏) 필터로, 과도하게 음수면 숏이 몰렸다고 보고 역발상 매수(롱) 필터로 흔히 씁니다. KOREA_PREMIUM과 마찬가지로 대상 코인마다 값이 다릅니다.',
  },
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/indicator-guide.ts
git commit -m "feat: add indicator guide entry for FUNDING_RATE"
```

---

### Task 7: 통합 검증

**Files:** 없음 (검증 전용 태스크)

- [ ] **Step 1: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: 전부 통과.

- [ ] **Step 2: 실제 백테스트 1회 실행 — 정상 케이스**

백엔드(`uvicorn backend.main:app --reload --port 8000`)와 프론트(`npm run dev`)가 떠 있는 상태에서, 조건식 빌더로 `KRW-ETH`(바이낸스에 실제 상장된 코인) 대상 매도 조건에 `FUNDING_RATE > 0.05`를 넣고 백테스트를 실행한다. 확인할 것:
- 에러 없이 완주하는지.
- "백테스트 결과" 상세 페이지에서 매도 전략에 `FUNDING_RATE>0.05`가 표기되는지.

- [ ] **Step 3: 실제 백테스트 1회 실행 — 선물 미상장/존재하지 않는 심볼 케이스**

바이낸스 선물에 없는 코인(또는 임의 문자열이 심볼에 섞이는 코인)으로 같은 조건을 시도해, 400 에러 메시지가 "펀딩비 데이터가 해당 기간에 없습니다"로 명확하게 나오는지 확인한다.

- [ ] **Step 4: 프론트 확인**

조건식 빌더 "시장 심리" 카테고리에 `FUNDING_RATE`가 보이는지, threshold 추천값(`<`→`-0.03`, `>`→`0.05`)이 채워지는지, 지표 가이드 탭에서 빈 화면이 아닌지 확인한다.

- [ ] **Step 5: 결과 보고**

위 단계가 모두 통과하면 "펀딩비 지표 구현 및 검증 완료"로 사용자에게 보고한다. 실패하는 항목이 있으면 어느 Task로 돌아가 고쳐야 하는지 명시한다.
