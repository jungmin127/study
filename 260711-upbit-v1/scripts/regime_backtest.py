"""
scripts/regime_backtest.py

engine.regime_detector.compute_regime_probs_series()가 실제로 쓸모 있는지 과거 캔들로
검증한다. 규칙기반 결정론적 함수라 학습 없이 지금 바로 확인 가능. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from engine.regime_detector import (
    CATEGORY_REFERENCE_SCORES,
    classify_score_to_category,
    compute_regime_probs_series,
    ewm_volatility,
    half_life_bars_for_timeframe,
)
from upbit_data_service import get_candles

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TIMEFRAME = "minutes60"
N_MULTIPLIER = 2.5
VALIDATION_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
VALIDATION_END = datetime.now(timezone.utc)


def _evaluate_market(market: str, half_life_bars: float, n_bars: int) -> dict:
    """market 하나의 (예측 카테고리 x 실제 카테고리) confusion matrix, 실제 카테고리
    전체분포, 확률벡터-실현수익률 상관계수를 반환한다.

    "적중" 기준의 정규화는 판별 스코어(compute_regime_probs)와 동일하게 봉당 스케일로
    맞춘다 — realized_return(N_BARS봉 누적수익률)을 그대로 쓰면 realized_volatility(봉당
    변동성)와 시간 스케일이 안 맞아(대략 sqrt(N_BARS)배) 무의미한 비교가 된다. 정정 내역:
    docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md
    "정정(Task 5 최종리뷰, 2026-08-23)" 문단.
    """
    df = get_candles(market, TIMEFRAME, VALIDATION_START, VALIDATION_END)
    closes = df["close"]
    returns = closes.pct_change(fill_method=None)
    regime_series = compute_regime_probs_series(df, half_life_bars)

    labels = list(CATEGORY_REFERENCE_SCORES.keys())
    confusion: dict[str, dict[str, int]] = {p: {a: 0 for a in labels} for p in labels}
    actual_totals: dict[str, int] = {a: 0 for a in labels}
    expected_scores: list[float] = []
    normalized_realized_values: list[float] = []

    for t in range(len(df) - n_bars):
        probs = regime_series[t]
        if probs is None:
            continue
        predicted = max(probs, key=probs.get)

        future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
        if future_returns.empty or future_returns.isna().any():
            continue
        realized_volatility = ewm_volatility(future_returns, half_life_bars)
        if realized_volatility <= 0:
            continue
        # 봉당 평균수익률 / 봉당 변동성 (== realized_return / n_bars / realized_volatility) —
        # 판별 스코어(EWMA 봉당평균 / EWMA 봉당표준편차)와 동일한 시간 스케일.
        normalized_realized = future_returns.mean() / realized_volatility
        actual = classify_score_to_category(normalized_realized)

        confusion[predicted][actual] += 1
        actual_totals[actual] += 1
        # probs[predicted](=max 확률, 항상 양수인 "확신도")는 부호가 없어 실현값의
        # 방향과 상관시키면 급상승 확신과 급하락 확신이 서로 상쇄돼 신호가 사라진다
        # (Task 5 재리뷰, 2026-08-23). 확률벡터의 기댓값(부호 있는 스코어)을 대신 쓴다.
        expected_score = sum(probs[label] * CATEGORY_REFERENCE_SCORES[label] for label in probs)
        expected_scores.append(expected_score)
        normalized_realized_values.append(normalized_realized)

    correlation = float("nan")
    if len(expected_scores) >= 2:
        correlation = float(np.corrcoef(expected_scores, normalized_realized_values)[0, 1])

    return {
        "confusion": confusion,
        "actual_totals": actual_totals,
        "correlation": correlation,
    }


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={TIMEFRAME}")

    for market in MARKETS:
        print(f"\n=== {market} ({TIMEFRAME}) ===")
        result = _evaluate_market(market, half_life_bars, n_bars)
        confusion = result["confusion"]
        actual_totals = result["actual_totals"]
        correlation = result["correlation"]

        print("  [예측 카테고리별 hit-rate]")
        for label in CATEGORY_REFERENCE_SCORES:
            row = confusion[label]
            total = sum(row.values())
            if total == 0:
                print(f"    {label}: 샘플 없음")
                continue
            hit = row[label]
            hit_rate = hit / total * 100
            print(f"    {label}: {hit}/{total} 적중 ({hit_rate:.1f}%)")

        if math.isnan(correlation):
            print("  [확률벡터-실현수익률 상관계수] 계산 불가(샘플 부족)")
        else:
            print(f"  [확률벡터-실현수익률 상관계수] {correlation:.3f}")

        print("  [confusion matrix] 행=예측, 열=실제")
        header = "    " + "예측\\실제".ljust(10) + "".join(label.ljust(10) for label in CATEGORY_REFERENCE_SCORES)
        print(header)
        for predicted_label in CATEGORY_REFERENCE_SCORES:
            row = confusion[predicted_label]
            row_str = "    " + predicted_label.ljust(10) + "".join(
                str(row[actual_label]).ljust(10) for actual_label in CATEGORY_REFERENCE_SCORES
            )
            print(row_str)

        total_samples = sum(actual_totals.values())
        print(f"  [실제 카테고리 분포(전체 샘플 {total_samples}건 기준)]")
        for label in CATEGORY_REFERENCE_SCORES:
            n = actual_totals[label]
            pct = n / total_samples * 100 if total_samples else 0.0
            print(f"    {label}: {n} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
