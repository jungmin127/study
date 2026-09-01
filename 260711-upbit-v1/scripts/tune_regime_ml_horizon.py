"""
scripts/tune_regime_ml_horizon.py

라벨 horizon(N_MULTIPLIER) 그리드서치. 지금 n_bars=60(1시간봉 기준 2.5일)은
HALF_LIFE_DAYS=1.0(2026-08-23 "반응속도 빠름" 사용자 선택) x N_MULTIPLIER=2.5로
계산되는데, 이 중 N_MULTIPLIER는 barrier_k(scripts/select_barrier_k.py)와 달리
kappa 기준으로 한 번도 실측 재탐색된 적이 없다. barrier_k와 같은 방식으로
horizon 후보들을 walk-forward pooled weighted kappa 기준으로 비교한다. 설계:
docs/superpowers/specs/2026-09-01-regime-ml-horizon-tuning-design.md

barrier_k는 5개 후보 전부 현재 프로덕션 값(BARRIER_K)으로 고정한다 — horizon이
길어질수록 만기 전 바리어 터치 확률이 자연히 올라가 라벨 분포가 후보마다
달라지는 걸 알고 있지만, 이번 조사 목적("horizon이 kappa에 영향을 주는가")에는
그 상태 그대로가 유의미한 관측치다. barrier_k와 horizon의 조인트 재탐색은
범위 밖(설계 문서 참고).

select_barrier_k.py와 같은 성격의 1회성 진단 스크립트 — 결과가 채택되면
engine/regime_math.py의 N_MULTIPLIER 상수를 수동으로 갱신한다(이 스크립트가
자동으로 반영하지 않음).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/tune_regime_ml_horizon.py
(5개 후보 재학습 — 단일 학습 실측 소요시간(~40분, c-2 세션 실측) 기준 총
3~4시간 예상)
"""
from __future__ import annotations

from engine.regime_math import N_MULTIPLIER
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

# N_MULTIPLIER를 하드코딩하지 않고 import해서 쓴다 — 이 상수가 실제로 바뀌면
# "<- 현재 프로덕션 값" 표시와 델타 비교 기준이 자동으로 따라가야 하므로
# (2026-09-01 최종 리뷰 Minor 지적).
_CANDIDATES = [0.5, 1.0, 1.5, N_MULTIPLIER, 4.0]
_CURRENT_PRODUCTION_VALUE = N_MULTIPLIER
# 2026-09-01 실측은 전체 5개를 한 번에 돌리지 않고 0.5/4.0만 개별 실행했다
# (단일 학습 실측 소요시간이 설계 추정보다 훨씬 길어 사용자 판단으로 조기 종료
# — docs/regime-ml-backlog.md "horizon(N_MULTIPLIER) 그리드서치" 절 참고).
# 이 파일 그대로(_CANDIDATES 5개 전부) 재실행하면 그 시점 실측 기준 총
# ~10시간 규모 작업이니, 재실행 전 후보 목록을 먼저 줄이는 것을 고려할 것.

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


def main() -> None:
    print(f"=== horizon(N_MULTIPLIER) 그리드서치: 후보 {_CANDIDATES} ===")
    results: list[tuple[float, float | None, float | None]] = []
    for n_multiplier in _CANDIDATES:
        print(f"\n=== N_MULTIPLIER={n_multiplier} 학습 중 ===")
        result = run_training(**_COMMON_KWARGS, n_multiplier=n_multiplier)
        kappa = result.pooled["weighted_kappa"]
        macro_f1 = result.pooled["macro_f1"]
        results.append((n_multiplier, kappa, macro_f1))
        print(f"N_MULTIPLIER={n_multiplier} -> weighted_kappa={kappa}, macro_f1={macro_f1}")

    print("\n=== 최종 비교 ===")
    print(f"{'N_MULTIPLIER':>12}{'weighted kappa':>18}{'macro F1':>12}")
    for n_multiplier, kappa, macro_f1 in results:
        kappa_str = f"{kappa:.3f}" if kappa is not None else "N/A"
        macro_f1_str = f"{macro_f1:.3f}" if macro_f1 is not None else "N/A"
        marker = " <- 현재 프로덕션 값" if n_multiplier == _CURRENT_PRODUCTION_VALUE else ""
        print(f"{n_multiplier:>12}{kappa_str:>18}{macro_f1_str:>12}{marker}")

    valid_results = [(n, k, f) for n, k, f in results if k is not None]
    if not valid_results:
        print("\n모든 후보가 pooled kappa를 계산하지 못했습니다(표본 부족).")
        return

    best_n_multiplier, best_kappa, _ = max(valid_results, key=lambda item: item[1])
    current_kappa = next((k for n, k, _ in results if n == _CURRENT_PRODUCTION_VALUE), None)
    if current_kappa is None:
        print(f"\n최고 kappa: N_MULTIPLIER={best_n_multiplier}(kappa={best_kappa:.3f})")
    else:
        delta = best_kappa - current_kappa
        print(
            f"\n최고 kappa: N_MULTIPLIER={best_n_multiplier}(kappa={best_kappa:.3f}), "
            f"현재값({_CURRENT_PRODUCTION_VALUE}) 대비 델타={delta:+.3f}"
        )


if __name__ == "__main__":
    main()
