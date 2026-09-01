"""
scripts/train_regime_ml_meta_label.py

메타 레이블링(c-3, AFML 표준 기법) 학습+측정. 1차 모델(하락/하락아님)이 이미
"하락"이라 분류한 케이스 중 실제로 믿을만한 건지 판단하는 2차 이진분류기를
얹어, engine/regime_ml_calibration.py의 단순 threshold 튜닝만으로는 못 넘은
precision 장벽(최고 threshold=0.70에서도 precision 39%대/recall 10.8%)을
구조적으로 공략한다. 설계:
docs/superpowers/specs/2026-09-01-regime-ml-meta-labeling-design.md

1차 모델은 한 번만 학습(재학습 없음) — run_training(collect_oof=True)로 얻은
전체 기간 out-of-fold 예측을 메타모델의 학습 재료로 재사용한다. 메타모델
자체는 1차처럼 다시 워크포워드하지 않고, 후보 집합(1차가 "하락"이라 분류한
proba_down>=0.5 전부)을 candle_time 기준 시간순 70/30으로 나눠 학습/평가한다
(사용자 확인 — 완전한 중첩 워크포워드는 범위 밖).

**비교 대상은 반드시 메타 테스트 구간과 같은 모집단이어야 한다**(2026-09-01
최종 리뷰 Important 지적) — 1차만 썼을 때의 precision/recall도 전체 OOF가
아니라 메타 테스트 구간(meta_test)에서 직접 재계산해서 비교한다. 전체 OOF
기준 baseline(예: threshold=0.70 precision=0.39)과 메타 테스트 구간의 결과를
그냥 나란히 놓으면 두 숫자의 분모(recall)와 모집단(precision)이 달라 비교가
성립하지 않는다.

**한계**: candle_time 기준 시간순 분할은 5개 fold(서로 다른 시점까지 학습된
서로 다른 1차 모델)가 섞인 OOF를 다시 시간순으로 자르는 것이라, 메타 학습
구간과 메타 테스트 구간이 서로 다른(그리고 다르게 보정된) 1차 모델들의
예측을 나눠 갖는다. 메타모델이 신호를 못 찾은 게 "74개 피처에 신호가
없어서"인지 "시점별로 다른 1차 모델의 확신도 스케일이 달라서"인지는 이번
실험만으로 완전히 분리되지 않는다(완전한 중첩 워크포워드를 안 한 대가).

**메모리**: 1차 OOF 프레임이 실측 약 39만 행 × 76컬럼(float64 기준 약
240MB)이라 로컬에서만 실행할 것 — 그리드서치와 같은 이유로 AWS 서버
(2GB RAM)에서 돌리지 않는다(2026-08-16 결정, docs/regime-ml-backlog.md
참고).

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
_MIN_SPLIT_SAMPLES = MIN_TRAIN_SAMPLES  # 1차와 동일 기준 재사용(상수 자체를 그대로 import)
_THRESHOLD_GRID = [round(0.30 + 0.05 * i, 2) for i in range(13)]  # 0.30~0.90


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

    if meta_train_y.nunique() < 2 or meta_test_y.nunique() < 2:
        print("메타 학습/테스트 구간의 라벨이 단일 클래스뿐입니다 — 중단합니다.")
        return

    meta_test_base_rate = float(meta_test_y.mean())
    print(f"메타 테스트 구간 실제 '하락' 비율(기저율) = {meta_test_base_rate:.3f}")

    meta_model = _meta_model_factory()
    meta_model.fit(meta_train_X, meta_train_y)

    meta_proba = meta_model.predict_proba(meta_test_X)[:, list(meta_model.classes_).index(1)]
    meta_true_labels = ["하락" if v == 1 else "하락아님" for v in meta_test_y]
    meta_table = compute_precision_recall_table(meta_true_labels, meta_proba.tolist(), _THRESHOLD_GRID)

    # 비교 대상(1차만 썼을 때의 precision/recall)도 반드시 메타 테스트와 같은
    # 모집단(meta_test)에서 재계산한다 — 전체 OOF 기준 수치와 비교하면 분모
    # (recall)와 모집단(precision) 자체가 달라져 비교가 성립하지 않는다
    # (2026-09-01 최종 리뷰 Important 지적).
    primary_table = compute_precision_recall_table(
        meta_test["true_label"].tolist(), meta_test["proba_down"].tolist(), _THRESHOLD_GRID
    )

    print("\n=== threshold별 precision/recall 비교(둘 다 메타 테스트 구간 기준) ===")
    print(f"{'threshold':>10}{'1차만 precision':>18}{'1차만 recall':>15}"
          f"{'1차+메타 precision':>20}{'1차+메타 recall':>17}{'메타 n_predicted':>18}")
    for primary_row, meta_row in zip(primary_table, meta_table):
        print(
            f"{primary_row['threshold']:>10.2f}"
            f"{primary_row['precision']:>18.3f}{primary_row['recall']:>15.3f}"
            f"{meta_row['precision']:>20.3f}{meta_row['recall']:>17.3f}"
            f"{meta_row['n_predicted_down']:>18}"
        )


if __name__ == "__main__":
    main()
