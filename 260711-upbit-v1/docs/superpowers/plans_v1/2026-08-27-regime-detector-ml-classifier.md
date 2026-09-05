# 장세 판별기 ML 전환 (2단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 규칙기반 EWMA 장세 판별기(`engine/regime_detector.py`)를 대체할 후보로, LightGBM 지도학습 파이프라인(학습+워크포워드 검증)을 만들어 규칙기반 대비 예측력이 실제로 개선되는지 실측한다.

**Architecture:** 레이블(정규화 실현수익률 기반 5분류, fold별 quantile 경계) → 피처(`trading.live_indicators.LIVE_INDICATOR_FACTORY` 전량 + `engine/regime_features.py` 5종 + raw_score) → 워크포워드 fold 생성(embargo로 레이블 누수 방지) → 각 fold에서 LightGBM 학습·평가 → 콘솔 리포트(기존 `/regime` 대시보드와 동일 정의: confusion matrix/hit-rate/상관계수) + 모델 아티팩트 저장. 4개의 독립 모듈(레이블/분할/피처/데이터로더)을 얇은 오케스트레이션 스크립트가 조립한다.

**Tech Stack:** Python, pandas, LightGBM(신규), pytest. 기존 `engine/regime_detector.py`, `backend/regime_service.py`, `trading/live_indicators.py`, `upbit_data_service.py`, `binance_data_service.py`, `external_data_service.py` 재사용.

## Global Constraints

- 스펙: `docs/superpowers/specs_v1/2026-08-27-regime-detector-ml-classifier-design.md`
- `engine/regime_detector.py`는 변경하지 않는다(비교 기준으로 그대로 유지)
- 이번 계획 범위는 학습+검증 파이프라인까지 — `/regime` 대시보드, API, 라이브 데몬 통합 없음
- 하이퍼파라미터 자동튜닝 없음(합리적 기본값 사용)
- 대상: KRW-BTC/KRW-ETH/KRW-XRP, `minutes60`(1시간봉), 2024-01-01~현재만 지원
- 모델 저장 위치: `data/regime_ml_models/`(기존 `.gitignore`의 `data/` 규칙에 이미 포함되어 커밋되지 않음)
- 레이블 타깃: `backend/regime_service.py`의 `normalized_realized` 계산(다음 n_bars 평균수익률 / 이후 EWM변동성)을 그대로 재사용
- 카테고리 경계는 각 워크포워드 fold의 훈련구간에서만 분위수(2%/16%/84%/98%)로 계산(고정값 금지)
- 마켓은 풀링 단일 모델(코인별 개별 모델 아님), `market` 범주형 피처로 구분

---

## Task 1: 레이블 계산 모듈 (`engine/regime_ml_labels.py`)

**Files:**
- Create: `engine/regime_ml_labels.py`
- Test: `tests/test_regime_ml_labels.py`

**Interfaces:**
- Consumes: `engine.regime_detector.ewm_volatility(returns: pd.Series, half_life_bars: float) -> float`(기존 함수)
- Produces:
  - `CATEGORY_LABELS: list[str]` = `["급하락", "완만하락", "횡보", "완만상승", "급상승"]`
  - `compute_normalized_realized_series(df: pd.DataFrame, half_life_bars: float, n_bars: int) -> pd.Series`
  - `compute_quantile_boundaries(values: pd.Series, quantiles: tuple[float, ...] = (0.02, 0.16, 0.84, 0.98)) -> list[float]`
  - `bucket_to_category(value: float, boundaries: list[float]) -> str`
  - `category_representative_scores(values: pd.Series, boundaries: list[float]) -> dict[str, float]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_labels.py`:
```python
"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels의 레이블 생성 함수를 검증한다. compute_normalized_realized_series는
backend/regime_service.py:evaluate_market()의 정규화 실현수익률 루프(100~119행)와 동일한
값을 내야 한다(같은 잣대로 규칙기반과 비교하기 위함).
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine.regime_ml_labels import (
    CATEGORY_LABELS,
    bucket_to_category,
    category_representative_scores,
    compute_normalized_realized_series,
    compute_quantile_boundaries,
)


def _make_close_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


def test_compute_normalized_realized_series_matches_evaluate_market_formula():
    # 상승폭이 점점 커지는 시계열 — 뒤로 갈수록 정규화 실현수익률이 커져야 함
    closes = [100.0 * (1.001**i) for i in range(80)]
    df = _make_close_df(closes)
    half_life_bars = 24.0
    n_bars = 60

    series = compute_normalized_realized_series(df, half_life_bars, n_bars)

    assert len(series) == len(df)
    # 마지막 n_bars 구간은 미래 데이터가 없어 NaN
    assert series.iloc[-n_bars:].isna().all()
    # 워밍업 이후 앞부분은 값이 존재
    assert series.iloc[0:len(df) - n_bars].notna().all()


def test_compute_normalized_realized_series_returns_all_nan_when_too_short():
    df = _make_close_df([100.0, 101.0, 102.0])
    series = compute_normalized_realized_series(df, half_life_bars=24.0, n_bars=60)
    assert series.isna().all()
    assert len(series) == 3


def test_compute_quantile_boundaries_are_ascending_and_within_range():
    values = pd.Series([float(i) for i in range(1, 101)])  # 1..100
    boundaries = compute_quantile_boundaries(values, quantiles=(0.02, 0.16, 0.84, 0.98))

    assert len(boundaries) == 4
    assert boundaries == sorted(boundaries)
    assert values.min() <= boundaries[0]
    assert boundaries[-1] <= values.max()


def test_compute_quantile_boundaries_ignores_nan():
    values = pd.Series([1.0, 2.0, float("nan"), 3.0, 4.0, float("nan"), 5.0])
    boundaries = compute_quantile_boundaries(values, quantiles=(0.25, 0.4, 0.6, 0.75))
    assert all(b == b for b in boundaries)  # NaN이 섞이지 않음


def test_compute_quantile_boundaries_raises_when_all_nan():
    values = pd.Series([float("nan"), float("nan")])
    with pytest.raises(ValueError, match="표본이 없습니다"):
        compute_quantile_boundaries(values)


def test_bucket_to_category_assigns_correct_label():
    boundaries = [-10.0, -1.0, 1.0, 10.0]
    assert bucket_to_category(-20.0, boundaries) == "급하락"
    assert bucket_to_category(-10.0, boundaries) == "완만하락"  # 경계값은 다음 구간(>=)
    assert bucket_to_category(0.0, boundaries) == "횡보"
    assert bucket_to_category(5.0, boundaries) == "완만상승"
    assert bucket_to_category(100.0, boundaries) == "급상승"


def test_category_representative_scores_uses_median_of_bucket():
    # 급하락 구간에 -20, -15 두 값 -> 중앙값 -17.5
    values = pd.Series([-20.0, -15.0, 0.0, 0.0, 5.0, 5.0, 20.0])
    boundaries = [-10.0, -1.0, 1.0, 10.0]

    scores = category_representative_scores(values, boundaries)

    assert set(scores.keys()) == set(CATEGORY_LABELS)
    assert scores["급하락"] == pytest.approx(-17.5)
    assert scores["횡보"] == pytest.approx(0.0)


def test_category_representative_scores_falls_back_when_bucket_empty():
    # "완만하락" 구간(-10<=v<-1)에 값이 하나도 없음
    values = pd.Series([-20.0, 0.0, 20.0])
    boundaries = [-10.0, -1.0, 1.0, 10.0]

    scores = category_representative_scores(values, boundaries)

    assert scores["완만하락"] == pytest.approx((boundaries[0] + boundaries[1]) / 2)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_ml_labels.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_ml_labels'`

- [ ] **Step 3: 구현 작성**

`engine/regime_ml_labels.py`:
```python
"""
engine/regime_ml_labels.py

장세 판별 ML 분류기의 레이블(정답 카테고리)을 만든다. 정규화 실현수익률 정의는
backend/regime_service.py:evaluate_market()의 100~119행 루프와 동일하다(같은 잣대로
규칙기반과 ML을 비교하기 위함) — 카테고리 경계만 고정값이 아니라 fold별 훈련구간
분위수로 계산한다는 점이 다르다. 설계 문서:
docs/superpowers/specs_v1/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

import pandas as pd

from engine.regime_detector import ewm_volatility

CATEGORY_LABELS: list[str] = ["급하락", "완만하락", "횡보", "완만상승", "급상승"]


def compute_normalized_realized_series(
    df: pd.DataFrame, half_life_bars: float, n_bars: int
) -> pd.Series:
    """df["close"] 기준 각 시점 t에서 "다음 n_bars 평균수익률 / 이후 EWM변동성"을 계산한다.
    미래 데이터가 부족한 마지막 n_bars 구간, 또는 구간 내 결측이 있으면 NaN."""
    returns = df["close"].pct_change(fill_method=None)
    values: list[float] = [float("nan")] * len(df)
    for t in range(max(len(df) - n_bars, 0)):
        future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
        if future_returns.empty or future_returns.isna().any():
            continue
        realized_volatility = ewm_volatility(future_returns, half_life_bars)
        if realized_volatility <= 0:
            continue
        values[t] = future_returns.mean() / realized_volatility
    return pd.Series(values, index=df.index)


def compute_quantile_boundaries(
    values: pd.Series, quantiles: tuple[float, ...] = (0.02, 0.16, 0.84, 0.98)
) -> list[float]:
    """values(NaN 제외)에서 quantiles에 해당하는 경계값을 오름차순으로 반환한다."""
    clean = values.dropna()
    if clean.empty:
        raise ValueError("경계값을 계산할 표본이 없습니다")
    return [float(clean.quantile(q)) for q in quantiles]


def bucket_to_category(value: float, boundaries: list[float]) -> str:
    """boundaries(오름차순 4개)를 기준으로 value를 5개 카테고리 중 하나로 분류한다.
    engine.regime_detector.classify_score_to_category와 같은 "미만이면 그 카테고리"
    규칙을 쓴다."""
    for label, boundary in zip(CATEGORY_LABELS[:-1], boundaries):
        if value < boundary:
            return label
    return CATEGORY_LABELS[-1]


def category_representative_scores(
    values: pd.Series, boundaries: list[float]
) -> dict[str, float]:
    """각 카테고리 구간에 속한 values의 중앙값을 대표값으로 반환한다(회귀 상관계수 계산용
    expected_score 산출에 씀). 구간에 표본이 하나도 없으면(fold 초반 등) 양끝 카테고리는
    해당 경계값, 중간 카테고리는 인접 경계값의 중점으로 대체한다."""
    clean = values.dropna()
    labels_per_value = clean.apply(lambda v: bucket_to_category(v, boundaries))

    result: dict[str, float] = {}
    for i, label in enumerate(CATEGORY_LABELS):
        bucket_values = clean[labels_per_value == label]
        if not bucket_values.empty:
            result[label] = float(bucket_values.median())
        elif i == 0:
            result[label] = boundaries[0]
        elif i == len(CATEGORY_LABELS) - 1:
            result[label] = boundaries[-1]
        else:
            result[label] = (boundaries[i - 1] + boundaries[i]) / 2
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_labels.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_labels.py tests/test_regime_ml_labels.py
git commit -m "feat: ML 장세 판별기 레이블(정규화 실현수익률 fold별 quantile 분류) 계산 모듈 추가"
```

---

## Task 2: 워크포워드 fold 분할 모듈 (`engine/regime_ml_splits.py`)

**Files:**
- Create: `engine/regime_ml_splits.py`
- Test: `tests/test_regime_ml_splits.py`

**Interfaces:**
- Consumes: 없음(순수 datetime 계산)
- Produces:
  - `@dataclass(frozen=True) class WalkForwardFold: fold_index: int; train_end: datetime; test_start: datetime; test_end: datetime`
  - `generate_walk_forward_folds(start: datetime, end: datetime, n_folds: int, embargo: timedelta) -> list[WalkForwardFold]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_splits.py`:
```python
"""
tests/test_regime_ml_splits.py

engine.regime_ml_splits.generate_walk_forward_folds()를 검증한다. expanding window로
test 구간을 겹침 없이 나누고, train_end는 embargo만큼 test_start 이전으로 당겨져야
한다(레이블이 미래 n_bars를 보는 데서 오는 누수를 막기 위함).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.regime_ml_splits import WalkForwardFold, generate_walk_forward_folds

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=400)


def test_folds_cover_full_range_without_gap_or_overlap():
    folds = generate_walk_forward_folds(START, END, n_folds=4, embargo=timedelta(days=3))

    assert len(folds) == 4
    assert folds[0].test_start == START
    assert folds[-1].test_end == END
    for i in range(len(folds) - 1):
        assert folds[i].test_end == folds[i + 1].test_start


def test_train_end_respects_embargo():
    embargo = timedelta(days=3)
    folds = generate_walk_forward_folds(START, END, n_folds=4, embargo=embargo)

    for fold in folds:
        assert fold.train_end == fold.test_start - embargo


def test_fold_index_is_sequential():
    folds = generate_walk_forward_folds(START, END, n_folds=3, embargo=timedelta(days=1))
    assert [f.fold_index for f in folds] == [0, 1, 2]


def test_raises_when_n_folds_less_than_one():
    with pytest.raises(ValueError, match="n_folds"):
        generate_walk_forward_folds(START, END, n_folds=0, embargo=timedelta(days=1))


def test_raises_when_end_before_start():
    with pytest.raises(ValueError, match="end"):
        generate_walk_forward_folds(END, START, n_folds=2, embargo=timedelta(days=1))


def test_fold_is_frozen_dataclass():
    fold = WalkForwardFold(0, START, START, END)
    with pytest.raises(Exception):
        fold.fold_index = 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_ml_splits.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_ml_splits'`

- [ ] **Step 3: 구현 작성**

`engine/regime_ml_splits.py`:
```python
"""
engine/regime_ml_splits.py

ML 장세 판별기 학습을 위한 워크포워드(walk-forward) 검증 fold 경계를 만든다. 무작위
shuffle 대신 시간순으로 test 구간을 나누고, train은 각 fold의 test 시작 이전 전체
데이터로 정의한다(expanding window) — 금융 시계열의 미래정보 누수를 막기 위함. 설계
문서: docs/superpowers/specs_v1/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_end: datetime
    test_start: datetime
    test_end: datetime


def generate_walk_forward_folds(
    start: datetime, end: datetime, n_folds: int, embargo: timedelta
) -> list[WalkForwardFold]:
    """[start, end]를 n_folds개의 동일 너비 test 구간으로 순서대로 나눈다. 각 fold의
    train_end는 test_start - embargo다 — 레이블이 미래 n_bars를 내다보므로, embargo가
    n_bars에 해당하는 기간(bar_duration * n_bars)만큼은 돼야 train/test 사이 레이블
    누수가 없다. 초반 fold는 train_end가 start보다 이전일 수 있다(훈련 표본 부족) —
    호출자가 최소 표본 수를 별도로 검증해야 한다."""
    if n_folds < 1:
        raise ValueError("n_folds는 1 이상이어야 합니다")
    if end <= start:
        raise ValueError("end는 start보다 이후여야 합니다")

    fold_width = (end - start) / n_folds
    folds: list[WalkForwardFold] = []
    for i in range(n_folds):
        test_start = start + fold_width * i
        test_end = end if i == n_folds - 1 else start + fold_width * (i + 1)
        train_end = test_start - embargo
        folds.append(WalkForwardFold(i, train_end, test_start, test_end))
    return folds
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_splits.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_splits.py tests/test_regime_ml_splits.py
git commit -m "feat: ML 장세 판별기 워크포워드 fold 분할 모듈 추가"
```

---

## Task 3: 피처 매트릭스 모듈 (`engine/regime_ml_features.py`)

**Files:**
- Create: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`

**Interfaces:**
- Consumes:
  - `trading.live_indicators.LIVE_INDICATOR_FACTORY: dict[str, Callable[[pd.DataFrame], pd.Series]]`(기존)
  - `engine.regime_features.{volume_confirm, pivot_levels, vpin_score, level_proximity, reversal_gate}`(기존)
- Produces: `build_feature_matrix(df: pd.DataFrame, market: str, half_life_bars: float) -> pd.DataFrame` — 입력 df와 같은 행 수/인덱스, 컬럼은 `LIVE_INDICATOR_FACTORY`의 키 전부 + `"RAW_SCORE"`, `"VOLUME_CONFIRM"`, `"VPIN_SCORE"`, `"LEVEL_PROXIMITY"`, `"REVERSAL_GATE"`, `"market"`(category dtype)

**입력 df 필수 컬럼**(scripts/regime_ml_data.py가 Task 4에서 준비): `close, high, low, volume, trade_value, btc_close, usdt_close, binance_close, fear_greed_value, funding_rate_value, korea_premium_value`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`:
```python
"""
tests/test_regime_ml_features.py

engine.regime_ml_features.build_feature_matrix()를 검증한다. LIVE_INDICATOR_FACTORY를
그대로 순회하므로, 반환 컬럼 집합이 그 레지스트리 키 전체 + regime 전용 5개 + market과
정확히 일치해야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.regime_ml_features import build_feature_matrix
from trading.live_indicators import LIVE_INDICATOR_FACTORY

_N = 150


def _make_full_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=_N, freq="h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, _N))
    high = close + rng.uniform(0.1, 1.0, _N)
    low = close - rng.uniform(0.1, 1.0, _N)
    volume = rng.uniform(10, 100, _N)
    return pd.DataFrame({
        "candle_time": dates,
        "close": close, "high": high, "low": low,
        "volume": volume, "trade_value": volume * close,
        "btc_close": close * 1.1, "usdt_close": np.full(_N, 1350.0),
        "binance_close": close / 1350.0,
        "fear_greed_value": rng.uniform(0, 100, _N),
        "funding_rate_value": rng.uniform(-0.05, 0.05, _N),
        "korea_premium_value": rng.uniform(-2, 2, _N),
    })


def test_build_feature_matrix_has_one_column_per_registered_indicator_plus_regime_features():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected_columns = (
        set(LIVE_INDICATOR_FACTORY.keys())
        | {"RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE", "market"}
    )
    assert set(result.columns) == expected_columns


def test_build_feature_matrix_preserves_row_count_and_index():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-ETH", half_life_bars=24.0)

    assert len(result) == len(df)
    assert list(result.index) == list(df.index)


def test_build_feature_matrix_sets_market_column_as_category():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-XRP", half_life_bars=24.0)

    assert (result["market"] == "KRW-XRP").all()
    assert str(result["market"].dtype) == "category"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_ml_features'`

- [ ] **Step 3: 구현 작성**

`engine/regime_ml_features.py`:
```python
"""
engine/regime_ml_features.py

장세 판별 ML 분류기의 피처 매트릭스를 만든다. trading.live_indicators.LIVE_INDICATOR_FACTORY
(이미 백트레이더 대비 골든테스트로 검증된 순수 pandas 지표)를 재구현 없이 그대로
순회하고, engine.regime_features.py의 반전게이팅 실험용 5개 함수 + momentum/volatility
EWMA(raw_score)를 더한다. I/O 없는 순수 함수 — 입력 df는
scripts/regime_ml_data.py가 준비한다. 설계 문서:
docs/superpowers/specs_v1/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

import pandas as pd

from engine.regime_features import (
    level_proximity,
    pivot_levels,
    reversal_gate,
    vpin_score,
    volume_confirm,
)
from trading.live_indicators import LIVE_INDICATOR_FACTORY

# engine.regime_detector._MIN_VOLATILITY_FLOOR와 값이 같아야 한다. 순환참조를 피하려고
# 별도 정의한다(engine/regime_features.py가 같은 이유로 이미 이렇게 하고 있음).
_MIN_VOLATILITY_FLOOR = 1e-6


def build_feature_matrix(df: pd.DataFrame, market: str, half_life_bars: float) -> pd.DataFrame:
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value를 전부 포함해야 한다
    (scripts.regime_ml_data.load_market_training_data()가 반환하는 형태). 반환
    DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN), 원본 OHLCV
    컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
    features: dict[str, pd.Series] = {
        name: factory(df) for name, factory in LIVE_INDICATOR_FACTORY.items()
    }

    returns = df["close"].pct_change(fill_method=None)
    momentum = returns.ewm(halflife=half_life_bars).mean()
    volatility = returns.ewm(halflife=half_life_bars).std()
    raw_score = momentum / volatility.clip(lower=_MIN_VOLATILITY_FLOOR)
    r1, s1 = pivot_levels(df["high"], df["low"], df["close"])
    proximity = level_proximity(df["close"], raw_score, r1, s1, volatility)
    vpin = vpin_score(df["volume"], df["close"])

    features["RAW_SCORE"] = raw_score
    features["VOLUME_CONFIRM"] = volume_confirm(df["trade_value"])
    features["VPIN_SCORE"] = vpin
    features["LEVEL_PROXIMITY"] = proximity
    features["REVERSAL_GATE"] = reversal_gate(vpin, proximity)

    result = pd.DataFrame(features, index=df.index)
    result["market"] = pd.Categorical([market] * len(df))
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_features.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_features.py tests/test_regime_ml_features.py
git commit -m "feat: ML 장세 판별기 피처 매트릭스 모듈 추가 (LIVE_INDICATOR_FACTORY 재사용)"
```

---

## Task 4: 마켓별 학습 데이터 로더 (`scripts/regime_ml_data.py`)

**Files:**
- Create: `scripts/regime_ml_data.py`
- Test: `tests/test_regime_ml_data.py`

**Interfaces:**
- Consumes(기존 함수, 전부 monkeypatch 대상):
  - `upbit_data_service.get_candles(market, timeframe, start, end) -> pd.DataFrame`
  - `binance_data_service.binance_symbol(market) -> str`(실패 시 `BinanceSymbolNotFoundError`)
  - `binance_data_service.get_binance_close(symbol, timeframe, start, end) -> pd.DataFrame`
  - `binance_data_service.get_binance_funding_rate(symbol, start, end) -> pd.DataFrame`
  - `binance_data_service.merge_funding_rate(df, funding_df) -> pd.DataFrame`
  - `external_data_service.get_fear_greed_cmc(start, end) -> pd.DataFrame`
  - `external_data_service.merge_fear_greed(df, fng_df) -> pd.DataFrame`
  - `trading.live_indicators.compute_korea_premium_value(df) -> pd.Series`
- Produces: `load_market_training_data(market: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame` — `get_candles()`가 주는 컬럼 + `btc_close, usdt_close, binance_close, fear_greed_value, funding_rate_value, korea_premium_value`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_data.py`:
```python
"""
tests/test_regime_ml_data.py

scripts.regime_ml_data.load_market_training_data()를 검증한다. backend/main.py의
_fetch_backtest_dataframe() 병합 패턴(get_candles + aux market close + 외부데이터
merge)을 조건트리 없이 항상 전체 재사용하되, 외부데이터 결측은 (그 컬럼이 완전히
비어있어도) 에러 없이 NaN으로 남긴다 — ML 피처는 LightGBM이 결측을 네이티브로
처리하므로 규칙기반 백테스트(backend/main.py)만큼 엄격할 필요가 없다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import scripts.regime_ml_data as regime_ml_data
from binance_data_service import BinanceSymbolNotFoundError
from scripts.regime_ml_data import load_market_training_data

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 3, tzinfo=timezone.utc)
_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume", "trade_value"]


def _make_candle_df(n: int, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    closes = [base + i for i in range(n)]
    return pd.DataFrame({
        "candle_time": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * n, "trade_value": [1.0] * n,
    })


def _patch_common(monkeypatch, *, symbol_found: bool = True):
    monkeypatch.setattr(regime_ml_data, "get_fear_greed_cmc", lambda *a, **k: pd.DataFrame(columns=["date", "fear_greed_value"]))
    monkeypatch.setattr(
        regime_ml_data, "merge_fear_greed",
        lambda df, fng_df: df.assign(fear_greed_value=float("nan")),
    )
    if symbol_found:
        monkeypatch.setattr(regime_ml_data, "binance_symbol", lambda market: "BTCUSDT")
        monkeypatch.setattr(regime_ml_data, "get_binance_close", lambda *a, **k: pd.DataFrame(columns=["candle_time", "close"]))
        monkeypatch.setattr(regime_ml_data, "get_binance_funding_rate", lambda *a, **k: pd.DataFrame(columns=["funding_time", "funding_rate_value"]))
        monkeypatch.setattr(
            regime_ml_data, "merge_funding_rate",
            lambda df, funding_df: df.assign(funding_rate_value=float("nan")),
        )
    else:
        def _raise_not_found(market):
            raise BinanceSymbolNotFoundError(market)
        monkeypatch.setattr(regime_ml_data, "binance_symbol", _raise_not_found)
    monkeypatch.setattr(regime_ml_data, "compute_korea_premium_value", lambda df: pd.Series([float("nan")] * len(df), index=df.index))


def test_load_market_training_data_has_all_required_columns(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: _make_candle_df(20))
    _patch_common(monkeypatch)

    df = load_market_training_data("KRW-ETH", "minutes60", START, END)

    required = {
        "close", "high", "low", "volume", "trade_value",
        "btc_close", "usdt_close", "binance_close",
        "fear_greed_value", "funding_rate_value", "korea_premium_value",
    }
    assert required.issubset(set(df.columns))
    assert len(df) == 20


def test_load_market_training_data_sets_btc_close_equal_to_close_for_btc_market(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: _make_candle_df(10))
    _patch_common(monkeypatch)

    df = load_market_training_data("KRW-BTC", "minutes60", START, END)

    assert (df["btc_close"] == df["close"]).all()


def test_load_market_training_data_tolerates_missing_binance_symbol(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: _make_candle_df(10))
    _patch_common(monkeypatch, symbol_found=False)

    df = load_market_training_data("KRW-WEIRD", "minutes60", START, END)

    assert df["binance_close"].isna().all()
    assert df["funding_rate_value"].isna().all()


def test_load_market_training_data_raises_when_primary_candles_empty(monkeypatch):
    monkeypatch.setattr(regime_ml_data, "get_candles", lambda market, tf, s, e: pd.DataFrame(columns=_CANDLE_COLUMNS))
    _patch_common(monkeypatch)

    with pytest.raises(ValueError, match="캔들 데이터가 없습니다"):
        load_market_training_data("KRW-BTC", "minutes60", START, END)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_ml_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.regime_ml_data'`

- [ ] **Step 3: 구현 작성**

`scripts/regime_ml_data.py`:
```python
"""
scripts/regime_ml_data.py

ML 장세 판별기 학습용 마켓별 데이터 로더. backend/main.py:_fetch_backtest_dataframe()의
병합 패턴(get_candles + aux market close + 외부데이터 merge)을 재사용하되, 그 함수는
FastAPI HTTPException과 조건트리(buy_dict/sell_dict)에 결합돼 있어 그대로 쓸 수 없다.
이 로더는 조건 없이 항상 전체 aux 데이터를 붙이고, 결측은 (에러 대신) NaN으로 남긴다 —
LightGBM이 결측 피처를 네이티브로 처리하므로 백테스트만큼 엄격할 필요가 없다(설계
문서 "B. 피처" 절 참고). 설계 문서:
docs/superpowers/specs_v1/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from binance_data_service import (
    BinanceSymbolNotFoundError,
    binance_symbol,
    get_binance_close,
    get_binance_funding_rate,
    merge_funding_rate,
)
from external_data_service import get_fear_greed_cmc, merge_fear_greed
from trading.live_indicators import compute_korea_premium_value
from upbit_data_service import get_candles

# engine/runner.py:AUX_MARKET_LINE_NAME과 값이 같아야 한다. engine/runner.py는
# backtrader Cerebro 실행기라 이 로더가 임포트할 이유가 없으므로 별도 정의한다
# (engine/regime_features.py의 _MIN_VOLATILITY_FLOOR와 같은 이유).
_AUX_MARKET_LINE_NAME = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}


def load_market_training_data(
    market: str, timeframe: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """market의 캔들 + 학습에 필요한 모든 aux 컬럼을 병합해 반환한다. market 자체의
    캔들이 비어있으면 ValueError(어떤 피처도 계산할 수 없으므로). 그 외(BTC/USDT
    상관관계용 aux 마켓, 바이낸스 심볼 부재, 외부데이터 커버리지 부족)는 NaN으로
    남기고 계속 진행한다."""
    df = get_candles(market, timeframe, start, end)
    if df.empty:
        raise ValueError(
            f"{market} {timeframe} 구간에 캔들 데이터가 없습니다: {start.date()}~{end.date()}"
        )

    for aux_market, line_name in _AUX_MARKET_LINE_NAME.items():
        if market == aux_market:
            df = df.assign(**{line_name: df["close"]})
            continue
        aux_df = get_candles(aux_market, timeframe, start, end)
        if aux_df.empty:
            df = df.assign(**{line_name: float("nan")})
            continue
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time", how="left",
        )
        df[line_name] = df[line_name].ffill().bfill()

    fng_df = get_fear_greed_cmc(start, end)
    df = merge_fear_greed(df, fng_df)

    try:
        symbol = binance_symbol(market)
    except BinanceSymbolNotFoundError:
        df = df.assign(binance_close=float("nan"), funding_rate_value=float("nan"))
    else:
        binance_df = get_binance_close(symbol, timeframe, start, end)
        if binance_df.empty:
            df = df.assign(binance_close=float("nan"))
        else:
            df = df.merge(
                binance_df.rename(columns={"close": "binance_close"}), on="candle_time", how="left"
            )
        funding_df = get_binance_funding_rate(symbol, start, end)
        df = merge_funding_rate(df, funding_df)

    df["korea_premium_value"] = compute_korea_premium_value(df)
    return df
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_data.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_ml_data.py tests/test_regime_ml_data.py
git commit -m "feat: ML 장세 판별기 마켓별 학습 데이터 로더 추가"
```

---

## Task 5: 학습+검증 오케스트레이션 스크립트 (`scripts/train_regime_ml.py`)

**Files:**
- Modify: `requirements.txt` (lightgbm 추가)
- Create: `scripts/train_regime_ml.py`
- Test: `tests/test_train_regime_ml.py`

**Interfaces:**
- Consumes:
  - `engine.regime_ml_labels.{CATEGORY_LABELS, compute_normalized_realized_series, compute_quantile_boundaries, bucket_to_category, category_representative_scores}`(Task 1)
  - `engine.regime_ml_splits.{WalkForwardFold, generate_walk_forward_folds}`(Task 2)
  - `engine.regime_ml_features.build_feature_matrix`(Task 3)
  - `scripts.regime_ml_data.load_market_training_data`(Task 4)
  - `engine.regime_detector.half_life_bars_for_timeframe`(기존)
  - `upbit_data_service.timeframe_duration`(기존)
  - `backend.regime_service.N_MULTIPLIER`(기존, = 2.5)
- Produces: `run_training(markets: list[str], timeframe: str, start: datetime, end: datetime, n_folds: int, min_train_samples: int, model_output_dir: Path) -> list[dict]` — fold별 리포트 딕셔너리 리스트(각각 `fold_index/n_train/n_test/confusion/actual_totals/correlation/top_features` 키), 마지막으로 학습에 성공한 fold의 LightGBM 모델을 `model_output_dir`에 저장. `top_features`는 gain 기준 중요도 상위 15개 `(피처명, 중요도)` 튜플 리스트(스펙 D절 "feature importance(gain) 출력" 요구사항)

- [ ] **Step 1: requirements.txt에 lightgbm 추가**

`requirements.txt`에 한 줄 추가:
```
lightgbm>=4.0
```

Run: `pip install -r requirements.txt`
Expected: lightgbm 설치 완료(다른 패키지는 이미 설치돼 있으므로 lightgbm만 새로 받음)

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`:
```python
"""
tests/test_train_regime_ml.py

scripts.train_regime_ml.run_training()의 end-to-end 스모크 테스트. 실제 네트워크
호출 없이(scripts.regime_ml_data.load_market_training_data를 monkeypatch) 합성
데이터로 전체 파이프라인(데이터 로드 -> 피처 -> fold 루프 -> LightGBM 학습 -> 리포트
-> 모델 저장)이 에러 없이 완주하는지만 검증한다. 개별 단계(레이블/분할/피처/로더)의
세부 동작은 각자의 유닛테스트(test_regime_ml_labels.py 등)가 이미 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import scripts.train_regime_ml as train_regime_ml
from scripts.train_regime_ml import run_training

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_N = 24 * 40  # minutes60, 40일치


def _make_synthetic_market_df(market: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START, periods=_N, freq="h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, _N))
    high = close + rng.uniform(0.1, 1.0, _N)
    low = close - rng.uniform(0.1, 1.0, _N)
    volume = rng.uniform(10, 100, _N)
    return pd.DataFrame({
        "candle_time": dates,
        "open": close, "high": high, "low": low, "close": close,
        "volume": volume, "trade_value": volume * close,
        "btc_close": close * 1.1, "usdt_close": np.full(_N, 1350.0),
        "binance_close": close / 1350.0,
        "fear_greed_value": rng.uniform(0, 100, _N),
        "funding_rate_value": rng.uniform(-0.05, 0.05, _N),
        "korea_premium_value": rng.uniform(-2, 2, _N),
    })


def test_run_training_completes_and_saves_model(tmp_path, monkeypatch):
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        model_output_dir=tmp_path,
    )

    assert len(reports) >= 1
    for report in reports:
        assert report["n_test"] > 0
        assert set(report["confusion"].keys()) <= {
            "급하락", "완만하락", "횡보", "완만상승", "급상승",
        }
        assert 1 <= len(report["top_features"]) <= 15
        assert all(isinstance(name, str) and isinstance(score, float) for name, score in report["top_features"])

    saved_models = list(tmp_path.glob("*.txt"))
    assert len(saved_models) == 1


def test_run_training_skips_folds_below_min_train_samples(tmp_path, monkeypatch):
    seeds = {"KRW-BTC": 1}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=10**9,  # 항상 표본 부족
        model_output_dir=tmp_path,
    )

    assert reports == []
    assert list(tmp_path.glob("*.txt")) == []
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.train_regime_ml'`

- [ ] **Step 4: 구현 작성**

`scripts/train_regime_ml.py`:
```python
"""
scripts/train_regime_ml.py

장세 판별기 ML 전환 — LightGBM 학습+워크포워드 검증 파이프라인. scripts/regime_backtest.py
(규칙기반 검증 CLI)와 나란히 비교할 수 있도록 같은 콘솔 리포트 형식(카테고리별 hit-rate/
confusion matrix/상관계수)을 쓴다. 설계 문서:
docs/superpowers/specs_v1/2026-08-27-regime-detector-ml-classifier-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from backend.regime_service import N_MULTIPLIER
from engine.regime_detector import half_life_bars_for_timeframe
from engine.regime_ml_features import build_feature_matrix
from engine.regime_ml_labels import (
    CATEGORY_LABELS,
    bucket_to_category,
    category_representative_scores,
    compute_normalized_realized_series,
    compute_quantile_boundaries,
)
from engine.regime_ml_splits import generate_walk_forward_folds
from scripts.regime_ml_data import load_market_training_data
from upbit_data_service import timeframe_duration

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TIMEFRAME = "minutes60"
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
TRAIN_END = datetime.now(timezone.utc)
N_FOLDS = 5
MIN_TRAIN_SAMPLES = 500
MODEL_OUTPUT_DIR = Path("data/regime_ml_models")


def run_training(
    markets: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    n_folds: int,
    min_train_samples: int,
    model_output_dir: Path,
) -> list[dict]:
    """마켓별로 데이터를 한 번씩만 로드/피처화(fold마다 반복하지 않음)하고, 워크포워드
    fold 루프를 돌며 LightGBM을 학습·평가한다. fold별 리포트 리스트를 반환하고,
    마지막으로 성공한 fold의 모델을 model_output_dir에 저장한다. 표본이
    min_train_samples 미만이거나 테스트 표본이 없는 fold는 건너뛴다."""
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    embargo = timeframe_duration(timeframe) * n_bars

    market_frames: dict[str, tuple[pd.Series, pd.DataFrame, pd.Series]] = {}
    for market in markets:
        raw_df = load_market_training_data(market, timeframe, start, end)
        features_df = build_feature_matrix(raw_df, market, half_life_bars)
        labels = compute_normalized_realized_series(raw_df, half_life_bars, n_bars)
        market_frames[market] = (raw_df["candle_time"], features_df, labels)

    folds = generate_walk_forward_folds(start, end, n_folds, embargo)

    reports: list[dict] = []
    last_model: lgb.LGBMClassifier | None = None

    for fold in folds:
        train_X_parts, train_y_parts, test_X_parts, test_y_parts = [], [], [], []
        for candle_time, features_df, labels in market_frames.values():
            valid = labels.notna()
            train_mask = valid & (candle_time <= fold.train_end)
            test_mask = valid & (candle_time >= fold.test_start) & (candle_time <= fold.test_end)
            train_X_parts.append(features_df[train_mask])
            train_y_parts.append(labels[train_mask])
            test_X_parts.append(features_df[test_mask])
            test_y_parts.append(labels[test_mask])

        train_X = pd.concat(train_X_parts)
        train_y_raw = pd.concat(train_y_parts)
        test_X = pd.concat(test_X_parts)
        test_y_raw = pd.concat(test_y_parts)

        if len(train_y_raw) < min_train_samples or test_y_raw.empty:
            print(f"[fold {fold.fold_index}] 표본 부족(train={len(train_y_raw)}, test={len(test_y_raw)}) — 건너뜀")
            continue

        boundaries = compute_quantile_boundaries(train_y_raw)
        train_labels = train_y_raw.apply(lambda v: bucket_to_category(v, boundaries))
        ref_scores = category_representative_scores(train_y_raw, boundaries)

        train_X_fit = train_X.assign(market=train_X["market"].astype("category"))
        test_X_fit = test_X.assign(market=test_X["market"].astype("category"))

        model = lgb.LGBMClassifier(
            objective="multiclass", class_weight="balanced", importance_type="gain", random_state=42
        )
        model.fit(train_X_fit, train_labels)
        last_model = model

        importances = dict(zip(train_X_fit.columns, model.feature_importances_))
        top_features = sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:15]

        probs_matrix = model.predict_proba(test_X_fit)
        class_order = list(model.classes_)

        confusion: dict[str, dict[str, int]] = {p: {a: 0 for a in CATEGORY_LABELS} for p in CATEGORY_LABELS}
        actual_totals: dict[str, int] = {a: 0 for a in CATEGORY_LABELS}
        expected_scores: list[float] = []
        actual_values: list[float] = []

        for row_probs, actual_value in zip(probs_matrix, test_y_raw.to_numpy()):
            probs = dict(zip(class_order, row_probs))
            predicted = max(probs, key=probs.get)
            actual = bucket_to_category(actual_value, boundaries)
            confusion[predicted][actual] += 1
            actual_totals[actual] += 1
            expected_score = sum(probs.get(label, 0.0) * ref_scores[label] for label in CATEGORY_LABELS)
            expected_scores.append(expected_score)
            actual_values.append(actual_value)

        correlation: float | None = None
        if len(expected_scores) >= 2:
            computed = float(np.corrcoef(expected_scores, actual_values)[0, 1])
            if not np.isnan(computed):
                correlation = computed

        report = {
            "fold_index": fold.fold_index,
            "n_train": len(train_y_raw),
            "n_test": len(test_y_raw),
            "confusion": confusion,
            "actual_totals": actual_totals,
            "correlation": correlation,
            "top_features": top_features,
        }
        reports.append(report)
        _print_fold_report(report)

    if last_model is not None:
        model_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        last_model.booster_.save_model(str(model_output_dir / f"regime_ml_{timestamp}.txt"))

    return reports


def _print_fold_report(report: dict) -> None:
    print(f"\n=== fold {report['fold_index']} (train={report['n_train']}, test={report['n_test']}) ===")
    confusion = report["confusion"]
    actual_totals = report["actual_totals"]

    print("  [예측 카테고리별 hit-rate]")
    for label in CATEGORY_LABELS:
        row = confusion[label]
        total = sum(row.values())
        if total == 0:
            print(f"    {label}: 샘플 없음")
            continue
        hit_rate = row[label] / total * 100
        print(f"    {label}: {row[label]}/{total} 적중 ({hit_rate:.1f}%)")

    correlation = report["correlation"]
    if correlation is None:
        print("  [확률벡터-실현수익률 상관계수] 계산 불가(샘플 부족)")
    else:
        print(f"  [확률벡터-실현수익률 상관계수] {correlation:.3f}")

    total_samples = sum(actual_totals.values())
    print(f"  [실제 카테고리 분포(전체 샘플 {total_samples}건 기준)]")
    for label in CATEGORY_LABELS:
        n = actual_totals[label]
        pct = n / total_samples * 100 if total_samples else 0.0
        print(f"    {label}: {n} ({pct:.1f}%)")

    print("  [피처 중요도(gain) 상위 15개]")
    for name, importance in report["top_features"]:
        print(f"    {name}: {importance:.1f}")


def main() -> None:
    run_training(
        markets=MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        model_output_dir=MODEL_OUTPUT_DIR,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -v`
Expected: PASS (2 passed) — 콘솔에 fold별 리포트가 출력되고, `tmp_path`에 `regime_ml_*.txt` 모델 파일이 하나 생성됨

- [ ] **Step 6: 전체 회귀 테스트 확인**

Run: `python -m pytest tests/ -v`
Expected: 기존 테스트 전부 PASS + 이번 계획에서 추가한 5개 테스트 파일 전부 PASS (기존 `engine/regime_detector.py`를 건드리지 않았으므로 회귀 없어야 함)

- [ ] **Step 7: 커밋**

```bash
git add requirements.txt scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: ML 장세 판별기 학습+워크포워드 검증 오케스트레이션 스크립트 추가"
```

---

## 실측 검증 (계획 완료 후, 별도 세션 아님 — 같은 세션에서 이어서 수행)

Task 5까지 커밋한 뒤, 실제 데이터로 학습을 돌려 규칙기반과 비교한다:

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
```

`scripts/regime_backtest.py`(규칙기반)의 최근 실행 결과와 fold별 상관계수/hit-rate를 나란히 비교해 실측으로 개선 여부를 판단한다. [[upbit-v1-dont-push-on-empirical-regression]] 원칙에 따라, 결과가 나빠졌다면 push하지 않고 사용자에게 먼저 보고한다.

## 비범위 확인 (실행 중 벗어나지 않도록)

- `engine/regime_detector.py` 수정 금지
- `/regime` 대시보드, API, 라이브 데몬 통합 코드 작성 금지
- 하이퍼파라미터 자동튜닝(grid search 등) 코드 작성 금지
- `minutes60` 외 타임프레임, KRW-BTC/ETH/XRP 외 마켓 지원 코드 작성 금지
