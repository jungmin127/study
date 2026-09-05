# 장세 판별 ML — HMM 상태 피처 추가 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마켓별 로그수익률+변동성 기반 Gaussian HMM 상태확률을 기존 LightGBM 장세 판별 파이프라인의 피처로 추가해, 실측(walk-forward pooled weighted kappa, 현재 배포 기준 0.072)으로 개선이 확인된 경우에만 프로덕션에 반영한다.

**Architecture:** `engine/regime_ml_hmm.py`에 fit/score를 분리한 순수 함수를 추가하고(HMM은 파라미터를 학습하는 모델이라 롤링 피처처럼 `build_feature_matrix()`에 넣지 않음), `scripts/train_regime_ml.py`의 fold 루프 안에서 그 fold의 train 구간에만 fit해 train/test 각각에 상태확률을 추론한다. Phase 1(검증 스크립트)에서 개선이 확인된 뒤에만 Phase 2(프로덕션 반영: 학습 파이프라인 통합 + HMM 파라미터 pickle 저장 + 서빙 경로 확장 + 배포 스크립트)를 진행한다.

**Tech Stack:** Python(pandas/LightGBM/hmmlearn), FastAPI 백엔드. 신규 의존성: `hmmlearn`(무료, 로컬 연산).

## Global Constraints

- HMM이 LightGBM을 대체하는 앙상블/스태킹 구조는 비범위 — 이번엔 피처 추가만
- HMM 상태와 Triple Barrier 3클래스(하락/횡보/상승)의 1:1 매핑을 강제하지 않는다
- 완전한 실시간 인과적 필터링(순방향 전용 저수준 구현)은 비범위 — `predict_proba()`(스무딩) 근사를 1차로 채택하고, test 구간 내부의 약한 정보유출은 알려진 한계로 문서화한다
- **Task 2(Phase 1 검증)에서 weighted kappa가 개선되지 않으면 Task 3부터 진행하지 않는다** — 이 플랜은 여기서 중단하고 결과를 사용자에게 보고한다
- AWS 라이브 배포 여부는 사용자 승인 필요 — 이 플랜은 로컬 구현+로컬 검증까지만
- 참고 문서: `docs/superpowers/specs_v1/2026-08-30-regime-ml-hmm-feature-design.md`

---

## File Structure

**신규 생성:**
- `engine/regime_ml_hmm.py` — HMM 관측치 생성/fit/상태확률 추론 순수 함수
- `tests/test_regime_ml_hmm.py`
- `scripts/validate_hmm_feature.py` — Phase 1 검증 스크립트(HMM 피처 있음/없음 비교, 향후 재검증용으로 커밋해 남겨둠 — `scripts/select_barrier_k.py`와 같은 성격)

**수정:**
- `requirements.txt` — `hmmlearn` 추가
- `scripts/train_regime_ml.py` — `run_training()`에 fold별 마켓별 HMM fit/추론 통합, HMM 파라미터 pickle 저장
- `tests/test_train_regime_ml.py` — 사이드카 키 집합 갱신 + pickle 저장 테스트 추가
- `backend/regime_ml_service.py` — `predict_current_ml_regime()`가 pickle을 불러와 서빙 시에도 HMM 피처를 추가하도록 확장(pickle 없으면 하위호환으로 건너뜀)
- `tests/test_regime_ml_service.py` — `_load_hmm_models()` 단위테스트 + HMM 피처 포함 서빙 통합테스트 추가
- `scripts/push_regime_ml_model.sh` — `.pkl`이 있으면 함께 전송(없으면 기존과 동일하게 `.txt`+`.json`만)

**변경 없음:** `engine/regime_ml_features.py`(HMM은 fold 경계가 필요해 여기 안 넣음), `engine/regime_ml_labels.py`, `engine/regime_ml_metrics.py`, `frontend/*`(관리자 패널은 이미 macro F1/weighted kappa를 범용으로 표시하므로 피처 개수 변화와 무관)

---

### Task 1: HMM 관측치/fit/추론 순수 함수

**Files:**
- Create: `engine/regime_ml_hmm.py`
- Test: `tests/test_regime_ml_hmm.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `N_STATES: int = 3`, `HMM_STATE_COLUMNS: list[str]`(`["HMM_STATE_0", "HMM_STATE_1", "HMM_STATE_2"]`), `build_hmm_observations(df: pd.DataFrame, half_life_bars: float) -> pd.DataFrame`(컬럼 `["returns", "volatility"]`, df와 같은 인덱스), `fit_hmm(observations: pd.DataFrame, n_states: int, random_state: int) -> GaussianHMM`, `score_hmm_state_probabilities(model: GaussianHMM, observations: pd.DataFrame) -> pd.DataFrame`(컬럼 `HMM_STATE_COLUMNS`, observations와 같은 인덱스)

- [ ] **Step 1: requirements.txt에 의존성 추가**

`requirements.txt`의 `scikit-learn>=1.3` 다음 줄에 추가:

```
hmmlearn>=0.3,<0.4
```

Run: `pip install hmmlearn`
Expected: 설치 성공(scipy/scikit-learn은 이미 설치돼 있어 추가 의존성 충돌 없음)

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_regime_ml_hmm.py` 신규 작성:

```python
"""
tests/test_regime_ml_hmm.py

engine.regime_ml_hmm의 build_hmm_observations/fit_hmm/score_hmm_state_probabilities를
검증한다. HMM은 파라미터를 학습하는 모델이라(롤링 피처와 다름) fit과 score를
분리해서 제공한다 — walk-forward 무결성을 위해 train 구간에서만 fit해야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.regime_ml_hmm import (
    HMM_STATE_COLUMNS,
    N_STATES,
    build_hmm_observations,
    fit_hmm,
    score_hmm_state_probabilities,
)

_HALF_LIFE_BARS = 24.0


def _make_close_df(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
    return pd.DataFrame({"close": close})


def test_build_hmm_observations_has_returns_and_volatility_columns():
    df = _make_close_df(200, seed=1)
    result = build_hmm_observations(df, _HALF_LIFE_BARS)

    assert list(result.columns) == ["returns", "volatility"]
    assert len(result) == len(df)
    assert pd.isna(result["returns"].iloc[0])


def test_fit_hmm_and_score_state_probabilities_sum_to_one():
    df = _make_close_df(500, seed=2)
    observations = build_hmm_observations(df, _HALF_LIFE_BARS)
    model = fit_hmm(observations.dropna(), n_states=N_STATES, random_state=42)

    result = score_hmm_state_probabilities(model, observations)

    assert list(result.columns) == HMM_STATE_COLUMNS
    assert len(result) == len(observations)
    assert pd.isna(result.iloc[0]).all()
    valid_rows = result.dropna()
    assert len(valid_rows) > 0
    assert np.allclose(valid_rows.sum(axis=1), 1.0, atol=1e-6)


def test_score_hmm_state_probabilities_does_not_refit_model():
    df = _make_close_df(500, seed=3)
    observations = build_hmm_observations(df, _HALF_LIFE_BARS)
    model = fit_hmm(observations.dropna(), n_states=N_STATES, random_state=42)
    means_before = model.means_.copy()

    score_hmm_state_probabilities(model, observations)

    assert np.array_equal(means_before, model.means_)


def test_score_hmm_state_probabilities_preserves_index():
    df = _make_close_df(300, seed=4)
    observations = build_hmm_observations(df, _HALF_LIFE_BARS)
    model = fit_hmm(observations.dropna(), n_states=N_STATES, random_state=42)

    result = score_hmm_state_probabilities(model, observations)

    assert list(result.index) == list(observations.index)
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_hmm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_ml_hmm'`

- [ ] **Step 4: 구현**

`engine/regime_ml_hmm.py` 신규 작성:

```python
"""
engine/regime_ml_hmm.py

장세 판별 ML의 HMM 상태 피처. 로그수익률+변동성 2변수로 마켓별 Gaussian HMM을
학습해 잠재 상태 확률을 만든다 — 기존 롤링 피처(ATR 등)와 달리 파라미터를 EM으로
학습하는 모델이라, fit()과 score(추론)를 분리해 제공한다: 워크포워드 fold의 train
구간에서만 fit_hmm()을 호출하고(미래 정보 유출 방지), 학습된 모델로
score_hmm_state_probabilities()를 train/test 각각에 대해 호출해야 한다. 이 모듈은
fold 경계를 모르므로(순수 함수), fold 루프는 scripts/train_regime_ml.py가 담당한다.

알려진 한계: score_hmm_state_probabilities()는 hmmlearn의 기본 predict_proba()
(순방향+역방향 스무딩)를 쓴다 — test 구간 "안에서" 미래 시점이 과거 시점의 상태확률
추정을 살짝 도와주는 약한 형태의 정보유출이 있다(모델 파라미터 자체는 train에서만
학습되므로 train->test 누출은 없음). 완전한 실시간 인과적 필터링이 필요하면
hmmlearn의 저수준 forward-pass를 직접 호출해야 한다(비범위, 설계 문서 참고).

설계 문서: docs/superpowers/specs_v1/2026-08-30-regime-ml-hmm-feature-design.md
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

N_STATES = 3
HMM_STATE_COLUMNS = [f"HMM_STATE_{i}" for i in range(N_STATES)]
_RANDOM_STATE = 42


def build_hmm_observations(df: pd.DataFrame, half_life_bars: float) -> pd.DataFrame:
    """df: close 컬럼을 포함해야 한다. 반환: returns/volatility 2컬럼 DataFrame(df와
    같은 인덱스). 앞부분(워밍업)은 NaN — pct_change 첫 행 + EWM std 초기 구간."""
    returns = df["close"].pct_change(fill_method=None)
    volatility = returns.ewm(halflife=half_life_bars).std()
    return pd.DataFrame({"returns": returns, "volatility": volatility}, index=df.index)


def fit_hmm(observations: pd.DataFrame, n_states: int = N_STATES, random_state: int = _RANDOM_STATE) -> GaussianHMM:
    """observations: build_hmm_observations()가 만든 2컬럼 DataFrame에서 NaN 행을
    제거한 것이어야 한다(호출자 책임). 워크포워드 fold의 train 구간에서만 호출해야
    한다 — test 구간을 섞어 fit하면 미래 정보가 파라미터에 스며든다."""
    model = GaussianHMM(n_components=n_states, covariance_type="diag", n_iter=100, random_state=random_state)
    model.fit(observations.to_numpy())
    return model


def score_hmm_state_probabilities(model: GaussianHMM, observations: pd.DataFrame) -> pd.DataFrame:
    """학습된(고정된) model로 observations 구간 전체의 상태확률을 추론한다(model을
    다시 fit하지 않음). NaN 행(워밍업 구간)은 그대로 NaN으로 남긴다. 반환:
    HMM_STATE_COLUMNS 컬럼, observations와 같은 인덱스."""
    valid = observations.notna().all(axis=1)
    result = pd.DataFrame(np.nan, index=observations.index, columns=HMM_STATE_COLUMNS)
    if valid.any():
        result.loc[valid, HMM_STATE_COLUMNS] = model.predict_proba(observations.loc[valid].to_numpy())
    return result
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_hmm.py -v`
Expected: PASS (전체 4개)

- [ ] **Step 6: 커밋**

```bash
git add requirements.txt engine/regime_ml_hmm.py tests/test_regime_ml_hmm.py
git commit -m "feat: 장세 ML용 HMM 관측치/fit/상태확률 추론 함수 추가"
```

---

### Task 2: Phase 1 — HMM 피처 효과 검증 스크립트 (조건부 게이트)

이 태스크는 TDD 대상이 아니다(실측 성능을 확인하는 1회성 분석 스크립트 —
`scripts/select_barrier_k.py`와 같은 성격). **이 태스크의 결과가 Task 3 이후 진행
여부를 결정한다.**

**Files:**
- Create: `scripts/validate_hmm_feature.py`

**Interfaces:**
- Consumes: `engine.regime_ml_hmm`의 `HMM_STATE_COLUMNS`/`N_STATES`/`build_hmm_observations`/`fit_hmm`/`score_hmm_state_probabilities`(Task 1), `scripts.train_regime_ml`의 `TIMEFRAME`/`TRAIN_START`/`TRAIN_END`/`N_FOLDS`/`MIN_TRAIN_SAMPLES`/`BARRIER_K`(기존 프로덕션 상수 재사용, 값 자체는 변경 없음)
- Produces: 콘솔에 baseline(HMM 없음) vs HMM 추가 버전의 pooled macro F1/weighted kappa 비교, 마지막 줄에 개선 여부 판정 출력

- [ ] **Step 1: 스크립트 작성**

`scripts/validate_hmm_feature.py` 신규 작성:

```python
"""
scripts/validate_hmm_feature.py

Phase 1 검증 스크립트(docs/superpowers/specs_v1/2026-08-30-regime-ml-hmm-feature-design.md
"C-1. 검증 단계" 참고) — HMM 상태확률 피처를 추가했을 때 walk-forward 성능(pooled
weighted kappa)이 실제로 개선되는지만 확인한다. 프로덕션 코드
(scripts/train_regime_ml.py, engine/regime_ml_features.py)는 건드리지 않는다 —
개선이 확인된 뒤에만 그쪽에 반영한다(이 플랜의 Task 3 이후).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/validate_hmm_feature.py
"""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
from engine.regime_ml_hmm import (
    HMM_STATE_COLUMNS,
    N_STATES,
    build_hmm_observations,
    fit_hmm,
    score_hmm_state_probabilities,
)
from engine.regime_ml_labels import compute_triple_barrier_labels
from engine.regime_ml_metrics import compute_classification_metrics
from engine.regime_ml_splits import generate_walk_forward_folds
from scripts.train_regime_ml import (
    BARRIER_K,
    MIN_TRAIN_SAMPLES,
    N_FOLDS,
    TIMEFRAME,
    TRAIN_END,
    TRAIN_START,
)
from upbit_data_service import timeframe_duration

_MIN_HMM_TRAIN_SAMPLES = 50


def _run_variant(market_frames: dict, folds: list, use_hmm: bool) -> dict:
    all_true, all_pred = [], []
    for fold in folds:
        train_X_parts, train_y_parts, test_X_parts, test_y_parts = [], [], [], []
        for market, (raw_df, features_df, labels, hmm_observations) in market_frames.items():
            candle_time = raw_df["candle_time"]
            valid = labels.notna()
            train_mask = valid & (candle_time <= fold.train_end)
            test_mask = valid & (candle_time >= fold.test_start) & (candle_time <= fold.test_end)

            fdf = features_df
            if use_hmm:
                train_obs = hmm_observations[train_mask].dropna()
                if len(train_obs) >= _MIN_HMM_TRAIN_SAMPLES:
                    model = fit_hmm(train_obs, n_states=N_STATES, random_state=42)
                    hmm_probs = score_hmm_state_probabilities(model, hmm_observations)
                else:
                    hmm_probs = pd.DataFrame(np.nan, index=hmm_observations.index, columns=HMM_STATE_COLUMNS)
                fdf = pd.concat([features_df, hmm_probs], axis=1)

            train_X_parts.append(fdf[train_mask])
            train_y_parts.append(labels[train_mask])
            test_X_parts.append(fdf[test_mask])
            test_y_parts.append(labels[test_mask])

        train_X = pd.concat(train_X_parts)
        train_y = pd.concat(train_y_parts)
        test_X = pd.concat(test_X_parts)
        test_y = pd.concat(test_y_parts)

        if len(train_y) < MIN_TRAIN_SAMPLES or test_y.empty:
            continue

        train_X_fit = train_X.assign(market=train_X["market"].astype("category"))
        test_X_fit = test_X.assign(market=test_X["market"].astype("category"))

        model = lgb.LGBMClassifier(
            objective="multiclass", class_weight="balanced", importance_type="gain", random_state=42, verbosity=-1
        )
        model.fit(train_X_fit, train_y)
        predictions = model.predict(test_X_fit)

        all_true.extend(test_y.to_numpy())
        all_pred.extend(predictions)

    return compute_classification_metrics(all_true, all_pred)


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    embargo = timeframe_duration(TIMEFRAME) * n_bars

    print("데이터 로드 + 피처 계산 중 (마켓당 1회)...")
    market_frames = {}
    for market in TRAINING_MARKETS:
        raw_df = load_market_training_data(market, TIMEFRAME, TRAIN_START, TRAIN_END)
        features_df = build_feature_matrix(raw_df, market, half_life_bars)
        labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, BARRIER_K)
        hmm_observations = build_hmm_observations(raw_df, half_life_bars)
        market_frames[market] = (raw_df, features_df, labels, hmm_observations)
        print(f"  {market}: {len(raw_df)}행")

    folds = generate_walk_forward_folds(TRAIN_START, TRAIN_END, N_FOLDS + 1, embargo)

    print("\n=== 결과 비교 ===")
    baseline = _run_variant(market_frames, folds, use_hmm=False)
    print(
        f"{'baseline(HMM 없음)':<30} macro F1={baseline['macro_f1']:.4f}  "
        f"weighted kappa={baseline['weighted_kappa']:.4f}"
    )
    with_hmm = _run_variant(market_frames, folds, use_hmm=True)
    print(
        f"{'HMM 상태확률 3개 추가':<30} macro F1={with_hmm['macro_f1']:.4f}  "
        f"weighted kappa={with_hmm['weighted_kappa']:.4f}"
    )

    delta = with_hmm["weighted_kappa"] - baseline["weighted_kappa"]
    print(f"\nweighted kappa 변화: {delta:+.4f}")
    if delta > 0:
        print(">>> 개선됨 — Task 3부터 프로덕션 반영 진행")
    else:
        print(">>> 개선 없음/악화 — 여기서 중단, 프로덕션 반영하지 않음")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행해서 개선 여부 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/validate_hmm_feature.py`
Expected: 14개 마켓 로딩 후 baseline/HMM 추가 버전의 macro F1/weighted kappa가
출력되고, 마지막 줄에 개선 여부 판정이 출력됨. 몇 분 정도 걸릴 수 있음(baseline+HMM
두 버전을 각각 5-fold 학습하므로 `scripts/train_regime_ml.py` 1회 실행의 약 2배
시간 — 2026-08-30 실측 기준 약 25~30분 예상).

- [ ] **Step 3: 커밋**

```bash
git add scripts/validate_hmm_feature.py
git commit -m "feat: HMM 상태 피처 효과 검증 스크립트 추가"
```

- [ ] **Step 4: 게이트 — 결과에 따라 분기**

Step 2의 출력에서 `weighted kappa 변화`가 **양수**면 Task 3으로 진행한다.
**0 이하면 여기서 플랜 실행을 멈추고**, 다음을 사용자에게 보고한 뒤 종료한다:
baseline/HMM 추가 버전 각각의 macro F1/weighted kappa 수치, 개선되지 않았다는
결론, 그리고 이 결과를 `[[upbit-v1-regime-ml-market-expansion-b]]` 메모리에
기록할 것(코드는 이미 커밋됐지만 프로덕션에는 반영되지 않은 상태로 남는다 —
`scripts/validate_hmm_feature.py`는 향후 재검증용으로 유지).

---

### Task 3: `scripts/train_regime_ml.py`에 HMM 통합

**Files:**
- Modify: `scripts/train_regime_ml.py` (전체 교체)
- Test: `tests/test_train_regime_ml.py`

**Interfaces:**
- Consumes: `engine.regime_ml_hmm`의 `HMM_STATE_COLUMNS`/`N_STATES`/`build_hmm_observations`/`fit_hmm`/`score_hmm_state_probabilities`(Task 1)
- Produces: `run_training(...)`의 반환값/시그니처는 변경 없음. 사이드카 JSON에 `"hmm_states": int` 키 추가. 모델 저장 시 `{base_name}.pkl`(마켓별 HMM 모델 dict, `market -> GaussianHMM`)을 `.txt`/`.json`과 함께 저장

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`의 임포트 블록(14-27번째 줄)을 다음으로 교체(기존
내용 유지 + `pickle`/`N_STATES` 추가):

```python
from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import scripts.train_regime_ml as train_regime_ml
from engine.regime_ml_hmm import N_STATES
from scripts.train_regime_ml import run_training

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_N = 24 * 40  # minutes60, 40일치
_BARRIER_K = 6.0
```

`test_run_training_saves_json_sidecar_alongside_model`의 사이드카 키 검증 부분
(기존 182-187번째 줄)을 다음으로 교체:

```python
    assert set(sidecar.keys()) == {
        "markets", "labeling_method", "barrier_k", "hmm_states", "classes", "fold_index", "performance",
    }
    assert sidecar["markets"] == list(seeds.keys())
    assert sidecar["labeling_method"] == "triple_barrier"
    assert sidecar["barrier_k"] == _BARRIER_K
    assert sidecar["hmm_states"] == N_STATES
```

파일 맨 끝에 다음 테스트를 추가:

```python
def test_run_training_saves_hmm_models_pickle_alongside_model(tmp_path, monkeypatch):
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

    pkl_files = list(tmp_path.glob("*.pkl"))
    txt_files = list(tmp_path.glob("*.txt"))
    assert len(pkl_files) == 1
    assert pkl_files[0].stem == txt_files[0].stem

    with open(pkl_files[0], "rb") as f:
        hmm_models = pickle.load(f)

    assert set(hmm_models.keys()) <= set(seeds.keys())
    assert len(hmm_models) > 0
    for model in hmm_models.values():
        assert hasattr(model, "predict_proba")
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -v`
Expected: FAIL — `AssertionError`(사이드카에 `hmm_states` 키가 없음) 및
`pkl_files` 관련 새 테스트의 실패(아직 `.pkl`을 저장하지 않으므로)

- [ ] **Step 3: 구현**

`scripts/train_regime_ml.py`를 다음 내용으로 전체 교체:

```python
"""
scripts/train_regime_ml.py

장세 판별기 ML 학습+워크포워드 검증 파이프라인. Triple Barrier Method(하락/횡보/상승
3단계)로 레이블링하고, fold별/전체 풀링/마켓별 분류지표(macro F1/weighted kappa/
confusion matrix/클래스별 precision·recall)를 콘솔에 리포트한다. 마켓별 Gaussian
HMM(로그수익률+변동성 2변수, 3상태) 상태확률을 피처로 추가한다(2026-08-30, 실측으로
개선 확인 후 도입 — docs/superpowers/specs_v1/2026-08-30-regime-ml-hmm-feature-design.md).
HMM은 파라미터를 학습하는 모델이라 다른 피처처럼 build_feature_matrix()에 넣지 않고,
fold 루프 안에서 그 fold의 train 구간에만 fit한다(walk-forward 무결성).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
from engine.regime_ml_hmm import (
    HMM_STATE_COLUMNS,
    N_STATES,
    build_hmm_observations,
    fit_hmm,
    score_hmm_state_probabilities,
)
from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels
from engine.regime_ml_metrics import compute_classification_metrics
from engine.regime_ml_splits import generate_walk_forward_folds
from upbit_data_service import timeframe_duration

TIMEFRAME = "minutes60"
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
TRAIN_END = datetime.now(timezone.utc)
N_FOLDS = 5
MIN_TRAIN_SAMPLES = 500
# scripts/select_barrier_k.py로 2026-08-29 실측(14마켓, 2024-01-01~현재) 최초 결정한
# 값은 5.5(클래스 분포 균형 기준 — 하락 35.3%/횡보 31.7%/상승 33.0%, 최대편차 2.0%p).
# 2026-08-30 실제 walk-forward 성능(kappa) 기준 재탐색(4.0/4.75/5.5/6.25/7.0 grid)에서
# 6.25가 pooled weighted kappa 0.0603→0.0658로 더 좋아 채택(분포 균형은 성능과 별개
# 기준이었다는 뜻). FEAR_GREED_CMC 제거(engine/regime_ml_features.py 참고)와 조합하면
# 0.0603→0.0724.
BARRIER_K = 6.25
# 마켓별 HMM을 그 fold의 train 구간에서 fit할 때 필요한 최소 관측치 수(2026-08-30
# ablation에서 실측 개선 확인 — docs/superpowers/specs_v1/2026-08-30-regime-ml-hmm-
# feature-design.md). 너무 적으면(예: fold 1의 초반 구간) EM이 불안정해질 수 있어
# 그 마켓/fold는 HMM 피처를 NaN으로 남긴다(LightGBM이 결측을 네이티브로 처리).
_MIN_HMM_TRAIN_SAMPLES = 50
MODEL_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"


def _fit_market_hmm(observations: pd.DataFrame, train_mask: pd.Series):
    """observations의 train_mask 구간에서 NaN을 제거한 뒤 HMM을 fit한다. 관측치가
    부족하거나(_MIN_HMM_TRAIN_SAMPLES 미만) EM이 수렴에 실패하면 None을 반환한다
    (호출자가 이 경우 해당 마켓/fold의 HMM 피처를 NaN으로 채운다 — 특이/퇴화
    공분산 등으로 hmmlearn이 ValueError를 던지는 드문 경우까지 방어)."""
    train_obs = observations[train_mask].dropna()
    if len(train_obs) < _MIN_HMM_TRAIN_SAMPLES:
        return None
    try:
        return fit_hmm(train_obs, n_states=N_STATES, random_state=42)
    except ValueError:
        return None


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
    precision·recall)를 계산한다. 마켓별 Gaussian HMM 상태확률(HMM_STATE_COLUMNS)을
    fold의 train 구간에서만 fit해 train/test 각각에 대해 추론한 뒤 피처에 추가한다.
    fold별 리포트 리스트를 반환하고, 마지막으로 성공한 fold의 LightGBM 모델 +
    그 fold에서 fit된 마켓별 HMM 모델을 model_output_dir에 저장한다. 표본이
    min_train_samples 미만이거나 테스트 표본이 없는 fold는 건너뛴다."""
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    embargo = timeframe_duration(timeframe) * n_bars

    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={timeframe}, barrier_k={barrier_k}")

    market_frames: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.DataFrame]] = {}
    for market in markets:
        raw_df = load_market_training_data(market, timeframe, start, end)
        features_df = build_feature_matrix(raw_df, market, half_life_bars)
        labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, barrier_k)
        hmm_observations = build_hmm_observations(raw_df, half_life_bars)
        market_frames[market] = (raw_df, features_df, labels, hmm_observations)

    # fold 0은 test_start == start라 train_end(=test_start - embargo)가 항상 start
    # 이전이 되어 훈련 표본이 구조적으로 0이다(아래 min_train_samples 가드로 항상
    # 건너뜀). n_folds보다 하나 더 요청해 그 "항상 비는" fold를 인덱스 0으로 흡수시키고,
    # 실제로 평가되는 나머지 n_folds개 fold가 [start, end] 거의 전체를 덮게 한다.
    folds = generate_walk_forward_folds(start, end, n_folds + 1, embargo)

    reports: list[dict] = []
    last_model: lgb.LGBMClassifier | None = None
    last_class_order: list[str] | None = None
    last_fold_index: int | None = None
    last_hmm_models: dict[str, object] = {}
    all_true: list[str] = []
    all_pred: list[str] = []
    all_markets: list[str] = []

    for fold in folds:
        train_X_parts, train_y_parts, test_X_parts, test_y_parts = [], [], [], []
        fold_hmm_models: dict[str, object] = {}
        for market, (raw_df, features_df, labels, hmm_observations) in market_frames.items():
            candle_time = raw_df["candle_time"]
            valid = labels.notna()
            train_mask = valid & (candle_time <= fold.train_end)
            test_mask = valid & (candle_time >= fold.test_start) & (candle_time <= fold.test_end)

            hmm_model = _fit_market_hmm(hmm_observations, train_mask)
            if hmm_model is not None:
                hmm_probs = score_hmm_state_probabilities(hmm_model, hmm_observations)
                fold_hmm_models[market] = hmm_model
            else:
                hmm_probs = pd.DataFrame(np.nan, index=hmm_observations.index, columns=HMM_STATE_COLUMNS)
            features_with_hmm = pd.concat([features_df, hmm_probs], axis=1)

            train_X_parts.append(features_with_hmm[train_mask])
            train_y_parts.append(labels[train_mask])
            test_X_parts.append(features_with_hmm[test_mask])
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
        last_hmm_models = fold_hmm_models

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

        with open(model_output_dir / f"{base_name}.pkl", "wb") as f:
            pickle.dump(last_hmm_models, f)

        sidecar = {
            "markets": markets,
            "labeling_method": "triple_barrier",
            "barrier_k": barrier_k,
            "hmm_states": N_STATES,
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
    """pooled_metrics/per_market_metrics는 모든 fold의 (실제,예측) 쌍을 이어붙인 뒤
    지표 함수를 단 한 번 호출해서 계산해야 한다 — fold별 지표값을 평균내는 방식은
    macro F1/kappa처럼 표본 크기에 비선형인 지표에서는 통계적으로 부적절하다."""
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
Expected: PASS (전체 7개)

- [ ] **Step 5: 커밋**

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: 학습 파이프라인에 마켓별 HMM 상태확률 피처 통합"
```

---

### Task 4: `backend/regime_ml_service.py` 서빙 경로에 HMM 통합

**Files:**
- Modify: `backend/regime_ml_service.py`
- Test: `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: `engine.regime_ml_hmm`의 `HMM_STATE_COLUMNS`/`build_hmm_observations`/`score_hmm_state_probabilities`(Task 1), Task 3이 저장하는 `{model_timestamp}.pkl`
- Produces: `_load_hmm_models(model_path: Path) -> dict[str, object] | None`(신규, `.pkl` 없으면 None). `predict_current_ml_regime()`의 반환값 스키마는 변경 없음

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_service.py`의 임포트 블록(13-26번째 줄)에 다음을 추가(기존
임포트 유지):

```python
import pickle
```

(3번째 줄, `import json` 다음에 추가)

파일 맨 끝에 다음 테스트 3개를 추가:

```python
def test_load_hmm_models_returns_none_when_pkl_missing(tmp_path):
    txt_path = tmp_path / "regime_ml_20260827T052047Z.txt"
    txt_path.write_text("stub")

    assert regime_ml_service._load_hmm_models(txt_path) is None


def test_load_hmm_models_returns_pickled_dict_when_present(tmp_path):
    txt_path = tmp_path / "regime_ml_20260827T052047Z.txt"
    txt_path.write_text("stub")
    pkl_path = tmp_path / "regime_ml_20260827T052047Z.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump({"KRW-BTC": "fake_model"}, f)

    result = regime_ml_service._load_hmm_models(txt_path)

    assert result == {"KRW-BTC": "fake_model"}


class _StubHmmModel:
    def predict_proba(self, X):
        return np.tile([0.2, 0.3, 0.5], (len(X), 1))


def test_predict_current_ml_regime_adds_hmm_features_when_pkl_present(tmp_path, monkeypatch):
    """_load_hmm_models가 모델을 반환하면(.pkl 존재) HMM_STATE_0/1/2가 피처에
    추가된다 — booster도 FEATURE_A/B + HMM_STATE_0/1/2 5개로 학습해 스키마를
    맞춘다. HMM 피처가 실제로 붙었는지는 booster.num_feature() 검증(2개였다면
    5개 기대 부스터와 불일치해 RuntimeError가 났을 것)으로 간접 확인한다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)

    rng = np.random.default_rng(0)
    rows = []
    for market in _MARKETS:
        for _ in range(30):
            rows.append({
                "FEATURE_A": rng.normal(), "FEATURE_B": rng.normal(),
                "HMM_STATE_0": rng.random(), "HMM_STATE_1": rng.random(), "HMM_STATE_2": rng.random(),
                "market": market,
            })
    df = pd.DataFrame(rows)
    df["market"] = df["market"].astype("category")
    labels = pd.Series(rng.choice(_LABELS, size=len(df)))
    model = lgb.LGBMClassifier(objective="multiclass", num_leaves=4, min_child_samples=1, random_state=0)
    model.fit(df, labels)

    txt_path = tmp_path / "regime_ml_20260827T052047Z.txt"
    json_path = tmp_path / "regime_ml_20260827T052047Z.json"
    model.booster_.save_model(str(txt_path))
    sidecar = {"classes": [str(c) for c in model.classes_], "fold_index": 5, "hmm_states": 3}
    json_path.write_text(json.dumps(sidecar), encoding="utf-8")

    fake_raw_df = pd.DataFrame({
        "close": 1000.0 + np.cumsum(rng.normal(0, 1.0, 60)),
        "candle_time": pd.date_range("2026-08-25T01:00:00", periods=60, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })
    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)
    monkeypatch.setattr(regime_ml_service, "_load_hmm_models", lambda model_path: {"KRW-ETH": _StubHmmModel()})

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    assert set(result["probs"].keys()) == set(_LABELS)
    assert sum(result["probs"].values()) == pytest.approx(1.0, abs=1e-6)
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_service.py -k "hmm" -v`
Expected: FAIL — `AttributeError: module 'backend.regime_ml_service' has no attribute '_load_hmm_models'`

- [ ] **Step 3: 구현**

`backend/regime_ml_service.py`의 임포트 블록(9-24번째 줄)을 다음으로 교체:

```python
from __future__ import annotations

import json
import pickle
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from engine.regime_math import half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
from engine.regime_ml_hmm import HMM_STATE_COLUMNS, build_hmm_observations, score_hmm_state_probabilities
```

`_to_utc_iso` 함수(기존 85-91번째 줄) 바로 다음에 `_load_hmm_models` 함수를 추가:

```python
def _load_hmm_models(model_path: Path) -> dict[str, object] | None:
    """model_path(.txt)와 짝을 이루는 .pkl(마켓별 HMM 파라미터)이 있으면 불러온다.
    없으면 None을 반환한다 — HMM 피처 도입 이전에 학습된 모델과의 하위호환
    (predict_current_ml_regime이 이 경우 HMM 피처를 아예 추가하지 않아, 그 구형
    모델이 학습 당시 기대한 피처 개수와 그대로 일치하게 된다)."""
    pkl_path = model_path.with_suffix(".pkl")
    if not pkl_path.exists():
        return None
    with open(pkl_path, "rb") as f:
        return pickle.load(f)
```

`predict_current_ml_regime` 안의 다음 부분:

```python
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)
    # 학습 시 train_X["market"].astype("category")가 TRAINING_MARKETS의 알파벳순으로
```

를 다음으로 교체(HMM 블록 삽입):

```python
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)

    hmm_models = _load_hmm_models(model_path)
    if hmm_models is not None:
        observations = build_hmm_observations(df, half_life_bars)
        hmm_model = hmm_models.get(market)
        if hmm_model is not None:
            hmm_probs = score_hmm_state_probabilities(hmm_model, observations)
        else:
            hmm_probs = pd.DataFrame(float("nan"), index=observations.index, columns=HMM_STATE_COLUMNS)
        features_df = pd.concat([features_df, hmm_probs], axis=1)

    # 학습 시 train_X["market"].astype("category")가 TRAINING_MARKETS의 알파벳순으로
```

(나머지 함수 본문은 변경 없음)

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_service.py -v`
Expected: PASS (전체, 기존 테스트 포함 — `.pkl`이 없는 기존 픽스처들은
`_load_hmm_models`가 None을 반환해 이전과 동일하게 동작해야 함)

- [ ] **Step 5: 커밋**

```bash
git add backend/regime_ml_service.py tests/test_regime_ml_service.py
git commit -m "feat: 서빙 경로에도 HMM 상태확률 피처 추가(pickle 하위호환 포함)"
```

---

### Task 5: 배포 스크립트가 `.pkl`도 함께 전송하도록 확장

**Files:**
- Modify: `scripts/push_regime_ml_model.sh`

**Interfaces:**
- Consumes: Task 3이 만드는 `{model_timestamp}.pkl`(있을 수도, 없을 수도 있음 — HMM
  도입 이전 모델은 없음)
- Produces: 없음(배포 스크립트 동작 변경만)

- [ ] **Step 1: 변경**

`scripts/push_regime_ml_model.sh`의 다음 줄:

```bash
LOCAL_JSON="${LOCAL_TXT%.txt}.json"
```

다음으로 교체:

```bash
LOCAL_JSON="${LOCAL_TXT%.txt}.json"
LOCAL_PKL="${LOCAL_TXT%.txt}.pkl"
```

다음 블록:

```bash
echo "=== 2/3: 모델 파일 전송 ==="
scp -i "$DEPLOY_SSH_KEY_PATH" "$LOCAL_TXT" "$LOCAL_JSON" "$DEPLOY_SERVER_HOST:$REMOTE_MODEL_DIR/"
```

다음으로 교체:

```bash
echo "=== 2/3: 모델 파일 전송 ==="
if [ -f "$LOCAL_PKL" ]; then
    scp -i "$DEPLOY_SSH_KEY_PATH" "$LOCAL_TXT" "$LOCAL_JSON" "$LOCAL_PKL" "$DEPLOY_SERVER_HOST:$REMOTE_MODEL_DIR/"
else
    scp -i "$DEPLOY_SSH_KEY_PATH" "$LOCAL_TXT" "$LOCAL_JSON" "$DEPLOY_SERVER_HOST:$REMOTE_MODEL_DIR/"
fi
```

마지막 줄:

```bash
echo "모델 전송 완료: $MODEL_NAME (.txt + .json + 배포 마커)"
```

다음으로 교체:

```bash
if [ -f "$LOCAL_PKL" ]; then
    echo "모델 전송 완료: $MODEL_NAME (.txt + .json + .pkl + 배포 마커)"
else
    echo "모델 전송 완료: $MODEL_NAME (.txt + .json + 배포 마커, HMM 피처 이전 모델이라 .pkl 없음)"
fi
```

- [ ] **Step 2: 문법 확인**

Run: `bash -n scripts/push_regime_ml_model.sh`
Expected: 출력 없이 종료(exit code 0) — 문법 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add scripts/push_regime_ml_model.sh
git commit -m "feat: 배포 스크립트가 HMM 파라미터(.pkl)도 함께 전송하도록 확장"
```

---

### Task 6: 로컬 실제 재학습 + 신구 모델 비교 (배포 안 함)

이 태스크는 코드 변경이 아니라 실측 검증이다. **여기서 배포 버튼은 누르지 않는다.**

**Files:** 없음(런타임 검증만)

- [ ] **Step 1: 백엔드/프론트 개발서버 기동 확인**

Run: `curl -s http://127.0.0.1:8000/health`와 `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/`
Expected: 각각 `{"status":"ok"}`와 `200`. 둘 다 안 떠 있으면
`PYTHONPATH=. python -m uvicorn backend.main:app --reload --port 8000`과
`cd frontend && npm run dev`로 기동. **이미 떠 있던 백엔드라면 Task 3/4의 코드
변경을 반영하기 위해 재시작해야 한다**(uvicorn --reload가 이번 세션에서 한 번
조용히 안 먹힌 전례가 있었다).

- [ ] **Step 2: 로컬 재학습 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`
Expected: 14개 마켓 로드 후 fold별 리포트 + "전체 fold 풀링" + "마켓별 성능" 블록이
출력되고, `data/regime_ml_models/`에 새 `.txt`+`.json`+`.pkl` 3종 세트가 생성됨.
Task 2의 검증 스크립트보다는 빠르다(변형 1개만 학습하므로) — 2026-08-30 HMM 없는
버전 기준 실측 약 14분.

- [ ] **Step 3: 관리자 패널에서 신구 모델 비교**

`/regime` 페이지의 "ML 재학습 관리자 패널" 테이블을 새로고침해서 확인:
- 새로 학습된 모델 행이 추가되고, "macro F1(신)"/"weighted κ(신)" 컬럼에 값이
  채워져 있는지(현재 배포 기준 0.401/0.072와 비교)
- 새 모델 행에는 "현재 배포됨" 배지가 **없는지**(아직 배포 안 함)

- [ ] **Step 4: 결과를 사용자에게 보고하고 배포 여부는 위임**

신모델의 풀링 macro F1/weighted kappa, 클래스별 precision/recall, 마켓별
breakdown(콘솔 출력)을 Task 2의 검증 스크립트 결과와 함께 사용자에게 요약
보고한다. **배포(`ml-deploy`) 버튼은 누르지 않는다** — 배포는 사용자가 이
수치를 보고 별도로 결정한다.

---

## 전체 테스트 스위트 확인 (마지막 태스크 이후)

- [ ] Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -v`
Expected: 전부 PASS. (알려진 무관 flaky 테스트 1개:
`tests/test_import_backtest_results.py::test_script_runs_as_real_subprocess_entry_point`
— 전체 스위트에서 가끔 실패하지만 격리 실행하면 항상 pass, 이번 플랜과 무관)
- [ ] Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(이 플랜은 프론트 코드를 건드리지 않으므로 원래 통과 상태
그대로여야 함)
