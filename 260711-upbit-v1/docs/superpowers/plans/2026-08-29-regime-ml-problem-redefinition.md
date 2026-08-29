# 장세 판별 ML 문제 재정의 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 장세 판별 ML 파이프라인을 Triple Barrier 레이블링 + 3단계 분류(하락/횡보/상승) + 코인 차별화 피처 + 마켓별 평가 분리로 재설계해, 기존 5단계+상관계수 방식(풀링 상관계수 0.077, "거의 사용 불가능한 수준")보다 실제로 신뢰할 수 있는 예측을 만든다.

**Architecture:** 공유 LightGBM 풀링 모델은 유지하되(코인별 완전 독립 모델은 데이터 부족 코인 리스크 때문에 기각), 레이블링을 Triple Barrier로, 평가를 표준 분류지표(macro F1/weighted kappa)로 바꾸고, 코인마다 다른 신호를 주는 자기상대적 피처 3종을 추가한다. 배포는 하지 않고(재학습 결과를 관리자 패널에서 기존 모델과 비교한 뒤 사용자가 결정), 실거래 연동은 비범위.

**Tech Stack:** Python(pandas/LightGBM/scikit-learn), FastAPI 백엔드, Next.js/TypeScript 프론트엔드. 새 의존성 없음(scikit-learn 1.9.0이 이미 requirements.txt에 있음).

## Global Constraints

- 실거래(`trading/`) 연동, 전략 자동전환, Hysteresis — 이번 범위 아님
- 규칙기반 하이브리드(제안 B), HMM 비지도 군집화(제안 C) — 이번 범위 아님(HMM은 이번 결과가 부진하면 후속 검토)
- 신규 외부데이터 소싱(트위터 등) — 별도 프로젝트, 이번 범위 아님
- 하이퍼파라미터 자동튜닝, 1시간봉 외 타임프레임 — 계속 비범위
- AWS 라이브 배포 여부는 사용자 승인 필요. 코드와 모델은 반드시 함께 배포한다(구/신 스키마 혼재로 대시보드가 깨지는 것을 방지) — 이 플랜은 로컬 구현+로컬 검증까지만, AWS 배포는 포함하지 않는다
- 참고 문서: `docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md`

---

## File Structure

**신규 생성:**
- `engine/regime_ml_metrics.py` — 3단계 분류 성능지표(macro F1/weighted kappa/confusion/precision·recall) 계산. sklearn을 얇게 감싼 순수 함수
- `tests/test_regime_ml_metrics.py`
- `scripts/select_barrier_k.py` — Triple Barrier 변동성 배수 k를 실측으로 정하는 1회성 그리드서치 스크립트(향후 재조정용으로 남겨둠)

**수정:**
- `engine/regime_ml_labels.py` — quantile 기반 함수 전부 제거, `compute_triple_barrier_labels()`로 교체. `CATEGORY_LABELS` 5→3
- `tests/test_regime_ml_labels.py` — 전면 재작성
- `engine/regime_ml_features.py` — 코인 차별화 피처 3종(`LISTING_AGE_BARS`/`VOLATILITY_PERCENTILE`/`LIQUIDITY_PERCENTILE`) 추가
- `tests/test_regime_ml_features.py` — 컬럼 집합 갱신 + 신규 피처 테스트 추가
- `scripts/train_regime_ml.py` — `run_training()` 재작성(Triple Barrier + 신규 지표 + 마켓별 분리), 사이드카 스키마 변경
- `tests/test_train_regime_ml.py` — 전면 재작성
- `tests/test_backend.py` — 5단계 fixture 1곳을 3단계로 갱신
- `frontend/lib/types/eda.ts` — `RegimeCategory` 5→3, `MlModelPerformance`/`MlFoldPerformance` 신규 필드 추가(레거시 필드는 관리자 패널의 과도기 표시를 위해 optional로 유지)
- `frontend/components/RegimeMlCurrentPrediction.tsx` — 3단계 표시 + macro F1/weighted kappa/precision·recall로 성능 패널 교체
- `frontend/components/RegimeMlAdminPanel.tsx` — 모델 목록 테이블에 macro F1/weighted kappa 컬럼 추가(기존 상관계수 컬럼은 레거시 모델 표시용으로 유지)

**변경 없음(확인됨):** `backend/regime_ml_service.py`(사이드카 스키마에 무관하게 `classes`/`fold_index`/`markets`/`performance`를 그대로 전달하는 구조라 코드 변경 불필요), `engine/regime_ml_constants.py`, `engine/regime_ml_splits.py`, `engine/regime_math.py`, `frontend/lib/api/eda.ts`

---

### Task 1: Triple Barrier 레이블링 함수

**Files:**
- Modify: `engine/regime_ml_labels.py` (전체 교체)
- Test: `tests/test_regime_ml_labels.py` (전체 교체)

**Interfaces:**
- Produces: `CATEGORY_LABELS: list[str] = ["하락", "횡보", "상승"]`, `compute_triple_barrier_labels(df: pd.DataFrame, half_life_bars: float, n_bars: int, k: float) -> pd.Series` (CATEGORY_LABELS 값 또는 NaN으로 이뤄진 object Series, df와 같은 길이/인덱스)

이 태스크 완료 직후 `scripts/train_regime_ml.py`와 `tests/test_train_regime_ml.py`는 옛 함수(`compute_normalized_realized_series` 등)를 계속 import하므로 깨진 상태가 됩니다 — Task 5에서 바로잡습니다. 정상입니다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_labels.py`를 다음 내용으로 전체 교체:

```python
"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels.compute_triple_barrier_labels()를 검증한다. Triple
Barrier Method — 상단/하단 경계 중 어느 쪽이 먼저 터치되는지, 둘 다 안
터치되면 만기(횡보), 미래 데이터가 부족하면 NaN인지 확인한다.
"""
from __future__ import annotations

import pandas as pd

from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels

_HALF_LIFE_BARS = 5.0
_N_BARS = 10
_K = 1.0
# 앞 50봉: ±1 오실레이션으로 EWM 변동성을 0보다 크게 만드는 워밍업 구간.
# 마지막 값(인덱스 49, 홀수라 -1 적용)이 99.0이라 이후 케이스의 기준가로 쓴다.
_WARMUP = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(50)]
_BASE = _WARMUP[-1]
assert _BASE == 99.0


def _make_close_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


def test_compute_triple_barrier_labels_assigns_up_when_upper_touched_first():
    future_up = [_BASE * (1.05**i) for i in range(1, 11)]  # 5%/bar 복리 급등
    closes = _WARMUP + future_up + [future_up[-1]] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "상승"


def test_compute_triple_barrier_labels_assigns_down_when_lower_touched_first():
    future_down = [_BASE * (0.95**i) for i in range(1, 11)]  # 5%/bar 복리 급락
    closes = _WARMUP + future_down + [future_down[-1]] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락"


def test_compute_triple_barrier_labels_assigns_sideways_when_neither_touched():
    future_flat = [_BASE] * 10  # 완전 횡보(수익률 0) -> 어떤 임계값도 못 넘음
    closes = _WARMUP + future_flat + [_BASE] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "횡보"


def test_compute_triple_barrier_labels_picks_whichever_barrier_hits_first():
    # 먼저 하단을 살짝 터치(-3%/-4%)한 뒤에야 상단을 크게 터치(+10%) — 크기가 아니라
    # "몇 봉째 터치했는지"만으로 결정돼야 하므로 하락이 정답.
    future_tie = [_BASE * 0.97, _BASE * 0.96, _BASE * 1.10] + [_BASE * 1.10] * 7
    closes = _WARMUP + future_tie + [_BASE * 1.10] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락"


def test_compute_triple_barrier_labels_nan_when_future_data_insufficient():
    future_flat = [_BASE] * 10
    closes = _WARMUP + future_flat + [_BASE] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[-_N_BARS:].isna().all()


def test_compute_triple_barrier_labels_preserves_length_and_index():
    closes = _WARMUP + [_BASE] * 15
    df = _make_close_df(closes)
    labels = compute_triple_barrier_labels(df, _HALF_LIFE_BARS, _N_BARS, _K)

    assert len(labels) == len(df)
    assert list(labels.index) == list(df.index)


def test_category_labels_has_three_ordered_classes():
    assert CATEGORY_LABELS == ["하락", "횡보", "상승"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_labels.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_triple_barrier_labels'`

- [ ] **Step 3: 구현**

`engine/regime_ml_labels.py`를 다음 내용으로 전체 교체:

```python
"""
engine/regime_ml_labels.py

장세 판별 ML 분류기의 레이블(정답 카테고리)을 만든다. Triple Barrier Method(Marcos
Lopez de Prado) — 상단 익절선/하단 손절선/만기 중 무엇이 먼저 터치되는지로 라벨을
정한다. 이전의 "다음 n_bars 평균수익률을 fold별 훈련구간 분위수로 나누는" 방식은
fold마다 카테고리 경계가 달라지는 불안정성이 있어 폐기했다(2026-08-27 도입,
2026-08-29 문제 재정의에서 교체). 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CATEGORY_LABELS: list[str] = ["하락", "횡보", "상승"]


def compute_triple_barrier_labels(
    df: pd.DataFrame, half_life_bars: float, n_bars: int, k: float
) -> pd.Series:
    """각 시점 t에서 상단(+k*vol_t)/하단(-k*vol_t) 경계와 n_bars 만기 중 무엇이
    먼저 터치되는지로 라벨링한다. vol_t는 t까지의 과거 수익률만으로 계산한
    EWM 변동성이라(pandas ewm은 인과적) 추론 시점에도 동일하게 재현 가능하다.
    두 경계가 같은 봉에서 동시에 터치되는 경우는 없다(k>0이면 상단·하단이
    서로 반대 부호). 반환: CATEGORY_LABELS 값 또는 NaN(미래 데이터 부족)으로
    이뤄진 object Series, df와 같은 길이/인덱스."""
    returns = df["close"].pct_change(fill_method=None)
    volatility = returns.ewm(halflife=half_life_bars).std()
    close = df["close"].to_numpy()
    n = len(df)

    labels: list[object] = [float("nan")] * n
    for t in range(max(n - n_bars, 0)):
        vol_t = volatility.iloc[t]
        if pd.isna(vol_t) or vol_t <= 0:
            continue
        upper = k * vol_t
        lower = -k * vol_t
        entry = close[t]
        future = close[t + 1 : t + 1 + n_bars] / entry - 1.0
        up_hits = np.flatnonzero(future >= upper)
        down_hits = np.flatnonzero(future <= lower)
        up_first = up_hits[0] if up_hits.size else None
        down_first = down_hits[0] if down_hits.size else None
        if up_first is not None and (down_first is None or up_first <= down_first):
            labels[t] = "상승"
        elif down_first is not None:
            labels[t] = "하락"
        else:
            labels[t] = "횡보"
    return pd.Series(labels, index=df.index)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_labels.py -v`
Expected: PASS (7개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_labels.py tests/test_regime_ml_labels.py
git commit -m "feat: 장세 ML 레이블을 Triple Barrier 3단계로 재정의"
```

---

### Task 2: 코인 차별화 피처(자기상대적)

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`

**Interfaces:**
- Consumes: 없음(독립적, Task 1과 무관)
- Produces: `build_feature_matrix()`가 반환하는 DataFrame에 `LISTING_AGE_BARS`/`VOLATILITY_PERCENTILE`/`LIQUIDITY_PERCENTILE` 컬럼 추가(시그니처 불변)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`의 기존 `test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features`를 다음으로 교체(파일 상단 import/`_make_full_df`는 그대로 유지):

```python
def test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features():
    df = _make_full_df()

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected_columns = (
        (set(LIVE_INDICATOR_FACTORY.keys()) - {"OBV"})
        | {
            "RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE",
            "LISTING_AGE_BARS", "VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE", "market",
        }
    )
    assert set(result.columns) == expected_columns
```

파일 맨 끝에 다음 테스트 2개를 추가:

```python
def test_build_feature_matrix_listing_age_bars_counts_from_zero():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert list(result["LISTING_AGE_BARS"]) == list(range(len(df)))


def test_build_feature_matrix_percentile_features_start_nan_then_bounded_zero_to_one():
    df = _make_full_df()  # _N=150 > 백분위 min_periods(100)
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    for column in ("VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE"):
        assert pd.isna(result[column].iloc[0])
        last_value = result[column].iloc[-1]
        assert not pd.isna(last_value)
        assert 0.0 <= last_value <= 1.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — `KeyError: 'LISTING_AGE_BARS'` (또는 컬럼 집합 불일치로 인한 assert 실패)

- [ ] **Step 3: 구현**

`engine/regime_ml_features.py`를 다음 내용으로 전체 교체:

```python
"""
engine/regime_ml_features.py

장세 판별 ML 분류기의 피처 매트릭스를 만든다. trading.live_indicators.LIVE_INDICATOR_FACTORY
(이미 백트레이더 대비 골든테스트로 검증된 순수 pandas 지표)를 재구현 없이 그대로
순회하고, engine.regime_features.py의 반전게이팅 실험용 5개 함수 + momentum/volatility
EWMA(raw_score)를 더한다. 코인 차별화 피처(자기상대적, 2026-08-29 문제 재정의 도입)
3개도 추가한다 — LISTING_AGE_BARS(상장 후 경과 봉 수)/VOLATILITY_PERCENTILE/
LIQUIDITY_PERCENTILE(둘 다 이 마켓 자신의 과거 1년 분포 대비 백분위). 공유 풀링
모델이 코인마다 다른 신호를 갖게 하려는 목적이며, 다른 마켓 데이터를 참조하지
않아(자기 자신의 df만 사용) 추론 시 여러 마켓을 새로 불러올 필요가 없다. I/O
없는 순수 함수 — 입력 df는 engine/regime_ml_data.py가 준비한다. 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md
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

# engine/regime_features.py:_MIN_VOLATILITY_FLOOR와 값이 같아야 한다(raw_score
# 0-나눗셈 방지) — 순환참조를 피하려 별도 정의.
_MIN_VOLATILITY_FLOOR = 1e-6
_PERCENTILE_WINDOW_BARS = 8760  # 1시간봉 기준 1년
_PERCENTILE_MIN_PERIODS = 100  # 약 4일치 이상 쌓이면 백분위 계산 시작(신규상장 코인도 이른 시점부터 값이 나오게)


def build_feature_matrix(df: pd.DataFrame, market: str, half_life_bars: float) -> pd.DataFrame:
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value를 전부 포함해야 한다
    (engine.regime_ml_data.load_market_training_data()가 반환하는 형태). 반환
    DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN), 원본 OHLCV
    컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
    # OBV(create_obv)는 윈도우 없는 누적합이라 추론 시(짧은 최근 구간)와 학습
    # 시(수년치) 스케일이 어긋난다(backend/regime_ml_service.py 참고) — 피처에서
    # 제외한다. 같은 레지스트리의 OBV_ROC는 rolling window 기반 %지표라 스케일
    # 문제가 없으므로 그대로 둔다.
    features: dict[str, pd.Series] = {
        name: factory(df) for name, factory in LIVE_INDICATOR_FACTORY.items() if name != "OBV"
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

    features["LISTING_AGE_BARS"] = pd.Series(range(len(df)), index=df.index, dtype=float)
    features["VOLATILITY_PERCENTILE"] = volatility.rolling(
        _PERCENTILE_WINDOW_BARS, min_periods=_PERCENTILE_MIN_PERIODS
    ).rank(pct=True)
    features["LIQUIDITY_PERCENTILE"] = df["trade_value"].rolling(
        _PERCENTILE_WINDOW_BARS, min_periods=_PERCENTILE_MIN_PERIODS
    ).rank(pct=True)

    result = pd.DataFrame(features, index=df.index)
    result["market"] = pd.Categorical([market] * len(df))
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_features.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_features.py tests/test_regime_ml_features.py
git commit -m "feat: 장세 ML에 코인 차별화(자기상대적) 피처 3종 추가"
```

---

### Task 3: 분류 평가지표 모듈

**Files:**
- Create: `engine/regime_ml_metrics.py`
- Test: `tests/test_regime_ml_metrics.py`

**Interfaces:**
- Consumes: `engine.regime_ml_labels.CATEGORY_LABELS`(Task 1)
- Produces: `compute_classification_metrics(y_true: list[str], y_pred: list[str]) -> dict` — 반환 dict 키: `n`(int), `macro_f1`(float|None), `weighted_kappa`(float|None), `confusion`(dict[str, dict[str, int]], 행=예측/열=실제), `class_precision_recall`(dict[str, dict[str, float|None]], 키는 `precision`/`recall`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_metrics.py` 신규 작성:

```python
"""
tests/test_regime_ml_metrics.py

engine.regime_ml_metrics.compute_classification_metrics()를 검증한다.
"""
from __future__ import annotations

from engine.regime_ml_labels import CATEGORY_LABELS
from engine.regime_ml_metrics import compute_classification_metrics


def test_compute_classification_metrics_returns_none_values_for_empty_input():
    result = compute_classification_metrics([], [])

    assert result["n"] == 0
    assert result["macro_f1"] is None
    assert result["weighted_kappa"] is None
    assert result["confusion"] == {p: {a: 0 for a in CATEGORY_LABELS} for p in CATEGORY_LABELS}
    assert all(
        result["class_precision_recall"][label] == {"precision": None, "recall": None}
        for label in CATEGORY_LABELS
    )


def test_compute_classification_metrics_perfect_predictions_score_maximally():
    y_true = ["하락", "횡보", "상승", "하락", "횡보", "상승"]
    y_pred = list(y_true)

    result = compute_classification_metrics(y_true, y_pred)

    assert result["n"] == 6
    assert result["macro_f1"] == 1.0
    assert result["weighted_kappa"] == 1.0
    for label in CATEGORY_LABELS:
        assert result["confusion"][label][label] == y_true.count(label)
        assert result["class_precision_recall"][label] == {"precision": 1.0, "recall": 1.0}


def test_compute_classification_metrics_confusion_matrix_is_row_predicted_col_actual():
    # 상승을 2번 예측했는데 실제로는 1번만 맞음(1번은 실제 횡보) -> precision(상승)=0.5
    y_true = ["상승", "횡보", "하락"]
    y_pred = ["상승", "상승", "하락"]

    result = compute_classification_metrics(y_true, y_pred)

    assert result["confusion"]["상승"] == {"하락": 0, "횡보": 1, "상승": 1}
    assert result["class_precision_recall"]["상승"]["precision"] == 0.5
    assert result["class_precision_recall"]["상승"]["recall"] == 1.0


def test_compute_classification_metrics_worst_case_kappa_and_f1_are_low():
    # 실제와 정반대로만 예측(하락<->상승 뒤바꿈) -> 우연보다도 못한 성능
    y_true = ["하락", "하락", "상승", "상승"]
    y_pred = ["상승", "상승", "하락", "하락"]

    result = compute_classification_metrics(y_true, y_pred)

    assert result["weighted_kappa"] == -1.0
    assert result["macro_f1"] == 0.0
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_ml_metrics'`

- [ ] **Step 3: 구현**

`engine/regime_ml_metrics.py` 신규 작성:

```python
"""
engine/regime_ml_metrics.py

3단계 장세 분류(CATEGORY_LABELS)의 성능 지표를 계산한다. sklearn 표준 함수를 얇게
감싸 팀 관례 스키마(dict, confusion은 행=예측/열=실제)로 변환한다 —
scripts/train_regime_ml.py가 fold별/마켓별로 반복 호출한다. 이전(5단계 시절)
피어슨 상관계수 기반 평가는 확률벡터의 기댓값과 연속값(실현수익률)을 비교하는
방식이었는데, Triple Barrier 이후 정답 자체가 범주형이라 더 이상 성립하지 않아
표준 분류지표로 교체했다(2026-08-29 문제 재정의). 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md
"""
from __future__ import annotations

from sklearn.metrics import (
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

from engine.regime_ml_labels import CATEGORY_LABELS


def compute_classification_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    """y_true/y_pred: CATEGORY_LABELS 값으로 이뤄진 같은 길이의 리스트. 표본이
    없으면 모든 값이 None/0인 빈 결과를 반환한다(계산 불가와 "성능이 0"을
    구분하기 위해 숫자가 아니라 None으로 표시)."""
    if not y_true:
        return {
            "n": 0,
            "macro_f1": None,
            "weighted_kappa": None,
            "confusion": {p: {a: 0 for a in CATEGORY_LABELS} for p in CATEGORY_LABELS},
            "class_precision_recall": {
                c: {"precision": None, "recall": None} for c in CATEGORY_LABELS
            },
        }

    macro_f1 = float(f1_score(y_true, y_pred, labels=CATEGORY_LABELS, average="macro", zero_division=0))
    weighted_kappa = float(cohen_kappa_score(y_true, y_pred, labels=CATEGORY_LABELS, weights="linear"))

    matrix = confusion_matrix(y_true, y_pred, labels=CATEGORY_LABELS)
    confusion = {
        predicted: {
            actual: int(matrix[actual_idx, predicted_idx])
            for actual_idx, actual in enumerate(CATEGORY_LABELS)
        }
        for predicted_idx, predicted in enumerate(CATEGORY_LABELS)
    }

    precision, recall, _, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=CATEGORY_LABELS, average=None, zero_division=0
    )
    class_precision_recall = {
        label: {"precision": float(p), "recall": float(r)}
        for label, p, r in zip(CATEGORY_LABELS, precision, recall)
    }

    return {
        "n": len(y_true),
        "macro_f1": macro_f1,
        "weighted_kappa": weighted_kappa,
        "confusion": confusion,
        "class_precision_recall": class_precision_recall,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_metrics.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_metrics.py tests/test_regime_ml_metrics.py
git commit -m "feat: 3단계 장세 분류용 평가지표 모듈 추가(macro F1/weighted kappa)"
```

---

### Task 4: Barrier k 그리드서치 스크립트

이 태스크는 TDD 대상이 아니다(실제 시장데이터 스캔 결과에 따라 상수를 정하는 1회성 분석 스크립트라 유닛테스트로 검증할 로직이 없음 — `scripts/train_regime_ml.py`에 이미 있는 같은 성격의 스크립트들과 동일 취급).

**Files:**
- Create: `scripts/select_barrier_k.py`

**Interfaces:**
- Consumes: `engine.regime_ml_labels.compute_triple_barrier_labels`(Task 1), `engine.regime_ml_constants.TRAINING_MARKETS`, `engine.regime_ml_data.load_market_training_data`, `engine.regime_math.N_MULTIPLIER`/`half_life_bars_for_timeframe`
- Produces: 콘솔에 k별 3클래스 분포와 최종 채택 k를 출력(Task 5의 `BARRIER_K` 상수 결정 근거)

- [ ] **Step 1: 스크립트 작성**

`scripts/select_barrier_k.py` 신규 작성:

```python
"""
scripts/select_barrier_k.py

Triple Barrier 라벨링의 변동성 배수 k를 한 번 정하기 위한 그리드서치. fold별로
다시 정하지 않고 파이프라인 상수로 고정하는 하이퍼파라미터라(design 문서 A절 참고),
전체 데이터를 한 번 스캔해 3클래스(하락/횡보/상승) 분포가 가장 균형에 가까운 k를
찾는다. 이 스크립트는 재실행용으로 남겨두되(데이터가 크게 바뀌면 재조정), 매
학습(scripts/train_regime_ml.py)마다 자동으로 다시 돌리지는 않는다 — 결과를
scripts/train_regime_ml.py의 BARRIER_K 상수로 수동 반영한다.

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/select_barrier_k.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels

TIMEFRAME = "minutes60"
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)
CANDIDATE_KS = [1.0, 2.0, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0, 10.0]


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)

    all_dfs = []
    for market in TRAINING_MARKETS:
        print(f"로딩 중: {market}...")
        df = load_market_training_data(market, TIMEFRAME, START, END)
        all_dfs.append(df)
        print(f"  -> {len(df)}행")

    print(f"\n{len(all_dfs)}개 마켓 로드 완료. half_life_bars={half_life_bars:.1f}, n_bars={n_bars}")
    print(f"{'k':>5} | " + " | ".join(f"{label:>6}" for label in CATEGORY_LABELS) + " | 최대편차")

    best_k = None
    best_deviation = float("inf")
    for k in CANDIDATE_KS:
        counts = {label: 0 for label in CATEGORY_LABELS}
        for df in all_dfs:
            labels = compute_triple_barrier_labels(df, half_life_bars, n_bars, k)
            for label in CATEGORY_LABELS:
                counts[label] += int((labels == label).sum())
        total = sum(counts.values())
        shares = {label: (counts[label] / total if total else 0.0) for label in CATEGORY_LABELS}
        max_deviation = max(abs(share - 1 / 3) for share in shares.values())
        print(
            f"{k:>5} | " + " | ".join(f"{shares[label]*100:5.1f}%" for label in CATEGORY_LABELS)
            + f" | {max_deviation*100:5.1f}%p"
        )
        if max_deviation < best_deviation:
            best_deviation = max_deviation
            best_k = k

    print(f"\n채택: k={best_k} (최대편차 {best_deviation*100:.1f}%p)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행해서 k 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/select_barrier_k.py`
Expected: 14개 마켓 로딩 후 k별 분포 표가 출력되고 마지막 줄에 `채택: k=...`가 출력됨. 2026-08-29 실측으로는 `k=5.5`(하락 35.3% / 횡보 31.7% / 상승 33.0%, 최대편차 2.0%p)가 나왔다 — 데이터가 갱신된 뒤 재실행하면 근처의 다른 값이 나올 수 있으며, 그 경우 출력된 값을 그대로 Task 5의 `BARRIER_K`에 반영한다.

- [ ] **Step 3: 커밋**

```bash
git add scripts/select_barrier_k.py
git commit -m "feat: Triple Barrier k값 그리드서치 스크립트 추가"
```

---

### Task 5: `scripts/train_regime_ml.py` 재작성

**Files:**
- Modify: `scripts/train_regime_ml.py` (전체 교체)
- Test: `tests/test_train_regime_ml.py` (전체 교체)

**Interfaces:**
- Consumes: `compute_triple_barrier_labels`/`CATEGORY_LABELS`(Task 1), `build_feature_matrix`(Task 2), `compute_classification_metrics`(Task 3), Task 4에서 확정한 k값
- Produces: `run_training(markets, timeframe, start, end, n_folds, min_train_samples, barrier_k, model_output_dir) -> list[dict]` — 각 report는 `fold_index`/`n_train`/`n_test`/`metrics`(compute_classification_metrics 반환 dict)/`top_features` 키를 가짐. 사이드카 JSON 키: `markets`/`labeling_method`/`barrier_k`/`classes`/`fold_index`/`performance`(`folds`/`pooled`/`per_market`)

이 태스크에서 **삭제**되는 옛 테스트(사유): `test_quantile_boundaries_computed_from_train_window_only` — quantile 경계 계산 자체가 없어져 전제가 사라짐(레이블이 fold와 무관하게 사전 계산되고, train/test 분리 자체는 embargo/마스킹 로직 불변이라 별도 누수 테스트 불필요). `test_aggregate_confusion_and_totals_sum_across_folds`, `test_correlation_from_pairs_*`(3개), `test_compute_hit_rate_divides_correct_by_predicted_total` — 전부 이번에 제거하는 헬퍼 함수(`_sum_confusion_matrices`/`_correlation_from_pairs`/`_compute_hit_rate`) 전용 테스트라 대상 자체가 없어짐(대체 검증은 Task 3의 `test_regime_ml_metrics.py` + 아래 새 통합 테스트).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`를 다음 내용으로 전체 교체:

```python
"""
tests/test_train_regime_ml.py

scripts.train_regime_ml.run_training()의 end-to-end 스모크 테스트. 실제 네트워크
호출 없이(engine.regime_ml_data.load_market_training_data를 monkeypatch) 합성
데이터로 전체 파이프라인(데이터 로드 -> 피처 -> fold 루프 -> LightGBM 학습 -> 리포트
-> 모델 저장)이 에러 없이 완주하는지만 검증한다. 개별 단계(레이블/분할/피처/로더/
분류지표)의 세부 동작은 각자의 유닛테스트(test_regime_ml_labels.py 등)가 이미
검증한다. barrier_k=6.0은 이 합성 데이터(seed 1/2/3, _N=24*40시간)에서 모든 fold의
train/test에 3클래스가 전부 나타나는 것으로 실측 확인된 값이다(LightGBM multiclass
학습이 클래스 1개짜리 표본으로 실패하지 않도록 — 실제 운영 상수 BARRIER_K=5.5와는
별개로, 테스트 전용으로 고른 값).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import scripts.train_regime_ml as train_regime_ml
from scripts.train_regime_ml import run_training

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_N = 24 * 40  # minutes60, 40일치
_BARRIER_K = 6.0


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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert len(reports) >= 1
    for report in reports:
        assert report["n_test"] > 0
        assert set(report["metrics"]["confusion"].keys()) == set(train_regime_ml.CATEGORY_LABELS)
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert reports == []
    assert list(tmp_path.glob("*.txt")) == []


def test_run_training_prints_aggregate_summary_after_folds(tmp_path, monkeypatch, capsys):
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1

    captured = capsys.readouterr().out
    assert "macro F1" in captured
    assert "전체 fold 풀링" in captured
    assert "마켓별 성능" in captured

    last_fold_marker = f"=== fold {reports[-1]['fold_index']}"
    assert captured.index(last_fold_marker) < captured.index("전체 fold 풀링")


def test_run_training_covers_all_requested_folds(tmp_path, monkeypatch):
    """fold 0은 train_end가 항상 start 이전이라 훈련 표본이 구조적으로 비어 있어
    언제나 스킵된다. 내부적으로 n_folds+1개를 만들어 fold 0만 스킵되고, fold
    1..n_folds는 모두 평가돼 반환된 report 개수가 n_folds와 같아야 한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    n_folds = 3
    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=n_folds,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert len(reports) == n_folds
    assert sorted(r["fold_index"] for r in reports) == list(range(1, n_folds + 1))


def test_run_training_saves_json_sidecar_alongside_model(tmp_path, monkeypatch):
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
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1

    txt_files = list(tmp_path.glob("*.txt"))
    json_files = list(tmp_path.glob("*.json"))
    assert len(txt_files) == 1
    assert len(json_files) == 1
    assert txt_files[0].stem == json_files[0].stem

    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert set(sidecar.keys()) == {
        "markets", "labeling_method", "barrier_k", "classes", "fold_index", "performance",
    }
    assert sidecar["markets"] == list(seeds.keys())
    assert sidecar["labeling_method"] == "triple_barrier"
    assert sidecar["barrier_k"] == _BARRIER_K
    assert set(sidecar["classes"]) == set(train_regime_ml.CATEGORY_LABELS)
    assert isinstance(sidecar["fold_index"], int)
    assert sidecar["fold_index"] == reports[-1]["fold_index"]

    performance = sidecar["performance"]
    assert len(performance["folds"]) == len(reports)
    for fold_perf, report in zip(performance["folds"], reports):
        assert fold_perf["fold_index"] == report["fold_index"]
        assert fold_perf["n_train"] == report["n_train"]
        assert fold_perf["n_test"] == report["n_test"]
        assert fold_perf["macro_f1"] == report["metrics"]["macro_f1"]
        assert fold_perf["weighted_kappa"] == report["metrics"]["weighted_kappa"]

    pooled = performance["pooled"]
    assert pooled["n"] == sum(r["n_test"] for r in reports)
    assert -1.0 <= pooled["weighted_kappa"] <= 1.0
    assert 0.0 <= pooled["macro_f1"] <= 1.0
    assert set(pooled["class_precision_recall"].keys()) == set(train_regime_ml.CATEGORY_LABELS)

    per_market = performance["per_market"]
    assert set(per_market.keys()) == set(seeds.keys())
    assert sum(m["n"] for m in per_market.values()) == pooled["n"]


def test_run_training_performance_folds_excludes_skipped_folds(tmp_path, monkeypatch):
    """fold 하나가 표본 부족으로 스킵됐을 때, 사이드카 performance.folds가 실제로
    평가된 fold만 담고(reports와 정확히 같은 fold_index 집합) 스킵된 fold는 포함하지
    않는지 확인한다."""
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
        n_folds=3,
        min_train_samples=600,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )

    assert [r["fold_index"] for r in reports] == [2, 3]

    # 이 합성 데이터(seed 1/2/3, _N=24*40시간, barrier_k=6.0)에서 n_folds=3일 때
    # 실측 n_train은 fold 1=537, fold 2=1257, fold 3=1977 —
    # min_train_samples=600이면 fold 1만 표본 부족으로 스킵되고 fold 2·3은 평가된다.
    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert [f["fold_index"] for f in sidecar["performance"]["folds"]] == [2, 3]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -v`
Expected: FAIL — Task 1에서 `engine/regime_ml_labels.py`의 `compute_normalized_realized_series` 등을 이미 제거했는데 (아직 고치지 않은) 옛 `scripts/train_regime_ml.py`가 여전히 그 이름들을 import하고 있어 컬렉션 단계에서 `ImportError: cannot import name 'compute_normalized_realized_series' from 'engine.regime_ml_labels'`가 발생함(Task 1 완료 직후부터 예상된 정상적인 중간 상태)

- [ ] **Step 3: 구현**

`scripts/train_regime_ml.py`를 다음 내용으로 전체 교체:

```python
"""
scripts/train_regime_ml.py

장세 판별기 ML 학습+워크포워드 검증 파이프라인. Triple Barrier Method(하락/횡보/상승
3단계)로 레이블링하고, fold별/전체 풀링/마켓별 분류지표(macro F1/weighted kappa/
confusion matrix/클래스별 precision·recall)를 콘솔에 리포트한다. 이전(5단계+상관계수)
버전은 2026-08-29 문제 재정의에서 교체됐다. 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels
from engine.regime_ml_metrics import compute_classification_metrics
from engine.regime_ml_splits import generate_walk_forward_folds
from upbit_data_service import timeframe_duration

TIMEFRAME = "minutes60"
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
TRAIN_END = datetime.now(timezone.utc)
N_FOLDS = 5
MIN_TRAIN_SAMPLES = 500
# scripts/select_barrier_k.py로 2026-08-29 실측(14마켓, 2024-01-01~현재) 결정.
# 하락 35.3% / 횡보 31.7% / 상승 33.0% (최대편차 2.0%p, 그리드 중 최선).
BARRIER_K = 5.5
MODEL_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"


def run_training(
    markets: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    n_folds: int,
    min_train_samples: int,
    barrier_k: float,
    model_output_dir: Path,
) -> list[dict]:
    """마켓별로 데이터를 한 번씩만 로드/피처화(fold마다 반복하지 않음)하고, 워크포워드
    fold 루프를 돌며 LightGBM을 학습·평가한다. Triple Barrier 레이블(하락/횡보/상승)로
    학습하고, fold별 + 전체 풀링 + 마켓별 분류지표(macro F1/weighted kappa/confusion/
    precision·recall)를 계산한다. fold별 리포트 리스트를 반환하고, 마지막으로 성공한
    fold의 모델을 model_output_dir에 저장한다. 표본이 min_train_samples 미만이거나
    테스트 표본이 없는 fold는 건너뛴다."""
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    embargo = timeframe_duration(timeframe) * n_bars

    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={timeframe}, barrier_k={barrier_k}")

    market_frames: dict[str, tuple[pd.Series, pd.DataFrame, pd.Series]] = {}
    for market in markets:
        raw_df = load_market_training_data(market, timeframe, start, end)
        features_df = build_feature_matrix(raw_df, market, half_life_bars)
        labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, barrier_k)
        market_frames[market] = (raw_df["candle_time"], features_df, labels)

    # fold 0은 test_start == start라 train_end(=test_start - embargo)가 항상 start
    # 이전이 되어 훈련 표본이 구조적으로 0이다(아래 min_train_samples 가드로 항상
    # 건너뜀). n_folds보다 하나 더 요청해 그 "항상 비는" fold를 인덱스 0으로 흡수시키고,
    # 실제로 평가되는 나머지 n_folds개 fold가 [start, end] 거의 전체를 덮게 한다.
    folds = generate_walk_forward_folds(start, end, n_folds + 1, embargo)

    reports: list[dict] = []
    last_model: lgb.LGBMClassifier | None = None
    last_class_order: list[str] | None = None
    last_fold_index: int | None = None
    all_true: list[str] = []
    all_pred: list[str] = []
    all_markets: list[str] = []

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
        train_y = pd.concat(train_y_parts)
        test_X = pd.concat(test_X_parts)
        test_y = pd.concat(test_y_parts)

        if len(train_y) < min_train_samples or test_y.empty:
            print(f"[fold {fold.fold_index}] 표본 부족(train={len(train_y)}, test={len(test_y)}) — 건너뜀")
            continue

        train_X_fit = train_X.assign(market=train_X["market"].astype("category"))
        test_X_fit = test_X.assign(market=test_X["market"].astype("category"))

        model = lgb.LGBMClassifier(
            objective="multiclass", class_weight="balanced", importance_type="gain", random_state=42
        )
        model.fit(train_X_fit, train_y)
        last_model = model
        last_class_order = [str(c) for c in model.classes_]
        last_fold_index = fold.fold_index

        importances = dict(zip(train_X_fit.columns, model.feature_importances_))
        top_features = sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:15]

        predictions = model.predict(test_X_fit)
        true_values = test_y.to_numpy()
        test_markets = test_X_fit["market"].astype(str).to_numpy()

        fold_metrics = compute_classification_metrics(list(true_values), list(predictions))

        report = {
            "fold_index": fold.fold_index,
            "n_train": len(train_y),
            "n_test": len(test_y),
            "metrics": fold_metrics,
            "top_features": top_features,
        }
        reports.append(report)
        _print_fold_report(report)

        all_true.extend(true_values)
        all_pred.extend(predictions)
        all_markets.extend(test_markets)

    pooled_metrics = compute_classification_metrics(all_true, all_pred)
    per_market_metrics: dict[str, dict] = {}
    all_true_arr = np.array(all_true)
    all_pred_arr = np.array(all_pred)
    all_markets_arr = np.array(all_markets)
    for market in markets:
        mask = all_markets_arr == market
        per_market_metrics[market] = compute_classification_metrics(
            list(all_true_arr[mask]), list(all_pred_arr[mask])
        )

    _print_aggregate_summary(reports, pooled_metrics, per_market_metrics)

    if last_model is not None:
        model_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_name = f"regime_ml_{timestamp}"
        last_model.booster_.save_model(str(model_output_dir / f"{base_name}.txt"))

        sidecar = {
            "markets": markets,
            "labeling_method": "triple_barrier",
            "barrier_k": barrier_k,
            "classes": last_class_order,
            "fold_index": last_fold_index,
            "performance": {
                "folds": [
                    {
                        "fold_index": r["fold_index"],
                        "n_train": r["n_train"],
                        "n_test": r["n_test"],
                        "macro_f1": r["metrics"]["macro_f1"],
                        "weighted_kappa": r["metrics"]["weighted_kappa"],
                    }
                    for r in reports
                ],
                "pooled": pooled_metrics,
                "per_market": per_market_metrics,
            },
        }
        with open(model_output_dir / f"{base_name}.json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=2)

    return reports


def _print_metrics_block(metrics: dict) -> None:
    if metrics["n"] == 0:
        print("  [표본 없음] 지표 계산 불가")
        return
    print(f"  [macro F1] {metrics['macro_f1']:.3f}  [weighted kappa] {metrics['weighted_kappa']:.3f}")
    print("  [클래스별 precision/recall]")
    for label in CATEGORY_LABELS:
        pr = metrics["class_precision_recall"][label]
        print(f"    {label}: precision={pr['precision']:.3f} recall={pr['recall']:.3f}")
    print("  [confusion matrix] 행=예측, 열=실제")
    header = "    " + "예측\\실제".ljust(10) + "".join(label.ljust(10) for label in CATEGORY_LABELS)
    print(header)
    for predicted_label in CATEGORY_LABELS:
        row = metrics["confusion"][predicted_label]
        row_str = "    " + predicted_label.ljust(10) + "".join(
            str(row[actual_label]).ljust(10) for actual_label in CATEGORY_LABELS
        )
        print(row_str)


def _print_fold_report(report: dict) -> None:
    print(f"\n=== fold {report['fold_index']} (train={report['n_train']}, test={report['n_test']}) ===")
    _print_metrics_block(report["metrics"])
    print("  [피처 중요도(gain) 상위 15개]")
    for name, importance in report["top_features"]:
        print(f"    {name}: {importance:.1f}")


def _print_aggregate_summary(
    reports: list[dict], pooled_metrics: dict, per_market_metrics: dict[str, dict]
) -> None:
    print(f"\n=== 전체 fold 풀링 (fold {len(reports)}개) ===")
    _print_metrics_block(pooled_metrics)
    print("\n=== 마켓별 성능(전체 fold 풀링) ===")
    for market, metrics in per_market_metrics.items():
        if metrics["n"] == 0:
            print(f"  {market}: 표본 없음")
        else:
            print(
                f"  {market}: n={metrics['n']} macro_f1={metrics['macro_f1']:.3f} "
                f"weighted_kappa={metrics['weighted_kappa']:.3f}"
            )


def main() -> None:
    reports = run_training(
        markets=TRAINING_MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        barrier_k=BARRIER_K,
        model_output_dir=MODEL_OUTPUT_DIR,
    )
    print(f"\n총 {len(reports)}개 fold 평가 완료(요청 n_folds={N_FOLDS})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -v`
Expected: PASS (전체 6개)

- [ ] **Step 5: 커밋**

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: 학습 파이프라인을 Triple Barrier + 마켓별 분류지표로 재작성"
```

---

### Task 6: `tests/test_backend.py` fixture 3단계로 갱신

**Files:**
- Modify: `tests/test_backend.py:2883-2905`

**Interfaces:**
- Consumes: 없음(fixture 데이터만 변경, 엔드포인트는 스키마에 무관하게 통과시키므로 로직 변경 없음)

- [ ] **Step 1: 변경**

`tests/test_backend.py`의 `test_regime_ml_current_prediction_returns_result`를 다음으로 교체:

```python
def test_regime_ml_current_prediction_returns_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def _fake_predict(market, timeframe):
        captured["args"] = (market, timeframe)
        return {
            "predicted_category": "횡보",
            "probs": {"하락": 0.3, "횡보": 0.4, "상승": 0.3},
            "model_trained_at": "2026-08-27T05:20:47+00:00",
            "model_fold_index": 5,
        }

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "minutes60"},
    )

    assert resp.status_code == 200
    assert resp.json()["predicted_category"] == "횡보"
    assert captured["args"] == ("KRW-ETH", "minutes60")
```

- [ ] **Step 2: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py -k test_regime_ml_current_prediction_returns_result -v`
Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add tests/test_backend.py
git commit -m "test: ml-current-prediction fixture를 3단계 카테고리로 갱신"
```

---

### Task 7: 프론트 타입 갱신

**Files:**
- Modify: `frontend/lib/types/eda.ts:118-155`

**Interfaces:**
- Produces: `RegimeCategory`(3값), `MlFoldPerformance`(레거시/신규 필드 optional 공존), `MlPooledMetrics`(신규), `ClassPrecisionRecall`(신규), `MlModelPerformance`(레거시/신규 필드 optional 공존)

레거시 필드(`pooled_correlation`/`pooled_hit_rate`)를 optional로 남기는 이유: `RegimeMlAdminPanel.tsx`(Task 9)가 예전에 학습된 5단계 모델과 새로 학습된 3단계 모델을 같은 목록에서 나란히 보여줘야 하기 때문이다(설계 문서 E절 "과도기 UI").

- [ ] **Step 1: 변경**

`frontend/lib/types/eda.ts`의 118번째 줄(`export type RegimeCategory = ...`)부터 155번째 줄(`}`, `RegimeMlModelSummary` 끝)까지를 다음으로 교체:

```typescript
export type RegimeCategory = '하락' | '횡보' | '상승';

export interface MlFoldPerformance {
  fold_index: number;
  n_train: number;
  n_test: number;
  // 레거시(5단계) 모델 전용
  correlation?: number | null;
  // 신규(3단계) 모델 전용
  macro_f1?: number | null;
  weighted_kappa?: number | null;
}

export interface ClassPrecisionRecall {
  precision: number | null;
  recall: number | null;
}

export interface MlPooledMetrics {
  n: number;
  macro_f1: number | null;
  weighted_kappa: number | null;
  confusion: Record<RegimeCategory, Record<RegimeCategory, number>>;
  class_precision_recall: Record<RegimeCategory, ClassPrecisionRecall>;
}

export interface MlModelPerformance {
  folds: MlFoldPerformance[];
  // 레거시(5단계) 모델 전용
  pooled_correlation?: number | null;
  pooled_hit_rate?: Record<string, number | null>;
  // 신규(3단계) 모델 전용
  pooled?: MlPooledMetrics;
  per_market?: Record<string, MlPooledMetrics>;
}

export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
  model_performance: MlModelPerformance | null;
}

export interface RegimeMlJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
}

export interface RegimeMlModelSummary {
  model_timestamp: string;
  trained_at: string;
  performance: MlModelPerformance | null;
  is_deployed: boolean;
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: Task 8/9 완료 전까지는 `RegimeMlCurrentPrediction.tsx`/`RegimeMlAdminPanel.tsx`가 옛 필드(`pooled_correlation`을 필수처럼 쓰는 부분 등)를 참조해 타입 에러가 날 수 있음 — 정상(다음 두 태스크에서 해소).

- [ ] **Step 3: 커밋**

```bash
git add frontend/lib/types/eda.ts
git commit -m "feat(frontend): RegimeCategory 3단계 + 분류지표 타입 추가"
```

---

### Task 8: `RegimeMlCurrentPrediction.tsx` 갱신

**Files:**
- Modify: `frontend/components/RegimeMlCurrentPrediction.tsx` (전체 교체)

**Interfaces:**
- Consumes: `RegimeCategory`/`MlModelPerformance`/`MlCurrentPrediction`(Task 7)

이 카드는 항상 "현재 배포된 딱 하나의 모델"만 보여준다(관리자 패널처럼 여러 모델을 동시에 비교하지 않음) — 그래서 레거시/신규 스키마를 동시에 지원할 필요가 없다. Task 10에서 로컬 재학습+배포를 마치면 이 카드가 참조하는 모델은 항상 3단계 스키마가 된다.

- [ ] **Step 1: 변경**

`frontend/components/RegimeMlCurrentPrediction.tsx`를 다음 내용으로 전체 교체:

```tsx
'use client';

import { useEffect, useState } from 'react';
import type { MlCurrentPrediction, RegimeCategory } from '@/lib/types/eda';
import { ApiError } from '@/lib/api/client';
import { getRegimeMlCurrentPrediction } from '@/lib/api/eda';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InfoPopover } from '@/components/ui/info-popover';

const CATEGORY_ORDER: RegimeCategory[] = ['상승', '횡보', '하락'];
export const TRAINED_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP',
  'KRW-SOL', 'KRW-DOGE', 'KRW-LINK', 'KRW-ADA', 'KRW-XLM', 'KRW-TRX',
  'KRW-TRUMP', 'KRW-BCH', 'KRW-BSV', 'KRW-QTUM', 'KRW-ALGO',
];

function formatPct(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${(value * 100).toFixed(1)}%`;
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : value.toFixed(3);
}

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '상승':
      return '--regime-surge-up';
    case '횡보':
      return '--marker-boundary';
    case '하락':
      return '--regime-surge-down';
  }
}

interface RegimeMlCurrentPredictionProps {
  market: string;
  timeframe: string;
}

export default function RegimeMlCurrentPrediction({ market, timeframe }: RegimeMlCurrentPredictionProps) {
  const [data, setData] = useState<MlCurrentPrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (timeframe !== 'minutes60' || !market || !TRAINED_MARKETS.includes(market)) {
      setData(null);
      setError(null);
      return;
    }
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeMlCurrentPrediction({ market, timeframe })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'ML 예측을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [market, timeframe]);

  const modelPerformance = data?.model_performance ?? null;
  const pooled = modelPerformance?.pooled ?? null;

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">ML 현재예측</h2>
      {timeframe !== 'minutes60' ? (
        <p className="text-sm text-muted-foreground">ML은 1시간봉 전용입니다.</p>
      ) : !TRAINED_MARKETS.includes(market) ? (
        <p className="text-sm text-muted-foreground">이 모델은 {TRAINED_MARKETS.join('/')}로만 학습되어 있습니다.</p>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            <div className="mb-3 flex items-baseline gap-2">
              <span className="text-2xl font-bold">{data.predicted_category}</span>
              <span className="text-sm text-muted-foreground">
                확신도 {(data.probs[data.predicted_category] * 100).toFixed(1)}%
              </span>
            </div>
            <div className="mb-3 space-y-1.5">
              {CATEGORY_ORDER.map((label) => (
                <div key={label} className="flex items-center gap-2 text-xs">
                  <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(data.probs[label] * 100).toFixed(1)}%`,
                        backgroundColor: `var(${categoryVarName(label)})`,
                      }}
                    />
                  </div>
                  <span className="w-10 shrink-0 text-right tabular-nums">
                    {(data.probs[label] * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {market} {formatTimeframe(timeframe)} 기준, {formatDateTime(data.bar_time)} 봉 데이터. (모델: {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index})
            </p>
          </div>
          <div className="border-t pt-3 md:border-l md:border-t-0 md:pl-6 md:pt-0">
            <h3 className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
              모델 성능
              <InfoPopover>
                macro F1(0~1)은 3개 클래스(하락/횡보/상승)의 F1-score 평균, weighted
                kappa(-1~+1)는 우연히 맞을 확률을 보정한 일치도(순서형 가중치 적용,
                하락↔상승처럼 먼 오분류에 더 큰 벌점)입니다. 둘 다 1(또는 macro
                F1=1)에 가까울수록 좋고, weighted kappa가 0 이하면 무작위 추측보다도
                못하다는 뜻입니다.
              </InfoPopover>
            </h3>
            {modelPerformance ? (
              <>
                <div className="overflow-hidden rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>fold</TableHead>
                        <TableHead className="text-right">train</TableHead>
                        <TableHead className="text-right">test</TableHead>
                        <TableHead className="text-right">macro F1</TableHead>
                        <TableHead className="text-right">weighted kappa</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {modelPerformance.folds.map((fold) => (
                        <TableRow
                          key={fold.fold_index}
                          className={fold.fold_index === data.model_fold_index ? 'font-semibold' : ''}
                        >
                          <TableCell>{fold.fold_index}</TableCell>
                          <TableCell className="text-right tabular-nums">{fold.n_train.toLocaleString()}</TableCell>
                          <TableCell className="text-right tabular-nums">{fold.n_test.toLocaleString()}</TableCell>
                          <TableCell className="text-right tabular-nums">{formatScore(fold.macro_f1)}</TableCell>
                          <TableCell className="text-right tabular-nums">{formatScore(fold.weighted_kappa)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  풀링 macro F1: {formatScore(pooled?.macro_f1)} / weighted kappa: {formatScore(pooled?.weighted_kappa)}
                </p>
                <h4 className="mt-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
                  클래스별 precision/recall(전체 fold 풀링)
                  <InfoPopover>
                    precision은 이 카테고리로 예측했을 때 실제로 맞았던 비율, recall은
                    실제로 이 카테고리였던 것 중 모델이 맞춘 비율입니다.
                  </InfoPopover>
                </h4>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  {CATEGORY_ORDER.map((label) => {
                    const pr = pooled?.class_precision_recall?.[label];
                    return (
                      <span key={label}>
                        {label} P {formatPct(pr?.precision)} / R {formatPct(pr?.recall)}
                      </span>
                    );
                  })}
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">성능 지표 없음(재학습 후 모델을 배포하면 표시됩니다)</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 이 파일 관련 에러 없음(Task 9 완료 전까지 `RegimeMlAdminPanel.tsx` 관련 에러는 남아있을 수 있음)

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/RegimeMlCurrentPrediction.tsx
git commit -m "feat(frontend): ML 현재예측 카드를 3단계+macro F1/weighted kappa로 갱신"
```

---

### Task 9: `RegimeMlAdminPanel.tsx` 컬럼 추가

**Files:**
- Modify: `frontend/components/RegimeMlAdminPanel.tsx:31-33, 129-163`

**Interfaces:**
- Consumes: `RegimeMlModelSummary`/`MlModelPerformance`(Task 7)

- [ ] **Step 1: 변경**

`frontend/components/RegimeMlAdminPanel.tsx`의 `formatCorrelation` 함수(31-33번째 줄)를 다음으로 교체:

```typescript
function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : value.toFixed(3);
}
```

같은 파일에서 `formatCorrelation(...)`을 호출하는 부분(현재 143번째 줄 근처)을 포함해, 테이블 부분(129-163번째 줄, `<Table>`부터 `</Table>`까지를 감싼 `<div className="overflow-hidden rounded-md border">`)을 다음으로 교체:

```tsx
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>학습시각</TableHead>
                <TableHead className="text-right">상관계수(구)</TableHead>
                <TableHead className="text-right">macro F1(신)</TableHead>
                <TableHead className="text-right">weighted κ(신)</TableHead>
                <TableHead>상태</TableHead>
                <TableHead className="text-right">배포</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model) => (
                <TableRow key={model.model_timestamp}>
                  <TableCell>{formatDateTime(model.trained_at)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatScore(model.performance?.pooled_correlation)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatScore(model.performance?.pooled?.macro_f1)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatScore(model.performance?.pooled?.weighted_kappa)}
                  </TableCell>
                  <TableCell>
                    {model.is_deployed && <Badge variant="default">현재 배포됨</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => setDeployTarget(model.model_timestamp)}
                    >
                      배포
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
```

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 개발서버로 육안 확인**

Run: `cd frontend && npm run dev` (이미 떠 있다면 생략)
Expected: `/regime` 페이지의 "ML 재학습 관리자 패널" 테이블에 "상관계수(구)/macro F1(신)/weighted κ(신)" 3개 컬럼이 보임(아직 재학습 전이라 기존 구모델 행만 있고 상관계수(구) 컬럼에만 값이 채워져 있음 — 정상, Task 10에서 신모델 행이 추가됨)

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/RegimeMlAdminPanel.tsx
git commit -m "feat(frontend): 관리자 패널에 macro F1/weighted kappa 컬럼 추가"
```

---

### Task 10: 로컬 실제 재학습 + 신구 모델 비교 (배포 안 함)

이 태스크는 코드 변경이 아니라 실측 검증이다. 목표는 사용자가 요청한 "성능비교를 보고 결정" 순간을 실제로 만드는 것 — **여기서 배포 버튼은 누르지 않는다.**

**Files:** 없음(런타임 검증만)

- [ ] **Step 1: 백엔드/프론트 개발서버 기동 확인**

Run: `curl -s http://127.0.0.1:8000/health`와 `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/`
Expected: 각각 `{"status":"ok"}`와 `200`. 둘 다 안 떠 있으면 `PYTHONPATH=. python -m uvicorn backend.main:app --reload --port 8000`과 `cd frontend && npm run dev`로 기동.

- [ ] **Step 2: 로컬 재학습 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`
Expected: 14개 마켓 로드 후 fold별 리포트(macro F1/weighted kappa/confusion/precision·recall) + "전체 fold 풀링" + "마켓별 성능" 블록이 출력되고, `data/regime_ml_models/`에 새 `.txt`+`.json` 쌍이 생성됨. 몇 분 정도 걸릴 수 있음(2026-08-29 14마켓 학습 실측 13분35초 기록 있음).

- [ ] **Step 3: 관리자 패널에서 신구 모델 비교**

`/regime` 페이지의 "ML 재학습 관리자 패널" 테이블을 새로고침(또는 재방문)해서 확인:
- 새로 학습된 모델 행이 추가되고, "macro F1(신)"/"weighted κ(신)" 컬럼에 값이 채워져 있는지
- 기존 배포된 구모델 행은 "상관계수(구)" 컬럼에 여전히 값(0.077 근방)이 있고 "현재 배포됨" 배지가 붙어 있는지
- 새 모델 행에는 "현재 배포됨" 배지가 **없는지**(아직 배포 안 함)

- [ ] **Step 4: 결과를 사용자에게 보고하고 배포 여부는 위임**

신모델의 풀링 macro F1/weighted kappa, 클래스별 precision/recall, 마켓별 breakdown(콘솔 출력의 "마켓별 성능" 블록)을 사용자에게 요약 보고한다. **배포(`ml-deploy`) 버튼은 누르지 않는다** — 배포는 사용자가 이 수치를 보고 별도로 결정한다(설계 문서 원칙: 실측이 목표지표를 악화시키면 사용자 승인 없이 배포하지 않는다 — 반대로 개선됐더라도 최종 배포 승인은 항상 사용자 몫).

---

## 전체 테스트 스위트 확인 (마지막 태스크 이후)

- [ ] Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -v`
Expected: 전부 PASS. (알려진 무관 flaky 테스트 1개: `tests/test_import_backtest_results.py::test_script_runs_as_real_subprocess_entry_point` — 전체 스위트에서 가끔 실패하지만 격리 실행하면 항상 pass, 이번 플랜과 무관)
- [ ] Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음
