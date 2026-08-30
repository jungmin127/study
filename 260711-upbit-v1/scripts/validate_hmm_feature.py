"""
scripts/validate_hmm_feature.py

Phase 1 검증 스크립트(docs/superpowers/specs/2026-08-30-regime-ml-hmm-feature-design.md
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
