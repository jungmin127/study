"""
scripts/train_regime_ml.py

장세 판별기 ML 전환 — LightGBM 학습+워크포워드 검증 파이프라인. scripts/regime_backtest.py
(규칙기반 검증 CLI)와 나란히 비교할 수 있도록 같은 콘솔 리포트 형식(카테고리별 hit-rate/
confusion matrix/상관계수)을 쓴다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md

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
