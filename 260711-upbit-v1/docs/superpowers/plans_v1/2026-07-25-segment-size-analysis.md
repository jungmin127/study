# 세그먼트(규모) 분석 배치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전체 KRW 마켓 코인을 거래대금·변동성 기준으로 대형주/중형주/잡주로 분류해 SQLite에 저장하고, 서버 기동 시 백그라운드로 자동 갱신하며, `/analysis` 페이지의 `세그먼트(규모)` 카드에서 결과를 보여준다.

**Architecture:** `upbit_data_service.py`에서 업비트 원본 데이터(티커, 유의종목 플래그, 캔들)를 가져오고, 새 모듈 `engine/segment_analysis.py`가 거래대금·변동성 percentile을 계산해 대형주/중형주/잡주로 분류한 뒤 `engine/cache.py`의 기존 SQLite DB에 저장한다. `backend/main.py`는 FastAPI startup 훅에서 백그라운드 스레드로 배치를 실행하고, 조회용 GET 엔드포인트를 노출한다. 프론트엔드는 이 엔드포인트를 서버 컴포넌트에서 호출해 카드로 렌더링한다.

**Tech Stack:** Python 3.11 / FastAPI 0.109 (`@app.on_event`) / pandas / httpx / SQLite (`engine/cache.py`) / Next.js 14 App Router / TypeScript / Tailwind CSS v3.

## Global Constraints

- 시가총액(유통량×가격)은 업비트 API가 제공하지 않는다 — 절대 사용하지 말 것. 규모 대리지표는 `acc_trade_price_24h`(24시간 누적 거래대금)만 쓴다.
- 변동성은 `days` 타임프레임 캔들의 최근 30일 종가 기준 일별 수익률 표준편차로 계산한다.
- 세그먼트 컷: 대형주 = 거래대금 percentile ≥ 70 **and** 변동성 percentile ≤ 50. 잡주 = 거래대금 percentile < 30 **and** 변동성 percentile > 50. 그 외 = 중형주. percentile은 0~100이며 값이 클수록 상위(거래대금은 클수록, 변동성은 클수록 큰 percentile).
- 유의종목 플래그(`market_event.warning` 또는 `caution` 중 하나라도 true)는 분류 점수에 반영하지 않고 `is_caution` 배지로만 노출한다.
- 배치 실행마다 `segment_classification` 테이블을 통째로 교체한다(히스토리 보관 없음).
- 배치는 FastAPI startup 이벤트에서 **백그라운드 스레드**로 실행해 서버 기동을 막지 않는다.
- 백엔드 코드는 `from __future__ import annotations`를 유지하고, 기존 파일의 타입힌트/시그니처 스타일(`list[dict]`, `dict[str, bool]` 등)을 따른다.
- 프론트엔드에는 테스트 러너가 없다(`package.json`에 test 스크립트 없음) — 프론트 변경은 `npx tsc --noEmit`과 Playwright 브라우저 확인으로 검증한다.
- 모든 사용자 노출 문구는 한국어.

---

## File Structure

- **Modify** `upbit_data_service.py` — `get_market_cautions()` 추가(업비트 공식 유의종목 플래그 조회).
- **Modify** `engine/cache.py` — `segment_classification` 테이블 스키마, `save_segment_classification()`, `list_segment_classification()` 추가.
- **Create** `engine/segment_analysis.py` — percentile 계산, 분류 규칙(`_classify`), 변동성 계산(`_compute_volatility`), 배치 진입점 `run_segment_batch()`.
- **Modify** `backend/main.py` — startup 훅(백그라운드 스레드로 `run_segment_batch()` 실행), `GET /api/v1/analysis/segments/size` 엔드포인트.
- **Modify** `frontend/lib/types/eda.ts` — `SegmentSizeEntry` 타입.
- **Modify** `frontend/lib/api/eda.ts` — `getSegmentSizeAnalysis()`.
- **Create** `frontend/components/SegmentSizeCard.tsx` — 대형주/중형주/잡주 그룹 테이블 렌더링, `groupBySegment()` export.
- **Modify** `frontend/app/analysis/page.tsx` — `세그먼트(규모)` 카드를 `SegmentSizeCard`로 교체(데이터는 이 페이지에서 fetch).
- **Modify** `tests/test_upbit_data_service.py`, `tests/test_cache.py`, `tests/test_backend.py` — 위 변경에 대한 테스트 추가.
- **Create** `tests/test_segment_analysis.py` — `engine/segment_analysis.py` 테스트.

---

### Task 1: `upbit_data_service.get_market_cautions()`

**Files:**
- Modify: `upbit_data_service.py` (파일 끝, `get_current_prices` 함수 뒤에 추가)
- Test: `tests/test_upbit_data_service.py`

**Interfaces:**
- Produces: `get_market_cautions() -> dict[str, bool]` — KRW 마켓 코드 → 유의종목 지정 여부(`market_event.warning` 또는 `caution` 중 하나라도 true).

- [ ] **Step 1: Write the failing test**

`tests/test_upbit_data_service.py` 맨 아래에 추가:

```python
def test_get_market_cautions_flags_warning_or_any_caution(monkeypatch):
    import upbit_data_service

    class _FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    def _fake_get(url, params=None, timeout=None):
        assert "market/all" in url
        assert params == {"isDetails": "true"}
        return _FakeResponse([
            {
                "market": "KRW-BTC",
                "market_event": {
                    "warning": False,
                    "caution": {
                        "PRICE_FLUCTUATIONS": False,
                        "TRADING_VOLUME_SOARING": False,
                        "DEPOSIT_AMOUNT_SOARING": False,
                        "GLOBAL_PRICE_DIFFERENCES": False,
                        "CONCENTRATION_OF_SMALL_ACCOUNTS": False,
                    },
                },
            },
            {
                "market": "KRW-XXX",
                "market_event": {
                    "warning": False,
                    "caution": {
                        "PRICE_FLUCTUATIONS": False,
                        "TRADING_VOLUME_SOARING": False,
                        "DEPOSIT_AMOUNT_SOARING": False,
                        "GLOBAL_PRICE_DIFFERENCES": False,
                        "CONCENTRATION_OF_SMALL_ACCOUNTS": True,
                    },
                },
            },
            {
                "market": "KRW-WARN",
                "market_event": {"warning": True, "caution": {}},
            },
            {"market": "BTC-ETH", "market_event": {"warning": True, "caution": {}}},
        ])

    monkeypatch.setattr(upbit_data_service.httpx, "get", _fake_get)

    cautions = upbit_data_service.get_market_cautions()
    assert cautions == {"KRW-BTC": False, "KRW-XXX": True, "KRW-WARN": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_upbit_data_service.py::test_get_market_cautions_flags_warning_or_any_caution -v`
Expected: FAIL with `AttributeError: module 'upbit_data_service' has no attribute 'get_market_cautions'`

- [ ] **Step 3: Write minimal implementation**

`upbit_data_service.py` 맨 끝(`get_current_prices` 함수 뒤)에 추가:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_upbit_data_service.py::test_get_market_cautions_flags_warning_or_any_caution -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add upbit_data_service.py tests/test_upbit_data_service.py
git commit -m "feat: 업비트 유의종목 플래그 조회 함수 추가"
```

---

### Task 2: `engine/cache.py` — `segment_classification` 저장/조회

**Files:**
- Modify: `engine/cache.py:46` (스키마 추가 지점 — 기존 `_SCHEMA += """..."""` 블록 뒤), 파일 끝(함수 추가)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: 없음(독립 모듈).
- Produces:
  - `save_segment_classification(rows: list[dict]) -> None` — `rows`의 각 dict는 `market, korean_name, segment, trade_value_24h, volatility_30d, trade_value_percentile, volatility_percentile, is_caution, computed_at` 키를 가짐. 호출마다 테이블 전체를 교체한다.
  - `list_segment_classification() -> list[dict]` — 같은 키 구조의 dict 리스트, `trade_value_24h` 내림차순 정렬.

- [ ] **Step 1: Write the failing test**

`tests/test_cache.py` 상단 import 블록(10~17행)을 다음으로 교체:

```python
from engine.cache import (
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_segment_classification,
    list_sweep_history,
    save_segment_classification,
    save_sweep_result,
)
```

파일 맨 아래에 추가:

```python
def _sample_segment_row(**overrides) -> dict:
    row = {
        "market": "KRW-BTC",
        "korean_name": "비트코인",
        "segment": "large",
        "trade_value_24h": 45_700_000_000.0,
        "volatility_30d": 0.012,
        "trade_value_percentile": 99.0,
        "volatility_percentile": 10.0,
        "is_caution": False,
        "computed_at": "2026-07-25T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_save_and_list_segment_classification_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_segment_classification([
        _sample_segment_row(),
        _sample_segment_row(
            market="KRW-XXX", korean_name="잡코인", segment="junk",
            trade_value_24h=1_000_000.0, volatility_30d=0.09,
            trade_value_percentile=2.0, volatility_percentile=95.0, is_caution=True,
        ),
    ])

    rows = list_segment_classification()
    assert [r["market"] for r in rows] == ["KRW-BTC", "KRW-XXX"]
    assert rows[0]["segment"] == "large"
    assert rows[0]["is_caution"] is False
    assert rows[1]["is_caution"] is True
    assert rows[1]["trade_value_percentile"] == 2.0


def test_save_segment_classification_replaces_previous_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_segment_classification([_sample_segment_row(market="KRW-OLD")])
    save_segment_classification([_sample_segment_row(market="KRW-NEW")])

    rows = list_segment_classification()
    assert [r["market"] for r in rows] == ["KRW-NEW"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py::test_save_and_list_segment_classification_round_trips -v`
Expected: FAIL with `ImportError: cannot import name 'save_segment_classification'`

- [ ] **Step 3: Write minimal implementation**

`engine/cache.py:46`의 `_SCHEMA += """..."""` (sweep_history) 블록 바로 뒤에 추가:

```python
_SCHEMA += """
CREATE TABLE IF NOT EXISTS segment_classification (
    market TEXT PRIMARY KEY,
    korean_name TEXT NOT NULL,
    segment TEXT NOT NULL,
    trade_value_24h REAL,
    volatility_30d REAL,
    trade_value_percentile REAL,
    volatility_percentile REAL,
    is_caution INTEGER NOT NULL,
    computed_at TEXT NOT NULL
);
"""
```

파일 맨 끝(`list_backtest_runs` 함수 뒤)에 추가:

```python
def save_segment_classification(rows: list[dict]) -> None:
    """세그먼트(규모) 분류 결과를 저장한다. 배치 실행마다 테이블을 통째로 교체한다
    (과거 분류 이력은 보관하지 않고 항상 최신 1회분만 유지)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM segment_classification")
        conn.executemany(
            "INSERT INTO segment_classification "
            "(market, korean_name, segment, trade_value_24h, volatility_30d, "
            " trade_value_percentile, volatility_percentile, is_caution, computed_at) "
            "VALUES (:market, :korean_name, :segment, :trade_value_24h, :volatility_30d, "
            " :trade_value_percentile, :volatility_percentile, :is_caution, :computed_at)",
            [{**r, "is_caution": int(r["is_caution"])} for r in rows],
        )
        conn.commit()
    finally:
        conn.close()


def list_segment_classification() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT market, korean_name, segment, trade_value_24h, volatility_30d, "
            "       trade_value_percentile, volatility_percentile, is_caution, computed_at "
            "FROM segment_classification "
            "ORDER BY trade_value_24h DESC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "market": r[0],
            "korean_name": r[1],
            "segment": r[2],
            "trade_value_24h": r[3],
            "volatility_30d": r[4],
            "trade_value_percentile": r[5],
            "volatility_percentile": r[6],
            "is_caution": bool(r[7]),
            "computed_at": r[8],
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache.py -k segment_classification -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: 세그먼트(규모) 분류 결과 저장/조회 테이블 추가"
```

---

### Task 3: `engine/segment_analysis.py` — 분류 로직 + 배치 진입점

**Files:**
- Create: `engine/segment_analysis.py`
- Test: `tests/test_segment_analysis.py` (신규)

**Interfaces:**
- Consumes:
  - `upbit_data_service.get_krw_markets_with_ticker() -> list[dict]` (기존, `market/korean_name/trade_price_24h` 등 포함)
  - `upbit_data_service.get_candles(market: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame` (기존, `close` 컬럼 포함, `candle_time` 오름차순)
  - `upbit_data_service.get_market_cautions() -> dict[str, bool]` (Task 1)
  - `engine.cache.save_segment_classification(rows: list[dict]) -> None` (Task 2)
- Produces:
  - `run_segment_batch() -> int` — 저장한 행 수를 반환. Task 4(백엔드 startup 훅)가 이 함수를 그대로 스레드 타깃으로 사용.
  - `_classify(trade_value_pct: float | None, volatility_pct: float | None) -> str` — `"large" | "mid" | "junk"`.
  - `_percentile_rank(values: list[float | None]) -> list[float | None]`.
  - `_compute_volatility(market: str) -> float | None`.

- [ ] **Step 1: Write the failing test**

`tests/test_segment_analysis.py` 신규 생성:

```python
from datetime import datetime, timezone

import pandas as pd
import pytest

import engine.segment_analysis as segment_analysis_module
from engine.segment_analysis import _classify, _compute_volatility, run_segment_batch


def test_classify_large_cap_requires_high_trade_value_and_low_volatility():
    assert _classify(trade_value_pct=80.0, volatility_pct=30.0) == "large"


def test_classify_junk_requires_low_trade_value_and_high_volatility():
    assert _classify(trade_value_pct=10.0, volatility_pct=80.0) == "junk"


def test_classify_mid_for_everything_else():
    assert _classify(trade_value_pct=50.0, volatility_pct=50.0) == "mid"
    assert _classify(trade_value_pct=90.0, volatility_pct=90.0) == "mid"
    assert _classify(trade_value_pct=10.0, volatility_pct=10.0) == "mid"


def test_classify_falls_back_to_mid_when_percentile_missing():
    assert _classify(trade_value_pct=None, volatility_pct=80.0) == "mid"
    assert _classify(trade_value_pct=80.0, volatility_pct=None) == "mid"


def test_percentile_rank_orders_values_from_zero_to_hundred():
    result = segment_analysis_module._percentile_rank([10.0, 30.0, 20.0])
    assert result[1] == 100.0
    assert result[0] < result[2] < result[1]


def test_percentile_rank_keeps_none_as_none():
    result = segment_analysis_module._percentile_rank([10.0, None, 20.0])
    assert result[1] is None
    assert result[0] is not None and result[2] is not None


def test_compute_volatility_matches_pandas_pct_change_std(monkeypatch):
    closes = [100.0, 102.0, 101.0, 105.0, 103.0]
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-07-01", periods=len(closes), freq="D", tz="UTC"),
        "close": closes,
    })

    def _fake_get_candles(market, timeframe, start, end):
        assert market == "KRW-BTC"
        assert timeframe == "days"
        return df

    monkeypatch.setattr(segment_analysis_module, "get_candles", _fake_get_candles)

    expected = pd.Series(closes).pct_change().dropna().std()
    assert _compute_volatility("KRW-BTC") == pytest.approx(expected)


def test_compute_volatility_returns_none_when_not_enough_candles(monkeypatch):
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-07-01", periods=1, freq="D", tz="UTC"),
        "close": [100.0],
    })
    monkeypatch.setattr(segment_analysis_module, "get_candles", lambda *a, **k: df)

    assert _compute_volatility("KRW-NEW") is None


def test_run_segment_batch_classifies_and_saves_all_markets(monkeypatch):
    markets = [
        {"market": f"KRW-M{i}", "korean_name": f"코인{i}", "trade_price_24h": i * 1_000_000_000.0}
        for i in range(1, 11)
    ]
    volatilities = {f"KRW-M{i}": (11 - i) * 0.01 for i in range(1, 11)}
    cautions = {f"KRW-M{i}": (i == 1) for i in range(1, 11)}

    monkeypatch.setattr(segment_analysis_module, "get_krw_markets_with_ticker", lambda: markets)
    monkeypatch.setattr(segment_analysis_module, "get_market_cautions", lambda: cautions)
    monkeypatch.setattr(
        segment_analysis_module, "_compute_volatility", lambda market: volatilities[market]
    )

    saved: dict = {}
    monkeypatch.setattr(
        segment_analysis_module,
        "save_segment_classification",
        lambda rows: saved.setdefault("rows", rows),
    )

    count = run_segment_batch()

    assert count == 10
    rows_by_market = {r["market"]: r for r in saved["rows"]}
    assert rows_by_market["KRW-M10"]["segment"] == "large"
    assert rows_by_market["KRW-M1"]["segment"] == "junk"
    assert rows_by_market["KRW-M1"]["is_caution"] is True
    assert rows_by_market["KRW-M10"]["is_caution"] is False
    assert rows_by_market["KRW-M5"]["segment"] == "mid"
    assert all(isinstance(r["computed_at"], str) for r in saved["rows"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_segment_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.segment_analysis'`

- [ ] **Step 3: Write minimal implementation**

`engine/segment_analysis.py` 신규 생성:

```python
"""
engine/segment_analysis.py

세그먼트(규모) 분석: KRW 마켓 코인을 24시간 거래대금과 30일 변동성 기준으로
대형주/중형주/잡주로 분류하고 SQLite(engine.cache)에 저장한다.

업비트 API는 시가총액(유통량 x 가격)을 제공하지 않으므로, 거래대금을 규모의
대리지표로 쓴다. 변동성이 높을수록, 거래대금이 낮을수록 "잡주"에 가깝다고 보는
분류 규칙은 도메인 설계 문서
docs/superpowers/specs_v1/2026-07-25-segment-size-analysis-design.md 참고.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from engine.cache import save_segment_classification
from upbit_data_service import get_candles, get_krw_markets_with_ticker, get_market_cautions

VOLATILITY_WINDOW_DAYS = 30
LARGE_CAP_TRADE_VALUE_PERCENTILE = 70.0
LARGE_CAP_VOLATILITY_PERCENTILE = 50.0
JUNK_TRADE_VALUE_PERCENTILE = 30.0
JUNK_VOLATILITY_PERCENTILE = 50.0


def _compute_volatility(market: str) -> float | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=VOLATILITY_WINDOW_DAYS + 5)
    df = get_candles(market, "days", start, end)
    if len(df) < 2:
        return None
    closes = df["close"].tail(VOLATILITY_WINDOW_DAYS + 1)
    returns = closes.pct_change().dropna()
    if returns.empty:
        return None
    return float(returns.std())


def _percentile_rank(values: list[float | None]) -> list[float | None]:
    """값이 클수록 큰 percentile(0~100)을 부여한다. None은 그대로 None 유지."""
    series = pd.Series(values, dtype="float64")
    ranked = series.rank(pct=True) * 100
    return [None if pd.isna(v) else float(v) for v in ranked]


def _classify(trade_value_pct: float | None, volatility_pct: float | None) -> str:
    if trade_value_pct is None or volatility_pct is None:
        return "mid"
    if (
        trade_value_pct >= LARGE_CAP_TRADE_VALUE_PERCENTILE
        and volatility_pct <= LARGE_CAP_VOLATILITY_PERCENTILE
    ):
        return "large"
    if trade_value_pct < JUNK_TRADE_VALUE_PERCENTILE and volatility_pct > JUNK_VOLATILITY_PERCENTILE:
        return "junk"
    return "mid"


def run_segment_batch() -> int:
    """모든 KRW 마켓을 대형주/중형주/잡주로 분류해 저장한다. 저장한 행 수를 반환한다."""
    markets = get_krw_markets_with_ticker()
    cautions = get_market_cautions()

    trade_values = [m["trade_price_24h"] for m in markets]
    volatilities = [_compute_volatility(m["market"]) for m in markets]

    trade_value_percentiles = _percentile_rank(trade_values)
    volatility_percentiles = _percentile_rank(volatilities)

    computed_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for m, trade_value, volatility, tv_pct, vol_pct in zip(
        markets, trade_values, volatilities, trade_value_percentiles, volatility_percentiles
    ):
        rows.append({
            "market": m["market"],
            "korean_name": m["korean_name"],
            "segment": _classify(tv_pct, vol_pct),
            "trade_value_24h": trade_value,
            "volatility_30d": volatility,
            "trade_value_percentile": tv_pct,
            "volatility_percentile": vol_pct,
            "is_caution": cautions.get(m["market"], False),
            "computed_at": computed_at,
        })

    save_segment_classification(rows)
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_segment_analysis.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add engine/segment_analysis.py tests/test_segment_analysis.py
git commit -m "feat: 세그먼트(규모) 분류 배치 로직 추가"
```

---

### Task 4: `backend/main.py` — startup 배치 트리거 + 조회 API

**Files:**
- Modify: `backend/main.py:9-33`(import), `backend/main.py:54`(CORS 미들웨어 뒤 startup 훅 추가), `backend/main.py:207-209`(`get_markets` 뒤에 신규 엔드포인트 추가)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.segment_analysis.run_segment_batch` (Task 3), `engine.cache.list_segment_classification` (Task 2).
- Produces: `GET /api/v1/analysis/segments/size -> list[dict]`.

- [ ] **Step 1: Write the failing test**

`tests/test_backend.py` 상단에 `import threading` 추가 없이(스레드 자체는 백엔드 모듈이 소유), 파일 상단 import 구역에 아래 한 줄 추가(기존 `from engine.cache import save_result, save_sweep_result` 다음 줄):

```python
from engine.cache import save_segment_classification
```

파일 맨 아래에 추가:

```python
def test_get_segment_size_analysis_returns_saved_rows(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_segment_classification([
        {
            "market": "KRW-BTC",
            "korean_name": "비트코인",
            "segment": "large",
            "trade_value_24h": 45_700_000_000.0,
            "volatility_30d": 0.012,
            "trade_value_percentile": 99.0,
            "volatility_percentile": 10.0,
            "is_caution": False,
            "computed_at": "2026-07-25T00:00:00+00:00",
        },
    ])

    resp = client.get("/api/v1/analysis/segments/size")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["market"] == "KRW-BTC"
    assert body[0]["segment"] == "large"
    assert body[0]["is_caution"] is False


def test_get_segment_size_analysis_returns_empty_list_before_first_batch(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/analysis/segments/size")

    assert resp.status_code == 200
    assert resp.json() == []


def test_startup_event_spawns_segment_batch_as_daemon_thread(monkeypatch):
    calls = []

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            calls.append((target, daemon))

        def start(self):
            pass

    monkeypatch.setattr(backend_module.threading, "Thread", _FakeThread)

    with TestClient(app):
        pass

    assert calls == [(backend_module.run_segment_batch, True)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend.py -k segment_size_analysis -v`
Expected: FAIL — `/api/v1/analysis/segments/size` returns 404 (route not registered)

Run: `pytest tests/test_backend.py::test_startup_event_spawns_segment_batch_as_daemon_thread -v`
Expected: FAIL with `AttributeError: module 'backend.main' has no attribute 'threading'`

- [ ] **Step 3: Write minimal implementation**

`backend/main.py:9-33`(import 블록)을 아래로 교체 — `import threading` 추가, `engine.cache` import 블록에 `list_segment_classification` 추가, `engine.segment_analysis` import 추가:

```python
import threading
from datetime import datetime, timezone
from typing import Literal, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.cache import (
    delete_backtest_run,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_segment_classification,
    list_sweep_history,
    load_result,
    run_backtest_cached,
)
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period
from engine.live_valuation import has_revaluable_open_trade, revalue_open_trades
from engine.metrics import calculate_metrics
from engine.segment_analysis import run_segment_batch
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles, get_current_prices, get_krw_markets, get_krw_markets_with_ticker
```

`backend/main.py:54`(`app.add_middleware(...)` 블록의 닫는 `)` 바로 뒤)에 추가:

```python

@app.on_event("startup")
def _start_segment_batch() -> None:
    """세그먼트(규모) 분류 배치를 백그라운드 스레드로 실행한다.

    코인마다 캔들 조회가 필요해 271개 KRW 마켓 기준 1~2분 걸린다(요청당 rate-limit
    딜레이 0.15초). 서버 기동을 이 시간만큼 막지 않기 위해 별도 스레드로 돌린다."""
    threading.Thread(target=run_segment_batch, daemon=True).start()
```

`backend/main.py:207-209`(`get_markets` 함수) 바로 뒤에 추가:

```python

@app.get("/api/v1/analysis/segments/size")
def get_segment_size_analysis() -> list[dict]:
    return list_segment_classification()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (전체 기존 테스트 포함 — 신규 startup 훅이 `with`문 없는 기존 `TestClient(app)` 호출에는 영향을 주지 않는다)

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 서버 기동 시 세그먼트(규모) 배치 실행 + 조회 API 추가"
```

---

### Task 5: 프론트엔드 API 클라이언트

**Files:**
- Modify: `frontend/lib/types/eda.ts:71-79`(`Market` 인터페이스 뒤)
- Modify: `frontend/lib/api/eda.ts:1-16`(타입 import, `getMarkets` 근처에 함수 추가— 파일에 `getMarkets`가 없다면 아무 export 함수 뒤에 추가)

**Interfaces:**
- Consumes: 없음(타입 정의).
- Produces: `SegmentSizeEntry` 타입, `getSegmentSizeAnalysis(): Promise<SegmentSizeEntry[]>`.

- [ ] **Step 1: `frontend/lib/types/eda.ts`에 타입 추가**

`frontend/lib/types/eda.ts:71-79`의 `Market` 인터페이스 바로 뒤에 추가:

```ts
export interface SegmentSizeEntry {
  market: string;
  korean_name: string;
  segment: 'large' | 'mid' | 'junk';
  trade_value_24h: number | null;
  volatility_30d: number | null;
  trade_value_percentile: number | null;
  volatility_percentile: number | null;
  is_caution: boolean;
  computed_at: string;
}
```

- [ ] **Step 2: `frontend/lib/api/eda.ts`에 함수 추가**

`frontend/lib/api/eda.ts` 상단 타입 import 목록(2~12행)에 `SegmentSizeEntry` 추가:

```ts
import { apiFetch } from './client';
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  IndicatorCatalogItem,
  Market,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```

파일 끝에 함수 추가:

```ts
export function getSegmentSizeAnalysis(): Promise<SegmentSizeEntry[]> {
  return apiFetch<SegmentSizeEntry[]>('/api/v1/analysis/segments/size');
}
```

- [ ] **Step 3: 타입 체크로 검증**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(exit code 0)

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: 세그먼트(규모) 분석 API 클라이언트 추가"
```

---

### Task 6: `SegmentSizeCard` 컴포넌트 + `/analysis` 페이지 연동

**Files:**
- Create: `frontend/components/SegmentSizeCard.tsx`
- Modify: `frontend/app/analysis/page.tsx` (전체 교체)

**Interfaces:**
- Consumes: `SegmentSizeEntry` 타입, `getSegmentSizeAnalysis()` (Task 5).
- Produces: `SegmentSizeCard({ entries }: { entries: SegmentSizeEntry[] })` 컴포넌트, `groupBySegment(entries: SegmentSizeEntry[])` export(향후 재사용/테스트 대비).

- [ ] **Step 1: `frontend/components/SegmentSizeCard.tsx` 생성**

```tsx
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { SegmentSizeEntry } from '@/lib/types/eda';

const SEGMENT_ORDER: SegmentSizeEntry['segment'][] = ['large', 'mid', 'junk'];
const SEGMENT_LABELS: Record<SegmentSizeEntry['segment'], string> = {
  large: '대형주',
  mid: '중형주',
  junk: '잡주',
};

function formatTradeValue(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value / 100_000_000).toLocaleString('ko-KR')}억`;
}

function formatVolatility(value: number | null): string {
  if (value === null) return '-';
  return `${(value * 100).toFixed(2)}%`;
}

export function groupBySegment(
  entries: SegmentSizeEntry[]
): { segment: SegmentSizeEntry['segment']; entries: SegmentSizeEntry[] }[] {
  return SEGMENT_ORDER.map((segment) => ({
    segment,
    entries: entries.filter((e) => e.segment === segment),
  }));
}

export default function SegmentSizeCard({ entries }: { entries: SegmentSizeEntry[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>세그먼트(규모)</CardTitle>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="text-muted-foreground">배치 실행 중입니다. 잠시 후 새로고침해 주세요.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {groupBySegment(entries).map(({ segment, entries: group }) => (
              <div key={segment}>
                <p className="mb-2 text-sm font-semibold">
                  {SEGMENT_LABELS[segment]} ({group.length})
                </p>
                <div className="flex flex-col gap-1">
                  {group.map((e) => (
                    <div key={e.market} className="flex items-center justify-between text-sm">
                      <span>
                        {e.korean_name}
                        {e.is_caution && (
                          <span className="ml-2 text-xs text-amber-600 dark:text-amber-400">
                            ⚠ 유의종목
                          </span>
                        )}
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        거래대금 {formatTradeValue(e.trade_value_24h)} · 변동성{' '}
                        {formatVolatility(e.volatility_30d)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: `frontend/app/analysis/page.tsx` 전체 교체**

```tsx
import { getSegmentSizeAnalysis } from '@/lib/api/eda';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import SegmentSizeCard from '@/components/SegmentSizeCard';

export default async function AnalysisPage() {
  const segmentSizeEntries = await getSegmentSizeAnalysis();

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">분석</h1>
      <div className="flex flex-col gap-4">
        <SegmentSizeCard entries={segmentSizeEntries} />
        <Card>
          <CardHeader>
            <CardTitle>세그먼트(섹터)</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground">준비 중입니다.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 브라우저로 확인**

1. 백엔드(`uvicorn backend.main:app --reload --port 8000`)와 프론트(`npm run dev`)가 이미 떠 있다면, 백엔드 재기동(`--reload`가 파일 변경을 감지해 자동 재시작) 후 **1~2분 대기**(첫 배치가 271개 KRW 마켓을 순회하는 시간).
2. Playwright로 `http://localhost:3000/analysis` 접속 후 스크린샷.
3. 확인: `세그먼트(규모)` 카드에 대형주/중형주/잡주 3개 그룹과 코인 수가 표시되고, 각 코인 행에 거래대금·변동성이 보이며, 유의종목 코인에 "⚠ 유의종목" 배지가 붙는지 확인. 배치가 아직 안 끝났다면 "배치 실행 중입니다..." 문구가 보이는지 확인 후, 잠시 뒤 새로고침해 실데이터로 바뀌는지 재확인.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/SegmentSizeCard.tsx frontend/app/analysis/page.tsx
git commit -m "feat: 분석 탭에 세그먼트(규모) 결과 테이블 표시"
```
