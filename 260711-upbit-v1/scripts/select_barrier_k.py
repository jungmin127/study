"""
scripts/select_barrier_k.py

Triple Barrier 라벨링의 변동성 배수 k를 한 번 정하기 위한 그리드서치. fold별로
다시 정하지 않고 파이프라인 상수로 고정하는 하이퍼파라미터라(design 문서 A절 참고),
전체 데이터를 한 번 스캔해 3클래스(하락/횡보/상승) 분포가 가장 균형에 가까운 k를
찾는다. 이 스크립트는 재실행용으로 남겨두되(데이터가 크게 바뀌면 재조정), 매
학습(scripts/train_regime_ml.py)마다 자동으로 다시 돌리지는 않는다 — 결과를
scripts/train_regime_ml.py의 BARRIER_K 상수로 수동 반영한다.

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/select_barrier_k.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_constants import TRAINING_MARKETS
from engine.regime_ml_data import load_market_training_data
from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels

TIMEFRAME = "minutes60"
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)
CANDIDATE_KS = [1.0, 2.0, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0, 10.0]


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)

    all_dfs = []
    for market in TRAINING_MARKETS:
        print(f"로딩 중: {market}...")
        df = load_market_training_data(market, TIMEFRAME, START, END)
        all_dfs.append(df)
        print(f"  -> {len(df)}행")

    print(f"\n{len(all_dfs)}개 마켓 로드 완료. half_life_bars={half_life_bars:.1f}, n_bars={n_bars}")
    print(f"{'k':>5} | " + " | ".join(f"{label:>6}" for label in CATEGORY_LABELS) + " | 최대편차")

    best_k = None
    best_deviation = float("inf")
    for k in CANDIDATE_KS:
        counts = {label: 0 for label in CATEGORY_LABELS}
        for df in all_dfs:
            labels = compute_triple_barrier_labels(df, half_life_bars, n_bars, k)
            for label in CATEGORY_LABELS:
                counts[label] += int((labels == label).sum())
        total = sum(counts.values())
        shares = {label: (counts[label] / total if total else 0.0) for label in CATEGORY_LABELS}
        max_deviation = max(abs(share - 1 / 3) for share in shares.values())
        print(
            f"{k:>5} | " + " | ".join(f"{shares[label]*100:5.1f}%" for label in CATEGORY_LABELS)
            + f" | {max_deviation*100:5.1f}%p"
        )
        if max_deviation < best_deviation:
            best_deviation = max_deviation
            best_k = k

    print(f"\n채택: k={best_k} (최대편차 {best_deviation*100:.1f}%p)")


if __name__ == "__main__":
    main()
