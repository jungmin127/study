"""
engine/regime_detector.py

실시간 장세 판별 — 규칙기반 EWMA 위험조정 모멘텀 스코어로 매 봉 인과적으로
5개 장세 카테고리의 확률벡터를 산출한다. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md
"""
from __future__ import annotations

import math

import pandas as pd

from upbit_data_service import timeframe_duration

HALF_LIFE_DAYS = 1.0
TEMPERATURE = 1.0

CATEGORY_REFERENCE_SCORES: dict[str, float] = {
    "급하락": -2.0,
    "완만하락": -0.7,
    "횡보": 0.0,
    "완만상승": 0.7,
    "급상승": 2.0,
}


def _softmax_categorize(score: float, temperature: float = TEMPERATURE) -> dict[str, float]:
    """score와 각 카테고리 대표값의 거리에 softmax를 적용해 확률벡터를 만든다.
    합계는 항상 1.0."""
    labels = list(CATEGORY_REFERENCE_SCORES.keys())
    neg_distances = [
        -abs(score - CATEGORY_REFERENCE_SCORES[label]) / temperature for label in labels
    ]
    max_val = max(neg_distances)
    exp_vals = [math.exp(v - max_val) for v in neg_distances]
    total = sum(exp_vals)
    return {label: exp_val / total for label, exp_val in zip(labels, exp_vals)}


def half_life_bars_for_timeframe(timeframe: str) -> float:
    """전략의 timeframe(예: 'minutes60', 'days')에서 HALF_LIFE_DAYS에 해당하는 봉 수를
    환산한다. 타임프레임이 달라도 체감 반응속도가 동일하게 유지된다."""
    bar_seconds = timeframe_duration(timeframe).total_seconds()
    return HALF_LIFE_DAYS * 86400.0 / bar_seconds


WARMUP_MULTIPLIER = 5.0
_MIN_VOLATILITY_FLOOR = 1e-6


def _ewm_series(returns: pd.Series, half_life_bars: float, abs_values: bool = False) -> pd.Series:
    series = returns.abs() if abs_values else returns
    return series.ewm(halflife=half_life_bars).mean()


def ewm_volatility(returns: pd.Series, half_life_bars: float) -> float:
    """수익률 절댓값의 지수가중이동평균(가장 최근 값) — 변동성 정규화용."""
    return float(_ewm_series(returns, half_life_bars, abs_values=True).iloc[-1])


def compute_regime_probs_series(
    df: pd.DataFrame, half_life_bars: float
) -> list[dict[str, float] | None]:
    """df 전체에 대해 매 봉마다의 regime_probs를 O(n)에 한 번에 계산한다(검증스크립트용
    벡터화 버전 — compute_regime_probs(df.iloc[:t+1], ...)를 매 t마다 반복호출하면
    O(n^2)라 느림). 두 방식이 동일한 결과를 내는지는
    test_compute_regime_probs_series_matches_pointwise_calls로 고정한다."""
    min_bars = int(half_life_bars * WARMUP_MULTIPLIER)
    returns = df["close"].pct_change(fill_method=None)
    valid_counts = returns.notna().cumsum()
    momentum_series = _ewm_series(returns, half_life_bars)
    volatility_series = _ewm_series(returns, half_life_bars, abs_values=True)

    results: list[dict[str, float] | None] = []
    for i in range(len(df)):
        if int(valid_counts.iloc[i]) < min_bars:
            results.append(None)
            continue
        momentum = momentum_series.iloc[i]
        volatility = volatility_series.iloc[i]
        if pd.isna(momentum) or pd.isna(volatility):
            results.append(None)
            continue
        score = momentum / max(volatility, _MIN_VOLATILITY_FLOOR)
        results.append(_softmax_categorize(score))
    return results


def compute_regime_probs(df: pd.DataFrame, half_life_bars: float) -> dict[str, float] | None:
    """df: candle_time 오름차순, close 컬럼 포함(get_candles()가 반환하는 형태 그대로).
    워밍업(half_life_bars * WARMUP_MULTIPLIER) 미만이면 None(판단불가) 반환."""
    series = compute_regime_probs_series(df, half_life_bars)
    return series[-1] if series else None
