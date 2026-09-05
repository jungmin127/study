# 장세 판별 ML — 로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝 (c-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-09-01-regime-ml-baseline-and-tuning-design.md`에서 승인된 설계대로 `scripts/train_regime_ml.py::run_training()`을 모델 교체 가능하게 확장하고, 그 위에 LightGBM vs LogisticRegression 비교 스크립트와 LightGBM 하이퍼파라미터 그리드서치 스크립트를 추가해 baseline(pooled weighted kappa 0.108) 대비 개선 여지를 실측한다.

**Architecture:** `run_training()`에 `model_factory`/`preprocess_fold`/`save_model` 확장점을 추가하고 반환값을 `TrainingResult` 데이터클래스로 바꿔 pooled kappa를 노출한다 → `scripts/compare_regime_ml_baseline.py`가 같은 `run_training()`을 LightGBM 기본 팩토리/LogisticRegression 팩토리로 두 번 호출해 비교 → `scripts/tune_regime_ml_hyperparams.py`가 LightGBM 하이퍼파라미터 조합으로 반복 호출해 2단계 축소 그리드서치.

**Tech Stack:** Python, pandas, scikit-learn(LogisticRegression/SimpleImputer/StandardScaler/OneHotEncoder/ColumnTransformer/Pipeline — 이미 requirements.txt에 있는 sklearn, 신규 의존성 없음), LightGBM, pytest.

## Global Constraints

- 평가지표: `weighted_kappa`(pooled) 1순위, `macro_f1` 2순위. baseline: **0.108**(`docs/regime-ml-backlog.md` 주가지수 라운드 결과).
- 이번 스코프에서는 **`scripts/train_regime_ml.py`의 실제 프로덕션 LightGBM 하이퍼파라미터(`BARRIER_K` 등 포함)를 자동으로 바꾸지 않는다** — 그리드서치 결과가 개선으로 나와도 콘솔 출력+백로그 문서 기록까지만 하고, 실제 반영은 사용자와 별도 논의 후 다음 세션에서.
- 각 태스크 끝에서 `PYTHONPATH=. python -m pytest tests/ -q`가 전부 통과해야 한다.
- Task 4(실데이터 실행)는 실제 네트워크로 20개 마켓 데이터를 조회하며 오래 걸릴 수 있다(수십 분). **백그라운드로 띄워놓고 응답을 끝내면 안 된다** — 결과 숫자를 실제로 손에 넣을 때까지 폴링하며 기다릴 것.
- 코스피/코스닥, PBO 프레임워크, 메타 레이블링, CUSUM 이벤트 샘플링은 이번 스코프 아님(백로그 다른 후보).
- 한국어 docstring/주석 관례 유지("왜"를 설명).

---

## Task 1: `run_training()` 모델 교체 가능하게 확장 + `TrainingResult` 반환

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Modify: `tests/test_train_regime_ml.py`

**Interfaces:**
- Produces: `TrainingResult(reports: list[dict], pooled: dict, per_market: dict[str, dict])` 데이터클래스. `run_training(..., model_factory: Callable[[], Any] = _default_lgbm_factory, preprocess_fold: Callable[[pd.DataFrame, pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]] | None = None, save_model: bool = True) -> TrainingResult`. 이후 Task 2/3이 `model_factory`/`preprocess_fold`/`save_model=False`로 이 함수를 재사용한다.

### Step 1: 기존 테스트를 새 반환 타입에 맞게 먼저 고치고, 신규 확장점 테스트를 추가한다(둘 다 지금 코드에서는 실패해야 정상)

`tests/test_train_regime_ml.py`의 9개 기존 테스트 함수를 아래 내용으로 전체 교체한다(각 함수에서 `reports = run_training(` 호출을 `result = run_training(`로 바꾸고, 호출 직후 `reports = result.reports`를 추가한 것 외에는 기존 로직과 100% 동일):

```python
def test_run_training_completes_and_saves_model(tmp_path, monkeypatch):
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
    )
    reports = result.reports

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

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=10**9,  # 항상 표본 부족
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    reports = result.reports

    assert reports == []
    assert list(tmp_path.glob("*.txt")) == []


def test_run_training_prints_aggregate_summary_after_folds(tmp_path, monkeypatch, capsys):
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
    )
    reports = result.reports
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
    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=n_folds,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    reports = result.reports

    assert len(reports) == n_folds
    assert sorted(r["fold_index"] for r in reports) == list(range(1, n_folds + 1))


def test_run_training_saves_json_sidecar_alongside_model(tmp_path, monkeypatch):
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
    )
    reports = result.reports
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
        "decision_threshold", "calibration_breakpoints", "threshold_table",
    }
    assert sidecar["markets"] == list(seeds.keys())
    assert sidecar["labeling_method"] == "triple_barrier"
    assert sidecar["barrier_k"] == _BARRIER_K
    # set()이 아니라 순서를 그대로 비교한다 — LightGBM의 model.classes_는 이
    # 한국어 레이블들을 유니코드 코드포인트 순으로 정렬하는데(실측: sorted(['하락',
    # '하락아님']) == ['하락', '하락아님']), 이 값이 마침 sorted(CATEGORY_LABELS)와
    # 같다. 이 순서가 binary objective에서 model.predict()가 반환하는 스칼라가 어느
    # 클래스의 확률인지(classes[1])를 결정하므로(backend/regime_ml_service.py 참고),
    # set 비교로는 순서가 어긋나는 회귀를 놓친다.
    assert sidecar["classes"] == sorted(train_regime_ml.CATEGORY_LABELS)
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

    # TrainingResult가 sidecar와 동일한 pooled/per_market을 노출하는지 확인
    # (비교/튜닝 스크립트가 sidecar 없이 result.pooled만으로 판단하므로 필수).
    assert result.pooled == pooled
    assert result.per_market == per_market


def test_run_training_performance_folds_excludes_skipped_folds(tmp_path, monkeypatch):
    """fold 하나가 표본 부족으로 스킵됐을 때, 사이드카 performance.folds가 실제로
    평가된 fold만 담고(reports와 정확히 같은 fold_index 집합) 스킵된 fold는 포함하지
    않는지 확인한다."""
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
        n_folds=3,
        min_train_samples=600,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    reports = result.reports

    assert [r["fold_index"] for r in reports] == [2, 3]

    # 이 합성 데이터(seed 1/2/3, _N=24*40시간, barrier_k=6.0)에서 n_folds=3일 때
    # 실측 n_train은 fold 1=537, fold 2=1257, fold 3=1977 —
    # min_train_samples=600이면 fold 1만 표본 부족으로 스킵되고 fold 2·3은 평가된다.
    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert [f["fold_index"] for f in sidecar["performance"]["folds"]] == [2, 3]


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

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    reports = result.reports
    assert len(reports) >= 1
    assert len(captured_calls) == len(reports)
    for call in captured_calls:
        assert call["sample_weight"] is not None
        assert len(call["sample_weight"]) == call["n_X"]
        assert all(w > 0 for w in call["sample_weight"])


def test_run_training_saves_calibration_fields_in_sidecar(tmp_path, monkeypatch):
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
    )
    reports = result.reports
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

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
    )
    reports = result.reports
    assert len(reports) >= 1
    for columns in captured_columns:
        assert "BETA_NEUTRAL_RETURN" in columns
        assert "CROSS_SECTIONAL_RANK" in columns
```

파일 끝에 아래 3개 테스트를 새로 추가한다(신규 확장점 검증):

```python
def test_run_training_uses_custom_model_factory(tmp_path, monkeypatch):
    """model_factory로 넘긴 모델이 실제로 fit/predict에 쓰이는지 확인한다.
    DummyClassifier는 feature_importances_가 없어 top_features 가드(빈 리스트)도
    함께 검증한다."""
    from sklearn.dummy import DummyClassifier

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
        model_factory=lambda: DummyClassifier(strategy="most_frequent"),
        save_model=False,
    )

    assert len(result.reports) >= 1
    for report in result.reports:
        assert report["top_features"] == []
    assert list(tmp_path.glob("*.txt")) == []
    assert list(tmp_path.glob("*.json")) == []


def test_run_training_applies_custom_preprocess_fold(tmp_path, monkeypatch):
    """preprocess_fold가 반환한 (train_X, test_X)가 그대로 model.fit에 쓰이는지
    컬럼 변형(market 컬럼 제거)으로 확인한다."""
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

    def _drop_market_column(train_X, test_X):
        return train_X.drop(columns=["market"]), test_X.drop(columns=["market"])

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
        preprocess_fold=_drop_market_column,
        save_model=False,
    )

    assert len(result.reports) >= 1
    for columns in captured_columns:
        assert "market" not in columns


def test_run_training_respects_save_model_false(tmp_path, monkeypatch):
    """save_model=False면 학습이 성공해도 .txt/.json을 저장하지 않는다."""
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

    assert len(result.reports) >= 1
    assert list(tmp_path.glob("*.txt")) == []
    assert list(tmp_path.glob("*.json")) == []
```

### Step 2: 테스트 실행해서 실패 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -q`
Expected: 12개 테스트 전부 FAIL(`AttributeError: 'list' object has no attribute 'reports'` 또는 `TypeError: run_training() got an unexpected keyword argument 'model_factory'`).

### Step 3: `scripts/train_regime_ml.py` 구현

파일 상단 import 블록(`from __future__ import annotations` 바로 다음)에 추가:

```python
from dataclasses import dataclass
from typing import Any, Callable
```

`MODEL_OUTPUT_DIR = ...` 상수 정의 다음, `def run_training(...)` 정의 앞에 추가:

```python
@dataclass
class TrainingResult:
    """run_training()의 반환값. reports는 기존 fold별 리포트 리스트와 완전히
    동일하다 — pooled/per_market은 원래 함수 내부에서만 계산되고 sidecar JSON에만
    남던 값을 호출자가 직접 접근할 수 있게 노출한 것(비교/튜닝 스크립트가 필요)."""
    reports: list[dict]
    pooled: dict
    per_market: dict[str, dict]


def _default_lgbm_factory() -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(
        objective="binary", class_weight="balanced", importance_type="gain", random_state=42
    )


def _default_preprocess(train_X: pd.DataFrame, test_X: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """LightGBM 네이티브 카테고리 지원을 쓰기 위한 기본 전처리 — market 컬럼만
    category dtype으로 cast한다(다른 피처는 그대로 통과, NaN도 LightGBM이 직접
    처리하므로 별도 대치 없음)."""
    train_X_fit = train_X.assign(market=train_X["market"].astype("category"))
    test_X_fit = test_X.assign(market=test_X["market"].astype("category"))
    return train_X_fit, test_X_fit
```

`run_training()` 시그니처를 아래로 교체(기존 파라미터 뒤에 3개 추가):

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
) -> TrainingResult:
```

함수 docstring 마지막 문장("fold별 리포트 리스트를 반환하고...") 뒤에 이어서 추가:

```
    model_factory/preprocess_fold로 LightGBM 대신 다른 분류기를 끼워 비교/튜닝
    스크립트에서 재사용할 수 있다(scripts/compare_regime_ml_baseline.py,
    scripts/tune_regime_ml_hyperparams.py 참고). save_model=False면 모델 파일/
    JSON 사이드카를 저장하지 않는다(data/regime_ml_models/ 오염 방지).
```

fold 루프 안의 아래 두 블록을 교체한다.

교체 전:
```python
        train_X_fit = train_X.assign(market=train_X["market"].astype("category"))
        test_X_fit = test_X.assign(market=test_X["market"].astype("category"))

        model = lgb.LGBMClassifier(
            objective="binary", class_weight="balanced", importance_type="gain", random_state=42
        )
        model.fit(train_X_fit, train_y, sample_weight=train_w.to_numpy())
```

교체 후:
```python
        preprocess = preprocess_fold or _default_preprocess
        train_X_fit, test_X_fit = preprocess(train_X, test_X)

        model = model_factory()
        model.fit(train_X_fit, train_y, sample_weight=train_w.to_numpy())
```

교체 전:
```python
        importances = dict(zip(train_X_fit.columns, model.feature_importances_))
        top_features = sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:15]
```

교체 후:
```python
        if hasattr(model, "feature_importances_"):
            importances = dict(zip(train_X_fit.columns, model.feature_importances_))
            top_features = sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:15]
        else:
            top_features = []
```

모델 저장 블록의 조건문을 교체한다.

교체 전:
```python
    if last_model is not None:
```

교체 후:
```python
    if last_model is not None and save_model:
```

함수 마지막 줄을 교체한다.

교체 전:
```python
    return reports
```

교체 후:
```python
    return TrainingResult(reports=reports, pooled=pooled_metrics, per_market=per_market_metrics)
```

`main()`을 교체한다.

교체 전:
```python
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
```

교체 후:
```python
def main() -> None:
    result = run_training(
        markets=TRAINING_MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        barrier_k=BARRIER_K,
        model_output_dir=MODEL_OUTPUT_DIR,
    )
    print(f"\n총 {len(result.reports)}개 fold 평가 완료(요청 n_folds={N_FOLDS})")
```

### Step 4: 테스트 실행해서 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -q`
Expected: 12 passed.

### Step 5: 전체 테스트 스위트 통과 확인 (다른 파일이 run_training을 쓰는지 회귀 확인)

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 기존 실패 0건 유지(회귀 없음).

### Step 6: 커밋

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: run_training에 모델 교체 확장점 추가, TrainingResult 반환"
```

---

## Task 2: `scripts/compare_regime_ml_baseline.py` — LightGBM vs LogisticRegression 비교

**Files:**
- Create: `scripts/compare_regime_ml_baseline.py`

**Interfaces:**
- Consumes: Task 1의 `run_training(..., model_factory=..., preprocess_fold=..., save_model=False) -> TrainingResult`, `scripts.train_regime_ml`의 `BARRIER_K`/`MIN_TRAIN_SAMPLES`/`MODEL_OUTPUT_DIR`/`N_FOLDS`/`TIMEFRAME`/`TRAIN_END`/`TRAIN_START`, `engine.regime_ml_constants.TRAINING_MARKETS`.

이 스크립트는 `select_barrier_k.py`와 같은 성격의 1회성 진단 도구라 전용 테스트를
작성하지 않는다(기존 프로젝트 관례). 대신 Step 2에서 synthetic 데이터로 실제
동작을 손으로 확인한다.

### Step 1: 스크립트 작성

```python
"""
scripts/compare_regime_ml_baseline.py

LightGBM(현재 배포 설정) vs LogisticRegression(L2) walk-forward pooled weighted
kappa 비교. docs/ML_Regime_Switching_Additional_Improvements.md 3-1절 — 신호대
잡음비가 낮은 금융시계열에서는 트리 앙상블이 노이즈에 과적합하기 쉽고 정규화
선형모델이 오히려 견고할 수 있다는 지적을 실측으로 확인한다. select_barrier_k.py와
같은 성격의 1회성 진단 스크립트 — 결과는 콘솔 출력+백로그 문서 기록으로만 남기고
모델은 저장하지 않는다(save_model=False).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/compare_regime_ml_baseline.py
"""
from __future__ import annotations

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from engine.regime_ml_constants import TRAINING_MARKETS
from scripts.train_regime_ml import (
    BARRIER_K,
    MIN_TRAIN_SAMPLES,
    MODEL_OUTPUT_DIR,
    N_FOLDS,
    TIMEFRAME,
    TRAIN_END,
    TRAIN_START,
    TrainingResult,
    run_training,
)

# 이 프로젝트가 과거 라운드들에서 실측한 pooled weighted kappa의 자연변동폭
# (동일 코드/설정으로 재실행해도 TRAIN_END=datetime.now()가 매번 조금씩 다른
# 최신 데이터를 포함해 생기는 변동). docs/regime-ml-backlog.md 참고.
_NATURAL_VARIATION = 0.005


def _lr_model_factory() -> LogisticRegression:
    return LogisticRegression(penalty="l2", class_weight="balanced", max_iter=1000, random_state=42)


def _lr_preprocess(train_X: pd.DataFrame, test_X: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """LogisticRegression은 NaN/카테고리를 직접 다루지 못한다 — 수치 피처는
    train으로만 median 대치+표준화하고 market은 원핫인코딩한다(fold 간 leakage
    방지를 위해 인코더/스케일러를 train에만 fit)."""
    numeric_columns = [c for c in train_X.columns if c != "market"]

    numeric_pipeline = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    transformer = ColumnTransformer([
        ("numeric", numeric_pipeline, numeric_columns),
        ("market", OneHotEncoder(handle_unknown="ignore"), ["market"]),
    ])

    train_arr = transformer.fit_transform(train_X)
    test_arr = transformer.transform(test_X)

    feature_names = transformer.get_feature_names_out()
    train_fit = pd.DataFrame(train_arr, columns=feature_names, index=train_X.index)
    test_fit = pd.DataFrame(test_arr, columns=feature_names, index=test_X.index)
    return train_fit, test_fit


def _print_comparison(lightgbm_result: TrainingResult, lr_result: TrainingResult) -> None:
    lgb_pooled = lightgbm_result.pooled
    lr_pooled = lr_result.pooled

    print("\n=== LightGBM vs LogisticRegression(L2) — pooled walk-forward 비교 ===")
    print(f"{'모델':<20}{'n':>8}{'macro F1':>12}{'weighted kappa':>18}")
    print(
        f"{'LightGBM(baseline)':<20}{lgb_pooled['n']:>8}"
        f"{lgb_pooled['macro_f1']:>12.3f}{lgb_pooled['weighted_kappa']:>18.3f}"
    )
    print(
        f"{'LogisticRegression':<20}{lr_pooled['n']:>8}"
        f"{lr_pooled['macro_f1']:>12.3f}{lr_pooled['weighted_kappa']:>18.3f}"
    )

    delta = lgb_pooled["weighted_kappa"] - lr_pooled["weighted_kappa"]
    if abs(delta) <= _NATURAL_VARIATION:
        verdict = "거의 동일(자연변동폭 이내) — 트리모델이 신호를 잡고 있다는 근거 약함"
    elif delta > 0:
        verdict = f"LightGBM 우세(+{delta:.3f}) — 트리모델이 실제로 신호를 더 잡음"
    else:
        verdict = f"LogisticRegression 우세({delta:.3f}) — LightGBM이 노이즈에 과적합했을 가능성"
    print(f"\n결론: {verdict}")


def main() -> None:
    common_kwargs = dict(
        markets=TRAINING_MARKETS,
        timeframe=TIMEFRAME,
        start=TRAIN_START,
        end=TRAIN_END,
        n_folds=N_FOLDS,
        min_train_samples=MIN_TRAIN_SAMPLES,
        barrier_k=BARRIER_K,
        model_output_dir=MODEL_OUTPUT_DIR,
        save_model=False,
    )

    print("=== LightGBM(baseline) 학습 중 ===")
    lightgbm_result = run_training(**common_kwargs)

    print("\n=== LogisticRegression(L2) 학습 중 ===")
    lr_result = run_training(
        **common_kwargs, model_factory=_lr_model_factory, preprocess_fold=_lr_preprocess
    )

    _print_comparison(lightgbm_result, lr_result)


if __name__ == "__main__":
    main()
```

### Step 2: synthetic 데이터로 손으로 동작 확인 (자동 테스트 대신)

`tests/test_train_regime_ml.py`의 `_make_synthetic_market_df`를 재사용해 소규모로
스크립트가 에러 없이 끝까지 도는지 확인한다. 아래를 스크래치 파일로 실행(커밋 안 함):

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
from datetime import datetime, timezone
import pandas as pd
from tests.test_train_regime_ml import _make_synthetic_market_df
import scripts.compare_regime_ml_baseline as compare_mod
import scripts.train_regime_ml as train_mod

seeds = {'KRW-BTC': 1, 'KRW-ETH': 2, 'KRW-XRP': 3}
train_mod.load_market_training_data = lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market])
compare_mod.TRAINING_MARKETS = list(seeds.keys())
compare_mod.N_FOLDS = 2
compare_mod.MIN_TRAIN_SAMPLES = 50
compare_mod.TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
compare_mod.TRAIN_END = compare_mod.TRAIN_START + pd.Timedelta(hours=24 * 40)
compare_mod.main()
"
```

Expected: 에러 없이 "결론: ..." 줄까지 출력됨(구체적인 kappa 숫자는 synthetic
데이터라 의미 없음 — 완주 여부만 확인).

### Step 3: 커밋

```bash
git add scripts/compare_regime_ml_baseline.py
git commit -m "feat: LightGBM vs LogisticRegression baseline 비교 스크립트 추가"
```

---

## Task 3: `scripts/tune_regime_ml_hyperparams.py` — LightGBM 하이퍼파라미터 2단계 그리드서치

**Files:**
- Create: `scripts/tune_regime_ml_hyperparams.py`

**Interfaces:**
- Consumes: Task 1의 `run_training(..., model_factory=..., save_model=False) -> TrainingResult`, `scripts.train_regime_ml`의 상수들, `engine.regime_ml_constants.TRAINING_MARKETS`.

`compare_regime_ml_baseline.py`와 같은 이유로 전용 테스트 없음 — Step 2에서
synthetic 데이터로 손으로 완주 확인.

### Step 1: 스크립트 작성

```python
"""
scripts/tune_regime_ml_hyperparams.py

LightGBM 하이퍼파라미터 축소 그리드서치. docs/ML_Regime_Switching_Additional_
Improvements.md 4절 우선순위 5번 — 지금 학습(scripts/train_regime_ml.py)은
objective/class_weight/importance_type/random_state 외 전부 sklearn 기본값을
쓴다. 2단계로 나눠 조합 폭발을 억제한다: 1단계(num_leaves/learning_rate/
min_child_samples, 27조합)로 큰 지렛대를 먼저 찾고, 2단계(reg_alpha/reg_lambda,
9조합)로 1단계 최적값 위에 정규화를 추가 탐색한다. select_barrier_k.py와 같은
성격의 1회성 진단 스크립트 — 결과가 채택되면 train_regime_ml.py의 LightGBM
생성 코드를 수동으로 갱신한다(이 스크립트가 자동으로 반영하지 않음).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/tune_regime_ml_hyperparams.py
(baseline 포함 총 37회 재학습 — 시작 시 baseline 1회를 먼저 재고 예상 총
소요시간을 출력한다. 이 값이 과도하게 크면(예: 60분 초과) 그리드를 줄이라는
안내만 출력하고 자동으로 중단하지는 않는다 — 백그라운드 실행 가능하므로.)
"""
from __future__ import annotations

import itertools
import time
from typing import Any

import lightgbm as lgb

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

_STAGE1_NUM_LEAVES = [15, 31, 63]
_STAGE1_LEARNING_RATE = [0.01, 0.05, 0.1]
_STAGE1_MIN_CHILD_SAMPLES = [10, 20, 50]
_STAGE2_REG_ALPHA = [0.0, 0.1, 1.0]
_STAGE2_REG_LAMBDA = [0.0, 0.1, 1.0]
_SLOW_TOTAL_MINUTES_WARNING = 60.0

_COMMON_KWARGS = dict(
    markets=TRAINING_MARKETS,
    timeframe=TIMEFRAME,
    start=TRAIN_START,
    end=TRAIN_END,
    n_folds=N_FOLDS,
    min_train_samples=MIN_TRAIN_SAMPLES,
    barrier_k=BARRIER_K,
    model_output_dir=MODEL_OUTPUT_DIR,
    save_model=False,
)


def _make_factory(params: dict[str, Any]):
    def factory() -> lgb.LGBMClassifier:
        return lgb.LGBMClassifier(
            objective="binary", class_weight="balanced", importance_type="gain", random_state=42,
            **params,
        )
    return factory


def _evaluate(params: dict[str, Any]) -> float | None:
    """조합 하나를 재학습해 pooled weighted kappa를 반환한다. 표본 부족으로
    전부 스킵되면(pooled.n == 0) None을 반환해 최적값 후보에서 제외한다."""
    result = run_training(**_COMMON_KWARGS, model_factory=_make_factory(params))
    if result.pooled["n"] == 0:
        return None
    return result.pooled["weighted_kappa"]


def main() -> None:
    print("=== baseline 1회 실행으로 소요시간 측정 ===")
    start_time = time.monotonic()
    baseline_kappa = _evaluate({})
    elapsed = time.monotonic() - start_time
    total_combos = (
        len(_STAGE1_NUM_LEAVES) * len(_STAGE1_LEARNING_RATE) * len(_STAGE1_MIN_CHILD_SAMPLES)
        + len(_STAGE2_REG_ALPHA) * len(_STAGE2_REG_LAMBDA)
    )
    estimated_minutes = elapsed * total_combos / 60
    print(f"baseline kappa={baseline_kappa}, 1회 소요={elapsed:.1f}초")
    print(f"예상 총 소요시간(baseline 제외 {total_combos}조합) = {estimated_minutes:.1f}분")
    if estimated_minutes > _SLOW_TOTAL_MINUTES_WARNING:
        print(
            f"경고: 예상 소요시간이 {_SLOW_TOTAL_MINUTES_WARNING:.0f}분을 넘습니다 — "
            "그리드 크기를 줄이는 것을 고려하세요(자동 중단하지 않고 계속 진행합니다)."
        )

    print("\n=== 1단계: num_leaves x learning_rate x min_child_samples ===")
    best_stage1_params: dict[str, Any] = {}
    best_stage1_kappa = float("-inf")
    for num_leaves, learning_rate, min_child_samples in itertools.product(
        _STAGE1_NUM_LEAVES, _STAGE1_LEARNING_RATE, _STAGE1_MIN_CHILD_SAMPLES
    ):
        params = {
            "num_leaves": num_leaves,
            "learning_rate": learning_rate,
            "min_child_samples": min_child_samples,
        }
        kappa = _evaluate(params)
        print(
            f"  num_leaves={num_leaves} learning_rate={learning_rate} "
            f"min_child_samples={min_child_samples} -> kappa={kappa}"
        )
        if kappa is not None and kappa > best_stage1_kappa:
            best_stage1_kappa = kappa
            best_stage1_params = params

    print(f"\n1단계 최적: {best_stage1_params} (kappa={best_stage1_kappa:.3f})")

    print("\n=== 2단계: reg_alpha x reg_lambda (1단계 최적값 고정) ===")
    best_params = dict(best_stage1_params)
    best_kappa = best_stage1_kappa
    for reg_alpha, reg_lambda in itertools.product(_STAGE2_REG_ALPHA, _STAGE2_REG_LAMBDA):
        params = {**best_stage1_params, "reg_alpha": reg_alpha, "reg_lambda": reg_lambda}
        kappa = _evaluate(params)
        print(f"  reg_alpha={reg_alpha} reg_lambda={reg_lambda} -> kappa={kappa}")
        if kappa is not None and kappa > best_kappa:
            best_kappa = kappa
            best_params = params

    print("\n=== 최종 결과 ===")
    print(f"baseline(현재 기본 하이퍼파라미터) kappa={baseline_kappa:.3f}")
    print(f"튜닝 최적 조합: {best_params}")
    print(f"튜닝 최적 kappa={best_kappa:.3f} (델타={best_kappa - baseline_kappa:+.3f})")


if __name__ == "__main__":
    main()
```

### Step 2: synthetic 데이터로 손으로 동작 확인

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
from datetime import datetime, timezone
import pandas as pd
from tests.test_train_regime_ml import _make_synthetic_market_df
import scripts.tune_regime_ml_hyperparams as tune_mod
import scripts.train_regime_ml as train_mod

seeds = {'KRW-BTC': 1, 'KRW-ETH': 2, 'KRW-XRP': 3}
train_mod.load_market_training_data = lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market])
tune_mod._COMMON_KWARGS['markets'] = list(seeds.keys())
tune_mod._COMMON_KWARGS['n_folds'] = 2
tune_mod._COMMON_KWARGS['min_train_samples'] = 50
tune_mod._COMMON_KWARGS['start'] = datetime(2024, 1, 1, tzinfo=timezone.utc)
tune_mod._COMMON_KWARGS['end'] = tune_mod._COMMON_KWARGS['start'] + pd.Timedelta(hours=24 * 40)
tune_mod._STAGE1_NUM_LEAVES = [15, 31]
tune_mod._STAGE1_LEARNING_RATE = [0.1]
tune_mod._STAGE1_MIN_CHILD_SAMPLES = [20]
tune_mod._STAGE2_REG_ALPHA = [0.0]
tune_mod._STAGE2_REG_LAMBDA = [0.0]
tune_mod.main()
"
```

Expected: 에러 없이 "=== 최종 결과 ===" 줄까지 출력됨(그리드를 임시로 축소해
빠르게 완주 여부만 확인 — 실제 스크립트 파일은 원래 그리드 크기 그대로 유지).

### Step 3: 커밋

```bash
git add scripts/tune_regime_ml_hyperparams.py
git commit -m "feat: LightGBM 하이퍼파라미터 2단계 그리드서치 스크립트 추가"
```

---

## Task 4: 실데이터 실행 + 백로그 문서 반영

**Files:**
- Modify: `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: Task 2/3 스크립트의 실제 콘솔 출력(20마켓, 실네트워크 데이터).

### Step 1: `compare_regime_ml_baseline.py` 실데이터 실행

Run(오래 걸릴 수 있음, 결과 나올 때까지 대기 — 긴 timeout 사용):
`PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/compare_regime_ml_baseline.py`

콘솔에 출력된 LightGBM/LogisticRegression pooled weighted kappa/macro F1 수치와
"결론" 문구를 그대로 기록해둔다(다음 스텝에서 문서화에 사용).

### Step 2: `tune_regime_ml_hyperparams.py` 실데이터 실행

Run(baseline 대비 최대 37배 오래 걸림 — 스크립트가 시작 시 예상 소요시간을
출력하니 그 값을 보고 기다릴 것. 결과 나올 때까지 대기):
`PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/tune_regime_ml_hyperparams.py`

콘솔에 출력된 1단계/2단계 최적 조합과 최종 kappa/델타를 그대로 기록해둔다.

### Step 3: `docs/regime-ml-backlog.md`에 결과 반영

`## 다음 세션 작업 후보` 섹션의 "우선순위 1(다음 착수 후보) — c-2 로지스틱회귀
baseline + LightGBM 하이퍼파라미터 튜닝" 절 바로 아래에 아래 형식으로 실측 결과를
추가한다(정확한 숫자는 Step 1/2 실행 결과로 채운다 — 아래는 형식 예시이며 실제
숫자로 교체할 것):

```markdown
**착수 결과(2026-09-01)**: `scripts/compare_regime_ml_baseline.py`/
`scripts/tune_regime_ml_hyperparams.py` 실데이터(20마켓) walk-forward 실행 완료.

- **baseline 비교**: LightGBM pooled weighted kappa=<실측값>(macro F1=<실측값>)
  vs LogisticRegression(L2) pooled weighted kappa=<실측값>(macro F1=<실측값>).
  <결론 — LightGBM/LR 우세 또는 자연변동폭 이내 동일>.
- **하이퍼파라미터 튜닝**: 1단계 최적 {num_leaves=<값>, learning_rate=<값>,
  min_child_samples=<값>}, 2단계 최적 {reg_alpha=<값>, reg_lambda=<값>} 추가.
  튜닝 최적 kappa=<실측값>(baseline <실측값> 대비 델타 <실측값>).
- **결정**: <채택/미채택 — 델타가 자연변동폭(±0.005) 이내면 "미채택(중립)",
  그 이상이면 재현성 확인 후 다음 세션에서 train_regime_ml.py 반영 검토>.
- 구현: `scripts/train_regime_ml.py`(model_factory/preprocess_fold/save_model
  확장), `scripts/compare_regime_ml_baseline.py`,
  `scripts/tune_regime_ml_hyperparams.py`. 설계:
  `docs/superpowers/specs_v1/2026-09-01-regime-ml-baseline-and-tuning-design.md`.
```

"남은 후보(다음 세션)" 목록에서 `(c-2) 로지스틱회귀 baseline 비교 +
LightGBM 하이퍼파라미터 튜닝(문서 우선순위 5번, 미착수)` 부분을
`(c-2) 로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝 — 완료
(2026-09-01, 위 "착수 결과" 참고)`로 수정한다.

### Step 4: 커밋

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: c-2 로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝 실측 결과 반영"
```

---

## 범위 밖

- 튜닝 결과를 `scripts/train_regime_ml.py`의 실제 프로덕션 하이퍼파라미터에
  자동 반영하는 것(사용자와 별도 논의 후 다음 세션).
- AWS 배포(별개 백로그 후보, 이미 미배포 상태로 쌓여있는 이전 라운드들과 함께
  나중에 처리).
- 코스피/코스닥 피처, PBO 프레임워크, 메타 레이블링, CUSUM 이벤트 샘플링.
