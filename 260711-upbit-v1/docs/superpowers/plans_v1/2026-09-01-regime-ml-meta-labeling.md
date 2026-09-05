# 장세 판별 ML — 메타 레이블링(c-3) 학습+측정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-09-01-regime-ml-meta-labeling-design.md`에서 승인된 설계대로 `run_training()`에 `collect_oof` 확장점을 추가해 1차 모델의 out-of-fold 예측을 노출하고, 그 위에 메타 레이블링(2차 이진분류기) 학습+측정 스크립트를 추가해 threshold 튜닝만으로는 못 넘은 precision 장벽(threshold=0.70에서도 39%대)을 메타 게이트로 넘을 수 있는지 실측한다.

**Architecture:** `run_training()`에 `collect_oof: bool = False` 파라미터를 추가해 fold 루프의 테스트 구간 원본 행(피처+proba_down+실제라벨)을 `TrainingResult.oof` DataFrame으로 노출 → `scripts/train_regime_ml_meta_label.py`가 1차 모델을 한 번만 돌려 이 OOF 데이터를 얻고, `proba_down>=0.5` 후보 집합을 시간순 70/30으로 나눠 메타모델(LightGBM)을 학습·평가.

**Tech Stack:** Python, pandas, LightGBM, pytest. 신규 의존성 없음(`engine/regime_ml_calibration.py::compute_precision_recall_table` 기존 함수 재사용).

## Global Constraints

- `half_life_bars`/피처 엔지니어링/기존 15개 `run_training()` 호출부는 `collect_oof` 기본값(`False`)에서 100% 영향받지 않는다.
- `collect_oof=True`는 `save_model` 가드(model_factory/preprocess_fold/n_multiplier가 전부 기본값일 때만 `save_model=True` 허용)와 무관하다 — `collect_oof`는 모델 자체를 바꾸지 않으므로 이 가드 조건에 포함시키지 않는다.
- 메타 레이블링 스크립트는 **1차 모델을 한 번만 학습**한다(재학습/그리드서치 없음) — c-2/horizon 라운드에서 반복된 "실행시간 과소평가" 문제와 무관하게, 이번 라운드는 단일 학습 1회(실측 40분~2시간, 세션 내 변동폭 그대로 인지) + 메타모델 학습 1회(후보 집합만 대상이라 1차보다 빠를 것으로 예상)로 스코프가 작다. 그래도 실행 전 시작 로그를 확인하고, 예상외로 오래 걸리면(예: 3시간 초과) 사용자에게 확인할 것.
- 메타 학습/메타 테스트 각각 500행 미만이면 진행하지 않고 경고 후 중단한다(`_MIN_SPLIT_SAMPLES`).
- 서빙(`backend/regime_ml_service.py`) 연결배선은 이번 스코프 아님 — 하지 않는다.
- 각 태스크 끝에서 `PYTHONPATH=. python -m pytest tests/ -q`가 전부 통과해야 한다(단, `tests/test_import_backtest_results.py::test_script_runs_as_real_subprocess_entry_point`는 이 작업과 무관한 기존 Windows subprocess 인코딩 flake로 알려져 있음 — 실패해도 회귀 아님).
- 한국어 docstring/주석 관례 유지("왜"를 설명).

---

## Task 1: `run_training()`에 `collect_oof` 파라미터 추가

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Modify: `tests/test_train_regime_ml.py`

**Interfaces:**
- Produces: `run_training(..., collect_oof: bool = False)`. `TrainingResult`에 `oof: pd.DataFrame | None = None` 필드 추가. `collect_oof=True`일 때 `oof`는 `candle_time`/`market`/`true_label`/`proba_down` + 74개 피처 컬럼을 가진 DataFrame, 행 수는 `pooled["n"]`과 일치. Task 2가 이 DataFrame을 소비한다.

### Step 1: 실패하는 테스트 작성

`tests/test_train_regime_ml.py` 파일 끝(기존 마지막 테스트 뒤)에 아래 2개
테스트를 추가한다:

```python
def test_run_training_collect_oof_returns_dataframe_with_expected_columns(tmp_path, monkeypatch):
    """collect_oof=True면 result.oof가 74개 피처+candle_time/market/true_label/
    proba_down 컬럼을 가진 DataFrame이고, 행 수가 pooled 표본 수와 일치하는지
    확인한다(메타 레이블링 스크립트가 이 데이터프레임에 의존)."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
        save_model=False,
        collect_oof=True,
    )

    assert result.oof is not None
    assert len(result.oof) == result.pooled["n"]
    for column in ("candle_time", "market", "true_label", "proba_down"):
        assert column in result.oof.columns
    assert "RAW_SCORE" in result.oof.columns  # 74개 피처 중 하나 대표 확인
    assert set(result.oof["true_label"].unique()) <= set(train_regime_ml.CATEGORY_LABELS)
    assert result.oof["proba_down"].between(0.0, 1.0).all()


def test_run_training_collect_oof_false_returns_none(tmp_path, monkeypatch):
    """collect_oof을 생략(기본값 False)하면 result.oof가 None이고 기존 동작에
    영향이 없는지 확인한다(회귀 안전장치)."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
        save_model=False,
    )

    assert result.oof is None
```

### Step 2: 테스트 실행해서 실패 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -k collect_oof -v`
Expected: 2개 테스트 전부 FAIL(`TypeError: run_training() got an unexpected keyword argument 'collect_oof'`).

### Step 3: `scripts/train_regime_ml.py` 구현

`TrainingResult` 데이터클래스에 필드를 추가한다.

교체 전:
```python
@dataclass
class TrainingResult:
    """run_training()의 반환값. reports는 기존 fold별 리포트 리스트와 완전히
    동일하다 — pooled/per_market은 원래 함수 내부에서만 계산되고 sidecar JSON에만
    남던 값을 호출자가 직접 접근할 수 있게 노출한 것(비교/튜닝 스크립트가 필요)."""
    reports: list[dict]
    pooled: dict
    per_market: dict[str, dict]
```

교체 후:
```python
@dataclass
class TrainingResult:
    """run_training()의 반환값. reports는 기존 fold별 리포트 리스트와 완전히
    동일하다 — pooled/per_market은 원래 함수 내부에서만 계산되고 sidecar JSON에만
    남던 값을 호출자가 직접 접근할 수 있게 노출한 것(비교/튜닝 스크립트가 필요).
    oof는 collect_oof=True일 때만 채워지는 원본 out-of-fold 예측 데이터프레임
    (메타 레이블링 스크립트가 소비, scripts/train_regime_ml_meta_label.py 참고)."""
    reports: list[dict]
    pooled: dict
    per_market: dict[str, dict]
    oof: pd.DataFrame | None = None
```

`run_training()` 시그니처를 교체한다.

교체 전:
```python
def run_training(
    markets: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    n_folds: int,
    min_train_samples: int,
    barrier_k: float,
    model_output_dir: Path,
    model_factory: Callable[[], Any] = _default_lgbm_factory,
    preprocess_fold: Callable[[pd.DataFrame, pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]] | None = None,
    save_model: bool = True,
    n_multiplier: float = N_MULTIPLIER,
) -> TrainingResult:
```

교체 후:
```python
def run_training(
    markets: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    n_folds: int,
    min_train_samples: int,
    barrier_k: float,
    model_output_dir: Path,
    model_factory: Callable[[], Any] = _default_lgbm_factory,
    preprocess_fold: Callable[[pd.DataFrame, pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]] | None = None,
    save_model: bool = True,
    n_multiplier: float = N_MULTIPLIER,
    collect_oof: bool = False,
) -> TrainingResult:
```

docstring 마지막 문장(`ValueError로 가드)."""`) 뒤에 이어서 추가한다.

교체 전:
```python
    save_model=True는 모든 파라미터가 기본값(기본 LightGBM 팩토리, 기본 전처리,
    기본 n_multiplier)일 때만 허용한다 — model_factory/preprocess_fold/n_multiplier
    중 하나라도 바뀐 실험용 모델이 data/regime_ml_models/에 저장되면
    backend/regime_ml_service.py가 파일명 정렬로 최신 모델을 서빙 모델로 골라
    실서비스에 실험 결과가 섞여 들어갈 수 있다(2026-09-01 최종 리뷰 Important
    지적, ValueError로 가드)."""
```

교체 후:
```python
    save_model=True는 모든 파라미터가 기본값(기본 LightGBM 팩토리, 기본 전처리,
    기본 n_multiplier)일 때만 허용한다 — model_factory/preprocess_fold/n_multiplier
    중 하나라도 바뀐 실험용 모델이 data/regime_ml_models/에 저장되면
    backend/regime_ml_service.py가 파일명 정렬로 최신 모델을 서빙 모델로 골라
    실서비스에 실험 결과가 섞여 들어갈 수 있다(2026-09-01 최종 리뷰 Important
    지적, ValueError로 가드).

    collect_oof=True면 fold 루프의 테스트 구간 원본 행(74개 피처+candle_time/
    market/true_label/proba_down)을 모아 TrainingResult.oof에 담는다(메타
    레이블링 스크립트가 소비, scripts/train_regime_ml_meta_label.py 참고).
    모델 자체를 바꾸지 않으므로 save_model 가드와 무관하다. 기본값(False)이면
    oof=None이고 추가 연산/메모리 비용이 없다."""
```

fold 루프 시작 전 리스트 초기화 블록을 교체한다.

교체 전:
```python
    reports: list[dict] = []
    last_model: lgb.LGBMClassifier | None = None
    last_class_order: list[str] | None = None
    last_fold_index: int | None = None
    all_true: list[str] = []
    all_pred: list[str] = []
    all_markets: list[str] = []
    all_proba_down: list[float] = []
```

교체 후:
```python
    reports: list[dict] = []
    last_model: lgb.LGBMClassifier | None = None
    last_class_order: list[str] | None = None
    last_fold_index: int | None = None
    all_true: list[str] = []
    all_pred: list[str] = []
    all_markets: list[str] = []
    all_proba_down: list[float] = []
    oof_records: list[pd.DataFrame] = []
```

per-market 루프에서 test 구간 candle_time도 함께 모으도록 교체한다.

교체 전:
```python
    for fold in folds:
        train_X_parts, train_y_parts, train_w_parts, test_X_parts, test_y_parts = [], [], [], [], []
        for candle_time, features_df, labels, weights in market_frames.values():
            valid = labels.notna()
            train_mask = valid & (candle_time <= fold.train_end)
            test_mask = valid & (candle_time >= fold.test_start) & (candle_time <= fold.test_end)
            train_X_parts.append(features_df[train_mask])
            train_y_parts.append(labels[train_mask])
            train_w_parts.append(weights[train_mask])
            test_X_parts.append(features_df[test_mask])
            test_y_parts.append(labels[test_mask])

        train_X = pd.concat(train_X_parts)
        train_y = pd.concat(train_y_parts)
        train_w = pd.concat(train_w_parts)
        test_X = pd.concat(test_X_parts)
        test_y = pd.concat(test_y_parts)
```

교체 후:
```python
    for fold in folds:
        train_X_parts, train_y_parts, train_w_parts, test_X_parts, test_y_parts = [], [], [], [], []
        test_time_parts = []
        for candle_time, features_df, labels, weights in market_frames.values():
            valid = labels.notna()
            train_mask = valid & (candle_time <= fold.train_end)
            test_mask = valid & (candle_time >= fold.test_start) & (candle_time <= fold.test_end)
            train_X_parts.append(features_df[train_mask])
            train_y_parts.append(labels[train_mask])
            train_w_parts.append(weights[train_mask])
            test_X_parts.append(features_df[test_mask])
            test_y_parts.append(labels[test_mask])
            test_time_parts.append(candle_time[test_mask])

        train_X = pd.concat(train_X_parts)
        train_y = pd.concat(train_y_parts)
        train_w = pd.concat(train_w_parts)
        test_X = pd.concat(test_X_parts)
        test_y = pd.concat(test_y_parts)
        test_time = pd.concat(test_time_parts)
```

`all_true.extend(...)` 직전에 OOF 레코드 누적 블록을 추가한다.

교체 전:
```python
        all_true.extend(true_values)
        all_pred.extend(predictions)
        all_markets.extend(test_markets)
        all_proba_down.extend(proba_down)
```

교체 후:
```python
        if collect_oof:
            oof_chunk = test_X_fit.copy()
            oof_chunk["candle_time"] = test_time.to_numpy()
            oof_chunk["true_label"] = true_values
            oof_chunk["proba_down"] = proba_down
            oof_records.append(oof_chunk)

        all_true.extend(true_values)
        all_pred.extend(predictions)
        all_markets.extend(test_markets)
        all_proba_down.extend(proba_down)
```

반환문을 교체한다.

교체 전:
```python
    return TrainingResult(reports=reports, pooled=pooled_metrics, per_market=per_market_metrics)
```

교체 후:
```python
    oof = pd.concat(oof_records, ignore_index=True) if oof_records else None
    return TrainingResult(reports=reports, pooled=pooled_metrics, per_market=per_market_metrics, oof=oof)
```

### Step 4: 테스트 실행해서 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -v`
Expected: 19 passed(기존 17개 + 신규 2개).

### Step 5: 전체 테스트 스위트 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 기존 실패 0건 유지(`test_import_backtest_results.py`의 알려진 무관한 flake 1건 제외).

### Step 6: 커밋

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: run_training에 collect_oof 확장점 추가(메타 레이블링용 OOF 노출)"
```

---

## Task 2: `scripts/train_regime_ml_meta_label.py` — 메타 레이블링 학습+측정

**Files:**
- Create: `scripts/train_regime_ml_meta_label.py`

**Interfaces:**
- Consumes: Task 1의 `run_training(..., collect_oof=True, save_model=False) -> TrainingResult`(`.oof` DataFrame), `engine.regime_ml_calibration.compute_precision_recall_table(y_true, proba, thresholds) -> list[dict]`(기존 함수, 시그니처 변경 없음), `scripts.train_regime_ml`의 `BARRIER_K`/`MIN_TRAIN_SAMPLES`/`MODEL_OUTPUT_DIR`/`N_FOLDS`/`TIMEFRAME`/`TRAIN_END`/`TRAIN_START`, `engine.regime_ml_constants.TRAINING_MARKETS`.

`select_barrier_k.py`/c-2/horizon 스크립트들과 같은 성격의 1회성 진단
스크립트라 전용 테스트를 작성하지 않는다(기존 프로젝트 관례). Step 2에서
synthetic 데이터로 실제 동작을 손으로 확인한다.

### Step 1: 스크립트 작성

```python
"""
scripts/train_regime_ml_meta_label.py

메타 레이블링(c-3, AFML 표준 기법) 학습+측정. 1차 모델(하락/하락아님)이 이미
"하락"이라 분류한 케이스 중 실제로 믿을만한 건지 판단하는 2차 이진분류기를
얹어, engine/regime_ml_calibration.py의 단순 threshold 튜닝만으로는 못 넘은
precision 장벽(최고 threshold=0.70에서도 precision 39%대/recall 10.8%)을
구조적으로 공략한다. 설계:
docs/superpowers/specs_v1/2026-09-01-regime-ml-meta-labeling-design.md

1차 모델은 한 번만 학습(재학습 없음) — run_training(collect_oof=True)로 얻은
전체 기간 out-of-fold 예측을 메타모델의 학습 재료로 재사용한다. 메타모델
자체는 1차처럼 다시 워크포워드하지 않고, 후보 집합(1차가 "하락"이라 분류한
proba_down>=0.5 전부)을 candle_time 기준 시간순 70/30으로 나눠 학습/평가한다
(사용자 확인 — 완전한 중첩 워크포워드는 범위 밖).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml_meta_label.py
"""
from __future__ import annotations

import lightgbm as lgb

from engine.regime_ml_calibration import compute_precision_recall_table
from engine.regime_ml_constants import TRAINING_MARKETS
from scripts.train_regime_ml import (
    BARRIER_K,
    MIN_TRAIN_SAMPLES,
    MODEL_OUTPUT_DIR,
    N_FOLDS,
    TIMEFRAME,
    TRAIN_END,
    TRAIN_START,
    run_training,
)

_CANDIDATE_PROBA_THRESHOLD = 0.5  # 1차가 "하락"으로 분류하는 기본 경계
_META_TRAIN_FRACTION = 0.7  # candle_time 기준 시간순 분할
_MIN_SPLIT_SAMPLES = 500  # 1차의 MIN_TRAIN_SAMPLES와 동일 기준 재사용
_THRESHOLD_GRID = [round(0.30 + 0.05 * i, 2) for i in range(13)]  # 0.30~0.90
_BASELINE_THRESHOLD = 0.70  # scripts/train_regime_ml.py 기존 threshold 튜닝 최선
_BASELINE_PRECISION = 0.39
_BASELINE_RECALL = 0.108


def _meta_model_factory() -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(objective="binary", class_weight="balanced", random_state=42)


def main() -> None:
    print("=== 1차 모델 학습(collect_oof=True) ===")
    result = run_training(
        markets=TRAINING_MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        barrier_k=BARRIER_K,
        model_output_dir=MODEL_OUTPUT_DIR,
        save_model=False,
        collect_oof=True,
    )
    if result.oof is None:
        print("1차 모델이 OOF 데이터를 만들지 못했습니다(표본 부족) — 중단합니다.")
        return

    oof = result.oof
    candidates = oof[oof["proba_down"] >= _CANDIDATE_PROBA_THRESHOLD].sort_values("candle_time")
    print(f"1차 OOF 전체 n={len(oof)}, 후보(proba_down>={_CANDIDATE_PROBA_THRESHOLD}) n={len(candidates)}")

    split_index = int(len(candidates) * _META_TRAIN_FRACTION)
    meta_train = candidates.iloc[:split_index]
    meta_test = candidates.iloc[split_index:]
    print(f"메타 학습 n={len(meta_train)}, 메타 테스트 n={len(meta_test)}")

    if len(meta_train) < _MIN_SPLIT_SAMPLES or len(meta_test) < _MIN_SPLIT_SAMPLES:
        print(f"메타 학습/테스트 표본이 {_MIN_SPLIT_SAMPLES}개 미만입니다 — 중단합니다.")
        return

    feature_columns = [c for c in oof.columns if c not in ("candle_time", "true_label")]

    meta_train_X = meta_train[feature_columns].assign(market=meta_train["market"].astype("category"))
    meta_train_y = (meta_train["true_label"] == "하락").astype(int)
    meta_test_X = meta_test[feature_columns].assign(market=meta_test["market"].astype("category"))
    meta_test_y = (meta_test["true_label"] == "하락").astype(int)

    if meta_train_y.nunique() < 2:
        print("메타 학습 구간의 라벨이 단일 클래스뿐입니다 — 중단합니다.")
        return

    meta_model = _meta_model_factory()
    meta_model.fit(meta_train_X, meta_train_y)

    meta_proba = meta_model.predict_proba(meta_test_X)[:, list(meta_model.classes_).index(1)]
    meta_true_labels = ["하락" if v == 1 else "하락아님" for v in meta_test_y]
    meta_table = compute_precision_recall_table(meta_true_labels, meta_proba.tolist(), _THRESHOLD_GRID)

    print("\n=== 1차+메타 게이트 — threshold별 precision/recall(메타 테스트 구간) ===")
    for row in meta_table:
        print(
            f"  threshold={row['threshold']:.2f}  precision={row['precision']:.3f}  "
            f"recall={row['recall']:.3f}  n_predicted_down={row['n_predicted_down']}"
        )

    print(
        f"\n비교 기준(1차만, threshold={_BASELINE_THRESHOLD}): "
        f"precision={_BASELINE_PRECISION:.3f}  recall={_BASELINE_RECALL:.3f}"
    )


if __name__ == "__main__":
    main()
```

### Step 2: synthetic 데이터로 손으로 동작 확인

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
from datetime import datetime, timezone
import pandas as pd
from tests.test_train_regime_ml import _make_synthetic_market_df
import scripts.train_regime_ml_meta_label as meta_mod
import scripts.train_regime_ml as train_mod

seeds = {'KRW-BTC': 1, 'KRW-ETH': 2, 'KRW-XRP': 3}
train_mod.load_market_training_data = lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market])
meta_mod.TRAINING_MARKETS = list(seeds.keys())
meta_mod.N_FOLDS = 2
meta_mod.MIN_TRAIN_SAMPLES = 50
meta_mod.TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
meta_mod.TRAIN_END = meta_mod.TRAIN_START + pd.Timedelta(hours=24 * 40)
meta_mod._MIN_SPLIT_SAMPLES = 5
meta_mod.main()
"
```

Expected: 에러 없이 완주. 합성 데이터 규모가 작아 표본 부족으로 중간에
"표본이 5개 미만입니다 — 중단합니다" 메시지로 끝나거나, 표본이 충분하면
"=== 1차+메타 게이트 ..." 표까지 출력됨 — **둘 중 어느 쪽이든 에러 없이
끝나면 통과**(합성 데이터라 숫자 자체는 의미 없음, 완주 여부만 확인).

### Step 3: 커밋

```bash
git add scripts/train_regime_ml_meta_label.py
git commit -m "feat: 메타 레이블링(c-3) 학습+측정 스크립트 추가"
```

---

## Task 3: 실데이터 실행 + 백로그 문서 반영

**Files:**
- Modify: `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: Task 2 스크립트의 실제 콘솔 출력(20마켓, 실네트워크 데이터).

### Step 1: `train_regime_ml_meta_label.py` 실데이터 실행

Run(`run_in_background: true`로 직접 실행 — `&`/`disown`으로 감싸지 말 것,
c-2/horizon 세션에서 확인된 함정). 1차 모델 학습 1회(실측 40분~2시간) +
메타모델 학습 1회(후보 집합만 대상이라 더 빠를 것으로 예상)이므로 총 소요는
1차 라운드들보다 짧을 것으로 예상되나, 완료까지 폴링하며 기다릴 것:

`PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml_meta_label.py`

콘솔에 출력된 "1차 OOF 전체 n=", "후보 n=", "메타 학습/테스트 n=", 메타
게이트의 threshold별 precision/recall 표, 그리고 비교 기준 줄을 그대로
기록해둔다.

### Step 2: `docs/regime-ml-backlog.md`에 결과 반영

가장 최근 완료 라운드(horizon 그리드서치) 섹션 바로 위에 새 `##` 최상위
섹션을 추가한다(2026-09-01 최종 리뷰에서 c-2/horizon을 최상위 섹션으로
승격·정리한 관례를 따름 — "다음 세션 작업 후보" 하위에 넣지 않는다).
정확한 숫자는 Step 1 실행 결과로 채운다 — 아래는 형식 예시이며 실제 숫자로
교체할 것:

```markdown
## 메타 레이블링(c-3) 학습+측정 (2026-09-01, horizon 그리드서치 직후) — 완료

설계: `docs/superpowers/specs_v1/2026-09-01-regime-ml-meta-labeling-design.md`.
계획: `docs/superpowers/plans_v1/2026-09-01-regime-ml-meta-labeling.md`. 구현:
`scripts/train_regime_ml.py`(`collect_oof` 확장), `scripts/train_regime_ml_meta_label.py`.

**실측 결과(20마켓 실데이터)**: 1차 OOF 전체 n=<실측값>, 후보(proba_down>=0.5)
n=<실측값>, 메타 학습 n=<실측값>/메타 테스트 n=<실측값>.

| threshold | precision(1차+메타) | recall(1차+메타) |
|---|---|---|
| <실측값> | <실측값> | <실측값> |
| ... | ... | ... |

**비교**: 1차만(threshold=0.70) precision=0.39/recall=0.108 대비, 메타 게이트
추가 시 <같은 recall대에서 precision이 유의미하게 올랐는지 서술 — 오르지
않았다면 "메타 레이블링도 이 문제의 근본 신호 부족을 넘지 못함"으로 결론>.

**결정**: <채택(서빙 연결배선 별도 세션 검토)/미채택 — 판단 기준 서술>.
```

### Step 3: 커밋

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: 메타 레이블링(c-3) 실측 결과 반영"
```

---

## 범위 밖

- 서빙(`backend/regime_ml_service.py`) 연결배선 — 결과가 채택할 만하면 별도 세션.
- 메타모델의 완전한 중첩(nested) 워크포워드 재검증.
- 메타모델 자체의 하이퍼파라미터 튜닝.
- `proba_down>=0.5`가 아닌 다른 후보 집합 정의 실험.
