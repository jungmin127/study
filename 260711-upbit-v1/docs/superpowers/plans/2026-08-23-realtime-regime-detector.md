# 실시간 장세 판별기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 캔들 DataFrame만으로 매 봉 인과적으로(그 시점까지의 데이터만 사용) 5개 장세 카테고리의
확률벡터를 산출하는 순수 함수 `engine/regime_detector.py`를 만들고, 과거 캔들로 정확도를
검증하는 스크립트 `scripts/regime_backtest.py`를 만든다.

**Architecture:** 위험조정 모멘텀(EWMA 수익률 / EWMA 변동성) 스코어를 5개 카테고리 대표값과의
softmax 거리로 확률화한다. 전체 이력에 대해 한 번에 벡터화 계산하는
`compute_regime_probs_series()`가 단일 진실 소스이고, 라이브 데몬이 매 봉 호출할
`compute_regime_probs()`(단일 시점 API)는 그 위에 얇게 얹는다 — 검증스크립트가
`compute_regime_probs()`를 봉마다 반복 호출하면 O(n²)라 느리므로, 벡터화 버전을 직접 쓰고
두 경로가 동일한 결과를 내는지 회귀테스트로 고정한다.

**Tech Stack:** Python, pandas(`>=2.2,<3.0`), pytest. 기존 `upbit_data_service.get_candles()`/
`timeframe_duration()` 재사용.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md`. 이 스펙과
  충돌하는 구현은 하지 않는다.
- **`pct_change()`는 반드시 `fill_method=None`을 명시한다.** pandas 2.2.0에서 기본값이
  결측값을 forward-fill해 상관계수 지표에서 실제 버그를 낸 전례가 있다
  (`trading/live_indicators.py`가 이미 이 패턴을 씀 — 그대로 따른다).
- 카테고리는 정확히 5개, 이 순서와 대표값을 쓴다:
  `{"급하락": -2.0, "완만하락": -0.7, "횡보": 0.0, "완만상승": 0.7, "급상승": 2.0}`.
- `HALF_LIFE_DAYS = 1.0`(확정값, 사용자가 "빠름" 선택).
- 이번 플랜 범위는 엔진 모듈 + 검증스크립트 + 테스트까지만. UI/API 없음. 라이브 데몬 연동,
  프리셋 매핑, 파라미터 자동튜닝은 전부 별도 세션(비범위, 스펙에 명시됨).
- 새 코드는 `from __future__ import annotations`를 파일 최상단에 둔다(이 코드베이스의
  `engine/trend_segments.py` 등 기존 관례).
- 스크립트 실행은 저장소 루트에서 `PYTHONPATH=. PYTHONIOENCODING=utf-8 python
  scripts/regime_backtest.py`(이 코드베이스의 `scripts/augment_search.py` 등과 동일한 관례).

---

## Task 1: 카테고리 대표값 + softmax 확률화

**Files:**
- Create: `engine/regime_detector.py`
- Test: `tests/test_regime_detector.py`

**Interfaces:**
- Produces: `CATEGORY_REFERENCE_SCORES: dict[str, float]`,
  `_softmax_categorize(score: float, temperature: float = TEMPERATURE) -> dict[str, float]`,
  `TEMPERATURE: float`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_detector.py` 신규 생성:

```python
from __future__ import annotations

import pytest

from engine.regime_detector import CATEGORY_REFERENCE_SCORES, _softmax_categorize


def test_softmax_categorize_sums_to_one():
    probs = _softmax_categorize(0.0)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_softmax_categorize_returns_all_five_categories():
    probs = _softmax_categorize(1.0)
    assert set(probs.keys()) == set(CATEGORY_REFERENCE_SCORES.keys())


def test_softmax_categorize_extreme_positive_score_favors_surge_up():
    probs = _softmax_categorize(10.0)
    assert max(probs, key=probs.get) == "급상승"


def test_softmax_categorize_extreme_negative_score_favors_surge_down():
    probs = _softmax_categorize(-10.0)
    assert max(probs, key=probs.get) == "급하락"


def test_softmax_categorize_zero_score_favors_sideways():
    probs = _softmax_categorize(0.0)
    assert max(probs, key=probs.get) == "횡보"


def test_softmax_categorize_all_probabilities_nonnegative():
    probs = _softmax_categorize(-3.5)
    assert all(p >= 0.0 for p in probs.values())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_detector'`

- [ ] **Step 3: 최소 구현 작성**

`engine/regime_detector.py` 신규 생성:

```python
"""
engine/regime_detector.py

실시간 장세 판별 — 규칙기반 EWMA 위험조정 모멘텀 스코어로 매 봉 인과적으로
5개 장세 카테고리의 확률벡터를 산출한다. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md
"""
from __future__ import annotations

import math

TEMPERATURE = 1.0

CATEGORY_REFERENCE_SCORES: dict[str, float] = {
    "급하락": -2.0,
    "완만하락": -0.7,
    "횡보": 0.0,
    "완만상승": 0.7,
    "급상승": 2.0,
}


def _softmax_categorize(score: float, temperature: float = TEMPERATURE) -> dict[str, float]:
    """score와 각 카테고리 대표값의 거리에 softmax를 적용해 확률벡터를 만든다.
    합계는 항상 1.0."""
    labels = list(CATEGORY_REFERENCE_SCORES.keys())
    neg_distances = [
        -abs(score - CATEGORY_REFERENCE_SCORES[label]) / temperature for label in labels
    ]
    max_val = max(neg_distances)
    exp_vals = [math.exp(v - max_val) for v in neg_distances]
    total = sum(exp_vals)
    return {label: exp_val / total for label, exp_val in zip(labels, exp_vals)}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_detector.py tests/test_regime_detector.py
git commit -m "feat: 장세 카테고리 softmax 확률화 함수 추가"
```

---

## Task 2: 타임프레임 → half-life 봉 수 환산

**Files:**
- Modify: `engine/regime_detector.py`
- Test: `tests/test_regime_detector.py`

**Interfaces:**
- Consumes: `upbit_data_service.timeframe_duration(timeframe: str) -> timedelta`
- Produces: `HALF_LIFE_DAYS: float`, `half_life_bars_for_timeframe(timeframe: str) -> float`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_detector.py`에 추가:

```python
from engine.regime_detector import half_life_bars_for_timeframe


def test_half_life_bars_for_timeframe_days_is_one():
    assert half_life_bars_for_timeframe("days") == pytest.approx(1.0)


def test_half_life_bars_for_timeframe_minutes60_is_24():
    assert half_life_bars_for_timeframe("minutes60") == pytest.approx(24.0)


def test_half_life_bars_for_timeframe_minutes15_is_96():
    assert half_life_bars_for_timeframe("minutes15") == pytest.approx(96.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: FAIL — `ImportError: cannot import name 'half_life_bars_for_timeframe'`

- [ ] **Step 3: 최소 구현 작성**

`engine/regime_detector.py`의 import 구역과 `TEMPERATURE = 1.0` 사이에 추가:

```python
from upbit_data_service import timeframe_duration

HALF_LIFE_DAYS = 1.0
```

파일 하단(softmax 함수 뒤)에 추가:

```python
def half_life_bars_for_timeframe(timeframe: str) -> float:
    """전략의 timeframe(예: 'minutes60', 'days')에서 HALF_LIFE_DAYS에 해당하는 봉 수를
    환산한다. 타임프레임이 달라도 체감 반응속도가 동일하게 유지된다."""
    bar_seconds = timeframe_duration(timeframe).total_seconds()
    return HALF_LIFE_DAYS * 86400.0 / bar_seconds
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_detector.py tests/test_regime_detector.py
git commit -m "feat: 타임프레임별 half-life 봉수 환산 함수 추가"
```

---

## Task 3: EWMA 위험조정 모멘텀 스코어 + regime_probs 계산

> **정정(Task 3 최종리뷰+재검증, 2026-08-23, 커밋 a9587ad):** 아래 Step 3의
> `_ewm_series(..., abs_values=True)`/`returns.abs().ewm(...).mean()` 기반 변동성 계산은
> **버그다** — 분자·분모가 같은 가중치 구조라 삼각부등식에 의해 score가 항상 [-1, 1]에
> 갇혀 급상승/급하락(±2.0) 카테고리에 영원히 도달할 수 없다(실측: 랜덤 시뮬레이션에서
> `|score|` 최댓값 0.991, 하루 +2%/+50% 추세가 똑같이 score=1.0). **실제로 구현된 최종
> 코드는 변동성을 `returns.ewm(halflife=half_life_bars).std()`(지수가중 표준편차)로
> 계산한다** — `abs_values` 파라미터는 제거되고 `_ewm_series`는 momentum 전용으로
> 단순화됐다. 아래 코드 블록은 이 태스크가 처음 실행됐을 때의 스냅샷이라 이 버그를
> 그대로 담고 있으니, 이 플랜을 다시 실행하거나 참고할 일이 있으면 **아래 코드가 아니라
> `engine/regime_detector.py`의 실제 코드 + 스펙 문서의 정정 노트를 따를 것.**

**Files:**
- Modify: `engine/regime_detector.py`
- Test: `tests/test_regime_detector.py`

**Interfaces:**
- Consumes: `CATEGORY_REFERENCE_SCORES`, `_softmax_categorize`(Task 1)
- Produces: `WARMUP_MULTIPLIER: float`, `ewm_volatility(returns: pd.Series, half_life_bars: float) -> float`,
  `compute_regime_probs_series(df: pd.DataFrame, half_life_bars: float) -> list[dict[str, float] | None]`,
  `compute_regime_probs(df: pd.DataFrame, half_life_bars: float) -> dict[str, float] | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_detector.py` 상단 import에 추가:

```python
import pandas as pd

from engine.regime_detector import (
    compute_regime_probs,
    compute_regime_probs_series,
    ewm_volatility,
)
```

파일 하단에 추가:

```python
def _make_price_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"candle_time": dates, "close": closes})


def test_ewm_volatility_of_constant_returns_equals_abs_value():
    returns = pd.Series([0.01] * 30)
    vol = ewm_volatility(returns, half_life_bars=5.0)
    assert vol == pytest.approx(0.01, rel=1e-6)


def test_compute_regime_probs_none_when_insufficient_warmup():
    df = _make_price_df([100.0, 101.0, 102.0])
    assert compute_regime_probs(df, half_life_bars=24.0) is None


def test_compute_regime_probs_monotonic_uptrend_favors_up_categories():
    closes = [100.0 * (1.02**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) in ("완만상승", "급상승")


def test_compute_regime_probs_monotonic_downtrend_favors_down_categories():
    closes = [100.0 * (0.98**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) in ("완만하락", "급하락")


def test_compute_regime_probs_flat_prices_favor_sideways():
    closes = [100.0] * 60
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert max(probs, key=probs.get) == "횡보"


def test_compute_regime_probs_probabilities_sum_to_one():
    closes = [100.0 * (1.01**i) for i in range(60)]
    df = _make_price_df(closes)
    probs = compute_regime_probs(df, half_life_bars=3.0)
    assert probs is not None
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_compute_regime_probs_scale_invariant_across_volatility():
    """변동성이 다른 두 코인이 '위험조정 기준 동일한 강도'의 순수 추세일 때
    같은 카테고리가 우세해야 한다(변동성 정규화 검증)."""
    low_vol_closes = [100.0 * (1.005**i) for i in range(60)]
    high_vol_closes = [100.0 * (1.02**i) for i in range(60)]
    probs_low = compute_regime_probs(_make_price_df(low_vol_closes), half_life_bars=3.0)
    probs_high = compute_regime_probs(_make_price_df(high_vol_closes), half_life_bars=3.0)
    assert probs_low is not None and probs_high is not None
    assert max(probs_low, key=probs_low.get) == max(probs_high, key=probs_high.get)


def test_compute_regime_probs_shorter_half_life_reacts_faster_to_recent_reversal():
    """앞 40봉 하락 후 뒤 15봉 급격히 상승 반전 — half-life가 짧을수록
    반전 이후 상승쪽 확률 합이 더 커야 한다."""
    down_leg = [100.0 * (0.98**i) for i in range(40)]
    up_leg = [down_leg[-1] * (1.03**i) for i in range(1, 16)]
    df = _make_price_df(down_leg + up_leg)

    probs_fast = compute_regime_probs(df, half_life_bars=2.0)
    probs_slow = compute_regime_probs(df, half_life_bars=8.0)
    assert probs_fast is not None and probs_slow is not None

    fast_up = probs_fast["완만상승"] + probs_fast["급상승"]
    slow_up = probs_slow["완만상승"] + probs_slow["급상승"]
    assert fast_up > slow_up


def test_compute_regime_probs_series_matches_pointwise_calls():
    """벡터화 버전이 매 시점 truncated df로 개별 호출한 것과 동일한 결과를 내는지
    고정한다(인과성 회귀가드 — 미래 데이터가 새어 들어가면 이 테스트가 깨진다)."""
    closes = [100.0 * (1.01**i) for i in range(60)]
    df = _make_price_df(closes)
    half_life_bars = 3.0

    series = compute_regime_probs_series(df, half_life_bars)
    assert len(series) == len(df)

    for t in (20, 40, 59):
        pointwise = compute_regime_probs(df.iloc[: t + 1], half_life_bars)
        assert series[t] == pointwise
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_regime_probs'`

- [ ] **Step 3: 최소 구현 작성**

`engine/regime_detector.py` 상단 import에 `pandas` 추가:

```python
import pandas as pd
```

파일 하단(`half_life_bars_for_timeframe` 뒤)에 추가:

```python
WARMUP_MULTIPLIER = 5.0
_MIN_VOLATILITY_FLOOR = 1e-6


def _ewm_series(returns: pd.Series, half_life_bars: float, abs_values: bool = False) -> pd.Series:
    series = returns.abs() if abs_values else returns
    return series.ewm(halflife=half_life_bars).mean()


def ewm_volatility(returns: pd.Series, half_life_bars: float) -> float:
    """수익률 절댓값의 지수가중이동평균(가장 최근 값) — 변동성 정규화용."""
    return float(_ewm_series(returns, half_life_bars, abs_values=True).iloc[-1])


def compute_regime_probs_series(
    df: pd.DataFrame, half_life_bars: float
) -> list[dict[str, float] | None]:
    """df 전체에 대해 매 봉마다의 regime_probs를 O(n)에 한 번에 계산한다(검증스크립트용
    벡터화 버전 — compute_regime_probs(df.iloc[:t+1], ...)를 매 t마다 반복호출하면
    O(n^2)라 느림). 두 방식이 동일한 결과를 내는지는
    test_compute_regime_probs_series_matches_pointwise_calls로 고정한다."""
    min_bars = int(half_life_bars * WARMUP_MULTIPLIER)
    returns = df["close"].pct_change(fill_method=None)
    valid_counts = returns.notna().cumsum()
    momentum_series = _ewm_series(returns, half_life_bars)
    volatility_series = _ewm_series(returns, half_life_bars, abs_values=True)

    results: list[dict[str, float] | None] = []
    for i in range(len(df)):
        if int(valid_counts.iloc[i]) < min_bars:
            results.append(None)
            continue
        momentum = momentum_series.iloc[i]
        volatility = volatility_series.iloc[i]
        if pd.isna(momentum) or pd.isna(volatility):
            results.append(None)
            continue
        score = momentum / max(volatility, _MIN_VOLATILITY_FLOOR)
        results.append(_softmax_categorize(score))
    return results


def compute_regime_probs(df: pd.DataFrame, half_life_bars: float) -> dict[str, float] | None:
    """df: candle_time 오름차순, close 컬럼 포함(get_candles()가 반환하는 형태 그대로).
    워밍업(half_life_bars * WARMUP_MULTIPLIER) 미만이면 None(판단불가) 반환."""
    series = compute_regime_probs_series(df, half_life_bars)
    return series[-1] if series else None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: PASS (18 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_detector.py tests/test_regime_detector.py
git commit -m "feat: EWMA 위험조정 모멘텀 기반 regime_probs 계산 추가"
```

---

## Task 4: 실현수익률 하드 분류 (검증스크립트용 정답 라벨링)

**Files:**
- Modify: `engine/regime_detector.py`
- Test: `tests/test_regime_detector.py`

**Interfaces:**
- Consumes: `CATEGORY_REFERENCE_SCORES`(Task 1)
- Produces: `classify_score_to_category(score: float) -> str`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_detector.py` 상단 import에 `classify_score_to_category` 추가, 파일
하단에 추가:

```python
def test_classify_score_to_category_boundaries():
    assert classify_score_to_category(-5.0) == "급하락"
    assert classify_score_to_category(-1.0) == "완만하락"
    assert classify_score_to_category(0.0) == "횡보"
    assert classify_score_to_category(1.0) == "완만상승"
    assert classify_score_to_category(5.0) == "급상승"


def test_classify_score_to_category_at_exact_midpoint_goes_to_higher_bucket():
    # 완만하락(-0.7)과 횡보(0.0) 사이 중간점 = -0.35
    assert classify_score_to_category(-0.35) == "횡보"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: FAIL — `ImportError: cannot import name 'classify_score_to_category'`

- [ ] **Step 3: 최소 구현 작성**

`engine/regime_detector.py` 파일 맨 끝에 추가:

```python
def classify_score_to_category(score: float) -> str:
    """score를 CATEGORY_REFERENCE_SCORES 대표값 사이 중간점 경계로 하드 분류한다
    (검증스크립트가 실현수익률의 "정답" 카테고리를 매길 때 사용 — compute_regime_probs의
    softmax 확률과 달리 단일 라벨만 반환)."""
    ordered = sorted(CATEGORY_REFERENCE_SCORES.items(), key=lambda kv: kv[1])
    for i in range(len(ordered) - 1):
        label, ref = ordered[i]
        _next_label, next_ref = ordered[i + 1]
        boundary = (ref + next_ref) / 2
        if score < boundary:
            return label
    return ordered[-1][0]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_detector.py tests/test_regime_detector.py
git commit -m "feat: 실현수익률 정답 라벨링용 하드 분류 함수 추가"
```

---

## Task 5: 검증 스크립트 (`scripts/regime_backtest.py`)

**Files:**
- Create: `scripts/regime_backtest.py`

**Interfaces:**
- Consumes: `engine.regime_detector.compute_regime_probs_series`,
  `engine.regime_detector.classify_score_to_category`, `engine.regime_detector.ewm_volatility`,
  `engine.regime_detector.half_life_bars_for_timeframe`,
  `engine.regime_detector.CATEGORY_REFERENCE_SCORES`, `upbit_data_service.get_candles`
- Produces: 콘솔 출력(리포트 파일 없음, pytest 대상 아님 — 사람이 실행해 눈으로 확인)

이 태스크는 pytest 테스트가 아니라 스크립트를 실제로 실행해 출력을 확인하는 것으로
검증한다(스펙의 "사람이 눈으로 보는 도구" 방침).

- [ ] **Step 1: 스크립트 작성**

`scripts/regime_backtest.py` 신규 생성:

```python
"""
scripts/regime_backtest.py

engine.regime_detector.compute_regime_probs_series()가 실제로 쓸모 있는지 과거 캔들로
검증한다. 규칙기반 결정론적 함수라 학습 없이 지금 바로 확인 가능. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from engine.regime_detector import (
    CATEGORY_REFERENCE_SCORES,
    classify_score_to_category,
    compute_regime_probs_series,
    ewm_volatility,
    half_life_bars_for_timeframe,
)
from upbit_data_service import get_candles

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TIMEFRAME = "minutes60"
N_MULTIPLIER = 2.5
VALIDATION_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
VALIDATION_END = datetime.now(timezone.utc)


def _evaluate_market(market: str, half_life_bars: float, n_bars: int) -> dict[str, dict[str, int]]:
    """market 하나의 카테고리별 hit/total 카운트를 반환한다."""
    df = get_candles(market, TIMEFRAME, VALIDATION_START, VALIDATION_END)
    closes = df["close"]
    returns = closes.pct_change(fill_method=None)
    regime_series = compute_regime_probs_series(df, half_life_bars)

    counts: dict[str, dict[str, int]] = {
        label: {"hit": 0, "total": 0} for label in CATEGORY_REFERENCE_SCORES
    }

    for t in range(len(df) - n_bars):
        probs = regime_series[t]
        if probs is None:
            continue
        predicted = max(probs, key=probs.get)

        future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
        if future_returns.empty or future_returns.isna().any():
            continue
        realized_return = closes.iloc[t + n_bars] / closes.iloc[t] - 1.0
        realized_volatility = ewm_volatility(future_returns, half_life_bars)
        if realized_volatility <= 0:
            continue
        normalized_realized = realized_return / realized_volatility
        actual = classify_score_to_category(normalized_realized)

        counts[predicted]["total"] += 1
        if actual == predicted:
            counts[predicted]["hit"] += 1

    return counts


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={TIMEFRAME}")

    for market in MARKETS:
        print(f"\n=== {market} ({TIMEFRAME}) ===")
        counts = _evaluate_market(market, half_life_bars, n_bars)
        for label in CATEGORY_REFERENCE_SCORES:
            c = counts[label]
            if c["total"] == 0:
                print(f"  {label}: 샘플 없음")
                continue
            hit_rate = c["hit"] / c["total"] * 100
            print(f"  {label}: {c['hit']}/{c['total']} 적중 ({hit_rate:.1f}%)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실제 실행해 출력 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py`

Expected: 각 코인·카테고리별로 `X/Y 적중 (Z%)` 형식의 출력이 나온다(캔들 데이터를 실제로
가져오므로 최초 실행 시 네트워크 호출로 몇 초~몇십 초 걸릴 수 있음). 5개 카테고리 무작위
추측 기준선(20%)보다 "급상승"/"급하락"/"횡보" 같은 뚜렷한 카테고리의 hit-rate가 유의미하게
높은지 눈으로 확인한다. 만약 전 카테고리가 20% 근처거나 특정 카테고리에 "샘플 없음"이
계속 뜨면(예: 급상승/급하락이 전혀 안 잡힘), `CATEGORY_REFERENCE_SCORES`나
`HALF_LIFE_DAYS` 튜닝이 필요하다는 신호이니 결과를 기록해 사용자와 공유한다.

- [ ] **Step 3: 커밋**

```bash
git add scripts/regime_backtest.py
git commit -m "feat: 장세 판별기 정확도 검증 스크립트 추가"
```

---

## Self-Review 결과

- **스펙 커버리지**: 판별 로직(Task 1~3), 워밍업/0-나눔 가드(Task 3), 5카테고리+대표값(Task
  1), half-life 타임프레임 환산(Task 2), 검증 스크립트와 "맞다" 기준(Task 4~5), 테스트 5종
  전부(Task 1~3에 분산) — 스펙의 모든 섹션에 대응하는 태스크가 있다. 휩쏘 방지/프리셋
  매핑/라이브 전환/자동튜닝은 스펙이 명시한 비범위라 태스크 없음(의도됨).
- **플레이스홀더 스캔**: TBD/TODO 없음. 모든 스텝에 완전한 코드가 있음.
- **타입 일관성**: `compute_regime_probs`/`compute_regime_probs_series`가
  `dict[str, float] | None`을 일관되게 쓰고, `half_life_bars`는 모든 함수 시그니처에서
  `float`로 통일됨. `ewm_volatility`가 Task 3에서 정의되고 Task 5(검증스크립트)에서 그대로
  같은 시그니처로 재사용됨 — 드리프트 없음.
