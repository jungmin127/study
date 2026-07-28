# 공포/탐욕 지수(CMC) 외부 데이터 연동 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs/2026-07-28-fear-greed-index-external-data-design.md`에서 설계한 대로,
alternative.me(CMC) 공포/탐욕 지수를 외부 API에서 가져와 캐싱하고, 조건 빌더에 `FEAR_GREED_CMC` 지표로
등록한다.

**Architecture:** 새 파일 `external_data_service.py`(fetch + parquet 캐시 + `merge_fear_greed` 병합 함수)를
만들고, 기존 `engine/indicators/*.py` + `INDICATOR_FACTORY` + `INDICATOR_CATALOG` 파이프라인과 `engine/runner.py`
의 `build_data_feed_class`(N개 보조 라인 동적 피드, B 레이어 플랜에서 이미 일반화됨)를 그대로 재사용한다.
`engine/condition_tree.py`의 `AUX_MARKET_INDICATORS`/`required_aux_markets`(업비트의 다른 마켓 캔들 병합용)는
건드리지 않는다 — 공포탐욕지수는 캔들이 아니라 값 하나짜리 외부 API 데이터라 모양이 달라서, `backend/main.py`
에 이 지표 전용의 별도 병합 분기를 하나 추가하는 최소 통합으로 간다(스펙의 "아키텍처 A안").

**Tech Stack:** Python 3.11, FastAPI, httpx, pandas, backtrader, pytest / Next.js 14, TypeScript.

## Global Constraints

- 기존 pytest 테스트는 계속 100% 통과해야 한다.
- `npx tsc --noEmit` (frontend)이 항상 깨끗해야 한다.
- 카탈로그(백엔드) ↔ 지표 가이드 탭(프론트) ↔ 조건 빌더 카테고리 상수는 항상 같이 갱신한다(B 레이어 플랜에서
  확립된 컨벤션). 단 이번엔 카테고리 자체가 기존 "시장 심리"를 재사용하므로 `frontend/lib/indicator-categories.ts`
  수정은 필요 없다(이미 등록돼 있음, B 레이어 Task 10에서 확인된 패턴과 동일).
- 외부 HTTP 호출의 재시도/백오프 패턴은 `upbit_data_service.py`의 기존 패턴(429 시 지수 백오프, 실패 시
  `RuntimeError`로 통일된 메시지)을 그대로 따른다.
- 공포탐욕지수는 코인이 아니라 **날짜 구간** 기준으로 사용 가능 여부가 갈린다(2018-02-01 이전 데이터 없음).
  이 구간을 벗어나면 부분 데이터로 조용히 진행하지 않고 명확히 400 에러를 낸다 — 기존 aux-market 패턴의
  `.ffill().bfill()`과 달리, 이 지표는 결측이 남으면 그대로 에러로 취급한다.
- 커밋은 Task 단위로 작게 나눠서 한다.

---

## Task 1: 공포탐욕지수 수집·캐싱 서비스

**Files:**
- Create: `external_data_service.py`
- Test: `tests/test_external_data_service.py` (신규)

**Interfaces:**
- Produces: `get_fear_greed_cmc(start: datetime, end: datetime) -> pd.DataFrame`(컬럼 `[date, fear_greed_value]`,
  `date`는 UTC 00:00 정규화된 tz-aware `datetime64`). `merge_fear_greed(df: pd.DataFrame, fng_df: pd.DataFrame)
  -> pd.DataFrame`(Task 3에서 씀 — 이 Task에서 같이 구현).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_external_data_service.py` (신규 파일):
```python
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

    assert len(result) == 1
    assert result.iloc[0]["fear_greed_value"] == 20.0


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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_external_data_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'external_data_service'`

- [ ] **Step 3: 최소 구현 작성**

`external_data_service.py` (신규, 저장소 루트 — `upbit_data_service.py`와 같은 위치):
```python
"""
external_data_service.py

업비트 API가 아닌 외부 API에서 가져오는 시장 데이터(공포/탐욕 지수 등 C 레이어)를 조회·캐싱한다.
재시도/캐싱 패턴은 upbit_data_service.py를 따른다.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

CMC_FNG_URL = "https://api.alternative.me/fng/"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0

CACHE_DIR = Path(__file__).parent / "data" / "cache" / "external"

_FNG_COLUMNS = ["date", "fear_greed_value"]


def _fetch_fear_greed_all(client: httpx.Client) -> list[dict]:
    """alternative.me에서 전체 히스토리를 한 번에 받아온다(limit=0)."""
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(CMC_FNG_URL, params={"limit": 0})
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()["data"]
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"alternative.me 공포탐욕지수 API 호출 실패: {last_exc}")


def _parse_fear_greed(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_FNG_COLUMNS)

    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True).dt.normalize()
    df["fear_greed_value"] = df["value"].astype(float)
    return df[_FNG_COLUMNS].drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def _cache_path() -> Path:
    return CACHE_DIR / "fear_greed_cmc.parquet"


def _load_cache() -> pd.DataFrame:
    path = _cache_path()
    if not path.exists():
        return pd.DataFrame(columns=_FNG_COLUMNS)
    return pd.read_parquet(path)


def _save_cache(df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(), index=False)


def get_fear_greed_cmc(start: datetime, end: datetime) -> pd.DataFrame:
    """캐시가 오늘(UTC) 날짜를 포함하지 않으면(=하루 지났으면) 전체를 재조회해 덮어쓴다.
    이 API는 하루 1회 갱신되고 limit=0으로 전체 히스토리를 한 번에 받아오는 방식이라,
    upbit_data_service.py의 gap-fill 로직과 달리 "통째로 다시 받아서 덮어쓰기"가 더
    단순하고 안전하다."""
    cached = _load_cache()
    today = datetime.now(timezone.utc).date()

    if cached.empty or cached["date"].max().date() < today:
        with httpx.Client(timeout=15) as client:
            raw = _fetch_fear_greed_all(client)
        cached = _parse_fear_greed(raw)
        _save_cache(cached)

    start_date = start.date()
    end_date = end.date()
    mask = (cached["date"].dt.date >= start_date) & (cached["date"].dt.date <= end_date)
    return cached[mask].reset_index(drop=True)


def merge_fear_greed(df: pd.DataFrame, fng_df: pd.DataFrame) -> pd.DataFrame:
    """대상 코인 캔들(df, candle_time 컬럼 필요)에 공포탐욕지수(fng_df, date 컬럼)를
    merge_asof(direction="backward")로 병합한다 — 각 캔들 시각 기준 그 시각 이전(또는 당일)의
    가장 최근 지수값을 채워, 미래 데이터가 과거 캔들에 섞여드는 것(look-ahead bias)을 막는다.
    fng_df가 비어있거나 df의 가장 이른 캔들보다 늦게 시작하면 그 구간은 NaN으로 남는다 —
    호출부(backend/main.py)가 이 NaN을 보고 400 에러를 낸다."""
    if fng_df.empty:
        return df.assign(fear_greed_value=float("nan"))

    merged = pd.merge_asof(
        df.sort_values("candle_time").reset_index(drop=True),
        fng_df.sort_values("date").reset_index(drop=True),
        left_on="candle_time",
        right_on="date",
        direction="backward",
    )
    return merged.drop(columns="date")


__all__ = ["get_fear_greed_cmc", "merge_fear_greed"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_external_data_service.py -v`
Expected: PASS (13개 테스트)

- [ ] **Step 5: 커밋**

```bash
git add external_data_service.py tests/test_external_data_service.py
git commit -m "feat: add CMC fear & greed index fetch/cache service"
```

---

## Task 2: 지표 등록 (`FEAR_GREED_CMC`)

**Files:**
- Create: `engine/indicators/sentiment.py`
- Modify: `engine/indicators/__init__.py`
- Modify: `engine/runner.py`
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Consumes: `data.fear_greed_value` 라인(Task 3에서 `build_data_feed_class`가 채움 — 이 Task에서는 테스트가
  직접 `build_data_feed_class(("fear_greed_value",))`로 채워서 검증한다).
- Produces: `create_fear_greed_cmc(data, **params) -> bt.LineBuffer`(pass-through, `TRADE_VALUE`와 동일 패턴).
  `INDICATOR_FACTORY["FEAR_GREED_CMC"]`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 끝에 추가:
```python
def test_fear_greed_cmc_matches_raw_fear_greed_value_column():
    df = make_oscillating_df()
    fear_greed = pd.Series([30.0 + (i % 50) for i in range(len(df))])
    values = _run_probe_with_aux("FEAR_GREED_CMC", {}, "fear_greed_value", fear_greed)
    assert abs(values[-1] - fear_greed.iloc[-1]) < 1e-6
```
(파일 상단에 `import pandas as pd` 추가 필요 — 없으면 `pd.Series` 사용 시 `NameError`.)

같은 파일의 `_NEEDS_EXTRA_LINE` 집합(현재 `{"MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION"}`)에
`"FEAR_GREED_CMC"`를 추가해, 이 지표가 플레인 피드로 도는 `test_all_registered_indicators_produce_values`
스모크 테스트에서 제외되도록 한다(`TRADE_VALUE`가 `_NEEDS_TRADE_VALUE_LINE`으로 빠지는 것과 같은 이유 —
이 지표는 `fear_greed_value` 라인이 있어야만 동작한다):
```python
_NEEDS_EXTRA_LINE = {"MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION", "FEAR_GREED_CMC"}
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_indicators.py -k fear_greed -v`
Expected: FAIL — `KeyError: 'FEAR_GREED_CMC'`(아직 `INDICATOR_FACTORY`에 없음)

- [ ] **Step 3: 최소 구현 작성**

`engine/indicators/sentiment.py` (신규):
```python
"""
engine/indicators/sentiment.py

시장 심리 계열 지표 — 코인 자체가 아니라 외부 데이터 소스(공포/탐욕 지수 등)에서 값을 가져온다.
engine.runner의 build_data_feed_class가 채워주는 self.data.fear_greed_value 라인(백엔드가
external_data_service.get_fear_greed_cmc로 조회한 값을 병합한다)을 그대로 반환한다.
"""
from __future__ import annotations

import backtrader as bt


def create_fear_greed_cmc(data: bt.feeds.PandasData, **params) -> bt.LineBuffer:
    return data.fear_greed_value
```

`engine/indicators/__init__.py` — import 줄에 추가:
```python
from .sentiment import create_fear_greed_cmc
```
`INDICATOR_FACTORY` dict에 추가(`"PIVOT_S1": create_pivot_s1,` 다음 줄):
```python
    "FEAR_GREED_CMC": create_fear_greed_cmc,
```

`engine/runner.py`의 `_OPTIONAL_LINE_CANDIDATES`(현재 `("trade_value", *AUX_MARKET_LINE_NAME.values())`)를:
```python
_OPTIONAL_LINE_CANDIDATES: tuple[str, ...] = ("trade_value", "fear_greed_value", *AUX_MARKET_LINE_NAME.values())
```
로 교체 — 이래야 `run_backtest()`가 df에 `fear_greed_value` 컬럼이 있을 때 자동으로 피드에 라인을 붙인다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: 기존 테스트 전부 PASS + `test_fear_greed_cmc_matches_raw_fear_greed_value_column` PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/indicators/sentiment.py engine/indicators/__init__.py engine/runner.py tests/test_indicators.py
git commit -m "feat: register FEAR_GREED_CMC indicator (pass-through of fear_greed_value line)"
```

---

## Task 3: 백엔드 병합 로직

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py` (append)

**Interfaces:**
- Consumes: Task 1의 `get_fear_greed_cmc`/`merge_fear_greed`, Task 2의 `INDICATOR_FACTORY["FEAR_GREED_CMC"]`.
- Produces: `run_backtest_endpoint()`가 조건 트리에 `FEAR_GREED_CMC`가 있으면 자동으로 데이터를 병합.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 끝에 추가(파일 상단에 `from datetime import timedelta` 추가 필요 — 없으면 아래
테스트에서 `NameError`):
```python
def test_run_backtest_forward_fills_fear_greed_across_hourly_candles(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    hourly_df = make_oscillating_df()  # 300시간 = 2026-01-01 00:00 ~ 2026-01-13 11:00, UTC
    _patch_get_candles(monkeypatch, hourly_df)

    fng_df = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True), "fear_greed_value": [30.0, 70.0]}
    )
    monkeypatch.setattr(backend_module, "get_fear_greed_cmc", lambda start, end: fng_df)

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "FEAR_GREED_CMC", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post(
        "/api/v1/backtests/run", json=_run_request(buy_conditions=buy, timeframe="minutes60")
    )

    assert resp.status_code == 200
    merged = captured["df"].reset_index(drop=True)
    day1 = merged[merged["candle_time"] < pd.Timestamp("2026-01-02", tz="UTC")]
    day2plus = merged[merged["candle_time"] >= pd.Timestamp("2026-01-02", tz="UTC")]
    assert (day1["fear_greed_value"] == 30.0).all()
    assert (day2plus["fear_greed_value"] == 70.0).all()


def test_run_backtest_rejects_fear_greed_when_date_range_predates_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    old_df = make_oscillating_df()
    old_df["candle_time"] = pd.date_range("2017-01-01", periods=len(old_df), freq="h", tz="UTC")
    _patch_get_candles(monkeypatch, old_df)
    monkeypatch.setattr(
        backend_module, "get_fear_greed_cmc",
        lambda start, end: pd.DataFrame(columns=["date", "fear_greed_value"]),
    )

    buy = {"type": "AND", "conditions": [{"indicator": "FEAR_GREED_CMC", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(buy_conditions=buy, timeframe="minutes60", start="2017-01-01", end="2017-01-05"),
    )

    assert resp.status_code == 400
    assert "공포탐욕지수" in resp.json()["detail"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `pytest tests/test_backend.py -k fear_greed -v`
Expected: FAIL — 두 테스트 모두 200으로 응답(아직 `FEAR_GREED_CMC` 병합 로직이 없어 `merge_fear_greed`가
호출되지 않고, `find_unknown_indicators`도 통과하므로 그냥 지표값 없이 진행되다가 다른 에러가 나거나
`fear_greed_value` 컬럼 자체가 없어서 첫 번째 테스트는 `KeyError`로 실패).

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 import 줄들을:
```python
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period, required_aux_markets
from engine.runner import AUX_MARKET_LINE_NAME
```
다음으로 교체(`collect_blocks` 추가):
```python
from engine.condition_tree import (
    collect_blocks,
    find_unknown_indicators,
    is_empty,
    max_required_period,
    required_aux_markets,
)
from engine.runner import AUX_MARKET_LINE_NAME
from external_data_service import get_fear_greed_cmc, merge_fear_greed
```

`run_backtest_endpoint()`의 aux-market 병합 루프(`aux_markets = required_aux_markets(...)`부터
`df[line_name] = df[line_name].ffill().bfill()`까지) 바로 다음, `risk_config = {...}` 줄 이전에 추가:
```python
    fear_greed_indicators = {
        b["indicator"] for b in collect_blocks(buy_dict) + collect_blocks(sell_dict)
    }
    if "FEAR_GREED_CMC" in fear_greed_indicators:
        try:
            fng_df = get_fear_greed_cmc(start_dt, end_dt)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        df = merge_fear_greed(df, fng_df)
        if df["fear_greed_value"].isna().any():
            raise HTTPException(
                status_code=400,
                detail="이 조건에 필요한 공포탐욕지수 데이터가 해당 기간에 없습니다 (2018-02-01 이전 구간은 지원하지 않습니다)",
            )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전부 PASS

Run: `pytest tests/ -v`
Expected: 전체 스위트 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: merge FEAR_GREED_CMC data into backtest feed with day-boundary forward-fill"
```

---

## Task 4: 카탈로그 등록

**Files:**
- Modify: `backend/main.py` (`INDICATOR_CATALOG`)

**Interfaces:**
- Consumes: Task 2의 `INDICATOR_FACTORY["FEAR_GREED_CMC"]`.
- Produces: `GET /api/v1/indicators/catalog` 응답에 `FEAR_GREED_CMC` 항목 추가(카테고리 `"시장 심리"` 재사용).

- [ ] **Step 1: 실패하는 테스트 확인**

기존 테스트 `test_get_indicator_catalog_covers_all_registered_indicators`(수정 없이 그대로 재사용)는
`catalog_values == set(INDICATOR_FACTORY.keys()) | POSITION_RELATIVE_INDICATORS`를 검증하므로, Task 2에서
`INDICATOR_FACTORY`엔 `FEAR_GREED_CMC`가 생겼는데 `INDICATOR_CATALOG`엔 아직 없는 지금 시점에 이 테스트가
저절로 실패한다.

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `catalog_values`에 `FEAR_GREED_CMC`가 빠져 있어 set 비교 실패

카테고리 화이트리스트(`assert item["category"] in {...}`)는 `"시장 심리"`가 이미 포함돼 있어 수정 불필요.

- [ ] **Step 2: (Step 1에서 이미 실패 확인함 — 별도 실행 불필요)**

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `INDICATOR_CATALOG` 리스트에서 `"USDT_CORRELATION"` 항목 바로 뒤에 추가:
```python
    {
        "value": "FEAR_GREED_CMC", "label": "공포/탐욕 지수(CMC)", "category": "시장 심리",
        "params": [],
        "description": "alternative.me(CMC)가 산출하는 암호화폐 시장 전체의 공포/탐욕 지수(0~100)입니다. 코인과 무관하게 시장 전체에 적용되는 공통값이며, 2018-02-01 이전 구간은 데이터가 없어 그 이전 기간의 백테스트에는 이 지표를 쓸 수 없습니다.",
        "example": "연산자 <, 임계값 20이면: 시장 전체가 극단적 공포 상태(패닉 매도)인 구간을 포착해 역발상 매수 필터로 씁니다. 연산자 >, 임계값 80이면: 극단적 탐욕(과열) 상태를 포착해 매도 필터로 씁니다.",
    },
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py
git commit -m "feat: register FEAR_GREED_CMC in the catalog under 시장 심리 category"
```

---

## Task 5: 조건 빌더 프론트엔드 (threshold 추천값)

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: 백엔드 카탈로그의 `category: "시장 심리"`, `value: "FEAR_GREED_CMC"`(Task 4).

- [ ] **Step 1~2: (프론트 로직 테스트는 이 저장소에 별도 단위테스트 인프라가 없음 — 기존 컨벤션대로 Step 3
      구현 후 `tsc`+Playwright 수동 검증으로 대체한다.)**

- [ ] **Step 3: 구현**

`frontend/components/StrategyConditionBuilder.tsx`의 `OSCILLATOR_BOUNDS`(현재 `RSI`/`STOCH_K`/`STOCH_D`/`CCI`/
`WILLIAMS_R` 5개)에 추가:
```typescript
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
  FEAR_GREED_CMC: { low: 20, high: 80 },
};
```
(`recommendedThreshold()`가 `OSCILLATOR_BOUNDS`에 있는 지표는 연산자에 따라 `low`/`high`/중간값을 자동으로
추천하는 기존 로직을 그대로 타므로, 이 한 줄 추가만으로 `<`/`<=`면 20, `>`/`>=`면 80이 채워진다 — 별도
분기 추가 불필요.)

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

브라우저(Playwright)에서 `/`(조건 빌더)의 "시장 심리" 카테고리에 "공포/탐욕 지수(CMC)"가 뜨는지, 선택 시
연산자를 `<`로 두면 threshold가 20으로, `>`로 바꾸면 80으로 자동 채워지는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add FEAR_GREED_CMC threshold recommendation to condition builder"
```

---

## Task 6: 지표 가이드 탭 콘텐츠

**Files:**
- Modify: `frontend/lib/guide-sample-data.ts`
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Produces: `guide-sample-data.ts`에 `SAMPLE_FEAR_GREED: number[]`(길이 60, `SAMPLE_BARS`와 같은 `bar` 인덱스에
  대응하는 0~100 합성 시계열) 추가.

- [ ] **Step 1~2: (지표 가이드 탭도 별도 단위테스트가 없는 순수 프레젠테이션 레이어 — 기존 컨벤션대로
      `tsc`+Playwright로 검증한다. Step 3 이후로 진행.)**

- [ ] **Step 3: 구현**

`frontend/lib/guide-sample-data.ts`의 `buildBtcCloseSeries` 함수 뒤, `const closeSeries = buildCloseSeries();`
줄 앞에 추가:
```typescript
function buildFearGreedSeries(): number[] {
  const values: number[] = [];
  for (let i = 0; i < TOTAL_BARS; i++) {
    const wave = 35 * Math.sin((2 * Math.PI * i) / 20) + 15 * Math.sin((2 * Math.PI * i) / 7);
    values.push(Math.max(0, Math.min(100, Math.round(50 + wave))));
  }
  return values;
}
```
`const btcCloseSeries = buildBtcCloseSeries();` 다음 줄에 추가:
```typescript
const fearGreedSeries = buildFearGreedSeries();
```
파일 끝(`HAND_VERIFIED_BAR_COUNT` 앞)에 추가:
```typescript
/** 공포탐욕지수는 코인 캔들과 무관한 고정 시계열이라 SAMPLE_BARS의 bar 인덱스에 맞춰 별도 배열로 둔다. */
export const SAMPLE_FEAR_GREED: number[] = fearGreedSeries;
```

`frontend/lib/indicator-guide.ts`의 `INDICATOR_GUIDE` 객체에서 `USDT_CORRELATION` 항목(파일 마지막 항목) 바로
뒤, 객체를 닫는 `};` 앞에 추가:
```typescript
  FEAR_GREED_CMC: {
    meaning: 'alternative.me(CMC)가 산출하는 암호화폐 시장 전체의 공포/탐욕 지수입니다. 0에 가까울수록 극단적 공포(패닉), 100에 가까울수록 극단적 탐욕(과열) 상태를 뜻합니다. 코인과 무관하게 시장 전체에 적용되는 공통값입니다.',
    params: [],
    formula: '거래소가 아니라 alternative.me가 변동성·거래량·소셜미디어·설문조사·도미넌스·검색트렌드 등을 조합해 산출하는 제3자 합성지수입니다. 이 앱은 산출된 값을 그대로 가져와 쓸 뿐, 직접 계산하지 않습니다.',
    thresholdExample: '값은 0~100 범위입니다. 예: 임계값 20, 연산자 "<"면 시장 전체가 극단적 공포 상태인 구간을, 임계값 80, 연산자 ">"면 극단적 탐욕(과열) 상태인 구간을 포착합니다.',
    usage: '패닉 매도 국면(극단적 공포)에서 역발상 매수, 과열 국면(극단적 탐욕)에서 이익 실현 매도 필터로 흔히 씁니다. 다른 코인 고유 지표(RSI, 이동평균 등)와 AND로 묶어 "시장 심리 + 개별 코인 신호"를 함께 보는 용도로도 씁니다. 2018-02-01 이전 구간은 데이터가 없어 백테스트에 쓸 수 없습니다.',
  },
```

`frontend/lib/indicator-example-builder.ts`의 import 줄을:
```typescript
import { SAMPLE_BARS, SAMPLE_BTC, type SampleBar } from '@/lib/guide-sample-data';
```
다음으로 교체:
```typescript
import { SAMPLE_BARS, SAMPLE_BTC, SAMPLE_FEAR_GREED, type SampleBar } from '@/lib/guide-sample-data';
```
`buildGuideExample` switch문의 `case 'MOMENTUM_PCT': { ... }` 블록이 끝나는 닫는 중괄호(`}`) 바로 뒤,
`case 'STOP_LOSS_PCT':` 시작 줄 바로 앞에 추가:
```typescript
    case 'FEAR_GREED_CMC': {
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { fearGreed: n(SAMPLE_FEAR_GREED[i], 0) },
      }));
      const gauge = gaugeExample(
        SAMPLE_FEAR_GREED,
        0,
        100,
        [
          { from: 0, to: 20, color: '#10b981', label: '공포(<20)' },
          { from: 20, to: 80, color: '#94a3b8', label: '중립' },
          { from: 80, to: 100, color: '#ef4444', label: '탐욕(>80)' },
        ],
        '공포탐욕지수'
      );
      return {
        columns: [{ key: 'fearGreed', label: '공포탐욕지수' }],
        rows,
        chart: gauge.chart,
      };
    }
```

- [ ] **Step 4: 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Playwright로 `/guide`를 열어 "시장 심리" 중분류에 "공포/탐욕 지수(CMC)"가 뜨는지, 클릭 시 표 + 게이지
차트(0~100, 20/80 구간 색상 구분)가 정상 렌더되는지 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/guide-sample-data.ts frontend/lib/indicator-guide.ts frontend/lib/indicator-example-builder.ts
git commit -m "feat: add FEAR_GREED_CMC to the indicator guide tab"
```

---

## 이 플랜에 포함하지 않은 것

`docs/superpowers/specs/2026-07-28-fear-greed-index-external-data-design.md`의 "이 스펙에 포함하지 않은 것"
절과 동일한 이유로 범위 밖이다 — 각각 별도 스펙·플랜이 필요하다.

- **업비트 데이터랩 공포탐욕지수(`FEAR_GREED_UPBIT`)**: 실제 JSON API 엔드포인트 리버스엔지니어링 필요 +
  코인별로 다른 지수라 "특정 코인만 사용 불가" 처리 로직이 별도로 필요.
- **김치프리미엄**: 코인마다 바이낸스 USDT 페어 존재 여부가 갈려 심볼 매핑과 skip/notify 로직이 핵심
  설계 과제.
- **시가총액(코인별 raw market cap)**: 업비트 티커→CoinGecko coin-id 매핑이 새로 필요.
- **온체인 데이터, 선물 미결제약정/펀딩비, 구글 트렌드 검색량**: 우선순위 밖, 나중에 각각 별도 스펙.

## Verification (전체)

- `pytest tests/ -v` — 전체 스위트 그린(기존 스위트 + 이번 플랜 신규 ~18개).
- `cd frontend && npx tsc --noEmit` — 클린.
- Playwright: `/`에서 "시장 심리" 카테고리에 "공포/탐욕 지수(CMC)"가 뜨고 실제 조건으로 백테스트 1건을
  끝까지 실행해 결과 화면까지 나오는지(2018-02-01 이후 구간으로). `/guide`에서 신규 항목이 표+게이지
  차트와 함께 렌더되는지.
- 백엔드는 코드 수정마다 재시작 필요(`uvicorn --reload`가 이 저장소에서 간헐적으로 안 먹는 이슈가 기존에
  있었음 — 반드시 수동 재시작 후 확인).
