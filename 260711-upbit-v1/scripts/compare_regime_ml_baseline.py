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
