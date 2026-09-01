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
