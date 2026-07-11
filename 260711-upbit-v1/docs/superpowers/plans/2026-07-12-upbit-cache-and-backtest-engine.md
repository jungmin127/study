# Upbit 로컬 시세 캐시 + 룰 기반 백테스팅 엔진 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upbit 공개 캔들 API를 lazy-fetch 방식으로 로컬 Parquet에 캐싱하는 `get_candles()`와, 그 데이터로 backtrader 기반 룰 전략을 실행하고 결과를 SQLite에 캐싱하는 `run_backtest_cached()`를 구현한다.

**Architecture:** 두 개의 독립 모듈. (1) `upbit_data_service.py` — Upbit 캔들 API를 호출해 마켓·타임프레임별 Parquet 파일에 부분 캐시(gap-only fetch)로 누적. (2) `engine/` 패키지 — `runner.py`(backtrader 실행, `backtesting_1/engine/runner.py` 포트) + `cache.py`(전략 소스코드 해시 기반 캐시 키, SQLite 저장/조회, `run_backtest_cached()` 오케스트레이션). 서버 프로세스, 스케줄러, 웹 UI 없음 — 전부 로컬 스크립트/노트북에서 함수 호출로 사용.

**Tech Stack:** Python 3.11, httpx(HTTP), pandas + pyarrow(Parquet), backtrader(백테스트 엔진), sqlite3(표준 라이브러리, 결과 캐시), pytest(테스트).

## Global Constraints

- 서버 프로세스·스케줄러·설정 파일 레이어를 두지 않는다 (`project-plan/2026-07-12-upbit-local-cache-design.md`, `project-plan/2026-07-12-upbit-backtest-engine-design.md`).
- 마감되지 않은(진행 중인) 캔들은 캐시/반환 결과에서 제외한다.
- 실패한 백테스트 실행 결과는 SQLite에 저장하지 않는다 — 예외는 그대로 호출부에 전파한다.
- 전략은 `bt.Strategy` 서브클래스를 코드로 직접 작성한다 — JSON 조건 트리나 동적 클래스 생성 레이어는 만들지 않는다.
- 캐시 키는 전략 클래스의 소스코드(`inspect.getsource`)를 포함해 해시한다 — 이름이 같아도 로직이 바뀌면 재실행되어야 한다.

---

### Task 0: 프로젝트 스캐폴딩

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `engine/__init__.py`

**Interfaces:**
- Produces: `pytest`가 저장소 루트에서 `import upbit_data_service`, `import engine.runner`, `import engine.cache`를 바로 할 수 있는 환경 (별도 `pip install -e .` 불필요).

- [ ] **Step 1: 의존성 파일 작성**

`requirements.txt`:
```
httpx>=0.26
pandas>=2.2
pyarrow>=15.0
backtrader>=1.9.78
pytest>=8.0
```

- [ ] **Step 2: pytest 설정 작성**

`pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 3: gitignore 작성**

`.gitignore`:
```
__pycache__/
*.pyc
.pytest_cache/
data/
```

- [ ] **Step 4: engine 패키지 초기화 파일 생성**

`engine/__init__.py`: (빈 파일)

- [ ] **Step 5: 의존성 설치 및 pytest 동작 확인**

Run:
```bash
pip install -r requirements.txt
mkdir tests
pytest --collect-only
```
Expected: `no tests ran` (에러 없이 수집 단계 통과)

- [ ] **Step 6: 커밋**

```bash
git add requirements.txt pytest.ini .gitignore engine/__init__.py
git commit -m "chore: scaffold Python project for cache + backtest engine"
```

---

### Task 1: Upbit 캔들 API 엔드포인트 매핑 + 응답 파싱

**Files:**
- Create: `upbit_data_service.py`
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Produces: `_CANDLE_COLUMNS: list[str]`, `_endpoint_for_timeframe(timeframe: str) -> str`, `_parse_candles(raw: list[dict]) -> pd.DataFrame` (컬럼 `[candle_time, open, high, low, close, volume]`, `candle_time`은 UTC-aware).

- [ ] **Step 1: 실패하는 테스트 작성 — 엔드포인트 매핑**

`tests/test_upbit_data_service.py`:
```python
import pytest

from upbit_data_service import _endpoint_for_timeframe


def test_endpoint_for_days():
    assert _endpoint_for_timeframe("days") == "https://api.upbit.com/v1/candles/days"


def test_endpoint_for_minutes():
    assert _endpoint_for_timeframe("minutes60") == "https://api.upbit.com/v1/candles/minutes/60"


def test_endpoint_for_unsupported_timeframe_raises():
    with pytest.raises(ValueError):
        _endpoint_for_timeframe("weeks")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'upbit_data_service'`

- [ ] **Step 3: 최소 구현 작성**

`upbit_data_service.py`:
```python
from __future__ import annotations

UPBIT_BASE_URL = "https://api.upbit.com/v1"

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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: map upbit candle timeframe to API endpoint"
```

- [ ] **Step 6: 실패하는 테스트 작성 — 응답 파싱**

`tests/test_upbit_data_service.py`에 추가:
```python
from upbit_data_service import _parse_candles


def test_parse_candles_maps_fields():
    raw = [
        {
            "market": "KRW-BTC",
            "candle_date_time_utc": "2026-07-10T00:00:00",
            "opening_price": 100.0,
            "high_price": 110.0,
            "low_price": 90.0,
            "trade_price": 105.0,
            "candle_acc_trade_volume": 12.5,
        }
    ]

    df = _parse_candles(raw)

    assert list(df.columns) == ["candle_time", "open", "high", "low", "close", "volume"]
    assert df.iloc[0]["open"] == 100.0
    assert df.iloc[0]["close"] == 105.0
    assert df.iloc[0]["volume"] == 12.5
    assert df["candle_time"].dt.tz is not None


def test_parse_candles_empty_input():
    df = _parse_candles([])
    assert list(df.columns) == ["candle_time", "open", "high", "low", "close", "volume"]
    assert len(df) == 0
```

- [ ] **Step 7: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_candles'`

- [ ] **Step 8: 구현 추가**

`upbit_data_service.py`에 추가:
```python
import pandas as pd


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
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: PASS (5 tests)

- [ ] **Step 10: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: parse upbit candle API response into OHLCV dataframe"
```

---

### Task 2: 재시도 포함 단일 페이지 API 호출

**Files:**
- Modify: `upbit_data_service.py`
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Consumes: `_endpoint_for_timeframe`, `_CANDLE_COLUMNS` (Task 1)
- Produces: `_fetch_page(client: httpx.Client, url: str, market: str, to: datetime | None, count: int = 200) -> list[dict]`, 모듈 상수 `RETRY_ATTEMPTS`, `RETRY_BASE_DELAY_SECONDS`, `RATE_LIMIT_BACKOFF_SECONDS` (테스트에서 monkeypatch로 0에 가깝게 낮춰 재시도 대기를 건너뜀).

- [ ] **Step 1: 실패하는 테스트 작성 — 정상 응답**

`tests/test_upbit_data_service.py`에 추가:
```python
from datetime import datetime, timezone

import httpx

import upbit_data_service as uds
from upbit_data_service import _fetch_page


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_page_returns_json_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["market"] == "KRW-BTC"
        return httpx.Response(200, json=[{"market": "KRW-BTC"}])

    with _mock_client(handler) as client:
        result = _fetch_page(client, "https://api.upbit.com/v1/candles/days", "KRW-BTC", None)

    assert result == [{"market": "KRW-BTC"}]


def test_fetch_page_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(uds, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[{"market": "KRW-BTC"}])

    with _mock_client(handler) as client:
        result = _fetch_page(
            client, "https://api.upbit.com/v1/candles/days", "KRW-BTC",
            datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

    assert calls["count"] == 2
    assert result == [{"market": "KRW-BTC"}]


def test_fetch_page_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(uds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_page(client, "https://api.upbit.com/v1/candles/days", "KRW-BTC", None)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_page'`

- [ ] **Step 3: 구현 작성**

`upbit_data_service.py`에 추가:
```python
import time
from datetime import datetime

import httpx

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0


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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: fetch single upbit candle page with 429/error retry"
```

---

### Task 3: 구간 페이지네이션 (`_fetch_range`)

**Files:**
- Modify: `upbit_data_service.py`
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Consumes: `_endpoint_for_timeframe`, `_fetch_page`, `_parse_candles` (Task 1, 2)
- Produces: `_fetch_range(market: str, timeframe: str, start: datetime, end: datetime, client: httpx.Client | None = None) -> pd.DataFrame`, 모듈 상수 `REQUEST_DELAY_SECONDS`

Upbit 캔들 API는 `to` 기준 과거로만 페이지네이션된다(최신→과거, 최대 200개/페이지). `end`부터 시작해 받아온 페이지의 가장 오래된 시각을 다음 `to`로 삼아 `start`에 도달하거나 200개 미만 응답을 받을 때까지 반복한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py`에 추가 (`timedelta`는 이 태스크에서 처음 쓰이므로 파일 상단 `from datetime import datetime, timezone` 옆에 `timedelta`도 추가해야 한다 — `from datetime import datetime, timedelta, timezone`로 수정):
```python
from upbit_data_service import _fetch_range


def _candle(iso_time: str, price: float) -> dict:
    return {
        "market": "KRW-BTC",
        "candle_date_time_utc": iso_time,
        "opening_price": price,
        "high_price": price,
        "low_price": price,
        "trade_price": price,
        "candle_acc_trade_volume": 1.0,
    }


def test_fetch_range_single_page_when_within_count(monkeypatch):
    monkeypatch.setattr(uds, "REQUEST_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _candle("2026-07-10T00:00:00", 103),
                _candle("2026-07-09T00:00:00", 102),
                _candle("2026-07-08T00:00:00", 101),
            ],
        )

    with _mock_client(handler) as client:
        df = _fetch_range(
            "KRW-BTC", "days",
            datetime(2026, 7, 8, tzinfo=timezone.utc),
            datetime(2026, 7, 10, tzinfo=timezone.utc),
            client=client,
        )

    assert len(df) == 3
    assert df["candle_time"].is_monotonic_increasing


def test_fetch_range_pages_backward_until_start_reached(monkeypatch):
    monkeypatch.setattr(uds, "REQUEST_DELAY_SECONDS", 0.0)
    calls = {"count": 0}
    page1_end = datetime(2026, 7, 10, tzinfo=timezone.utc)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            # 200개 꽉 채운 페이지(가장 오래된 캔들도 여전히 start보다 나중)를 반환해
            # "더 오래된 데이터가 있으니 한 페이지 더 가져와야 함"을 흉내낸다.
            page = [
                _candle(
                    (page1_end - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S"),
                    100 + i,
                )
                for i in range(200)
            ]
            return httpx.Response(200, json=page)
        # 두 번째 페이지에서 start 이전 캔들을 반환해 페이지네이션 종료 조건을 만족시킨다.
        page = [_candle("2025-01-01T00:00:00", 50)]
        return httpx.Response(200, json=page)

    with _mock_client(handler) as client:
        df = _fetch_range("KRW-BTC", "days", start, page1_end, client=client)

    assert calls["count"] == 2
    assert df["candle_time"].min() <= start
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_range'`

- [ ] **Step 3: 구현 작성**

`upbit_data_service.py`에 추가:
```python
from datetime import timedelta

REQUEST_DELAY_SECONDS = 0.15


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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: paginate upbit candle range fetch backward from end"
```

---

### Task 4: 부분 캐시 갭 계산 (`_compute_gaps`)

**Files:**
- Modify: `upbit_data_service.py`
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Produces: `_compute_gaps(cached: pd.DataFrame, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]`

순수 함수, 네트워크/파일 I/O 없음.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py`에 추가:
```python
from upbit_data_service import _compute_gaps


def _df_with_range(start_iso: str, end_iso: str) -> pd.DataFrame:
    idx = pd.date_range(start_iso, end_iso, freq="D", tz="UTC")
    return pd.DataFrame({"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1})


def test_compute_gaps_no_cache_returns_full_range():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(pd.DataFrame(columns=uds._CANDLE_COLUMNS), start, end)
    assert gaps == [(start, end)]


def test_compute_gaps_missing_front_only():
    cached = _df_with_range("2026-01-05", "2026-01-10")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(cached, start, end)
    assert len(gaps) == 1
    assert gaps[0][0] == start
    assert gaps[0][1] < datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_compute_gaps_missing_back_only():
    cached = _df_with_range("2026-01-01", "2026-01-05")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    gaps = _compute_gaps(cached, start, end)
    assert len(gaps) == 1
    assert gaps[0][1] == end
    assert gaps[0][0] > datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_compute_gaps_fully_covered_returns_empty():
    cached = _df_with_range("2026-01-01", "2026-01-10")
    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    end = datetime(2026, 1, 9, tzinfo=timezone.utc)
    assert _compute_gaps(cached, start, end) == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: FAIL with `ImportError: cannot import name '_compute_gaps'`

- [ ] **Step 3: 구현 작성**

`upbit_data_service.py`에 추가:
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

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: compute uncovered date ranges against cached candles"
```

---

### Task 5: `get_candles()` 오케스트레이션 + Parquet 캐시

**Files:**
- Modify: `upbit_data_service.py`
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Consumes: `_compute_gaps` (Task 4), `_fetch_range` (Task 3), `_CANDLE_COLUMNS` (Task 1)
- Produces: `get_candles(market: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame` — 서브프로젝트 2가 그대로 소비하는 공개 인터페이스. 모듈 레벨 `CACHE_DIR: Path` (테스트에서 monkeypatch로 임시 디렉터리로 교체).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_data_service.py`에 추가:
```python
from pathlib import Path

from upbit_data_service import get_candles


def test_get_candles_fetches_full_range_when_no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(uds, "CACHE_DIR", tmp_path)
    calls: list[tuple] = []

    def fake_fetch_range(market, timeframe, start, end, client=None):
        calls.append((market, timeframe, start, end))
        idx = pd.date_range(start, end, freq="D", tz="UTC")
        return pd.DataFrame(
            {"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        )

    monkeypatch.setattr(uds, "_fetch_range", fake_fetch_range)
    monkeypatch.setattr(
        uds, "datetime",
        type("_FixedDatetime", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 1, 20, tzinfo=timezone.utc))
        }),
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    df = get_candles("KRW-BTC", "days", start, end)

    assert len(calls) == 1
    assert len(df) == 10
    assert (tmp_path / "KRW-BTC_days.parquet").exists()


def test_get_candles_skips_fetch_when_fully_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(uds, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        uds, "datetime",
        type("_FixedDatetime", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 1, 20, tzinfo=timezone.utc))
        }),
    )

    idx = pd.date_range("2026-01-01", "2026-01-10", freq="D", tz="UTC")
    existing = pd.DataFrame(
        {"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    existing.to_parquet(tmp_path / "KRW-BTC_days.parquet", index=False)

    def fail_fetch_range(*args, **kwargs):
        raise AssertionError("캐시가 이미 구간을 커버하므로 호출되면 안 됨")

    monkeypatch.setattr(uds, "_fetch_range", fail_fetch_range)

    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    end = datetime(2026, 1, 9, tzinfo=timezone.utc)
    df = get_candles("KRW-BTC", "days", start, end)

    assert len(df) == 8
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_candles'`

- [ ] **Step 3: 구현 작성**

`upbit_data_service.py`에 추가:
```python
from datetime import timezone
from pathlib import Path

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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_upbit_data_service.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: 커밋**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: add get_candles() lazy-fetch parquet cache orchestration"
```

---

### Task 6: backtrader 실행 엔진 포팅 (`engine/runner.py`)

**Files:**
- Create: `engine/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `run_backtest(df: pd.DataFrame, strategy_cls: type[bt.Strategy], risk_config: dict, strategy_params: dict | None = None) -> dict` — 반환 키: `equity_curve`, `trades`, `final_value`, `sharpe`, `max_drawdown`.
- `backtesting_1/backend/app/engine/runner.py`를 포팅하되 다음을 변경: (1) `df.set_index("open_time")` → `df.set_index("candle_time")` (서브프로젝트 1 캐시 컬럼명에 맞춤), (2) `sharpe`/`max_drawdown`을 반환 dict에 포함 (원본은 analyzer를 등록만 하고 반환하지 않았음 — 이번 설계 문서가 요구하는 필드이므로 추가).

- [ ] **Step 1: 실패하는 스모크 테스트 작성**

`tests/test_runner.py`:
```python
from datetime import datetime, timezone

import backtrader as bt
import pandas as pd

from engine.runner import run_backtest


class BuyAndHoldOnce(bt.Strategy):
    def __init__(self):
        self.bought = False

    def next(self):
        if not self.bought and len(self) == 5:
            self.buy()
            self.bought = True


def _make_synthetic_df(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    # 봉 사이 등락폭을 작게(바 대비 0.05%) 유지해야 한다 — FractionalPercentSizer는
    # 신호 발생 시점(종가)의 가격으로 매수 수량을 계산하지만 실제 체결은 다음 봉의 시가에서
    # 이뤄진다. 등락폭이 사이저의 버퍼(0.5%)+수수료를 넘으면 체결 시점에 현금이 모자라
    # Margin으로 주문이 거부된다 — 100 대신 10000을 기준가로, +1 대신 +5를 스텝으로 사용해
    # 봉 사이 변동을 충분히 작게 유지한다.
    prices = [10000 + i * 5 for i in range(n)]
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1.0] * n,
        }
    )


def test_run_backtest_buy_and_hold_once():
    df = _make_synthetic_df()
    result = run_backtest(
        df=df,
        strategy_cls=BuyAndHoldOnce,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
    )

    assert result["final_value"] > 10000
    assert len(result["equity_curve"]) == 30
    assert len(result["trades"]) == 1
    assert result["trades"][0]["forceClosed"] is True
    assert "sharpe" in result
    assert "max_drawdown" in result
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.runner'`

- [ ] **Step 3: `backtesting_1/backend/app/engine/runner.py`를 포팅**

`engine/runner.py` — 아래 세 부분만 원본과 다르다: 클래스/함수 본문은 `C:\Users\jungm\project\backtesting_1\backend\app\engine\runner.py`와 동일하게 유지하고, (a) `run_backtest()` 안의 `df_bt = df_bt.set_index("open_time")`를 `df_bt = df_bt.set_index("candle_time")`로 변경, (b) 함수 끝의 `return` 직전에 sharpe/max_drawdown 추출을 추가, (c) `return` dict에 `sharpe`, `max_drawdown` 키 추가.

```python
"""
engine/runner.py

bt.Cerebro 설정 및 실행, 분석기 결과 추출.
backtesting_1/backend/app/engine/runner.py 포팅 버전.
"""
from __future__ import annotations

import backtrader as bt
import pandas as pd


class PandasDataWithExtra(bt.feeds.PandasData):
    """dominance, kimchi_premium 등 외부 데이터 컬럼을 포함하는 커스텀 피드.

    DataFrame에 'extra' 컬럼이 있어야 한다.
    전략에서 self.data.extra[0] 으로 접근.
    """

    lines = ("extra",)
    params = (("extra", "extra"),)


class FractionalPercentSizer(bt.Sizer):
    """소수점 수량을 지원하는 퍼센트 사이저 (암호화폐 소수점 거래용)."""
    params = (("percents", 100),)

    def _getsizing(self, comminfo, cash, data, isbuy):
        if isbuy:
            pct = self.params.percents / 100.0
            price = data.close[0]
            comm_rate = comminfo.p.commission if hasattr(comminfo.p, 'commission') else 0.001
            size = cash * pct / (price * (1.0 + comm_rate + 0.005))
        else:
            size = self.broker.getposition(data).size
        return size


class EquityAnalyzer(bt.Analyzer):
    """각 bar의 포트폴리오 가치를 기록."""

    def start(self) -> None:
        self.rets: list[dict] = []

    def next(self) -> None:
        dt = self.data.datetime.datetime(0)
        self.rets.append({
            "timestamp": dt.isoformat(),
            "value": round(self.strategy.broker.getvalue(), 4),
        })

    def get_analysis(self) -> list[dict]:
        return self.rets


class TradeLogger(bt.Analyzer):
    """완료된 거래(trade.isclosed)를 기록. 미청산 포지션도 추적."""

    def start(self) -> None:
        self.trades: list[dict] = []
        self._open: dict[int, dict] = {}

    def notify_trade(self, trade: bt.Trade) -> None:
        if trade.isopen:
            self._open[trade.ref] = {
                "entryTime": bt.num2date(trade.dtopen).isoformat(),
                "entryPrice": round(trade.price, 8),
                "size": abs(trade.size),
                "baropen": trade.baropen,
            }
            return

        if not trade.isclosed:
            return

        open_info = self._open.pop(trade.ref, None)
        size = open_info["size"] if open_info else 1

        entry_price = trade.price
        exit_price = entry_price + trade.pnl / size if size else entry_price
        return_rate = (trade.pnlcomm / (entry_price * size) * 100) if (entry_price and size) else 0.0

        self.trades.append({
            "entryTime": bt.num2date(trade.dtopen).isoformat(),
            "exitTime": bt.num2date(trade.dtclose).isoformat(),
            "entryPrice": round(entry_price, 8),
            "exitPrice": round(exit_price, 8),
            "returnRate": round(return_rate, 4),
            "holdingPeriod": int(trade.barclose - trade.baropen),
            "pnl": round(trade.pnlcomm, 4),
            "forceClosed": False,
        })

    def get_analysis(self) -> list[dict]:
        return self.trades

    def get_open_trades(self) -> list[dict]:
        return list(self._open.values())


def run_backtest(
    df: pd.DataFrame,
    strategy_cls: type[bt.Strategy],
    risk_config: dict,
    strategy_params: dict | None = None,
    extra_column: str | None = None,
) -> dict:
    """
    백테스트를 실행하고 결과를 반환.

    Args:
        df: OHLCV DataFrame (컬럼: candle_time, open, high, low, close, volume)
        strategy_cls: bt.Strategy 서브클래스
        risk_config: {initial_capital, commission_rate, position_sizing,
                       position_size, stop_loss, take_profit, trailing_stop}
        strategy_params: 전략 파라미터 (addstrategy에 키워드 인수로 전달)
        extra_column: df에 포함된 외부 데이터 컬럼명

    Returns:
        {equity_curve, trades, final_value, sharpe, max_drawdown}
    """
    if strategy_params is None:
        strategy_params = {}

    df_bt = df.copy()
    df_bt = df_bt.set_index("candle_time")

    if df_bt.index.tz is not None:
        df_bt.index = df_bt.index.tz_localize(None)

    if extra_column and extra_column in df_bt.columns:
        df_bt = df_bt.rename(columns={extra_column: "extra"})
        data_feed = PandasDataWithExtra(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, extra="extra",
        )
    else:
        data_feed = bt.feeds.PandasData(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1,
        )

    cerebro = bt.Cerebro()
    cerebro.adddata(data_feed)

    initial_capital: float = float(risk_config.get("initial_capital", 10000))
    commission_rate: float = float(risk_config.get("commission_rate", 0.001))
    position_size: float = float(risk_config.get("position_size", 100))

    cerebro.broker.setcash(initial_capital)
    cerebro.broker.setcommission(commission=commission_rate)
    cerebro.addsizer(FractionalPercentSizer, percents=min(position_size, 100))

    cerebro.addstrategy(strategy_cls, **strategy_params)

    cerebro.addanalyzer(EquityAnalyzer, _name="equity")
    cerebro.addanalyzer(TradeLogger, _name="trades")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

    results = cerebro.run()
    strategy = results[0]

    equity_curve: list[dict] = strategy.analyzers.equity.get_analysis()
    trades: list[dict] = strategy.analyzers.trades.get_analysis()
    final_value: float = round(cerebro.broker.getvalue(), 4)

    sharpe_analysis = strategy.analyzers.sharpe.get_analysis()
    sharpe_ratio = sharpe_analysis.get("sharperatio")

    drawdown_analysis = strategy.analyzers.drawdown.get_analysis()
    max_drawdown_pct = drawdown_analysis.get("max", {}).get("drawdown")

    open_trades = strategy.analyzers.trades.get_open_trades()
    if open_trades:
        last_close = float(df_bt["close"].iloc[-1])
        last_dt = df_bt.index[-1].isoformat()
        total_bars = len(df_bt)

        for ot in open_trades:
            entry_price = ot["entryPrice"]
            size = ot["size"]
            pnl_gross = (last_close - entry_price) * size
            commission_cost = last_close * size * commission_rate
            pnlcomm = pnl_gross - commission_cost
            return_rate = (pnlcomm / (entry_price * size) * 100) if (entry_price and size) else 0.0
            holding_period = max(total_bars - 1 - ot["baropen"], 0)

            trades.append({
                "entryTime": ot["entryTime"],
                "exitTime": last_dt,
                "entryPrice": round(entry_price, 8),
                "exitPrice": round(last_close, 8),
                "returnRate": round(return_rate, 4),
                "holdingPeriod": holding_period,
                "pnl": round(pnlcomm, 4),
                "forceClosed": True,
            })

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "final_value": final_value,
        "sharpe": sharpe_ratio,
        "max_drawdown": max_drawdown_pct,
    }


__all__ = ["run_backtest"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_runner.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: 커밋**

```bash
git add engine/runner.py tests/test_runner.py
git commit -m "feat: port backtrader runner engine for upbit candle_time schema"
```

---

### Task 7: 캐시 키 계산 (`engine/cache.py` — `compute_cache_key`)

**Files:**
- Create: `engine/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `compute_cache_key(strategy_cls: type, strategy_params: dict, market: str, timeframe: str, start: datetime, end: datetime, risk_config: dict) -> str`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`:
```python
from datetime import datetime, timezone

import backtrader as bt

from engine.cache import compute_cache_key


class _StrategyA(bt.Strategy):
    def next(self):
        pass


class _StrategyB(bt.Strategy):
    def next(self):
        self.buy()


def _key(strategy_cls=_StrategyA, params=None, risk=None):
    return compute_cache_key(
        strategy_cls,
        params or {"threshold": 1},
        "KRW-BTC",
        "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk or {"initial_capital": 10000},
    )


def test_same_inputs_produce_same_key():
    assert _key() == _key()


def test_different_params_produce_different_key():
    assert _key(params={"threshold": 1}) != _key(params={"threshold": 2})


def test_different_strategy_source_produces_different_key():
    assert _key(strategy_cls=_StrategyA) != _key(strategy_cls=_StrategyB)


def test_different_risk_config_produces_different_key():
    assert _key(risk={"initial_capital": 10000}) != _key(risk={"initial_capital": 5000})
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.cache'`

- [ ] **Step 3: 구현 작성**

`engine/cache.py`:
```python
"""
engine/cache.py

전략 실행 결과를 SQLite에 캐싱해 동일 조건 재실행을 피한다.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime


def compute_cache_key(
    strategy_cls: type,
    strategy_params: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    risk_config: dict,
) -> str:
    payload = {
        "strategy_source": inspect.getsource(strategy_cls),
        "strategy_params": strategy_params,
        "market": market,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "risk_config": risk_config,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: hash strategy source + params + data range into cache key"
```

---

### Task 8: SQLite 결과 저장/조회 (`save_result` / `load_result`)

**Files:**
- Modify: `engine/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: 없음 (독립적인 저장 계층)
- Produces: `save_result(run_id: str, strategy_name: str, strategy_params: dict, market: str, timeframe: str, start: datetime, end: datetime, risk_config: dict, result: dict) -> None`, `load_result(run_id: str) -> dict | None`, 모듈 레벨 `DB_PATH: Path`

`backtest_runs`(메타데이터)와 `backtest_results`(요약 지표 + equity_curve/trades를 JSON 컬럼으로) 2개 테이블로 단순화한다 — 설계 문서는 `trades`/`equity_curve`를 별도 테이블로 둘지 JSON 컬럼으로 둘지 "구현 단계에서 결정"하도록 남겨뒀고, 현재는 SQL로 개별 거래를 조회할 필요가 없으므로 JSON 컬럼이 더 단순하다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`에 추가:
```python
import engine.cache as cache_module
from engine.cache import load_result, save_result


def test_save_then_load_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    result = {
        "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
        "trades": [{"entryTime": "2026-01-01T00:00:00", "pnl": 5.0}],
        "final_value": 10500.0,
        "sharpe": 1.2,
        "max_drawdown": 3.4,
    }

    save_result(
        run_id="abc123",
        strategy_name="_StrategyA",
        strategy_params={"threshold": 1},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result=result,
    )

    loaded = load_result("abc123")

    assert loaded is not None
    assert loaded["final_value"] == 10500.0
    assert loaded["sharpe"] == 1.2
    assert loaded["max_drawdown"] == 3.4
    assert loaded["equity_curve"] == result["equity_curve"]
    assert loaded["trades"] == result["trades"]
    assert loaded["from_cache"] is True


def test_load_missing_run_id_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert load_result("does-not-exist") is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_result'`

- [ ] **Step 3: 구현 작성**

`engine/cache.py`에 추가:
```python
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "backtest_results.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    params_json TEXT NOT NULL,
    market TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    risk_config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_results (
    run_id TEXT PRIMARY KEY REFERENCES backtest_runs(id),
    final_value REAL,
    sharpe REAL,
    max_drawdown REAL,
    equity_curve_json TEXT NOT NULL,
    trades_json TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT final_value, sharpe, max_drawdown, equity_curve_json, trades_json "
            "FROM backtest_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    final_value, sharpe, max_drawdown, equity_curve_json, trades_json = row
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "from_cache": True,
    }


def save_result(
    run_id: str,
    strategy_name: str,
    strategy_params: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    risk_config: dict,
    result: dict,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_runs "
            "(id, strategy_name, params_json, market, timeframe, start, end, risk_config_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id,
                strategy_name,
                json.dumps(strategy_params, sort_keys=True),
                market,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                json.dumps(risk_config, sort_keys=True),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO backtest_results "
            "(run_id, final_value, sharpe, max_drawdown, equity_curve_json, trades_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result["final_value"],
                result["sharpe"],
                result["max_drawdown"],
                json.dumps(result["equity_curve"]),
                json.dumps(result["trades"]),
            ),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: persist and load backtest results from local sqlite file"
```

---

### Task 9: `run_backtest_cached()` 오케스트레이션

**Files:**
- Modify: `engine/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `compute_cache_key`, `load_result`, `save_result` (Task 7, 8), `engine.runner.run_backtest` (Task 6)
- Produces: `run_backtest_cached(df: pd.DataFrame, strategy_cls: type[bt.Strategy], risk_config: dict, market: str, timeframe: str, start: datetime, end: datetime, strategy_params: dict | None = None) -> dict` — 서브프로젝트 2의 최종 공개 인터페이스.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py`에 추가:
```python
import pandas as pd

from engine.cache import run_backtest_cached


def _synthetic_df() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": range(10),
            "high": range(10),
            "low": range(10),
            "close": range(10),
            "volume": [1.0] * 10,
        }
    )


def test_run_backtest_cached_hits_cache_on_second_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    call_count = {"n": 0}

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
        call_count["n"] += 1
        return {
            "equity_curve": [],
            "trades": [],
            "final_value": 10000.0,
            "sharpe": None,
            "max_drawdown": None,
        }

    monkeypatch.setattr(cache_module, "run_backtest", fake_run_backtest)

    kwargs = dict(
        df=_synthetic_df(),
        strategy_cls=_StrategyA,
        risk_config={"initial_capital": 10000},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        strategy_params={"threshold": 1},
    )

    first = run_backtest_cached(**kwargs)
    second = run_backtest_cached(**kwargs)

    assert call_count["n"] == 1
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["final_value"] == 10000.0


def test_run_backtest_cached_does_not_cache_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    def failing_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
        raise ValueError("전략 실행 실패")

    monkeypatch.setattr(cache_module, "run_backtest", failing_run_backtest)

    with pytest.raises(ValueError):
        run_backtest_cached(
            df=_synthetic_df(),
            strategy_cls=_StrategyA,
            risk_config={"initial_capital": 10000},
            market="KRW-BTC",
            timeframe="days",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        )

    run_id = compute_cache_key(
        _StrategyA, {}, "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
        {"initial_capital": 10000},
    )
    assert load_result(run_id) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_backtest_cached'`

- [ ] **Step 3: 구현 작성**

`engine/cache.py` 상단 import에 추가:
```python
import pandas as pd
import backtrader as bt

from engine.runner import run_backtest
```

`engine/cache.py` 끝에 추가:
```python
def run_backtest_cached(
    df: pd.DataFrame,
    strategy_cls: type[bt.Strategy],
    risk_config: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    strategy_params: dict | None = None,
) -> dict:
    strategy_params = strategy_params or {}
    run_id = compute_cache_key(
        strategy_cls, strategy_params, market, timeframe, start, end, risk_config
    )

    cached = load_result(run_id)
    if cached is not None:
        return cached

    result = run_backtest(df, strategy_cls, risk_config, strategy_params)
    save_result(
        run_id=run_id,
        strategy_name=strategy_cls.__name__,
        strategy_params=strategy_params,
        market=market,
        timeframe=timeframe,
        start=start,
        end=end,
        risk_config=risk_config,
        result=result,
    )
    result["from_cache"] = False
    return result
```

`tests/test_cache.py` 상단 import에 `import engine.cache as cache_module`가 이미 있어야 한다(Task 8에서 추가됨). `run_backtest`를 `cache_module.run_backtest`로 monkeypatch할 수 있도록 `engine/cache.py`에서 `from engine.runner import run_backtest` 형태로 이름을 모듈 네임스페이스에 바인딩해야 한다(위 import 그대로 사용하면 됨).

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_cache.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 전체 테스트 스위트 확인**

Run: `pytest -v`
Expected: PASS (모든 테스트, Task 0~9 누적)

- [ ] **Step 6: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: wire run_backtest_cached() orchestration with hit/miss/failure handling"
```

---

### Task 10: 수동 통합 스모크 테스트 (실제 Upbit API, 실제 전략)

이 태스크는 자동화된 pytest가 아니라, 엔지니어가 직접 실행해 눈으로 확인하는 수동 검증이다. 두 설계 문서 모두 "실제 마켓 1개, 짧은 최근 구간"으로 수동 스모크 테스트를 요구한다.

**Files:**
- Create: `scripts/smoke_test.py`

- [ ] **Step 1: 스모크 스크립트 작성**

`scripts/smoke_test.py`:
```python
"""
수동 통합 스모크 테스트.
Run: python scripts/smoke_test.py
"""
from datetime import datetime, timedelta, timezone

import backtrader as bt

from upbit_data_service import get_candles
from engine.cache import run_backtest_cached


class SmaCrossOnce(bt.Strategy):
    params = (("period", 5),)

    def __init__(self):
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.period)

    def next(self):
        if not self.position and self.data.close[0] > self.sma[0]:
            self.buy()


def main() -> None:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=30)

    print(f"[1/2] get_candles(KRW-BTC, days, {start.date()} ~ {end.date()})")
    df = get_candles("KRW-BTC", "days", start, end)
    print(f"  받아온 캔들 수: {len(df)}")
    print(df.head(3))
    print(df.tail(3))

    risk_config = {
        "initial_capital": 10_000_000,
        "commission_rate": 0.0005,
        "position_sizing": "percent",
        "position_size": 100,
    }

    print("[2/2] run_backtest_cached() 첫 실행 (miss 예상)")
    first = run_backtest_cached(
        df=df, strategy_cls=SmaCrossOnce, risk_config=risk_config,
        market="KRW-BTC", timeframe="days", start=start, end=end,
        strategy_params={"period": 5},
    )
    print(f"  from_cache={first['from_cache']}, final_value={first['final_value']}, "
          f"trades={len(first['trades'])}")

    print("[2/2] run_backtest_cached() 두 번째 실행 (hit 예상)")
    second = run_backtest_cached(
        df=df, strategy_cls=SmaCrossOnce, risk_config=risk_config,
        market="KRW-BTC", timeframe="days", start=start, end=end,
        strategy_params={"period": 5},
    )
    print(f"  from_cache={second['from_cache']}, final_value={second['final_value']}")

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["final_value"] == first["final_value"]
    print("스모크 테스트 통과")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행 및 육안 확인**

Run: `python scripts/smoke_test.py`

확인할 것:
- `data/cache/ohlcv/KRW-BTC_days.parquet` 파일이 생성되었는지
- `data/backtest_results.db` 파일이 생성되었는지
- 콘솔에 "스모크 테스트 통과" 출력, 두 번째 실행이 `from_cache=True`인지

- [ ] **Step 3: 커밋**

```bash
git add scripts/smoke_test.py
git commit -m "test: add manual end-to-end smoke test against real upbit api"
```

---

## Self-Review 결과

- **스펙 커버리지**: `2026-07-12-upbit-local-cache-design.md`의 lazy-fetch/부분 캐시/재시도/진행 중 캔들 제외는 Task 1~5, `2026-07-12-upbit-backtest-engine-design.md`의 backtrader 이식/캐시 키/SQLite 저장/실패 시 미캐싱은 Task 6~9에서 각각 구현됨.
- **스코프에서 의도적으로 제외한 것**: `stop_loss`/`take_profit`/`trailing_stop` 리스크 설정 필드는 `risk_config` dict를 그대로 통과시키기만 하고 `FractionalPercentSizer`/`run_backtest`에서 실제로 사용하지 않는다 — 원본 `backtesting_1`에도 해당 필드를 소비하는 로직이 없었고(파라미터만 받고 매수 수량 계산에만 반영), 이번 스펙도 명시적으로 요구하지 않았으므로 그대로 유지.
- **서브프로젝트 3(ML 모델링), 4(자동매매)**: 두 설계 문서 모두 범위 밖으로 명시 — 이 계획에도 포함하지 않음.
