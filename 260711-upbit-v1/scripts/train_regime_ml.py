"""
scripts/train_regime_ml.py

장세 판별기 ML 학습+워크포워드 검증 파이프라인. Triple Barrier Method(하락 vs
하락아님 이진분류)로 레이블링하고, fold별/전체 풀링/마켓별 분류지표(macro F1/
weighted kappa/confusion matrix/클래스별 precision·recall)를 콘솔에 리포트한다.
이전(5단계+상관계수) 버전은 2026-08-29 문제 재정의에서 3단계로, 3단계는
2026-08-30 실측(pooled weighted kappa 0.072→0.0914, 14마켓 기준)으로 이진분류로
교체됐다. 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import lightgbm as lgb
import numpy as np
import pandas as pd

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_calibration import (
    compute_precision_recall_table,
    fit_isotonic_breakpoints,
    select_threshold_for_target_precision,
)
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_cross_sectional import compute_cross_sectional_features
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_features import build_feature_matrix
from engine.regime_ml_labels import (
    CATEGORY_LABELS,
    compute_sample_uniqueness_weights,
    compute_triple_barrier_labels,
)
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
# docs/ML_Regime_Switching_Additional_Improvements.md 1-2절 예시값("예 55%+")을
# 그대로 채택 — "하락" 경고의 신뢰도를 55% 이상으로 끌어올리는 게 목표.
TARGET_DOWN_PRECISION = 0.55
_THRESHOLD_GRID = [round(0.30 + 0.05 * i, 2) for i in range(13)]  # 0.30~0.90
MODEL_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"


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
    """마켓별로 데이터를 한 번씩만 로드/피처화(fold마다 반복하지 않음)하고, 워크포워드
    fold 루프를 돌며 모델을 학습·평가한다(기본은 LightGBM, model_factory로 교체
    가능). Triple Barrier 레이블(하락 vs 하락아님 이진분류)로 학습하고, fold별 +
    전체 풀링 + 마켓별 분류지표(macro F1/weighted kappa/confusion/precision·recall)를
    계산해 TrainingResult(reports/pooled/per_market)로 반환한다. save_model=True
    (기본값)이면 마지막으로 성공한 fold의 모델을 model_output_dir에 저장한다.
    표본이 min_train_samples 미만이거나 테스트 표본이 없는 fold는 건너뛴다.

    model_factory/preprocess_fold로 LightGBM 대신 다른 분류기를 끼워 비교/튜닝
    스크립트에서 재사용할 수 있다(scripts/compare_regime_ml_baseline.py,
    scripts/tune_regime_ml_hyperparams.py 참고). save_model=False면 모델 파일/
    JSON 사이드카를 저장하지 않는다(data/regime_ml_models/ 오염 방지).

    n_multiplier로 라벨 horizon(n_bars = half_life_bars * n_multiplier)을 바꿔
    scripts/tune_regime_ml_horizon.py에서 재사용할 수 있다. half_life_bars(피처
    EWM 윈도우)는 영향받지 않는다.

    save_model=True는 모든 파라미터가 기본값(기본 LightGBM 팩토리, 기본 전처리,
    기본 n_multiplier)일 때만 허용한다 — model_factory/preprocess_fold/n_multiplier
    중 하나라도 바뀐 실험용 모델이 data/regime_ml_models/에 저장되면
    backend/regime_ml_service.py가 파일명 정렬로 최신 모델을 서빙 모델로 골라
    실서비스에 실험 결과가 섞여 들어갈 수 있다(2026-09-01 최종 리뷰 Important
    지적, ValueError로 가드)."""
    if save_model and (
        model_factory is not _default_lgbm_factory
        or preprocess_fold is not None
        or n_multiplier != N_MULTIPLIER
    ):
        raise ValueError(
            "save_model=True는 model_factory/preprocess_fold/n_multiplier가 전부 기본값일 "
            "때만 허용됩니다 — 실험용 설정을 쓸 때는 save_model=False를 명시하세요(실험용 "
            "모델이 data/regime_ml_models/에 저장돼 서빙 모델로 오인되는 걸 막기 위함)."
        )
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * n_multiplier)
    embargo = timeframe_duration(timeframe) * n_bars

    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={timeframe}, barrier_k={barrier_k}")

    # cross-sectional(베타중립) 피처는 마켓 하나만으로 계산할 수 없다 — 모든 마켓의
    # 수익률을 먼저 다 모아야 "같은 시각 다른 마켓 대비 지금 이 코인이 어떤지"를 구할
    # 수 있으므로, raw 데이터 로드를 피처화보다 먼저 전체 마켓에 대해 끝낸다.
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
        # cross_sectional[market]은 candle_time(전체 마켓 합집합)을 인덱스로 갖는다.
        # 이 마켓 고유의 candle_time 순서로 reindex하면 raw_df/features_df와 정확히
        # 같은 행 수·같은 순서가 보장된다(reindex는 대상 인덱스 길이만큼 결과를
        # 만들며, 없는 시점은 NaN으로 채운다 — 행이 늘거나 줄지 않는다). 두 프레임
        # 모두 위치 기반(0..n-1)으로 reset한 뒤 axis=1 concat해 위치로 정렬하고,
        # 마지막에 원래 인덱스(raw_df.index == features_df.index)를 복원한다.
        cs_df = cross_sectional[market].reindex(raw_df["candle_time"]).reset_index(drop=True)
        features_df = pd.concat([features_df.reset_index(drop=True), cs_df], axis=1)
        features_df.index = raw_df.index
        labels = compute_triple_barrier_labels(raw_df, half_life_bars, n_bars, barrier_k)
        # AFML sample uniqueness 가중치 — 겹치는(=동시활성) 라벨이 많은 구간을
        # LightGBM이 과도하게 반복학습하지 않도록 class_weight="balanced"와는 별개
        # 축으로 sample_weight에 곱해 함께 쓴다(engine/regime_ml_labels.py 참고).
        weights = compute_sample_uniqueness_weights(labels, n_bars)
        market_frames[market] = (raw_df["candle_time"], features_df, labels, weights)

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
    all_proba_down: list[float] = []

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

        if len(train_y) < min_train_samples or test_y.empty:
            print(f"[fold {fold.fold_index}] 표본 부족(train={len(train_y)}, test={len(test_y)}) — 건너뜀")
            continue

        test_markets = test_X["market"].astype(str).to_numpy()

        preprocess = preprocess_fold or _default_preprocess
        train_X_fit, test_X_fit = preprocess(train_X, test_X)

        model = model_factory()
        model.fit(train_X_fit, train_y, sample_weight=train_w.to_numpy())
        last_model = model
        last_class_order = [str(c) for c in model.classes_]
        last_fold_index = fold.fold_index

        if hasattr(model, "feature_importances_"):
            importances = dict(zip(train_X_fit.columns, model.feature_importances_))
            top_features = sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:15]
        else:
            top_features = []

        predictions = model.predict(test_X_fit)
        proba_matrix = model.predict_proba(test_X_fit)
        down_col = list(model.classes_).index("하락")
        proba_down = proba_matrix[:, down_col].tolist()
        true_values = test_y.to_numpy()

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
        all_proba_down.extend(proba_down)

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

    # all_true가 비어있으면(모든 fold가 표본 부족으로 스킵) last_model도 None이라
    # 아래 sidecar 저장 블록이 통째로 스킵된다 — IsotonicRegression.fit이 표본 0개를
    # 거부하므로 여기서도 같은 조건으로 계산을 건너뛴다.
    threshold_table: list[dict] = []
    decision_threshold = 0.5
    calibration_breakpoints: list[list[float]] = []
    if all_true:
        threshold_table = compute_precision_recall_table(all_true, all_proba_down, _THRESHOLD_GRID)
        decision_threshold = select_threshold_for_target_precision(threshold_table, TARGET_DOWN_PRECISION)
        calibration_breakpoints = fit_isotonic_breakpoints(all_true, all_proba_down)
        _print_threshold_table(threshold_table, decision_threshold)

    _print_aggregate_summary(reports, pooled_metrics, per_market_metrics)

    if last_model is not None and save_model:
        model_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_name = f"regime_ml_{timestamp}"
        last_model.booster_.save_model(str(model_output_dir / f"{base_name}.txt"))

        sidecar = {
            "markets": markets,
            "labeling_method": "triple_barrier",
            "barrier_k": barrier_k,
            "n_multiplier": n_multiplier,
            "n_bars": n_bars,
            "classes": last_class_order,
            "fold_index": last_fold_index,
            "decision_threshold": decision_threshold,
            "calibration_breakpoints": calibration_breakpoints,
            "threshold_table": threshold_table,
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

    return TrainingResult(reports=reports, pooled=pooled_metrics, per_market=per_market_metrics)


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


def _print_threshold_table(table: list[dict], decision_threshold: float) -> None:
    print(f"\n=== Threshold별 '하락' precision/recall (목표 precision={TARGET_DOWN_PRECISION}) ===")
    for row in table:
        marker = " <- 채택" if row["threshold"] == decision_threshold else ""
        print(
            f"  threshold={row['threshold']:.2f}  precision={row['precision']:.3f}  "
            f"recall={row['recall']:.3f}  n_predicted_down={row['n_predicted_down']}{marker}"
        )


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


if __name__ == "__main__":
    main()
