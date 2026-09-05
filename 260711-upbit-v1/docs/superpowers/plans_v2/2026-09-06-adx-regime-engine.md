# ADX 장세 판별 엔진 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ADX(14)+방향지표(+DI/-DI) 규칙기반으로 1시간봉 상승/하락/횡보를 과거+현재 모두 인과적으로 판별하는 엔진을 만들고, "장세 판별"(`/regime`) 탭을 이 엔진 기반으로 재구축한다(20개 메이저 코인 오버뷰 히트맵 + 단일 코인 상세 뷰어).

**Architecture:** 순수 pandas로 Wilder 방식 ADX/+DI/-DI를 계산하는 엔진 함수(`engine/regime_adx.py`)를 만들고, 이를 감싸는 백엔드 서비스(`backend/regime_adx_service.py`)가 (1) 코인 1개의 전체 히스토리 라벨+연속구간, (2) 메이저 코인 20개의 현재 라벨을 계산해 API로 노출한다. 프론트는 옛(삭제된) `RegimeFactSegmentView` 패턴(캔들 색칠 차트 + 구간 표 + 그리드서치 복사 버튼)을 라벨 3종(상승/하락/횡보)으로 확장해 재사용하고, 그 위에 20개 코인 오버뷰 히트맵을 얹는다.

**Tech Stack:** Python/FastAPI/pandas(백엔드), Next.js/React/TypeScript/lightweight-charts(프론트), pytest, backtrader(테스트에서만 golden-test 오라클로 사용 — 프로덕션 코드에는 쓰지 않음)

## Global Constraints

- ADX 계산 기간(period) = 14, 추세 판정 임계치 = 25 (ADX≤25 → 횡보, ADX>25 → +DI/-DI 비교로 상승/하락) — `engine/regime_adx.py`의 상수로 정의
- 최소 지속봉수(표에 나열할 연속 구간 필터) = 24봉(1시간봉 기준 1일) — `backend/regime_adx_service.py:MIN_SEGMENT_BARS`
- 오버뷰 조회 기간 = 최근 200시간(`OVERVIEW_LOOKBACK_BARS`), 히스토리 조회 시작일 = 2024-01-01(`HISTORY_START`)
- 타임프레임은 `minutes60` 고정 — 다른 타임프레임 지원하지 않음
- 대상 코인 20개 고정 리스트(`MAJOR_MARKETS`), 백엔드/프론트 양쪽에 정확히 동일한 값을 유지해야 함(둘 다 아래 정확한 순서로):
  ```
  KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-ADA, KRW-DOGE, KRW-LINK,
  KRW-DOT, KRW-AVAX, KRW-TRX, KRW-POL, KRW-BCH, KRW-ETC, KRW-XLM,
  KRW-ATOM, KRW-UNI, KRW-NEAR, KRW-ICP, KRW-HBAR, KRW-SUI
  ```
- 색상: 상승=`--regime-surge-up`, 하락=`--regime-surge-down`, 횡보=`--marker-boundary`, 미분류(워밍업)=`--trend-unclassified` (전부 `frontend/app/globals.css`에 이미 존재하는 기존 변수, 새로 정의하지 않음)
- 캐싱 없음 — 모든 계산은 요청마다 즉시 수행
- 설계 스펙: `docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md` (이 플랜의 모든 세부사항은 이 스펙에서 파생됨)

---

### Task 1: 대상 코인 상수

**Files:**
- Create: `engine/regime_adx_constants.py`
- Test: `tests/test_regime_adx_constants.py`

**Interfaces:**
- Produces: `MAJOR_MARKETS: list[str]` (20개 원소, `"KRW-XXX"` 형식) — Task 3(서비스), Task 5(프론트 동기화 테스트)에서 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_adx_constants.py`:
```python
"""
tests/test_regime_adx_constants.py

engine.regime_adx_constants.MAJOR_MARKETS의 형식(개수/포맷/중복 여부)을 검증한다.
"""
from __future__ import annotations

from engine.regime_adx_constants import MAJOR_MARKETS


def test_major_markets_has_twenty_unique_krw_markets():
    assert len(MAJOR_MARKETS) == 20
    assert len(set(MAJOR_MARKETS)) == 20
    assert all(m.startswith("KRW-") for m in MAJOR_MARKETS)


def test_major_markets_includes_required_coins():
    required = {"KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-LINK", "KRW-DOGE", "KRW-ADA"}
    assert required.issubset(set(MAJOR_MARKETS))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx_constants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.regime_adx_constants'`

- [ ] **Step 3: 최소 구현 작성**

`engine/regime_adx_constants.py`:
```python
"""
engine/regime_adx_constants.py

"장세 판별" 탭 오버뷰 히트맵 + 3단계 전략 라이브러리 UI가 공유하는 대상
코인 목록. 설계 문서: docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md

**확장 포인트**: 코인 추가/제외는 이 리스트만 수정하면 된다. 한글명은
저장하지 않는다 — 프론트가 기존 getMarkets() API로 조회해 표시한다.
프론트 미러(frontend/lib/constants/regime.ts)와 값을 반드시 동기화해야
하며, tests/test_regime_adx_constants_frontend_sync.py가 이를 감시한다.
"""
MAJOR_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA", "KRW-DOGE",
    "KRW-LINK", "KRW-DOT", "KRW-AVAX", "KRW-TRX", "KRW-POL", "KRW-BCH",
    "KRW-ETC", "KRW-XLM", "KRW-ATOM", "KRW-UNI", "KRW-NEAR", "KRW-ICP",
    "KRW-HBAR", "KRW-SUI",
]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx_constants.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_adx_constants.py tests/test_regime_adx_constants.py
git commit -m "feat: ADX 장세판별 대상 코인 20개 상수 추가"
```

---

### Task 2: ADX/DI 계산 엔진

**Files:**
- Create: `engine/regime_adx.py`
- Test: `tests/test_regime_adx.py`

**Interfaces:**
- Consumes: 없음(순수 함수, `df`는 `high`/`low`/`close` 컬럼을 가진 `pd.DataFrame`)
- Produces:
  - `compute_adx_di(df: pd.DataFrame, period: int = 14) -> pd.DataFrame` (컬럼: `adx`, `plus_di`, `minus_di`, df와 같은 인덱스) — Task 3에서 사용
  - `classify_regime(adx: float, plus_di: float, minus_di: float, threshold: float = 25.0) -> str | None` (`"상승"`/`"하락"`/`"횡보"`/`None`) — Task 3에서 사용
  - `PERIOD = 14`, `ADX_TREND_THRESHOLD = 25.0` 모듈 상수

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_adx.py`:
```python
"""
tests/test_regime_adx.py

engine.regime_adx의 ADX/+DI/-DI 계산(compute_adx_di)과 3-라벨 분류
(classify_regime)를 검증한다. compute_adx_di는 backtrader의
DirectionalMovementIndex(같은 Wilder 공식의 검증된 구현)를 golden-test
오라클로 써서 별도 참조 계산 없이 교차검증한다.
"""
from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest

from engine.regime_adx import ADX_TREND_THRESHOLD, PERIOD, classify_regime, compute_adx_di
from tests.signal_fixtures import make_oscillating_df


def test_compute_adx_di_matches_backtrader_directional_movement_index():
    df = make_oscillating_df()
    result = compute_adx_di(df)

    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _Probe(bt.Strategy):
        def __init__(self):
            self.dmi = bt.indicators.DirectionalMovementIndex(self.data, period=PERIOD)
            self.seen: list[tuple[float, float, float]] = []

        def next(self):
            self.seen.append((self.dmi.adx[0], self.dmi.plusDI[0], self.dmi.minusDI[0]))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_Probe)
    bt_seen = cerebro.run()[0].seen

    bt_adx = [v[0] for v in bt_seen]
    bt_plus = [v[1] for v in bt_seen]
    bt_minus = [v[2] for v in bt_seen]
    pandas_adx = result["adx"].dropna().tolist()
    pandas_plus = result["plus_di"].dropna().tolist()
    pandas_minus = result["minus_di"].dropna().tolist()

    tail = 50
    for bt_v, pd_v in zip(bt_adx[-tail:], pandas_adx[-tail:]):
        assert abs(bt_v - pd_v) < 0.5, f"adx mismatch: bt={bt_v} pandas={pd_v}"
    for bt_v, pd_v in zip(bt_plus[-tail:], pandas_plus[-tail:]):
        assert abs(bt_v - pd_v) < 0.5, f"plus_di mismatch: bt={bt_v} pandas={pd_v}"
    for bt_v, pd_v in zip(bt_minus[-tail:], pandas_minus[-tail:]):
        assert abs(bt_v - pd_v) < 0.5, f"minus_di mismatch: bt={bt_v} pandas={pd_v}"


def test_compute_adx_di_warmup_region_is_nan():
    df = make_oscillating_df(n=50)
    result = compute_adx_di(df)
    assert result["adx"].iloc[0:2 * PERIOD].isna().all()


def test_classify_regime_returns_none_when_adx_is_nan():
    assert classify_regime(float("nan"), 20.0, 10.0) is None


def test_classify_regime_returns_sideways_at_and_below_threshold():
    assert classify_regime(ADX_TREND_THRESHOLD, 30.0, 10.0) == "횡보"
    assert classify_regime(10.0, 30.0, 10.0) == "횡보"


def test_classify_regime_returns_uptrend_when_plus_di_dominates_above_threshold():
    assert classify_regime(30.0, 25.0, 10.0) == "상승"


def test_classify_regime_returns_downtrend_when_minus_di_dominates_above_threshold():
    assert classify_regime(30.0, 10.0, 25.0) == "하락"


def test_classify_regime_handles_synthetic_pure_uptrend():
    n = 60
    prices = [100.0 + i * 2 for i in range(n)]
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
    })
    result = compute_adx_di(df)
    last = result.iloc[-1]
    assert classify_regime(last.adx, last.plus_di, last.minus_di) == "상승"


def test_classify_regime_handles_synthetic_pure_downtrend():
    n = 60
    prices = [200.0 - i * 2 for i in range(n)]
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
    })
    result = compute_adx_di(df)
    last = result.iloc[-1]
    assert classify_regime(last.adx, last.plus_di, last.minus_di) == "하락"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.regime_adx'`

- [ ] **Step 3: 최소 구현 작성**

`engine/regime_adx.py`:
```python
"""
engine/regime_adx.py

ADX(Average Directional Index)+방향지표(+DI/-DI)로 상승/하락/횡보를
인과적으로(미래 데이터 불필요) 판별한다. Wilder 원 공식의 순수 pandas
구현 — backtrader Cerebro를 거치지 않아 과거 전체 기간 재계산과 최신
시점 계산에 동일한 함수를 쓸 수 있고, 반복 호출 메모리 누수
(docs/superpowers/references의 runner.py 메모리 누수 기록 참고) 위험이
없다. 설계 문서: docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md
"""
from __future__ import annotations

import pandas as pd

PERIOD = 14
ADX_TREND_THRESHOLD = 25.0


def compute_adx_di(df: pd.DataFrame, period: int = PERIOD) -> pd.DataFrame:
    """df는 high/low/close 컬럼을 포함해야 한다. Wilder 스무딩(alpha=1/period)으로
    ADX/plus_di/minus_di 3개 컬럼을 가진 DataFrame을 df와 같은 인덱스로 반환한다.
    앞쪽 워밍업 구간(대략 2*period봉)은 NaN이다."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)

    alpha = 1.0 / period
    smoothed_tr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    plus_di = 100 * smoothed_plus_dm / smoothed_tr
    minus_di = 100 * smoothed_minus_dm / smoothed_tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})


def classify_regime(
    adx: float, plus_di: float, minus_di: float, threshold: float = ADX_TREND_THRESHOLD
) -> str | None:
    """단일 시점 값을 "상승"/"하락"/"횡보" 중 하나로 분류한다. adx/plus_di/minus_di
    중 하나라도 NaN이면(워밍업 구간) None을 반환한다."""
    if pd.isna(adx) or pd.isna(plus_di) or pd.isna(minus_di):
        return None
    if adx <= threshold:
        return "횡보"
    return "상승" if plus_di > minus_di else "하락"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_adx.py tests/test_regime_adx.py
git commit -m "feat: ADX+DI 기반 상승/하락/횡보 판별 엔진 추가"
```

---

### Task 3: 백엔드 서비스 (히스토리 + 오버뷰)

**Files:**
- Create: `backend/regime_adx_service.py`
- Test: `tests/test_regime_adx_service.py`

**Interfaces:**
- Consumes: `engine.regime_adx.compute_adx_di`, `engine.regime_adx.classify_regime` (Task 2), `engine.regime_adx_constants.MAJOR_MARKETS` (Task 1), `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame`(기존 함수)
- Produces:
  - `compute_adx_regime_history(market: str, timeframe: str) -> dict` (`{market, timeframe, bars, segments}`) — Task 4에서 사용
  - `compute_adx_regime_overview(timeframe: str) -> list[dict]` (`[{market, label, adx, plus_di, minus_di}, ...]`) — Task 4에서 사용
  - `MIN_SEGMENT_BARS = 24`, `OVERVIEW_LOOKBACK_BARS = 200`, `HISTORY_START` 모듈 상수

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_adx_service.py`:
```python
"""
tests/test_regime_adx_service.py

backend.regime_adx_service.compute_adx_regime_history()/
compute_adx_regime_overview()를 검증한다. ADX 계산 수학 자체(compute_adx_di,
classify_regime)는 tests/test_regime_adx.py가 이미 검증하므로, 여기서는
compute_adx_di를 monkeypatch로 고정해 이 모듈이 새로 하는 일(봉별 bars
배열 조립, 연속 구간 묶기, 최소 지속봉수 필터링, 오버뷰 순회)만 검증한다.
"""
from __future__ import annotations

import pandas as pd

import backend.regime_adx_service as regime_adx_service
from backend.regime_adx_service import _same_label, compute_adx_regime_history, compute_adx_regime_overview


def _make_df(n: int) -> pd.DataFrame:
    times = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "candle_time": times,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
    })


def _make_adx_di(n: int, adx: list[float], plus_di: list[float], minus_di: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})[:n]


def test_same_label_treats_both_none_as_equal():
    assert _same_label(None, None) is True


def test_same_label_treats_label_and_none_as_different():
    assert _same_label("상승", None) is False
    assert _same_label(None, "상승") is False


def test_same_label_compares_equal_and_different_strings():
    assert _same_label("상승", "상승") is True
    assert _same_label("상승", "하락") is False


def test_compute_adx_regime_history_bars_carry_per_bar_label(monkeypatch):
    df = _make_df(4)
    adx_di = _make_adx_di(4, [30, 30, 30, float("nan")], [40, 40, 10, float("nan")], [10, 10, 40, float("nan")])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert [b["label"] for b in result["bars"]] == ["상승", "상승", "하락", None]
    assert result["market"] == "KRW-BTC"
    assert result["timeframe"] == "minutes60"
    assert len(result["bars"]) == 4
    assert result["bars"][0]["open"] == 100.0
    assert result["bars"][0]["time"] == df["candle_time"].iloc[0].isoformat()


def test_compute_adx_regime_history_merges_consecutive_same_label_into_one_segment(monkeypatch):
    df = _make_df(6)
    adx_di = _make_adx_di(
        6,
        [30, 30, 30, 30, 30, 30],
        [40, 40, 40, 10, 10, 10],
        [10, 10, 10, 40, 40, 40],
    )
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 2)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 2
    first, second = result["segments"]
    assert first["label"] == "상승"
    assert first["bar_count"] == 3
    assert first["start"] == df["candle_time"].iloc[0].isoformat()
    assert first["end"] == df["candle_time"].iloc[2].isoformat()
    assert second["label"] == "하락"
    assert second["bar_count"] == 3
    assert second["start"] == df["candle_time"].iloc[3].isoformat()
    assert second["end"] == df["candle_time"].iloc[5].isoformat()


def test_compute_adx_regime_history_excludes_runs_shorter_than_min_bars(monkeypatch):
    df = _make_df(5)
    adx_di = _make_adx_di(5, [10, 30, 30, 30, 30], [40, 40, 40, 40, 40], [10, 10, 10, 10, 10])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 2)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "상승"
    assert result["segments"][0]["bar_count"] == 4


def test_compute_adx_regime_history_excludes_none_runs_from_segments(monkeypatch):
    df = _make_df(5)
    adx_di = _make_adx_di(
        5,
        [30, 30, float("nan"), float("nan"), float("nan")],
        [40, 40, float("nan"), float("nan"), float("nan")],
        [10, 10, float("nan"), float("nan"), float("nan")],
    )
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 1)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "상승"
    assert result["segments"][0]["bar_count"] == 2


def test_compute_adx_regime_overview_returns_one_entry_per_major_market(monkeypatch):
    df = _make_df(30)
    adx_di = pd.DataFrame({
        "adx": [30.0] * 30, "plus_di": [40.0] * 30, "minus_di": [10.0] * 30,
    })
    monkeypatch.setattr(regime_adx_service, "MAJOR_MARKETS", ["KRW-BTC", "KRW-ETH"])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_overview("minutes60")

    assert [r["market"] for r in result] == ["KRW-BTC", "KRW-ETH"]
    assert all(r["label"] == "상승" for r in result)
    assert all(r["adx"] == 30.0 for r in result)


def test_compute_adx_regime_overview_reports_none_label_when_last_bar_is_warmup(monkeypatch):
    df = _make_df(5)
    adx_di = pd.DataFrame({
        "adx": [float("nan")] * 5, "plus_di": [float("nan")] * 5, "minus_di": [float("nan")] * 5,
    })
    monkeypatch.setattr(regime_adx_service, "MAJOR_MARKETS", ["KRW-BTC"])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_overview("minutes60")

    assert result[0]["label"] is None
    assert result[0]["adx"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.regime_adx_service'`

- [ ] **Step 3: 최소 구현 작성**

`backend/regime_adx_service.py`:
```python
"""
backend/regime_adx_service.py

engine/regime_adx.py의 ADX+DI 규칙기반 판정으로 "장세 판별" 탭의 (1) 코인별
과거 전체 기간 상승/하락/횡보 구간 뷰어, (2) 메이저 코인 20개 현재 판정
오버뷰를 계산한다. 캐싱 없음 — 요청마다 즉시 계산. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from engine.regime_adx import classify_regime, compute_adx_di
from engine.regime_adx_constants import MAJOR_MARKETS
from upbit_data_service import get_candles

HISTORY_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
# 표에 나열할 최소 지속봉수(24봉=minutes60 기준 1일). 미만인 구간은 차트에는
# 그대로 색칠되지만 표에는 나오지 않는다.
MIN_SEGMENT_BARS = 24
# 오버뷰(최신 시점만 필요)용 조회 기간 — ADX(14) 워밍업(약 28봉)에 여유를
# 둔 값. 전체 히스토리를 긁을 필요 없다.
OVERVIEW_LOOKBACK_BARS = 200


def _to_iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _same_label(a: object, b: object) -> bool:
    """둘 다 None이면(같은 미분류 구간) 같다고 취급한다."""
    if a is None and b is None:
        return True
    return a == b


def compute_adx_regime_history(market: str, timeframe: str) -> dict:
    """market의 전체 기간(HISTORY_START~현재) ADX 장세 라벨을 봉별 OHLCV+라벨
    배열과, 최소 지속봉수 이상인 연속 구간 목록으로 반환한다. 반환값:
    {market, timeframe, bars, segments}. bars의 각 원소는
    {time, open, high, low, close, label}(label은 "상승"/"하락"/"횡보"/None).
    segments의 각 원소는 {start, end, label, bar_count}."""
    df = get_candles(market, timeframe, HISTORY_START, datetime.now(timezone.utc))
    adx_di = compute_adx_di(df)
    labels = [
        classify_regime(row.adx, row.plus_di, row.minus_di)
        for row in adx_di.itertuples()
    ]

    bars = [
        {
            "time": _to_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "label": label,
        }
        for row, label in zip(df.itertuples(), labels)
    ]

    segments: list[dict] = []
    if labels:
        run_start_idx = 0
        run_label = labels[0]
        for i in range(1, len(labels) + 1):
            at_end = i == len(labels)
            cur_label = None if at_end else labels[i]
            if at_end or not _same_label(cur_label, run_label):
                bar_count = i - run_start_idx
                if run_label is not None and bar_count >= MIN_SEGMENT_BARS:
                    segments.append({
                        "start": _to_iso(df["candle_time"].iloc[run_start_idx]),
                        "end": _to_iso(df["candle_time"].iloc[i - 1]),
                        "label": run_label,
                        "bar_count": bar_count,
                    })
                run_start_idx = i
                run_label = cur_label

    return {"market": market, "timeframe": timeframe, "bars": bars, "segments": segments}


def compute_adx_regime_overview(timeframe: str) -> list[dict]:
    """MAJOR_MARKETS 각각의 현재(최신 봉) ADX 장세 판정을 반환한다. 반환값:
    [{market, label, adx, plus_di, minus_di}, ...] (label은 "상승"/"하락"/
    "횡보"/None, 순서는 MAJOR_MARKETS와 동일)."""
    start = datetime.now(timezone.utc) - timedelta(hours=OVERVIEW_LOOKBACK_BARS)
    results = []
    for market in MAJOR_MARKETS:
        df = get_candles(market, timeframe, start, datetime.now(timezone.utc))
        adx_di = compute_adx_di(df)
        last = adx_di.iloc[-1]
        label = classify_regime(last.adx, last.plus_di, last.minus_di)
        results.append({
            "market": market,
            "label": label,
            "adx": None if pd.isna(last.adx) else float(last.adx),
            "plus_di": None if pd.isna(last.plus_di) else float(last.plus_di),
            "minus_di": None if pd.isna(last.minus_di) else float(last.minus_di),
        })
    return results
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx_service.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/regime_adx_service.py tests/test_regime_adx_service.py
git commit -m "feat: ADX 장세판별 히스토리/오버뷰 백엔드 서비스 추가"
```

---

### Task 4: API 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `backend.regime_adx_service.compute_adx_regime_history`, `compute_adx_regime_overview` (Task 3)
- Produces: `GET /api/v1/regime/adx-segments?market=&timeframe=`, `GET /api/v1/regime/adx-overview?timeframe=` — Task 5(프론트 API 함수)에서 호출

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가(기존 파일의 `_client(monkeypatch, tmp_path)` 헬퍼를 그대로 사용):
```python
def test_regime_adx_segments_endpoint_calls_service(monkeypatch, tmp_path):
    import backend.main as main_module

    client = _client(monkeypatch, tmp_path)
    fake_result = {"market": "KRW-BTC", "timeframe": "minutes60", "bars": [], "segments": []}
    monkeypatch.setattr(main_module, "compute_adx_regime_history", lambda market, timeframe: fake_result)

    response = client.get("/api/v1/regime/adx-segments", params={"market": "KRW-BTC", "timeframe": "minutes60"})

    assert response.status_code == 200
    assert response.json() == fake_result


def test_regime_adx_overview_endpoint_calls_service(monkeypatch, tmp_path):
    import backend.main as main_module

    client = _client(monkeypatch, tmp_path)
    fake_result = [{"market": "KRW-BTC", "label": "상승", "adx": 30.0, "plus_di": 40.0, "minus_di": 10.0}]
    monkeypatch.setattr(main_module, "compute_adx_regime_overview", lambda timeframe: fake_result)

    response = client.get("/api/v1/regime/adx-overview", params={"timeframe": "minutes60"})

    assert response.status_code == 200
    assert response.json() == fake_result
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k regime_adx`
Expected: FAIL with 404 (엔드포인트가 아직 없음)

- [ ] **Step 3: 최소 구현 작성**

`backend/main.py`의 `from backend.trading_analytics_service import get_journal_summary, get_market_journal` 줄 다음에 추가:
```python
from backend.regime_adx_service import compute_adx_regime_history, compute_adx_regime_overview
```

`backend/main.py`의 `@app.get("/api/v1/markets")` 엔드포인트 바로 다음에 추가:
```python
@app.get("/api/v1/regime/adx-segments")
def get_regime_adx_segments_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    return compute_adx_regime_history(market, timeframe)


@app.get("/api/v1/regime/adx-overview")
def get_regime_adx_overview_endpoint(timeframe: str = Query(...)) -> list[dict]:
    return compute_adx_regime_overview(timeframe)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -v -k regime_adx`
Expected: PASS (2 passed)

전체 회귀 확인도 같이 실행:
Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 5: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: ADX 장세판별 히스토리/오버뷰 API 엔드포인트 추가"
```

---

### Task 5: 프론트 타입/API/상수 + 동기화 테스트

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`
- Create: `frontend/lib/constants/regime.ts`
- Test: `tests/test_regime_adx_constants_frontend_sync.py`

**Interfaces:**
- Consumes: `engine.regime_adx_constants.MAJOR_MARKETS` (Task 1, 파이썬 쪽 파싱 대상)
- Produces:
  - TS 타입 `RegimeAdxLabel`, `RegimeAdxBar`, `RegimeAdxSegment`, `RegimeAdxHistory`, `RegimeAdxOverviewItem` — Task 6, 7에서 사용
  - `getRegimeAdxHistory(params: {market: string; timeframe: string}): Promise<RegimeAdxHistory>`, `getRegimeAdxOverview(timeframe: string): Promise<RegimeAdxOverviewItem[]>` — Task 7에서 사용
  - `frontend/lib/constants/regime.ts`의 `TIMEFRAME = 'minutes60'`, `MAJOR_MARKETS: readonly string[]` — Task 7에서 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_adx_constants_frontend_sync.py`:
```python
"""
tests/test_regime_adx_constants_frontend_sync.py

engine.regime_adx_constants.MAJOR_MARKETS와
frontend/lib/constants/regime.ts의 MAJOR_MARKETS 배열이 어긋나지 않는지
감시하는 가드레일 테스트. 한쪽만 바뀌면 이 테스트가 실패해 드리프트를 잡는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from engine.regime_adx_constants import MAJOR_MARKETS

_FRONTEND_FILE = Path(__file__).parent.parent / "frontend" / "lib" / "constants" / "regime.ts"
_ARRAY_PATTERN = re.compile(r"MAJOR_MARKETS\s*=\s*\[([^\]]*)\]")
_QUOTED_STRING_PATTERN = re.compile(r"['\"]([^'\"]*)['\"]")


def _extract_frontend_markets() -> list[str]:
    content = _FRONTEND_FILE.read_text(encoding="utf-8")
    match = _ARRAY_PATTERN.search(content)
    assert match is not None, (
        f"{_FRONTEND_FILE}에서 MAJOR_MARKETS 배열을 찾지 못했습니다 — "
        "파일 구조가 바뀌었으면 이 테스트의 정규식도 갱신하세요."
    )
    return _QUOTED_STRING_PATTERN.findall(match.group(1))


def test_frontend_major_markets_matches_backend_major_markets():
    frontend_markets = _extract_frontend_markets()
    assert sorted(frontend_markets) == sorted(MAJOR_MARKETS)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx_constants_frontend_sync.py -v`
Expected: FAIL with `FileNotFoundError` 또는 `AssertionError`(파일이 아직 없음)

- [ ] **Step 3: 최소 구현 작성**

`frontend/lib/constants/regime.ts` (신규):
```typescript
export const TIMEFRAME = 'minutes60';

export const MAJOR_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-ADA', 'KRW-DOGE',
  'KRW-LINK', 'KRW-DOT', 'KRW-AVAX', 'KRW-TRX', 'KRW-POL', 'KRW-BCH',
  'KRW-ETC', 'KRW-XLM', 'KRW-ATOM', 'KRW-UNI', 'KRW-NEAR', 'KRW-ICP',
  'KRW-HBAR', 'KRW-SUI',
] as const;
```

`frontend/lib/types/eda.ts` 파일 끝에 추가:
```typescript
export type RegimeAdxLabel = '상승' | '하락' | '횡보';

export interface RegimeAdxBar extends OhlcvPoint {
  label: RegimeAdxLabel | null;
}

export interface RegimeAdxSegment {
  start: string;
  end: string;
  label: RegimeAdxLabel;
  bar_count: number;
}

export interface RegimeAdxHistory {
  market: string;
  timeframe: string;
  bars: RegimeAdxBar[];
  segments: RegimeAdxSegment[];
}

export interface RegimeAdxOverviewItem {
  market: string;
  label: RegimeAdxLabel | null;
  adx: number | null;
  plus_di: number | null;
  minus_di: number | null;
}
```

`frontend/lib/api/eda.ts` 파일 끝에 추가:
```typescript
export function getRegimeAdxHistory(params: {
  market: string;
  timeframe: string;
}): Promise<RegimeAdxHistory> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeAdxHistory>(`/api/v1/regime/adx-segments?${query.toString()}`);
}

export function getRegimeAdxOverview(timeframe: string): Promise<RegimeAdxOverviewItem[]> {
  const query = new URLSearchParams({ timeframe });
  return apiFetch<RegimeAdxOverviewItem[]>(`/api/v1/regime/adx-overview?${query.toString()}`);
}
```
(`frontend/lib/api/eda.ts` 상단의 `import type { ... } from '@/lib/types/eda'` 목록에 `RegimeAdxHistory`, `RegimeAdxOverviewItem` 추가)

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_adx_constants_frontend_sync.py -v`
Expected: PASS (1 passed)

프론트 타입체크도 확인:
Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts frontend/lib/constants/regime.ts tests/test_regime_adx_constants_frontend_sync.py
git commit -m "feat: ADX 장세판별 프론트 타입/API/상수 추가"
```

---

### Task 6: 차트 + 구간 표 컴포넌트

**Files:**
- Create: `frontend/components/RegimeAdxChart.tsx`
- Create: `frontend/components/RegimeAdxSegmentTable.tsx`

**Interfaces:**
- Consumes: `RegimeAdxBar`, `RegimeAdxSegment` 타입(Task 5)
- Produces: `RegimeAdxChart({ bars: RegimeAdxBar[] })`, `RegimeAdxSegmentTable({ segments: RegimeAdxSegment[]; market: string; timeframe: string })` — Task 7에서 사용

이 두 컴포넌트는 순수 렌더링 컴포넌트라 이 프로젝트 관례상 자동화 유닛
테스트를 추가하지 않는다(기존 `RegimeFactChart`/`RegimeFactSegmentTable`도
테스트 없이 Playwright 수동 검증만 했음 — Task 8에서 수행).

- [ ] **Step 1: `RegimeAdxChart.tsx` 작성**

`frontend/components/RegimeAdxChart.tsx`:
```typescript
'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode, type UTCTimestamp } from 'lightweight-charts';
import type { RegimeAdxBar } from '@/lib/types/eda';

export default function RegimeAdxChart({ bars }: { bars: RegimeAdxBar[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;

    // getComputedStyle의 oklch() 반환값을 lightweight-charts가 파싱하지 못해,
    // canvas 2D로 한 번 그려 rgba()로 변환한다.
    const resolveColor = (varName: string): string => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return raw;
      ctx.fillStyle = raw;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      return `rgba(${pixel[0]}, ${pixel[1]}, ${pixel[2]}, ${(pixel[3] / 255).toFixed(3)})`;
    };

    const upColor = resolveColor('--regime-surge-up');
    const downColor = resolveColor('--regime-surge-down');
    const sidewaysColor = resolveColor('--marker-boundary');
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor, downColor, borderVisible: false,
      wickUpColor: upColor, wickDownColor: downColor,
    });

    const toUnix = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

    const colorFor = (label: RegimeAdxBar['label']): string => {
      if (label === '상승') return upColor;
      if (label === '하락') return downColor;
      if (label === '횡보') return sidewaysColor;
      return unclassifiedColor;
    };

    const candleData = bars.map((bar) => {
      const color = colorFor(bar.label);
      return {
        time: toUnix(bar.time),
        open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        color, borderColor: color, wickColor: color,
      };
    });
    candleSeries.setData(candleData);

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [bars]);

  return (
    <div className="w-full">
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--regime-surge-up)' }} />
          상승
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--regime-surge-down)' }} />
          하락
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          횡보
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--trend-unclassified)' }} />
          미분류(워밍업)
        </span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
```

- [ ] **Step 2: `RegimeAdxSegmentTable.tsx` 작성**

`frontend/components/RegimeAdxSegmentTable.tsx`:
```typescript
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import type { RegimeAdxSegment } from '@/lib/types/eda';
import { formatDateTimeShort } from '@/lib/format';

const LABEL_TEXT_CLASS: Record<RegimeAdxSegment['label'], string> = {
  상승: 'text-[color:var(--regime-surge-up)]',
  하락: 'text-[color:var(--regime-surge-down)]',
  횡보: 'text-muted-foreground',
};

function buildGridSearchHref(market: string, timeframe: string, seg: RegimeAdxSegment): string {
  const params = new URLSearchParams({
    market,
    timeframe,
    start: seg.start.slice(0, 10),
    end: seg.end.slice(0, 10),
  });
  return `/grid-search?${params.toString()}`;
}

type SortKey = 'start' | 'bar_count';
type SortDir = 'asc' | 'desc';

export default function RegimeAdxSegmentTable({
  segments, market, timeframe,
}: { segments: RegimeAdxSegment[]; market: string; timeframe: string }) {
  const [sortKey, setSortKey] = useState<SortKey>('start');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const sorted = useMemo(() => {
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...segments].sort((a, b) => {
      if (sortKey === 'start') {
        return a.start < b.start ? -factor : a.start > b.start ? factor : 0;
      }
      return (a.bar_count - b.bar_count) * factor;
    });
  }, [segments, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ sortKeyOf }: { sortKeyOf: SortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  if (segments.length === 0) {
    return <p className="text-muted-foreground">최소 지속봉수를 넘는 구간이 없습니다.</p>;
  }

  return (
    <div className="max-h-96 overflow-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground"
                onClick={() => toggleSort('start')}
              >
                기간 <SortIcon sortKeyOf="start" />
              </button>
            </TableHead>
            <TableHead className="text-right">
              <button
                type="button"
                className="flex w-full items-center justify-end gap-1 hover:text-foreground"
                onClick={() => toggleSort('bar_count')}
              >
                지속 <SortIcon sortKeyOf="bar_count" />
              </button>
            </TableHead>
            <TableHead>라벨</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((seg) => (
            <TableRow key={`${seg.start}-${seg.end}`}>
              <TableCell className="whitespace-nowrap">
                {formatDateTimeShort(seg.start)} ~ {formatDateTimeShort(seg.end)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{seg.bar_count}봉</TableCell>
              <TableCell className={LABEL_TEXT_CLASS[seg.label]}>{seg.label}</TableCell>
              <TableCell>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  nativeButton={false}
                  role="link"
                  aria-label="그리드서치로 복사"
                  title="그리드서치로 복사"
                  render={<Link href={buildGridSearchHref(market, timeframe, seg)} />}
                >
                  <Copy className="size-3.5" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

- [ ] **Step 3: 타입체크 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(아직 아무 페이지도 이 컴포넌트들을 import하지 않으므로 미사용 export 경고만 있을 수 있음 — 에러가 아니면 통과)

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/RegimeAdxChart.tsx frontend/components/RegimeAdxSegmentTable.tsx
git commit -m "feat: ADX 장세판별 캔들 차트 + 구간 표 컴포넌트 추가"
```

---

### Task 7: 오버뷰 히트맵 + 상세 뷰어 + 탭 조립

**Files:**
- Create: `frontend/components/RegimeAdxOverview.tsx`
- Create: `frontend/components/RegimeAdxDetailView.tsx`
- Create: `frontend/components/RegimeDashboard.tsx`
- Create: `frontend/app/regime/page.tsx`
- Modify: `frontend/components/NavTabs.tsx`

**Interfaces:**
- Consumes: `getRegimeAdxOverview`, `getRegimeAdxHistory`, `getMarkets` (기존), `RegimeAdxOverviewItem`/`RegimeAdxHistory`/`Market` 타입(Task 5), `RegimeAdxChart`/`RegimeAdxSegmentTable`(Task 6), `MAJOR_MARKETS`/`TIMEFRAME`(Task 5)
- Produces: `/regime` 라우트(브라우저에서 접근 가능한 최종 화면) — 이후 태스크 없음(Task 8이 이 결과물을 수동 검증)

- [ ] **Step 1: `RegimeAdxOverview.tsx` 작성**

`frontend/components/RegimeAdxOverview.tsx`:
```typescript
'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getMarkets, getRegimeAdxOverview } from '@/lib/api/eda';
import type { Market, RegimeAdxOverviewItem } from '@/lib/types/eda';
import { MAJOR_MARKETS, TIMEFRAME } from '@/lib/constants/regime';

const LABEL_BG_CLASS: Record<string, string> = {
  상승: 'bg-[color:var(--regime-surge-up)]/15 border-[color:var(--regime-surge-up)]/40',
  하락: 'bg-[color:var(--regime-surge-down)]/15 border-[color:var(--regime-surge-down)]/40',
  횡보: 'bg-muted border-border',
};

export default function RegimeAdxOverview({
  selectedMarket, onSelectMarket,
}: { selectedMarket: string; onSelectMarket: (market: string) => void }) {
  const [overview, setOverview] = useState<RegimeAdxOverviewItem[] | null>(null);
  const [markets, setMarkets] = useState<Market[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    Promise.all([getRegimeAdxOverview(TIMEFRAME), getMarkets()])
      .then(([overviewResult, marketsResult]) => {
        if (ignore) return;
        setOverview(overviewResult);
        setMarkets(marketsResult);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : '오버뷰를 불러오지 못했습니다.');
      });
    return () => {
      ignore = true;
    };
  }, []);

  function koreanNameFor(market: string): string {
    return markets?.find((m) => m.market === market)?.korean_name ?? market.replace('KRW-', '');
  }

  function labelFor(market: string): string | null {
    return overview?.find((o) => o.market === market)?.label ?? null;
  }

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold">메이저 코인 20 현재 장세</h2>
      {error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : !overview || !markets ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-5">
          {MAJOR_MARKETS.map((market) => {
            const label = labelFor(market);
            const bgClass = label ? LABEL_BG_CLASS[label] : 'bg-muted border-border';
            const isSelected = market === selectedMarket;
            return (
              <button
                key={market}
                type="button"
                onClick={() => onSelectMarket(market)}
                className={`rounded-lg border p-3 text-left transition ${bgClass} ${isSelected ? 'ring-2 ring-primary' : ''}`}
              >
                <div className="text-xs font-medium">{koreanNameFor(market)}</div>
                <div className="text-xs text-muted-foreground">{market.replace('KRW-', '')}</div>
                <div className="mt-1 text-sm font-semibold">{label ?? '미분류'}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `RegimeAdxDetailView.tsx` 작성**

`frontend/components/RegimeAdxDetailView.tsx`:
```typescript
'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getRegimeAdxHistory } from '@/lib/api/eda';
import RegimeAdxChart from '@/components/RegimeAdxChart';
import RegimeAdxSegmentTable from '@/components/RegimeAdxSegmentTable';
import type { RegimeAdxHistory } from '@/lib/types/eda';
import { MAJOR_MARKETS, TIMEFRAME } from '@/lib/constants/regime';

export default function RegimeAdxDetailView({
  market, onMarketChange,
}: { market: string; onMarketChange: (market: string) => void }) {
  const [data, setData] = useState<RegimeAdxHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeAdxHistory({ market, timeframe: TIMEFRAME })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'ADX 장세 구간을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [market]);

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold">ADX 장세 구간 (상승/하락/횡보)</h2>
        <select
          value={market}
          onChange={(e) => onMarketChange(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-sm"
        >
          {MAJOR_MARKETS.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>
      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div className="space-y-4">
          <RegimeAdxChart bars={data.bars} />
          <RegimeAdxSegmentTable segments={data.segments} market={data.market} timeframe={data.timeframe} />
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 3: `RegimeDashboard.tsx` + `app/regime/page.tsx` 작성**

`frontend/components/RegimeDashboard.tsx`:
```typescript
'use client';

import { useState } from 'react';
import RegimeAdxOverview from '@/components/RegimeAdxOverview';
import RegimeAdxDetailView from '@/components/RegimeAdxDetailView';
import { MAJOR_MARKETS } from '@/lib/constants/regime';

export default function RegimeDashboard() {
  const [market, setMarket] = useState<string>(MAJOR_MARKETS[0]);

  return (
    <div className="space-y-6">
      <RegimeAdxOverview selectedMarket={market} onSelectMarket={setMarket} />
      <RegimeAdxDetailView market={market} onMarketChange={setMarket} />
    </div>
  );
}
```

`frontend/app/regime/page.tsx`:
```typescript
import RegimeDashboard from '@/components/RegimeDashboard';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">장세 판별</h1>
      <RegimeDashboard />
    </div>
  );
}
```

- [ ] **Step 4: `NavTabs.tsx`에 탭 복원**

`frontend/components/NavTabs.tsx`의 import 줄:
```typescript
import { BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
```
을
```typescript
import { Activity, BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
```
로 바꾸고, `STEPS` 배열의 `{ href: '/analysis', title: '세그먼트', icon: BarChart3 }` 다음 줄에 추가:
```typescript
  { href: '/regime', title: '장세 판별', icon: Activity },
```

- [ ] **Step 5: 타입체크 + 빌드 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

Run: `cd frontend && npm run build`
Expected: 빌드 성공(에러 없음)

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/RegimeAdxOverview.tsx frontend/components/RegimeAdxDetailView.tsx frontend/components/RegimeDashboard.tsx frontend/app/regime/page.tsx frontend/components/NavTabs.tsx
git commit -m "feat: 장세 판별 탭 재구축(오버뷰 히트맵 + 상세 뷰어)"
```

---

### Task 8: 전체 검증 및 브라우저 수동 확인

**Files:** 없음(검증 전용 태스크)

**Interfaces:** 없음

- [ ] **Step 1: 백엔드 전체 테스트 스위트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 2: 프론트 빌드 재확인**

Run: `cd frontend && npm run build`
Expected: 빌드 성공

- [ ] **Step 3: 로컬 서버 기동 확인**

Run(백그라운드로 띄운 뒤 몇 초 후 확인): `PYTHONPATH=. PYTHONIOENCODING=utf-8 uvicorn backend.main:app --port 8000`
Expected: import 에러 없이 정상 기동(터미널에 `Application startup complete.`)

- [ ] **Step 4: Playwright로 브라우저 수동 검증**

`npm run dev`(프론트)와 uvicorn(백엔드)을 함께 띄운 상태에서 Playwright MCP로:
1. `/regime` 탭으로 이동 — 20개 코인 오버뷰 타일이 뜨는지 확인
2. 타일 하나를 클릭 — 하단 상세 뷰어의 코인이 바뀌고 차트+구간 표가 로드되는지 확인
3. 상세 뷰어의 코인 선택 드롭다운으로 다른 코인 선택 — 오버뷰 하이라이트도 같이 바뀌는지 확인
4. 구간 표의 "그리드서치로 복사" 아이콘 클릭 — `/grid-search`로 이동하며 market/timeframe/start/end가 정확히 채워지는지 확인
5. 구간 표 정렬 버튼(기간/지속) 클릭 — 정렬이 바뀌는지 확인

Expected: 위 5가지 모두 정상 동작. 문제 발견 시 관련 태스크로 돌아가 수정 후 재검증.

- [ ] **Step 5: 최종 확인 — 커밋 없음(검증 전용 태스크, 코드 변경 없으면 커밋 생략)**

앞선 단계에서 버그를 발견해 수정했다면 그 수정 파일들을 커밋:
```bash
git add -A
git commit -m "fix: 장세 판별 탭 브라우저 검증 중 발견한 문제 수정"
```
(수정할 게 없었다면 이 커밋은 생략)

## Self-Review 결과

**Spec 커버리지**: 스펙의 1~5 설계 섹션(엔진/상수/서비스/엔드포인트/프론트) 전부 Task 1~7에 1:1로 대응. 테스트 전략 섹션은 Task 1~5의 각 Step 1~2(TDD)+Task 8(빌드/pytest 전체+Playwright 수동검증)로 커버. 완료 기준 5개 항목 전부 Task 7(빌드)/Task 8(수동검증+pytest)에서 확인됨.

**스펙 대비 구현 세부사항 조정 2건** (스펙의 의도를 벗어나지 않는 범위의 구체화):
1. 스펙은 `test_regime_adx.py` 하나에 엔진+서비스 테스트를 모두 넣는 것처럼 서술했으나, 옛 코드베이스 관례(`test_regime_math.py` vs `test_regime_fact_service.py`가 분리돼 있었음)를 따라 엔진 테스트(`test_regime_adx.py`, Task 2)와 서비스 테스트(`test_regime_adx_service.py`, Task 3)를 분리했다.
2. 서비스 테스트가 옛 `test_regime_fact_service.py`와 동일한 격리 패턴(수학 함수를 monkeypatch로 고정)을 쓸 수 있도록 `compute_adx_regime_history`/`compute_adx_regime_overview`가 `compute_adx_di`를 모듈 레벨에서 직접 호출하는 구조를 유지했다(스펙의 pseudocode와 동일).

**Placeholder 스캔**: "TBD"/"TODO"/미완성 코드 없음 — 모든 Step에 완전한 코드 블록 포함.

**타입 일관성**: `RegimeAdxLabel`/`RegimeAdxBar`/`RegimeAdxSegment`/`RegimeAdxHistory`/`RegimeAdxOverviewItem`(Task 5에서 정의) 명칭이 Task 6, 7 전체에서 동일하게 쓰임. `compute_adx_regime_history`/`compute_adx_regime_overview`/`MAJOR_MARKETS`/`TIMEFRAME` 등 함수·상수명도 정의 지점(Task 1, 2, 3)과 사용 지점(Task 4, 5, 7) 간 일치 확인됨.
