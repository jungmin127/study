"""
scripts/tune_regime_ml_hyperparams.py

LightGBM 하이퍼파라미터 축소 그리드서치. docs/ML_Regime_Switching_Additional_
Improvements.md 4절 우선순위 5번 — 지금 학습(scripts/train_regime_ml.py)은
objective/class_weight/importance_type/random_state 외 전부 sklearn 기본값을
쓴다. 2단계로 나눠 조합 폭발을 억제한다: 1단계(num_leaves/learning_rate/
min_child_samples)로 큰 지렛대를 먼저 찾고, 2단계(reg_alpha/reg_lambda)로
1단계 최적값 위에 정규화를 추가 탐색한다. 실제 그리드 크기(원래 27+9조합 →
두 차례 축소돼 지금은 2+1조합)는 아래 "실행 전/중 그리드 축소" 이력 참고 —
select_barrier_k.py와 같은 성격의 1회성 진단 스크립트로, 결과가 채택되면
train_regime_ml.py의 LightGBM 생성 코드를 수동으로 갱신한다(이 스크립트가
자동으로 반영하지 않음).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/tune_regime_ml_hyperparams.py
(현재 그리드 기준 baseline 포함 총 4회 재학습 — 시작 시 baseline 1회를 먼저
재고 예상 총 소요시간을 출력한다. 이 값이 과도하게 크면(예: 60분 초과) 그리드를
줄이라는 안내만 출력하고 자동으로 중단하지는 않는다 — 백그라운드 실행 가능하므로.)

2026-09-01 실행 전 그리드 축소(1차): 과거 모델 파일 타임스탬프로 역산한 단일 학습
(5-fold, 20마켓) 소요시간이 15~30분이라, 원래 그리드(27+9=36조합)는 baseline
포함 총 9~12시간이 걸릴 것으로 추정됐다(설계 문서의 "20~30분" 추정은 과소평가).
1차로 1단계 num_leaves x learning_rate 6조합(min_child_samples 기본값 20 고정),
2단계 reg_alpha/reg_lambda 각 2개 4조합으로 축소(baseline+10조합=11회, 예상 2~4시간).

2026-09-01 실행 중 2차 축소: 실제 baseline 1회 실행이 40분을 넘겨 총 예상이
6~8시간으로 늘어나(1차 추정도 과소평가였음), 실행 중이던 프로세스를 중단하고
1단계를 num_leaves=31(기본값) 고정 x learning_rate(0.05/0.1) 2조합으로, 2단계를
reg_alpha=0.5/reg_lambda=0.5 1조합으로 재축소했다(baseline+3조합=4회, 예상
2.5~3시간, 실측 단일 학습 시간 ~40분 기준).
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

_STAGE1_NUM_LEAVES = [31]  # 기본값 고정 — 2026-09-01 2차 그리드 축소로 이번엔 탐색하지 않음
_STAGE1_LEARNING_RATE = [0.05, 0.1]
_STAGE1_MIN_CHILD_SAMPLES = [20]  # 기본값 고정 — 1차 그리드 축소로 탐색하지 않음
_STAGE2_REG_ALPHA = [0.5]  # 2차 그리드 축소로 단일값만 추가 확인
_STAGE2_REG_LAMBDA = [0.5]
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
    if baseline_kappa is None:
        print("baseline 실행이 pooled kappa를 계산하지 못했습니다(표본 부족 또는 단일 클래스만 존재) — 중단합니다.")
        return
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

    if best_stage1_kappa == float("-inf"):
        print("\n1단계 모든 조합이 pooled kappa를 계산하지 못했습니다(표본 부족 또는 단일 클래스만 존재) — 중단합니다.")
        return

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
