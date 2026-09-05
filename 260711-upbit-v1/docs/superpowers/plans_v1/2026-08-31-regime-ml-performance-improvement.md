# 장세 판별 ML 성능 개선(①~④) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-08-31-regime-ml-performance-improvement-design.md`의 4개 항목(threshold 튜닝+확률보정, sample uniqueness 가중치+vol_t shift, 베타중립 cross-sectional 피처, 캔들 결측구간 스캔+보정)을 순서대로 구현해 장세 판별 ML의 walk-forward pooled weighted kappa(현재 baseline 0.097)를 개선한다.

**Architecture:** `scripts/train_regime_ml.py`의 기존 walk-forward 파이프라인(마켓별 데이터 로드 → 피처 → fold 루프 → LightGBM 학습 → 지표 리포트)에 각 항목을 순차적으로 얹는다. 순수 계산 로직(threshold/calibration, sample weight, cross-sectional 피처)은 각각 독립 모듈로 분리해 유닛테스트로 검증하고, `train_regime_ml.py`/`regime_ml_service.py`는 그 모듈을 호출하는 얇은 배선(wiring)만 담당한다. 각 구조 변경은 실데이터 재학습으로 pooled weighted kappa를 측정해 개선 시에만 채택(누적 방식)한다.

**Tech Stack:** Python, pandas, scikit-learn(`sklearn.metrics`, `sklearn.isotonic.IsotonicRegression`), LightGBM(`lightgbm.LGBMClassifier`/`lightgbm.Booster`), pytest.

## Global Constraints

- 평가지표: `scripts/train_regime_ml.py`가 리포트하는 **pooled weighted kappa**를 1순위, **macro F1**을 2순위로 판단한다. 현재 baseline: pooled weighted kappa **0.097**, macro F1 **0.538**(20마켓, 이진분류, barrier_k=6.25).
- 실데이터 재학습 커맨드(모든 실행 태스크 공통): `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py` (저장소 루트에서 실행, 수 분 소요될 수 있음 — 네트워크로 20개 마켓 캔들을 불러온다).
- 각 구조 변경은 **자체 git 커밋**으로 만들고, 실데이터 ablation 결과 kappa가 악화되면 그 커밋을 `git revert`로 되돌린 뒤 다음 태스크로 진행한다(재확인 질문 없이 자동 진행 — 사용자 승인 완료된 정책).
- 기존 테스트(`pytest`, 저장소 루트에서 `PYTHONPATH=. python -m pytest tests/ -q`)는 각 태스크 끝에서 항상 전부 통과해야 한다(회귀 없음).
- 한국어 docstring/주석 관례를 그대로 따른다(기존 파일들과 동일 톤 — "왜"를 설명, "무엇"은 설명 안 함).
- 새 코드가 저장 모델 아티팩트(`data/regime_ml_models/*.json` sidecar)의 스키마를 바꿀 때는, 기존 sidecar에 새 키가 없어도(구형 모델) 안전하게 동작하는 폴백을 반드시 포함한다(`backend/regime_ml_service.py`의 기존 `sidecar.get(..., default)` 관례를 따름).

---

## Phase 1 — Threshold 튜닝 + 확률 보정 (①)

### Task 1: `engine/regime_ml_calibration.py` — precision-recall/threshold/isotonic 순수 함수

**Files:**
- Create: `engine/regime_ml_calibration.py`
- Test: `tests/test_regime_ml_calibration.py`

**Interfaces:**
- Produces: `compute_precision_recall_table(y_true: list[str], proba_down: list[float], thresholds: list[float]) -> list[dict]` (각 dict: `{"threshold": float, "precision": float, "recall": float, "n_predicted_down": int}`)
- Produces: `select_threshold_for_target_precision(table: list[dict], target_precision: float) -> float`
- Produces: `fit_isotonic_breakpoints(y_true: list[str], proba_down: list[float]) -> list[list[float]]` (정렬된 `[x, y]` 쌍 리스트)
- Produces: `apply_calibration(breakpoints: list[list[float]], proba: float) -> float`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""
tests/test_regime_ml_calibration.py

engine.regime_ml_calibration의 threshold/확률보정 순수 함수를 검증한다.
"""
from __future__ import annotations

from engine.regime_ml_calibration import (
    apply_calibration,
    compute_precision_recall_table,
    fit_isotonic_breakpoints,
    select_threshold_for_target_precision,
)

_LABELS = ["하락", "하락아님"]


def test_compute_precision_recall_table_matches_hand_computed_values():
    # threshold=0.5: proba>=0.5인 4개 중 실제 "하락"은 3개(precision=0.75),
    # 전체 "하락" 5개 중 3개 잡음(recall=0.6).
    y_true = ["하락", "하락", "하락", "하락", "하락", "하락아님", "하락아님", "하락아님"]
    proba_down = [0.9, 0.8, 0.7, 0.3, 0.2, 0.6, 0.4, 0.1]

    table = compute_precision_recall_table(y_true, proba_down, thresholds=[0.5])

    assert len(table) == 1
    row = table[0]
    assert row["threshold"] == 0.5
    assert row["precision"] == 0.75
    assert row["recall"] == 0.6
    assert row["n_predicted_down"] == 4


def test_compute_precision_recall_table_covers_all_requested_thresholds_in_order():
    y_true = ["하락", "하락아님"]
    proba_down = [0.9, 0.1]

    table = compute_precision_recall_table(y_true, proba_down, thresholds=[0.3, 0.6, 0.9])

    assert [row["threshold"] for row in table] == [0.3, 0.6, 0.9]


def test_compute_precision_recall_table_zero_predicted_down_gives_zero_precision_not_error():
    y_true = ["하락", "하락아님"]
    proba_down = [0.1, 0.05]  # 어떤 threshold에서도 "하락"으로 예측되는 표본 없음

    table = compute_precision_recall_table(y_true, proba_down, thresholds=[0.9])

    assert table[0]["precision"] == 0.0
    assert table[0]["recall"] == 0.0
    assert table[0]["n_predicted_down"] == 0


def test_select_threshold_for_target_precision_picks_lowest_threshold_meeting_target():
    table = [
        {"threshold": 0.3, "precision": 0.40, "recall": 0.90, "n_predicted_down": 10},
        {"threshold": 0.5, "precision": 0.55, "recall": 0.70, "n_predicted_down": 6},
        {"threshold": 0.7, "precision": 0.80, "recall": 0.30, "n_predicted_down": 3},
    ]

    threshold = select_threshold_for_target_precision(table, target_precision=0.55)

    assert threshold == 0.5  # 0.55 이상인 것 중 recall이 가장 높은(=threshold가 가장 낮은) 것


def test_select_threshold_for_target_precision_falls_back_to_highest_precision_when_unreachable():
    table = [
        {"threshold": 0.3, "precision": 0.20, "recall": 0.95, "n_predicted_down": 10},
        {"threshold": 0.5, "precision": 0.35, "recall": 0.50, "n_predicted_down": 6},
    ]

    threshold = select_threshold_for_target_precision(table, target_precision=0.55)

    assert threshold == 0.5  # 목표 미달성 -> precision이 가장 높은 threshold로 폴백


def test_fit_isotonic_breakpoints_returns_monotonic_nondecreasing_y():
    y_true = ["하락아님"] * 20 + ["하락"] * 20
    # proba_down이 커질수록 실제 "하락" 비율도 커지는 단조 증가 패턴(사전 정렬)
    proba_down = [i / 39 for i in range(40)]

    breakpoints = fit_isotonic_breakpoints(y_true, proba_down)

    ys = [pt[1] for pt in breakpoints]
    assert ys == sorted(ys)
    assert all(0.0 <= y <= 1.0 for y in ys)


def test_apply_calibration_interpolates_between_breakpoints():
    breakpoints = [[0.0, 0.1], [0.5, 0.5], [1.0, 0.9]]

    assert apply_calibration(breakpoints, 0.25) == 0.3  # 0.0~0.5 구간 선형보간
    assert apply_calibration(breakpoints, 0.5) == 0.5


def test_apply_calibration_clips_outside_breakpoint_range():
    breakpoints = [[0.2, 0.1], [0.8, 0.9]]

    assert apply_calibration(breakpoints, 0.0) == 0.1  # 왼쪽 밖 -> 첫 y로 clip
    assert apply_calibration(breakpoints, 1.0) == 0.9  # 오른쪽 밖 -> 마지막 y로 clip
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_calibration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.regime_ml_calibration'`

- [ ] **Step 3: 최소 구현 작성**

```python
"""
engine/regime_ml_calibration.py

장세 판별 ML의 "하락" 클래스 예측 확률에 대한 threshold 선택과 확률 보정(isotonic
calibration) 순수 함수. scripts/train_regime_ml.py가 walk-forward pooled
out-of-fold 확률로 이 함수들을 호출해 모델 sidecar에 decision_threshold/
calibration_breakpoints를 저장하고, backend/regime_ml_service.py가 서빙 시
저장된 breakpoints로 apply_calibration()을 재현한다(sklearn 의존 없이 np.interp
기반이라 서빙 경로가 가볍다). 설계 문서:
docs/superpowers/specs_v1/2026-08-31-regime-ml-performance-improvement-design.md
"""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression

_DOWN_LABEL = "하락"


def compute_precision_recall_table(
    y_true: list[str], proba_down: list[float], thresholds: list[float]
) -> list[dict]:
    """threshold별로 "proba_down >= threshold면 하락으로 예측"했을 때의
    precision/recall/예측된 하락 표본 수를 계산한다. 어떤 threshold에서도 "하락"으로
    예측된 표본이 없으면(0으로 나눗셈) precision을 0.0으로 취급한다(정의 불가가
    아니라 "경고를 전혀 안 냄"이므로 0이 자연스러운 값)."""
    y_true_arr = np.array(y_true)
    proba_arr = np.array(proba_down)
    n_actual_down = int((y_true_arr == _DOWN_LABEL).sum())

    table: list[dict] = []
    for threshold in thresholds:
        predicted_down = proba_arr >= threshold
        n_predicted_down = int(predicted_down.sum())
        if n_predicted_down == 0:
            precision = 0.0
        else:
            true_positive = int(((y_true_arr == _DOWN_LABEL) & predicted_down).sum())
            precision = true_positive / n_predicted_down
        if n_actual_down == 0:
            recall = 0.0
        else:
            true_positive = int(((y_true_arr == _DOWN_LABEL) & predicted_down).sum())
            recall = true_positive / n_actual_down
        table.append({
            "threshold": threshold,
            "precision": precision,
            "recall": recall,
            "n_predicted_down": n_predicted_down,
        })
    return table


def select_threshold_for_target_precision(table: list[dict], target_precision: float) -> float:
    """precision >= target_precision인 행 중 threshold가 가장 낮은(=recall이 가장
    높은) 것을 고른다. 목표를 만족하는 threshold가 없으면 precision이 가장 높은
    threshold로 폴백한다(그마저도 여러 개면 threshold가 가장 낮은 쪽)."""
    meeting = [row for row in table if row["precision"] >= target_precision]
    if meeting:
        return min(meeting, key=lambda row: row["threshold"])["threshold"]
    best = max(table, key=lambda row: (row["precision"], -row["threshold"]))
    return best["threshold"]


def fit_isotonic_breakpoints(y_true: list[str], proba_down: list[float]) -> list[list[float]]:
    """pooled out-of-fold (proba_down, 실제 "하락" 여부) 쌍에 IsotonicRegression을
    적합해 정렬된 [x, y] 브레이크포인트 리스트를 반환한다. sklearn의
    IsotonicRegression 객체를 그대로 저장하지 않는 이유: 서빙 경로
    (backend/regime_ml_service.py)가 sklearn 없이 np.interp만으로 재현 가능하게
    하기 위함(모델 파일과 같은 텍스트 기반 sidecar json에 담기도 더 쉬움)."""
    y_binary = [1.0 if label == _DOWN_LABEL else 0.0 for label in y_true]
    regressor = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    regressor.fit(proba_down, y_binary)
    xs = regressor.X_thresholds_
    ys = regressor.y_thresholds_
    return [[float(x), float(y)] for x, y in zip(xs, ys)]


def apply_calibration(breakpoints: list[list[float]], proba: float) -> float:
    """breakpoints 기준 선형보간으로 보정된 확률을 계산한다. breakpoints가 비어있으면
    (구형 모델 등) 원래 확률을 그대로 반환한다(항등 보정)."""
    if not breakpoints:
        return proba
    xs = [pt[0] for pt in breakpoints]
    ys = [pt[1] for pt in breakpoints]
    return float(np.interp(proba, xs, ys))
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_calibration.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_calibration.py tests/test_regime_ml_calibration.py
git commit -m "feat: 장세 판별 ML threshold/확률보정 순수 함수 추가"
```

---

### Task 2: `scripts/train_regime_ml.py`에 threshold/calibration 계산 배선 + 실데이터 반영

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Test: `tests/test_train_regime_ml.py`

**Interfaces:**
- Consumes: `engine.regime_ml_calibration.compute_precision_recall_table/select_threshold_for_target_precision/fit_isotonic_breakpoints`(Task 1)
- Produces: sidecar json에 신규 키 `"decision_threshold": float`, `"calibration_breakpoints": list[list[float]]`, `"threshold_table": list[dict]` 추가(기존 키는 그대로 유지)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`에 아래 테스트를 추가한다(파일 하단, 기존 임포트에 `TARGET_DOWN_PRECISION` 추가 필요 없음 — 테스트는 값 자체를 하드코딩하지 않고 구조만 확인):

```python
def test_run_training_saves_calibration_fields_in_sidecar(tmp_path, monkeypatch):
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

    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert isinstance(sidecar["decision_threshold"], float)
    assert 0.0 <= sidecar["decision_threshold"] <= 1.0
    assert isinstance(sidecar["calibration_breakpoints"], list)
    for point in sidecar["calibration_breakpoints"]:
        assert len(point) == 2
        assert 0.0 <= point[1] <= 1.0
    assert isinstance(sidecar["threshold_table"], list)
    assert len(sidecar["threshold_table"]) > 0
    for row in sidecar["threshold_table"]:
        assert set(row.keys()) == {"threshold", "precision", "recall", "n_predicted_down"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_train_regime_ml.py::test_run_training_saves_calibration_fields_in_sidecar -v`
Expected: FAIL with `KeyError: 'decision_threshold'`

- [ ] **Step 3: `scripts/train_regime_ml.py` 수정**

`from engine.regime_ml_metrics import compute_classification_metrics` 아래에 임포트 추가:

```python
from engine.regime_ml_calibration import (
    compute_precision_recall_table,
    fit_isotonic_breakpoints,
    select_threshold_for_target_precision,
)
```

`BARRIER_K = 6.25` 아래에 상수 추가:

```python
# docs/ML_Regime_Switching_Additional_Improvements.md 1-2절 예시값("예 55%+")을
# 그대로 채택 — "하락" 경고의 신뢰도를 55% 이상으로 끌어올리는 게 목표.
TARGET_DOWN_PRECISION = 0.55
_THRESHOLD_GRID = [round(0.30 + 0.05 * i, 2) for i in range(13)]  # 0.30~0.90
```

`run_training` 내부, fold 루프 안의 `predictions = model.predict(test_X_fit)` 아래에 추가:

```python
        proba_matrix = model.predict_proba(test_X_fit)
        down_col = list(model.classes_).index("하락")
        proba_down = proba_matrix[:, down_col].tolist()
```

같은 fold 루프에서 `all_pred.extend(predictions)` 아래에 추가:

```python
        all_proba_down.extend(proba_down)
```

fold 루프 진입 전(`all_true: list[str] = []` 근처)에 리스트 초기화 추가:

```python
    all_proba_down: list[float] = []
```

`pooled_metrics = compute_classification_metrics(all_true, all_pred)` 아래, `_print_aggregate_summary(...)` 호출 이전에 추가:

```python
    threshold_table = compute_precision_recall_table(all_true, all_proba_down, _THRESHOLD_GRID)
    decision_threshold = select_threshold_for_target_precision(threshold_table, TARGET_DOWN_PRECISION)
    calibration_breakpoints = fit_isotonic_breakpoints(all_true, all_proba_down)
    _print_threshold_table(threshold_table, decision_threshold)
```

`_print_aggregate_summary` 함수 정의 위에 새 출력 헬퍼 추가:

```python
def _print_threshold_table(table: list[dict], decision_threshold: float) -> None:
    print(f"\n=== Threshold별 '하락' precision/recall (목표 precision={TARGET_DOWN_PRECISION}) ===")
    for row in table:
        marker = " <- 채택" if row["threshold"] == decision_threshold else ""
        print(
            f"  threshold={row['threshold']:.2f}  precision={row['precision']:.3f}  "
            f"recall={row['recall']:.3f}  n_predicted_down={row['n_predicted_down']}{marker}"
        )
```

`sidecar = {...}` 딕셔너리 리터럴(`"performance": {...}` 다음)에 키 추가:

```python
        sidecar = {
            "markets": markets,
            "labeling_method": "triple_barrier",
            "barrier_k": barrier_k,
            "classes": last_class_order,
            "fold_index": last_fold_index,
            "decision_threshold": decision_threshold,
            "calibration_breakpoints": calibration_breakpoints,
            "threshold_table": threshold_table,
            "performance": {
                ...  # 기존 그대로
            },
        }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_train_regime_ml.py -v`
Expected: PASS — 기존 테스트(`test_run_training_saves_json_sidecar_alongside_model` 등)는 `assert set(sidecar.keys()) == {...}`를 정확히 검사하므로, 새 키 3개를 그 집합에도 추가해야 한다. `tests/test_train_regime_ml.py`의 `test_run_training_saves_json_sidecar_alongside_model`에서:

```python
    assert set(sidecar.keys()) == {
        "markets", "labeling_method", "barrier_k", "classes", "fold_index", "performance",
    }
```

를

```python
    assert set(sidecar.keys()) == {
        "markets", "labeling_method", "barrier_k", "classes", "fold_index", "performance",
        "decision_threshold", "calibration_breakpoints", "threshold_table",
    }
```

로 수정한다.

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: 학습 파이프라인에 threshold/확률보정 계산 배선"
```

- [ ] **Step 7: 실데이터 학습 실행(정보 확인용 — 이 태스크는 항상 채택, kappa에 영향 없음)**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

결과에서 "=== Threshold별 '하락' precision/recall ===" 블록을 확인해 채택된 threshold와 그때의 recall을 기록한다(다음 리뷰에서 보고). recall이 0에 가까우면(예: <0.05) `TARGET_DOWN_PRECISION`을 0.55에서 0.50으로 낮추는 걸 고려할 수 있다는 점만 리뷰 코멘트에 남기고, 이 태스크 자체는 코드를 되돌리지 않는다(threshold/calibration은 서빙 정책이지 kappa를 바꾸는 구조 변경이 아니므로 revert 대상이 아님).

---

### Task 3: `backend/regime_ml_service.py`에 threshold/calibration 적용

**Files:**
- Modify: `backend/regime_ml_service.py`
- Test: `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: `engine.regime_ml_calibration.apply_calibration`(Task 1), sidecar의 `decision_threshold`/`calibration_breakpoints`(Task 2)
- Produces: `predict_current_ml_regime()`의 반환값 `predicted_category`가 보정된 확률+커스텀 threshold 기준으로 결정됨, `probs`도 보정된 값으로 교체

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_service.py`의 `_train_and_save_tiny_model`은 sidecar에 `decision_threshold`/`calibration_breakpoints`가 없는 상태(구형)를 만든다 — 이건 기존 테스트들이 "레거시 폴백"을 검증하는 형태로 이미 커버되므로 그대로 둔다(요구사항: 이 두 키가 없으면 기존과 동일하게 동작 — threshold 0.5, 항등 보정). 신규 테스트를 추가한다:

```python
def test_predict_current_ml_regime_applies_custom_threshold_and_calibration(tmp_path, monkeypatch):
    """sidecar에 decision_threshold=0.3(0.5보다 낮음)과, 원래 확률을 항상 0.9로
    끌어올리는 calibration_breakpoints가 있으면, 원래라면 "하락아님"으로
    argmax됐을 raw_prediction도 보정+낮은 threshold를 거쳐 "하락"으로 뒤집혀야
    한다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    txt_path, json_path, model = _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)
    sidecar = json.loads(json_path.read_text(encoding="utf-8"))
    sidecar["decision_threshold"] = 0.3
    # 모든 입력 확률을 0.9로 보정하는 항등에 가까운(사실상 상수) 브레이크포인트
    sidecar["calibration_breakpoints"] = [[0.0, 0.9], [1.0, 0.9]]
    json_path.write_text(json.dumps(sidecar), encoding="utf-8")

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    assert result["predicted_category"] == "하락"
    assert result["probs"]["하락"] == pytest.approx(0.9, abs=1e-6)
    assert result["probs"]["하락아님"] == pytest.approx(0.1, abs=1e-6)


def test_predict_current_ml_regime_legacy_sidecar_keeps_argmax_behavior(tmp_path, monkeypatch):
    """decision_threshold/calibration_breakpoints 키가 아예 없는 구형 sidecar는
    기존과 동일하게 항등 보정 + threshold 0.5(=argmax와 동치)로 동작해야 한다."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    # 보정 전 raw 확률 그대로 argmax한 결과와 같아야 한다.
    assert result["predicted_category"] == max(result["probs"], key=result["probs"].get)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_service.py::test_predict_current_ml_regime_applies_custom_threshold_and_calibration -v`
Expected: FAIL — `predicted_category`가 여전히 "하락아님"(현재 코드는 argmax만 사용, threshold/calibration 미적용)

- [ ] **Step 3: `backend/regime_ml_service.py` 수정**

임포트에 추가:

```python
from engine.regime_ml_calibration import apply_calibration
```

`predict_current_ml_regime` 내부, 기존:

```python
    raw_prediction = booster.predict(last_row, validate_features=True)[0]
    classes: list[str] = sidecar["classes"]
    if len(classes) == 2:
        positive_prob = float(raw_prediction)
        probs = {classes[0]: 1.0 - positive_prob, classes[1]: positive_prob}
    else:
        probs = {label: float(p) for label, p in zip(classes, raw_prediction)}
    predicted_category = max(probs, key=probs.get)
```

를 아래로 교체:

```python
    raw_prediction = booster.predict(last_row, validate_features=True)[0]
    classes: list[str] = sidecar["classes"]
    if len(classes) == 2:
        positive_prob = float(raw_prediction)
        raw_probs = {classes[0]: 1.0 - positive_prob, classes[1]: positive_prob}
    else:
        raw_probs = {label: float(p) for label, p in zip(classes, raw_prediction)}

    # sidecar에 decision_threshold/calibration_breakpoints가 없으면(구형 모델)
    # 항등 보정 + threshold 0.5로 폴백한다 — 이는 원래 argmax 방식과 동치다.
    breakpoints = sidecar.get("calibration_breakpoints", [])
    calibrated_down = apply_calibration(breakpoints, raw_probs["하락"])
    probs = {"하락": calibrated_down, "하락아님": 1.0 - calibrated_down}
    decision_threshold = sidecar.get("decision_threshold", 0.5)
    predicted_category = "하락" if calibrated_down >= decision_threshold else "하락아님"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_service.py -v`
Expected: PASS 전체(신규 2개 포함). `test_predict_current_ml_regime_matches_sklearn_wrapper_for_same_row`도 여전히 통과해야 한다 — 그 테스트의 sidecar는 `_train_and_save_tiny_model`이 만든 구형(키 없음) 사이드카라 항등 보정이 적용되고, 기존 검증(원시 확률 비교)과 값이 그대로 같다.

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/regime_ml_service.py tests/test_regime_ml_service.py
git commit -m "feat: 서빙 시 보정된 확률 + 커스텀 threshold로 하락 판정"
```

---

## Phase 2 — 구조 개선: vol_t shift + sample uniqueness 가중치 (②)

### Task 4: vol_t가 자기 자신 수익률을 안 보게 shift

**Files:**
- Modify: `engine/regime_ml_labels.py`
- Test: `tests/test_regime_ml_labels.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_labels.py`에 추가:

```python
def test_compute_triple_barrier_labels_vol_t_excludes_current_bar_return():
    """급락이 일어난 바로 그 봉(t=crash_index) 자신의 수익률이 그 봉의 barrier 폭
    계산에 포함되면(이전 구현), 급락 자체가 vol_t를 급등시켜 barrier가 넓어지고,
    급락 직후의 완만한 추가 하락은 그 넓어진 barrier를 못 건드려 "하락아님"으로
    잘못 라벨링될 수 있다(docs/regime-ml-backlog.md 기술부채 항목, KRW-SHIB 실측
    사례 재현). vol_t를 t-1까지만 쓰도록 shift하면, 급락 봉의 barrier는 급락 이전의
    평온한 vol 기준으로 좁게 잡히므로 같은 완만한 추가 하락도 "하락"으로 잡혀야
    한다. 추가 하락폭은 "직전 평온 구간의 vol"보다 큰 값으로 직접 계산해서 정하므로
    (하드코딩된 %가 아님) 어떤 halflife/k 조합에서도 재현 가능하다."""
    crash_index = 59  # _WARMUP(인덱스 0~49) 이후 stable 구간의 10번째 봉
    crashed_close = _BASE * 0.5  # 그 봉 자체가 -50% 급락

    # shift(1) 적용 시 t=crash_index의 barrier에 쓰이는 vol은 급락 이전(워밍업
    # 오실레이션) 수준으로 안정돼 있다 — 그 크기를 먼저 직접 계산해, 이후의
    # 완만한 추가 하락폭을 "좁은 barrier는 반드시 넘지만, 급락을 포함해 부풀려진
    # barrier는 절대 못 넘을" 크기(안전 마진 확보를 위해 3배 차이)로 잡는다.
    warmup_returns = pd.Series(_WARMUP).pct_change(fill_method=None)
    pre_crash_vol = warmup_returns.ewm(halflife=_HALF_LIFE_BARS).std().iloc[-1]
    assert pre_crash_vol > 0
    narrow_barrier = _K * pre_crash_vol
    further_decline_pct = narrow_barrier * 1.5  # 좁은 barrier는 확실히 넘는 크기

    future_after_crash = [
        crashed_close * (1 - further_decline_pct * (i / 9)) for i in range(1, 10)
    ]  # 급락 이후 9봉에 걸쳐 완만하게 further_decline_pct까지 추가 하락(더 이상 급락 아님)
    closes = (
        _WARMUP + [_BASE] * 9 + [crashed_close] + future_after_crash + [future_after_crash[-1]] * 5
    )

    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[crash_index] == "하락"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_labels.py::test_compute_triple_barrier_labels_vol_t_excludes_current_bar_return -v`
Expected: FAIL — `assert labels.iloc[59] == "하락"`에서 실제 값은 "하락아님"(현재 구현은 급락 봉 자신의 -50% 수익률이 vol_t에 포함돼 barrier가 크게 넓어지고, further_decline_pct 크기의 완만한 추가 하락으로는 그 넓은 barrier를 못 건드림).

- [ ] **Step 3: `engine/regime_ml_labels.py` 수정**

```python
    returns = df["close"].pct_change(fill_method=None)
    volatility = returns.ewm(halflife=half_life_bars).std().shift(1)
```

(기존 `volatility = returns.ewm(halflife=half_life_bars).std()`에서 `.shift(1)` 추가)

함수 docstring에 아래 문단 추가(기존 docstring 끝, `df와 같은 길이/인덱스.` 다음 줄):

```
    vol_t는 t-1까지의 수익률만으로 계산한다(.shift(1)) — t 시점 자신의 수익률까지
    포함하면 급락이 일어난 바로 그 봉에서 vol이 급등해 barrier가 넓어지고, 그 결과
    "이미 크게 빠진 봉"이 역설적으로 "하락아님"으로 라벨링되는 문제가 있었다
    (docs/regime-ml-backlog.md 기술부채 항목, 2026-08-31 KRW-SHIB 실측으로 확인).
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_labels.py -v`
Expected: 전부 PASS. 기존 5개 테스트(`test_compute_triple_barrier_labels_assigns_*` 등)도 여전히 통과해야 한다 — `_WARMUP`이 50봉이라 index 49 시점에는 shift(1) 적용 후에도(=index 48까지의 데이터로) EWM 변동성이 이미 충분히 안정화돼 있어(0이 아님) 기존 단언이 깨지지 않을 것으로 예상된다. 만약 실패하면 `_WARMUP` 길이를 51로 늘려 재확인한다(워밍업 여유 한 봉 추가).

이 태스크는 `compute_triple_barrier_labels`의 시그니처를 바꾸지 않는다(내부 `volatility` 계산 한 줄만 수정) — Task 11에서 이 함수에 `candle_time` 파라미터를 추가할 때도 이 태스크의 `.shift(1)` 변경이 이미 반영된 상태를 그대로 이어받는다(단, Task 4의 ablation이 아래 Step 7에서 폐기되면 Task 11 착수 시점에는 `.shift(1)` 없이 진행해야 한다 — Task 11은 "현재 코드"를 기준으로 진행하므로 자동으로 맞다).

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS(`test_train_regime_ml.py`, `test_regime_fact_service.py` 등 `compute_triple_barrier_labels`를 호출하는 다른 테스트들도 포함 — 라벨 함수의 반환값이 바뀌었어도 그 테스트들은 특정 라벨 문자열을 하드코딩 검증하지 않고 구조/타입만 확인하므로 영향 없을 것으로 예상. 실패하면 개별 확인 후 수정)

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_labels.py tests/test_regime_ml_labels.py
git commit -m "fix: Triple Barrier vol_t가 자기 자신 수익률을 안 보게 shift"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

출력의 "=== 전체 fold 풀링 ===" 블록에서 weighted kappa를 확인한다.

- **kappa >= 0.097(현재 baseline)이면 채택** — 그대로 다음 태스크로 진행. 이 실행에서 얻은 kappa를 다음 태스크(Task 6)의 새 baseline으로 기록해둔다.
- **kappa < 0.097이면 폐기** — `git revert HEAD~1`(Task 4의 커밋만 되돌림, Task 1~3 커밋은 유지)을 실행하고, 되돌린 뒤 `PYTHONPATH=. python -m pytest tests/ -q`로 전체 테스트가 다시 통과하는지 확인한 뒤 다음 태스크로 진행. baseline은 0.097 그대로 유지.

---

### Task 5: sample uniqueness 가중치 계산 함수

**Files:**
- Modify: `engine/regime_ml_labels.py`
- Test: `tests/test_regime_ml_labels.py`

**Interfaces:**
- Produces: `compute_sample_uniqueness_weights(labels: pd.Series, n_bars: int) -> pd.Series` (labels와 같은 인덱스/길이, 라벨이 NaN인 위치는 NaN, 유효한 위치는 양수 가중치)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_labels.py`에 추가:

```python
def test_compute_sample_uniqueness_weights_isolated_label_gets_weight_one():
    """다른 라벨과 활성구간이 전혀 안 겹치는 라벨은 c_t가 항상 1이라
    uniqueness weight도 정확히 1.0이어야 한다."""
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    # 라벨 2개, n_bars=2라 활성구간은 [0,2]와 [10,12] — 전혀 안 겹침.
    labels = pd.Series([float("nan")] * 13)
    labels.iloc[0] = "하락"
    labels.iloc[10] = "하락아님"

    weights = compute_sample_uniqueness_weights(labels, n_bars=2)

    assert weights.iloc[0] == pytest.approx(1.0)
    assert weights.iloc[10] == pytest.approx(1.0)


def test_compute_sample_uniqueness_weights_fully_overlapping_labels_get_weight_half():
    """두 라벨이 활성구간을 완전히 공유하면(t=0, t=1, n_bars=1 -> 둘 다 [t,t+1]이
    [0,1]과 [1,2]로 한 시점(t=1)을 공유) 그 겹치는 시점에서는 c_t=2가 되어
    각 라벨의 평균 uniqueness가 1보다 작아져야 한다."""
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    labels = pd.Series(["하락", "하락아님", float("nan")])  # n_bars=1

    weights = compute_sample_uniqueness_weights(labels, n_bars=1)

    # 라벨0 활성구간=[0,1], 라벨1 활성구간=[1,2] -> t=1에서 c_t=2, 나머지는 c_t=1.
    # 라벨0 weight = mean(1/c_0, 1/c_1) = mean(1/1, 1/2) = 0.75
    assert weights.iloc[0] == pytest.approx(0.75)


def test_compute_sample_uniqueness_weights_nan_labels_stay_nan_and_are_excluded_from_concurrency():
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    labels = pd.Series(["하락", float("nan"), "하락아님"])

    weights = compute_sample_uniqueness_weights(labels, n_bars=1)

    assert pd.isna(weights.iloc[1])
    assert weights.iloc[0] == pytest.approx(1.0)  # 이웃이 NaN이라 안 겹침


def test_compute_sample_uniqueness_weights_preserves_length_and_index():
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    labels = pd.Series(["하락"] * 5, index=range(100, 105))
    weights = compute_sample_uniqueness_weights(labels, n_bars=2)

    assert len(weights) == len(labels)
    assert list(weights.index) == list(labels.index)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_labels.py -k uniqueness -v`
Expected: FAIL with `ImportError: cannot import name 'compute_sample_uniqueness_weights'`

- [ ] **Step 3: `engine/regime_ml_labels.py`에 함수 추가**

파일 끝에 추가:

```python
def compute_sample_uniqueness_weights(labels: pd.Series, n_bars: int) -> pd.Series:
    """AFML(López de Prado)의 sample uniqueness 가중치. 라벨 i의 활성구간은
    [i, i+n_bars](Triple Barrier가 최대 n_bars 앞을 내다보므로)다. 각 시점 t에서
    동시에 활성인 라벨 개수 c_t를 구한 뒤, 라벨 i의 가중치 = i의 활성구간에 속한
    모든 t에 대한 1/c_t의 평균이다 — 겹치는 라벨이 많을수록(=서로 독립적이지
    않을수록) 가중치가 작아져 LightGBM이 그 구간을 과도하게 반복학습하지 않게
    한다. class_weight="balanced"와는 별개 축이라 sample_weight로 곱해서 함께
    쓴다(scripts/train_regime_ml.py 참고). NaN 라벨은 애초에 학습에 안 쓰이므로
    동시활성 카운트에도 안 넣고, 반환값도 NaN으로 남긴다."""
    active = labels.notna().astype(float)
    # c_t = t를 활성구간에 포함하는 라벨 개수 = sum(active[i] for i in [t-n_bars, t])
    # (라벨 i의 구간이 [i, i+n_bars]이므로 t를 포함하려면 t-n_bars <= i <= t).
    concurrency = active.rolling(window=n_bars + 1, min_periods=1).sum()
    inverse_concurrency = 1.0 / concurrency
    # 라벨 i의 가중치 = t in [i, i+n_bars] 구간에 대한 1/c_t의 평균(전방 롤링) ->
    # 역순으로 뒤집어 trailing rolling mean을 적용한 뒤 다시 뒤집는 표준 트릭.
    forward_mean = inverse_concurrency[::-1].rolling(window=n_bars + 1, min_periods=1).mean()[::-1]
    return forward_mean.where(labels.notna())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_labels.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_labels.py tests/test_regime_ml_labels.py
git commit -m "feat: sample uniqueness 가중치 계산 함수 추가"
```

---

### Task 6: sample uniqueness 가중치를 학습 파이프라인에 배선 + 실데이터 ablation

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Test: `tests/test_train_regime_ml.py`

**Interfaces:**
- Consumes: `engine.regime_ml_labels.compute_sample_uniqueness_weights`(Task 5)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`에 추가:

```python
def test_run_training_passes_sample_weight_to_fit(tmp_path, monkeypatch):
    """model.fit()이 sample_weight 인자를 받는지, 그리고 그 길이가 train 표본
    수와 같은지 확인한다 — LGBMClassifier.fit을 monkeypatch해 실제 호출 인자를
    가로챈다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    captured_calls = []
    original_fit = train_regime_ml.lgb.LGBMClassifier.fit

    def _capturing_fit(self, X, y, sample_weight=None, **kwargs):
        captured_calls.append({"n_X": len(X), "n_y": len(y), "sample_weight": sample_weight})
        return original_fit(self, X, y, sample_weight=sample_weight, **kwargs)

    monkeypatch.setattr(train_regime_ml.lgb.LGBMClassifier, "fit", _capturing_fit)

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
    assert len(captured_calls) == len(reports)
    for call in captured_calls:
        assert call["sample_weight"] is not None
        assert len(call["sample_weight"]) == call["n_X"]
        assert all(w > 0 for w in call["sample_weight"])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_train_regime_ml.py::test_run_training_passes_sample_weight_to_fit -v`
Expected: FAIL — `call["sample_weight"] is not None` 단언 실패(현재 코드는 `sample_weight`를 안 넘김)

- [ ] **Step 3: `scripts/train_regime_ml.py` 수정**

임포트 추가:

```python
from engine.regime_ml_labels import (
    CATEGORY_LABELS,
    compute_sample_uniqueness_weights,
    compute_triple_barrier_labels,
)
```

(기존 `from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels` 줄을 교체)

market별 데이터 준비 루프(`for market in markets:` 블록)를 수정해 가중치도 함께 계산·저장:

```python
    market_frames: dict[str, tuple[pd.Series, pd.DataFrame, pd.Series, pd.Series]] = {}
    for market in markets:
        raw_df = load_market_training_data(market, timeframe, start, end)
        features_df = build_feature_matrix(raw_df, market, half_life_bars)
        labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, barrier_k)
        weights = compute_sample_uniqueness_weights(labels, n_bars)
        market_frames[market] = (raw_df["candle_time"], features_df, labels, weights)
```

fold 루프의 언패킹과 train 파트 수집을 수정:

```python
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

`model.fit(train_X_fit, train_y)` 호출을 수정:

```python
        model.fit(train_X_fit, train_y, sample_weight=train_w.to_numpy())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_train_regime_ml.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: sample uniqueness 가중치를 학습에 반영"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

Task 4에서 기록한 baseline(vol_t shift 채택 여부에 따라 0.097 또는 그 이상)과 이번 pooled weighted kappa를 비교한다.

- **개선되면 채택** — 다음 Phase(3)로 진행. 이 kappa를 다음 baseline으로 기록.
- **악화되면 폐기** — `git revert HEAD~1`(Task 6 커밋만 되돌림)을 실행하고 테스트 재확인 후 다음 Phase로 진행. baseline은 Task 4 종료 시점 값 유지.

---

## Phase 3 — 베타중립 cross-sectional 피처 (③)

### Task 7: cross-sectional 피처 계산 함수

**Files:**
- Create: `engine/regime_ml_cross_sectional.py`
- Test: `tests/test_regime_ml_cross_sectional.py`

**Interfaces:**
- Produces: `compute_cross_sectional_features(market_returns: dict[str, pd.Series], btc_market: str) -> dict[str, pd.DataFrame]` — 각 Series는 `candle_time`을 인덱스로 하는 수익률(pct_change). 반환값은 market -> `DataFrame(columns=["BETA_NEUTRAL_RETURN", "CROSS_SECTIONAL_RANK"], index=candle_time)`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""
tests/test_regime_ml_cross_sectional.py

engine.regime_ml_cross_sectional.compute_cross_sectional_features()를 검증한다.
"""
from __future__ import annotations

import pandas as pd

from engine.regime_ml_cross_sectional import compute_cross_sectional_features


def test_compute_cross_sectional_features_beta_neutral_subtracts_btc_return():
    index = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    market_returns = {
        "KRW-BTC": pd.Series([0.01, 0.02, -0.01], index=index),
        "KRW-ETH": pd.Series([0.03, 0.02, -0.05], index=index),
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    eth_beta_neutral = result["KRW-ETH"]["BETA_NEUTRAL_RETURN"]
    assert eth_beta_neutral.iloc[0] == pytest.approx(0.03 - 0.01)
    assert eth_beta_neutral.iloc[1] == pytest.approx(0.02 - 0.02)
    assert eth_beta_neutral.iloc[2] == pytest.approx(-0.05 - (-0.01))

    btc_beta_neutral = result["KRW-BTC"]["BETA_NEUTRAL_RETURN"]
    assert (btc_beta_neutral == 0.0).all()  # BTC 자신은 항상 0


def test_compute_cross_sectional_features_rank_is_percentile_across_markets():
    index = pd.date_range("2024-01-01", periods=1, freq="h", tz="UTC")
    market_returns = {
        "KRW-BTC": pd.Series([0.01], index=index),
        "KRW-ETH": pd.Series([0.05], index=index),  # 3개 중 1등(최고 수익률)
        "KRW-XRP": pd.Series([-0.02], index=index),  # 3개 중 3등(최저)
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    assert result["KRW-ETH"]["CROSS_SECTIONAL_RANK"].iloc[0] == pytest.approx(1.0)
    assert result["KRW-XRP"]["CROSS_SECTIONAL_RANK"].iloc[0] == pytest.approx(1 / 3)
    assert result["KRW-BTC"]["CROSS_SECTIONAL_RANK"].iloc[0] == pytest.approx(2 / 3)


def test_compute_cross_sectional_features_handles_misaligned_timestamps_with_nan():
    """마켓마다 candle_time이 완전히 같지 않을 수 있다(결측 캔들 등) — outer join
    후 없는 시점은 NaN으로 남아야 하고, 에러가 나면 안 된다."""
    index_a = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    index_b = pd.date_range("2024-01-01 01:00", periods=3, freq="h", tz="UTC")  # 1시간 밀림
    market_returns = {
        "KRW-BTC": pd.Series([0.01, 0.02, -0.01], index=index_a),
        "KRW-ETH": pd.Series([0.03, 0.02, -0.05], index=index_b),
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    assert pd.isna(result["KRW-ETH"]["BETA_NEUTRAL_RETURN"].loc[index_a[0]])
    assert pd.isna(result["KRW-BTC"]["BETA_NEUTRAL_RETURN"].loc[index_b[-1]])


def test_compute_cross_sectional_features_returns_one_frame_per_input_market():
    index = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    market_returns = {
        "KRW-BTC": pd.Series([0.01, 0.02], index=index),
        "KRW-ETH": pd.Series([0.03, 0.02], index=index),
        "KRW-XRP": pd.Series([-0.01, 0.00], index=index),
    }

    result = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    assert set(result.keys()) == set(market_returns.keys())
    for df in result.values():
        assert list(df.columns) == ["BETA_NEUTRAL_RETURN", "CROSS_SECTIONAL_RANK"]
        assert len(df) == 2
```

파일 상단에 `import pytest` 추가.

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_cross_sectional.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 구현**

```python
"""
engine/regime_ml_cross_sectional.py

장세 판별 ML의 베타중립(cross-sectional) 피처. 코인별 자기상대적 피처
(VOLATILITY_PERCENTILE 등, engine/regime_ml_features.py)와 달리, 이 피처들은
"같은 시각 다른 마켓들과 비교해 지금 이 코인이 어떤지"를 표현한다 — 알트코인
대부분이 BTC와 강하게 동조하므로, 종목 고유 신호만 걸러내려는 목적. 설계 문서:
docs/superpowers/specs_v1/2026-08-31-regime-ml-performance-improvement-design.md
"""
from __future__ import annotations

import pandas as pd


def compute_cross_sectional_features(
    market_returns: dict[str, pd.Series], btc_market: str
) -> dict[str, pd.DataFrame]:
    """market_returns: market -> candle_time을 인덱스로 하는 수익률(pct_change)
    Series. 마켓마다 인덱스가 완전히 같지 않아도 된다(outer join, 없는 시점은
    NaN). 반환: market -> DataFrame(columns=[BETA_NEUTRAL_RETURN,
    CROSS_SECTIONAL_RANK], index=전체 마켓 candle_time 합집합)."""
    wide = pd.DataFrame(market_returns)  # outer join, 컬럼=market
    btc_return = wide[btc_market]
    beta_neutral = wide.sub(btc_return, axis=0)
    rank_pct = wide.rank(axis=1, pct=True)

    result: dict[str, pd.DataFrame] = {}
    for market in market_returns:
        result[market] = pd.DataFrame({
            "BETA_NEUTRAL_RETURN": beta_neutral[market],
            "CROSS_SECTIONAL_RANK": rank_pct[market],
        })
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_cross_sectional.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_ml_cross_sectional.py tests/test_regime_ml_cross_sectional.py
git commit -m "feat: 베타중립 cross-sectional 피처 계산 함수 추가"
```

---

### Task 8: cross-sectional 피처를 학습 파이프라인에 배선 + 실데이터 ablation

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Test: `tests/test_train_regime_ml.py`

**Interfaces:**
- Consumes: `engine.regime_ml_cross_sectional.compute_cross_sectional_features`(Task 7)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`에 추가:

```python
def test_run_training_features_include_cross_sectional_columns(tmp_path, monkeypatch):
    """model.fit()에 실제로 넘어가는 학습 피처에 BETA_NEUTRAL_RETURN/
    CROSS_SECTIONAL_RANK가 포함되는지 확인한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    captured_columns = []
    original_fit = train_regime_ml.lgb.LGBMClassifier.fit

    def _capturing_fit(self, X, y, **kwargs):
        captured_columns.append(list(X.columns))
        return original_fit(self, X, y, **kwargs)

    monkeypatch.setattr(train_regime_ml.lgb.LGBMClassifier, "fit", _capturing_fit)

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
    for columns in captured_columns:
        assert "BETA_NEUTRAL_RETURN" in columns
        assert "CROSS_SECTIONAL_RANK" in columns
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_train_regime_ml.py::test_run_training_features_include_cross_sectional_columns -v`
Expected: FAIL — `AssertionError: assert 'BETA_NEUTRAL_RETURN' in [...]`

- [ ] **Step 3: `scripts/train_regime_ml.py` 수정**

임포트 추가:

```python
from engine.regime_ml_cross_sectional import compute_cross_sectional_features
```

market별 데이터 준비 루프 전체를 교체(BTC 마켓을 먼저 로드해 cross-sectional 계산에 쓸 수 있게, 두 단계로 나눔):

```python
    raw_frames: dict[str, pd.DataFrame] = {
        market: load_market_training_data(market, timeframe, start, end) for market in markets
    }
    market_returns = {
        market: raw_df.set_index("candle_time")["close"].pct_change(fill_method=None)
        for market, raw_df in raw_frames.items()
    }
    cross_sectional = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")

    market_frames: dict[str, tuple[pd.Series, pd.DataFrame, pd.Series, pd.Series]] = {}
    for market, raw_df in raw_frames.items():
        features_df = build_feature_matrix(raw_df, market, half_life_bars)
        cs_df = cross_sectional[market].reindex(raw_df["candle_time"]).reset_index(drop=True)
        features_df = pd.concat([features_df.reset_index(drop=True), cs_df], axis=1)
        features_df.index = raw_df.index
        labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, barrier_k)
        weights = compute_sample_uniqueness_weights(labels, n_bars)
        market_frames[market] = (raw_df["candle_time"], features_df, labels, weights)
```

(기존 `for market in markets:` 블록 전체를 이 코드로 교체 — `markets`에 `"KRW-BTC"`가 항상 포함돼 있는지는 `TRAINING_MARKETS` 상수가 이미 보장한다)

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_train_regime_ml.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: 베타중립 cross-sectional 피처를 학습에 반영"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

Phase 2 종료 시점 baseline과 이번 pooled weighted kappa를 비교한다.

- **개선되면 채택** — Task 9(서빙 배선)로 진행. 이 kappa를 다음 baseline으로 기록.
- **악화되면 폐기** — `git revert HEAD~1`(Task 8 커밋만 되돌림) 후 테스트 재확인. **Task 9는 건너뛴다**(서빙 배선은 이 피처가 채택된 경우에만 의미가 있음 — 설계 문서의 트레이드오프 참고). Phase 4로 바로 진행.

---

### Task 9 (Task 8에서 채택된 경우에만 진행): cross-sectional 피처를 서빙에 배선

**Files:**
- Modify: `backend/regime_ml_service.py`
- Test: `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: `engine.regime_ml_cross_sectional.compute_cross_sectional_features`(Task 7)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_predict_current_ml_regime_loads_all_training_markets_for_cross_sectional_features(
    tmp_path, monkeypatch
):
    """cross-sectional 피처가 배선된 뒤에는 단일 마켓 예측이라도
    load_market_training_data가 TRAINING_MARKETS 전체 개수만큼 호출돼야 한다
    (베타중립/순위 피처 계산에 다른 마켓들의 동시각 수익률이 필요하므로)."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5, markets=list(_MARKETS))

    call_log = []

    def _fake_load(market, timeframe, start, end):
        call_log.append(market)
        return pd.DataFrame({
            "close": [1.0] * 5,
            "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
        })

    monkeypatch.setattr(regime_ml_service, "load_market_training_data", _fake_load)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    predict_current_ml_regime("KRW-ETH", "minutes60")

    assert set(call_log) == set(_MARKETS)
```

(`_MARKETS`가 이 테스트 파일에서 `["KRW-BTC", "KRW-ETH", "KRW-XRP"]`로 정의돼 있음 — Task 9 구현체는 sidecar의 `markets` 리스트 전체를 순회해야 이 테스트가 통과한다)

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_service.py::test_predict_current_ml_regime_loads_all_training_markets_for_cross_sectional_features -v`
Expected: FAIL — `call_log`에 `KRW-ETH`만 담김(현재는 단일 마켓만 로드)

- [ ] **Step 3: `backend/regime_ml_service.py` 수정**

임포트 추가:

```python
from engine.regime_ml_cross_sectional import compute_cross_sectional_features
```

`predict_current_ml_regime` 내부, 기존:

```python
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WARMUP_DAYS)
    df = load_market_training_data(market, timeframe, start, end)
    bar_time = _to_utc_iso(df["candle_time"].iloc[-1])

    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)
```

를 아래로 교체:

```python
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WARMUP_DAYS)
    raw_frames = {m: load_market_training_data(m, timeframe, start, end) for m in serving_markets}
    df = raw_frames[market]
    bar_time = _to_utc_iso(df["candle_time"].iloc[-1])

    half_life_bars = half_life_bars_for_timeframe(timeframe)
    features_df = build_feature_matrix(df, market, half_life_bars)

    market_returns = {
        m: raw_df.set_index("candle_time")["close"].pct_change(fill_method=None)
        for m, raw_df in raw_frames.items()
    }
    cross_sectional = compute_cross_sectional_features(market_returns, btc_market="KRW-BTC")
    cs_df = cross_sectional[market].reindex(df["candle_time"]).reset_index(drop=True)
    features_df = pd.concat([features_df.reset_index(drop=True), cs_df], axis=1)
    features_df.index = df.index
```

(`serving_markets`는 이미 이 함수 앞부분에 `serving_markets = sidecar.get("markets", _LEGACY_SIDECAR_MARKETS)`로 정의돼 있으므로 그대로 재사용한다 — "KRW-BTC"가 `serving_markets`에 없는 구형 모델은 애초에 이 피처를 쓰지 않았을 것이므로 고려 불필요)

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/regime_ml_service.py tests/test_regime_ml_service.py
git commit -m "feat: cross-sectional 피처를 서빙 예측에도 반영"
```

- [ ] **Step 7: 실API 스모크 테스트**

Run(백엔드가 떠 있어야 함 — 아니면 `PYTHONPATH=. PYTHONIOENCODING=utf-8 uvicorn backend.main:app --port 8000 &`로 먼저 기동): `curl -s http://127.0.0.1:8000/api/v1/regime/ml-current-prediction?market=KRW-BTC&timeframe=minutes60`

Expected: 200 응답, `predicted_category`/`probs` 필드가 정상적으로 채워짐(에러 없음 — 20마켓을 순회하는 동안 특정 마켓 데이터 로드가 느리거나 실패하면 여기서 드러난다).

---

## Phase 4 — 캔들 결측 구간 스캔 (④)

### Task 10: 결측 구간 스캔 스크립트 + 실행

**Files:**
- Create: `scripts/scan_candle_gaps.py`

**Interfaces:**
- Produces: `scan_market_gaps(market: str, timeframe: str, start: datetime, end: datetime) -> list[dict]` (각 dict: `{"gap_start": Timestamp, "gap_end": Timestamp, "gap_hours": float}`) — 순수 함수라 유닛테스트 가능

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_scan_candle_gaps.py`:

```python
"""
tests/test_scan_candle_gaps.py

scripts.scan_candle_gaps.scan_market_gaps()를 검증한다.
"""
from __future__ import annotations

import pandas as pd

from scripts.scan_candle_gaps import scan_market_gaps


def test_scan_market_gaps_detects_single_gap():
    candle_time = pd.to_datetime([
        "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z",
        "2024-01-01T05:00:00Z",  # 1시간 간격이어야 하는데 4시간 결측
        "2024-01-01T06:00:00Z",
    ])
    df = pd.DataFrame({"candle_time": candle_time, "close": [1.0, 1.0, 1.0, 1.0]})

    gaps = scan_market_gaps(df, expected_interval_hours=1.0)

    assert len(gaps) == 1
    assert gaps[0]["gap_hours"] == 4.0


def test_scan_market_gaps_no_gaps_when_regular_interval():
    candle_time = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    df = pd.DataFrame({"candle_time": candle_time, "close": [1.0] * 5})

    gaps = scan_market_gaps(df, expected_interval_hours=1.0)

    assert gaps == []


def test_scan_market_gaps_detects_multiple_gaps():
    candle_time = pd.to_datetime([
        "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z",
        "2024-01-01T04:00:00Z",  # 3시간 결측
        "2024-01-01T05:00:00Z",
        "2024-01-01T10:00:00Z",  # 5시간 결측
    ])
    df = pd.DataFrame({"candle_time": candle_time, "close": [1.0] * 5})

    gaps = scan_market_gaps(df, expected_interval_hours=1.0)

    assert [g["gap_hours"] for g in gaps] == [3.0, 5.0]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_scan_candle_gaps.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 구현**

```python
"""
scripts/scan_candle_gaps.py

20개 학습 마켓의 minutes60 캔들에서 결측 구간(캔들 간 시간 간격이 timeframe
배수를 벗어나는 지점)을 스캔한다. docs/regime-ml-backlog.md 기술부채 항목 —
KRW-DOGE 2026-07-05 17:00~20:59 결측이 Triple Barrier 라벨링을 왜곡할 수 있음이
확인됐고, 다른 마켓에도 있는지는 미확인이었다. 설계 문서:
docs/superpowers/specs_v1/2026-08-31-regime-ml-performance-improvement-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/scan_candle_gaps.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from engine.regime_ml_constants import TRAINING_MARKETS
from upbit_data_service import get_candles

TIMEFRAME = "minutes60"
EXPECTED_INTERVAL_HOURS = 1.0
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def scan_market_gaps(df: pd.DataFrame, expected_interval_hours: float) -> list[dict]:
    """df["candle_time"]이 오름차순 정렬돼 있다고 가정(get_candles()의 보장 —
    upbit_data_service.py 참고). 연속한 두 캔들 간 간격이 expected_interval_hours를
    초과하는 지점을 전부 찾아 반환한다."""
    diffs = df["candle_time"].diff().dt.total_seconds() / 3600.0
    gap_mask = diffs > expected_interval_hours
    gaps = []
    for idx in diffs[gap_mask].index:
        gaps.append({
            "gap_start": df["candle_time"].iloc[idx - 1],
            "gap_end": df["candle_time"].iloc[idx],
            "gap_hours": float(diffs.loc[idx]),
        })
    return gaps


def main() -> None:
    end = datetime.now(timezone.utc)
    print(f"스캔 구간: {TRAIN_START.date()} ~ {end.date()}, timeframe={TIMEFRAME}\n")
    for market in TRAINING_MARKETS:
        df = get_candles(market, TIMEFRAME, TRAIN_START, end)
        gaps = scan_market_gaps(df, EXPECTED_INTERVAL_HOURS)
        total_gap_hours = sum(g["gap_hours"] for g in gaps)
        total_span_hours = (df["candle_time"].iloc[-1] - df["candle_time"].iloc[0]).total_seconds() / 3600.0
        pct = (total_gap_hours / total_span_hours * 100.0) if total_span_hours > 0 else 0.0
        print(f"{market}: 결측 {len(gaps)}건, 총 {total_gap_hours:.1f}시간({pct:.3f}% of 전체 구간)")
        for g in gaps:
            print(f"    {g['gap_start']} ~ {g['gap_end']} ({g['gap_hours']:.1f}시간)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_scan_candle_gaps.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/scan_candle_gaps.py tests/test_scan_candle_gaps.py
git commit -m "feat: 캔들 결측 구간 스캔 스크립트 추가"
```

- [ ] **Step 6: 실데이터 스캔 실행 및 규모 판단**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/scan_candle_gaps.py`

**판단 기준**: 20개 마켓 중 하나라도 `총 결측시간이 전체 구간의 0.1%를 초과`하거나, 단일 결측 구간이 `n_bars(60시간)의 절반(30시간) 이상`이면 "유의미"로 판단해 Task 11을 진행한다. 그 외(모든 마켓이 이 기준 미만)면 "미미"로 판단해 Task 11을 건너뛰고 Phase 5(Task 12)로 바로 진행하되, 스캔 결과 요약(마켓별 결측 건수/총 시간)을 Task 12에서 `docs/regime-ml-backlog.md`에 기록한다.

---

### Task 11 (Task 10에서 "유의미"로 판단된 경우에만 진행): 결측 구간 걸친 라벨 NaN 처리

**Files:**
- Modify: `engine/regime_ml_labels.py`
- Modify: `scripts/train_regime_ml.py`
- Test: `tests/test_regime_ml_labels.py`

**Interfaces:**
- `compute_triple_barrier_labels`에 옵션 파라미터 `candle_time: pd.Series | None = None`을 추가한다(기본값 `None`이면 기존과 완전히 동일하게 동작 — `backend/regime_fact_service.py`, `scripts/analyze_regime_fact_performance.py`, `scripts/select_barrier_k.py`, `scripts/validate_hmm_feature.py` 등 이 함수의 다른 호출자들은 이 인자를 안 넘기므로 영향 없음).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_labels.py`에 추가:

```python
def test_compute_triple_barrier_labels_nans_out_labels_whose_window_spans_a_gap():
    """라벨 i의 미래 윈도우 [i+1, i+1+n_bars](행 기준)가 실제로는 결측 구간을
    걸쳐 있어(그 구간의 실제 경과 시간이 half_life_bars_for_timeframe가 가정하는
    "n_bars개 봉 = n_bars 시간"보다 훨씬 길면) 그 라벨은 NaN 처리돼야 한다."""
    closes = _WARMUP + [_BASE] * 15
    df = _make_close_df(closes)
    candle_time = pd.date_range("2024-01-01", periods=len(df), freq="h", tz="UTC")
    # index 49(=_WARMUP 마지막)부터 시작하는 미래 윈도우 한가운데(index 55)에서
    # 24시간 결측을 만든다 -> index 49 라벨의 [50, 59] 윈도우가 이 결측을 포함.
    candle_time = pd.Series(candle_time)
    candle_time.iloc[55:] = candle_time.iloc[55:] + pd.Timedelta(hours=24)

    labels_without_gap_check = compute_triple_barrier_labels(df, _HALF_LIFE_BARS, _N_BARS, _K)
    labels_with_gap_check = compute_triple_barrier_labels(
        df, _HALF_LIFE_BARS, _N_BARS, _K, candle_time=candle_time
    )

    assert not pd.isna(labels_without_gap_check.iloc[49])  # 결측을 모르면 라벨이 붙음
    assert pd.isna(labels_with_gap_check.iloc[49])  # 결측을 알면 NaN 처리


def test_compute_triple_barrier_labels_candle_time_none_matches_previous_behavior():
    """candle_time을 안 넘기면(기존 호출자들의 방식) 결과가 정확히 이전과
    동일해야 한다 — 이 함수의 다른 호출자(backend/regime_fact_service.py 등)에
    영향이 없다는 회귀 방지."""
    closes = _WARMUP + [_BASE] * 15
    df = _make_close_df(closes)

    labels_default = compute_triple_barrier_labels(df, _HALF_LIFE_BARS, _N_BARS, _K)
    labels_explicit_none = compute_triple_barrier_labels(
        df, _HALF_LIFE_BARS, _N_BARS, _K, candle_time=None
    )

    pd.testing.assert_series_equal(labels_default, labels_explicit_none)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_labels.py -k gap -v`
Expected: FAIL with `TypeError: compute_triple_barrier_labels() got an unexpected keyword argument 'candle_time'`

- [ ] **Step 3: `engine/regime_ml_labels.py` 수정**

이 태스크는 함수 본문(변동성/barrier 계산 로직)은 건드리지 않는다 — Task 4에서 `.shift(1)`이 채택됐든 폐기됐든, 그 시점의 "현재 코드"를 그대로 두고 **시그니처와 return 문 근처만** 수정한다.

함수 시그니처를 수정(기존 `def compute_triple_barrier_labels(df: pd.DataFrame, half_life_bars: float, n_bars: int, k: float) -> pd.Series:`를 아래로 교체):

```python
def compute_triple_barrier_labels(
    df: pd.DataFrame,
    half_life_bars: float,
    n_bars: int,
    k: float,
    candle_time: pd.Series | None = None,
) -> pd.Series:
```

docstring 끝(마지막 문장 다음)에 문단 추가:

```
    candle_time: 제공하면(캔들 시각 Series, df와 같은 인덱스) 라벨 i의 미래
    윈도우가 실제로 걸치는 경과시간이 n_bars * (candle_time 간 최빈 간격)의
    1.5배를 넘는 경우(=캔들 결측 구간을 걸침) 그 라벨을 NaN 처리한다. None이면
    (기본값) 이 검사를 생략해 기존 동작과 완전히 동일하다.
```

함수 본문 끝의 기존 `return pd.Series(labels, index=df.index)`를 아래로 교체(그 위의 `volatility`/`close`/`labels` 계산 루프는 절대 수정하지 않는다):

```python
    result = pd.Series(labels, index=df.index)
    if candle_time is not None:
        result = _mask_labels_spanning_gaps(result, candle_time, n_bars)
    return result
```

파일 끝에 새 헬퍼 함수를 추가:

```python
def _mask_labels_spanning_gaps(labels: pd.Series, candle_time: pd.Series, n_bars: int) -> pd.Series:
    """candle_time 간 최빈 간격(median)을 "정상 간격"으로 추정하고, 라벨 i의
    미래 윈도우 [i+1, i+1+n_bars](행 기준)가 실제로 걸치는 경과시간이
    n_bars*정상간격*1.5를 넘으면 그 라벨을 NaN으로 덮어쓴다."""
    intervals = candle_time.diff().dropna()
    if intervals.empty:
        return labels
    normal_interval = intervals.median()
    threshold = normal_interval * n_bars * 1.5

    n = len(labels)
    masked = labels.copy()
    for i in range(n):
        if pd.isna(masked.iloc[i]):
            continue
        window_end = min(i + n_bars, n - 1)
        elapsed = candle_time.iloc[window_end] - candle_time.iloc[i]
        if elapsed > threshold:
            masked.iloc[i] = float("nan")
    return masked
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_labels.py -v`
Expected: 전부 PASS

- [ ] **Step 5: `scripts/train_regime_ml.py`에서 candle_time을 넘기도록 수정**

```python
        labels = compute_triple_barrier_labels(
            raw_df, half_life_bars, n_bars, barrier_k, candle_time=raw_df["candle_time"]
        )
```

(기존 `labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, barrier_k)` 줄을 교체)

- [ ] **Step 6: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add engine/regime_ml_labels.py scripts/train_regime_ml.py tests/test_regime_ml_labels.py
git commit -m "fix: 캔들 결측 구간을 걸친 라벨을 NaN 처리"
```

- [ ] **Step 8: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

Phase 3 종료 시점 baseline과 비교해 개선이면 채택(그대로 Task 12로), 악화면 `git revert HEAD~1` 후 테스트 재확인하고 Task 12로 진행.

---

## Phase 5 — 마무리

### Task 12: 최종 재학습 + 백로그 문서 갱신

**Files:**
- Modify: `docs/regime-ml-backlog.md`

- [ ] **Step 1: 최종 모델로 재학습(이미 직전 태스크에서 최신 상태로 학습했다면 생략 가능 — 생략 시 그 결과를 그대로 사용)**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

출력에서 최종 pooled weighted kappa/macro F1을 기록한다.

- [ ] **Step 2: `docs/regime-ml-backlog.md` 갱신**

"우선순위 1(다음 착수) — ② 모델 성능 대폭 개선" 섹션 아래에 결과 요약을 추가한다(정확한 문구는 실제 실행 결과에 맞춰 작성 — 아래는 구조 예시):

```markdown
**② 모델 성능 개선 완료(2026-08-31)**: `docs/superpowers/specs_v1/2026-08-31-regime-ml-performance-improvement-design.md`
1~4번 순서로 ablation. pooled weighted kappa 0.097 → [실측값]. 채택된 항목:
[Task 4/6/8/11 중 실제로 채택된 것 나열]. 폐기된 항목: [git revert된 것 나열,
폐기 사유(kappa 악화) 포함]. Threshold/확률보정(①)은 kappa와 무관하게 항상 적용
(서빙 시 decision_threshold=[실측값], target precision=0.55). 캔들 결측 스캔
결과: [Task 10 요약 — 유의미했는지, 어느 마켓에서 얼마나]. 다음 후보: CUSUM
이벤트 샘플링(②에서 sample uniqueness가 유의미하지 않았을 때만), 로지스틱회귀
baseline 비교 + LightGBM 하이퍼파라미터 튜닝(문서 우선순위 5번), 메타 레이블링
(6번).
```

같은 섹션의 "우선순위 2 — ③ 실시간 자동 장세 대응 개발" 위에 있던 "①②가 검증된 뒤에만 착수" 조건을 이번 결과로 충족했는지(kappa가 실제로 개선됐는지)에 따라, 충족했다면 "**전제 조건 충족됨(②)**" 문구를 추가하고, 충족하지 못했다면(모든 ablation이 폐기됨) 그 사실과 함께 "③ 착수는 재검토 필요"로 남긴다.

- [ ] **Step 3: 커밋**

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: ② 모델 성능 개선 결과 백로그에 반영"
```

- [ ] **Step 4: 세션 종료 보고**

사용자에게 다음을 요약 보고한다(코드 변경 아님 — 대화로 전달):
- 최종 pooled weighted kappa(baseline 0.097 대비 변화)
- 채택/폐기된 항목 목록과 각각의 이유
- 최종 모델을 AWS에 배포할지 여부를 질문(`backend/regime_ml_service.py::deploy_model` /
  관리자 패널의 배포 버튼 — 이 플랜의 범위 밖, 사용자 승인 필요)
