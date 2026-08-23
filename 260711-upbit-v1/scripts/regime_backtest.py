"""
scripts/regime_backtest.py

engine.regime_detector.compute_regime_probs_series()가 실제로 쓸모 있는지 과거 캔들로
검증한다. 규칙기반 결정론적 함수라 학습 없이 지금 바로 확인 가능. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py
"""
from __future__ import annotations

from datetime import datetime, timezone

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


def _evaluate_market(market: str, half_life_bars: float, n_bars: int) -> dict[str, dict[str, int]]:
    """market 하나의 카테고리별 hit/total 카운트를 반환한다."""
    df = get_candles(market, TIMEFRAME, VALIDATION_START, VALIDATION_END)
    closes = df["close"]
    returns = closes.pct_change(fill_method=None)
    regime_series = compute_regime_probs_series(df, half_life_bars)

    counts: dict[str, dict[str, int]] = {
        label: {"hit": 0, "total": 0} for label in CATEGORY_REFERENCE_SCORES
    }

    for t in range(len(df) - n_bars):
        probs = regime_series[t]
        if probs is None:
            continue
        predicted = max(probs, key=probs.get)

        future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
        if future_returns.empty or future_returns.isna().any():
            continue
        realized_return = closes.iloc[t + n_bars] / closes.iloc[t] - 1.0
        realized_volatility = ewm_volatility(future_returns, half_life_bars)
        if realized_volatility <= 0:
            continue
        normalized_realized = realized_return / realized_volatility
        actual = classify_score_to_category(normalized_realized)

        counts[predicted]["total"] += 1
        if actual == predicted:
            counts[predicted]["hit"] += 1

    return counts


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    print(f"half_life_bars={half_life_bars:.1f}, n_bars={n_bars}, timeframe={TIMEFRAME}")

    for market in MARKETS:
        print(f"\n=== {market} ({TIMEFRAME}) ===")
        counts = _evaluate_market(market, half_life_bars, n_bars)
        for label in CATEGORY_REFERENCE_SCORES:
            c = counts[label]
            if c["total"] == 0:
                print(f"  {label}: 샘플 없음")
                continue
            hit_rate = c["hit"] / c["total"] * 100
            print(f"  {label}: {c['hit']}/{c['total']} 적중 ({hit_rate:.1f}%)")


if __name__ == "__main__":
    main()
