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
# scripts/select_barrier_k.py로 2026-08-29 실측(14마켓, 2024-01-01~현재) 최초 결정한
# 값은 5.5(클래스 분포 균형 기준 — 하락 35.3%/횡보 31.7%/상승 33.0%, 최대편차 2.0%p).
# 2026-08-30 실제 walk-forward 성능(kappa) 기준 재탐색(4.0/4.75/5.5/6.25/7.0 grid)에서
# 6.25가 pooled weighted kappa 0.0603→0.0658로 더 좋아 채택(분포 균형은 성능과 별개
# 기준이었다는 뜻). FEAR_GREED_CMC 제거(engine/regime_ml_features.py 참고)와 조합하면
# 0.0603→0.0724.
BARRIER_K = 6.25
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
