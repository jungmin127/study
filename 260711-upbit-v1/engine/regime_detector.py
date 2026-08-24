"""
engine/regime_detector.py

실시간 장세 판별 — 규칙기반 EWMA 위험조정 모멘텀 스코어로 매 봉 인과적으로
5개 장세 카테고리의 확률벡터를 산출한다. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md
"""
from __future__ import annotations

import math

import pandas as pd

from engine.regime_features import (
    level_proximity,
    pivot_levels,
    reversal_gate,
    vpin_score,
    volume_confirm,
)
from upbit_data_service import timeframe_duration

HALF_LIFE_DAYS = 1.0
TEMPERATURE = 0.1

# 2026-08-23 실측 재보정: KRW-BTC/ETH/XRP 1시간봉(2024-01-01~현재, half_life_bars=24)의
# 실제 score 풀표본(n=69,045) std≈0.11 기준으로 산출. 완만=±1σ 근방(0.15), 급=±p98~99
# 근방(0.35) — 원래 값(±0.7/±2.0)은 이 스케일에서 급상승/급하락이 통계적으로 도달
# 불가능했다(score std≈0.12인데 대표값 2.0은 ~17시그마). TEMPERATURE도 대표값 간격에
# 맞춰 같이 낮췄다(1.0을 그대로 두면 모든 확률이 ~0.2로 뭉개짐, _softmax_categorize
# docstring 참고). 재보정 근거: scripts/regime_backtest.py 실행 결과 +
# docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md의 재보정 노트.
CATEGORY_REFERENCE_SCORES: dict[str, float] = {
    "급하락": -0.35,
    "완만하락": -0.15,
    "횡보": 0.0,
    "완만상승": 0.15,
    "급상승": 0.35,
}


def _softmax_categorize(score: float, temperature: float = TEMPERATURE) -> dict[str, float]:
    """score와 각 카테고리 대표값의 거리에 softmax를 적용해 확률벡터를 만든다.
    합계는 항상 1.0.

    L1 거리 커널이라 |score|가 가장 바깥 대표값(현재 0.35)을 넘어서면 확률벡터가
    포화된다 — score=0.35와 score=1000이 동일한 벡터를 내고, 최댓값 확률은 절대
    0.852를 넘지 않는다(2026-08-23 재보정 실측). CATEGORY_REFERENCE_SCORES를
    재보정할 때는 temperature도 같이 조정해야 한다 — 대표값 간격만 좁히고
    temperature를 그대로 두면 모든 확률이 ~0.2로 뭉개져 신뢰도 수치가 무의미해진다."""
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


def _ewm_series(returns: pd.Series, half_life_bars: float) -> pd.Series:
    """수익률의 지수가중이동평균(모멘텀 계산 전용)."""
    return returns.ewm(halflife=half_life_bars).mean()


def _ewm_std_series(returns: pd.Series, half_life_bars: float) -> pd.Series:
    """수익률의 지수가중 표준편차 시계열(변동성 계산 전용). ewm_volatility와
    compute_regime_probs_series가 공유한다(momentum이 _ewm_series를 공유하는 것과
    대칭 구조)."""
    return returns.ewm(halflife=half_life_bars).std()


def ewm_volatility(returns: pd.Series, half_life_bars: float) -> float:
    """수익률의 지수가중 표준편차(가장 최근 값) — 변동성 정규화용.
    분자(모멘텀=EWMA 평균)와 분모가 서로 다른 통계량이어야 score가 카테고리 대표값
    ±2.0(급상승/급하락)에 실제로 도달할 수 있다(EWMA 절댓값평균을 쓰면 삼각부등식으로
    score가 [-1, 1]에 갇히는 버그가 있었다 — Task 3 최종리뷰에서 발견)."""
    return float(_ewm_std_series(returns, half_life_bars).iloc[-1])


_ADJUSTMENT_COLUMNS = {"volume", "trade_value", "high", "low"}


def compute_regime_probs_series(
    df: pd.DataFrame, half_life_bars: float
) -> list[dict[str, float] | None]:
    """df 전체에 대해 매 봉마다의 regime_probs를 O(n)에 한 번에 계산한다(검증스크립트용
    벡터화 버전 — compute_regime_probs(df.iloc[:t+1], ...)를 매 t마다 반복호출하면
    O(n^2)라 느림). 두 방식이 동일한 결과를 내는지는
    test_compute_regime_probs_series_matches_pointwise_calls로 고정한다.

    df에 volume/trade_value/high/low 컬럼이 전부 있으면 거래량 확인·VPIN 불균형·
    지지저항 근접도로 raw_score를 조정한 adjusted_score를 쓴다(설계 문서:
    docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md).
    컬럼이 없으면(기존 호출자 하위호환) 조정 없이 raw_score를 그대로 쓴다."""
    if df.empty or "close" not in df.columns:
        return []
    min_bars = int(half_life_bars * WARMUP_MULTIPLIER)
    returns = df["close"].pct_change(fill_method=None)
    valid_counts = returns.notna().cumsum()
    momentum_series = _ewm_series(returns, half_life_bars)
    volatility_series = _ewm_std_series(returns, half_life_bars)
    raw_score_series = momentum_series / volatility_series.clip(lower=_MIN_VOLATILITY_FLOOR)

    if _ADJUSTMENT_COLUMNS.issubset(df.columns):
        confirm_series = volume_confirm(df["trade_value"])
        r1_series, s1_series = pivot_levels(df["high"], df["low"], df["close"])
        vpin_series_values = vpin_score(df["volume"], df["close"])
        proximity_series = level_proximity(
            df["close"], raw_score_series, r1_series, s1_series, volatility_series
        )
        gate_series = reversal_gate(vpin_series_values, proximity_series)
        adjusted_score_series = raw_score_series * confirm_series * gate_series
    else:
        adjusted_score_series = raw_score_series

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
        results.append(_softmax_categorize(adjusted_score_series.iloc[i]))
    return results


def compute_regime_probs(df: pd.DataFrame, half_life_bars: float) -> dict[str, float] | None:
    """df: candle_time 오름차순, close 컬럼 포함(get_candles()가 반환하는 형태 그대로).
    워밍업(half_life_bars * WARMUP_MULTIPLIER) 미만이면 None(판단불가) 반환."""
    series = compute_regime_probs_series(df, half_life_bars)
    return series[-1] if series else None


def classify_score_to_category(score: float) -> str:
    """score를 CATEGORY_REFERENCE_SCORES 대표값 사이 중간점 경계로 하드 분류한다
    (검증스크립트가 실현수익률의 "정답" 카테고리를 매길 때 사용 — compute_regime_probs의
    softmax 확률과 달리 단일 라벨만 반환)."""
    ordered = sorted(CATEGORY_REFERENCE_SCORES.items(), key=lambda kv: kv[1])
    for i in range(len(ordered) - 1):
        label, ref = ordered[i]
        _next_label, next_ref = ordered[i + 1]
        boundary = (ref + next_ref) / 2
        if score < boundary:
            return label
    return ordered[-1][0]
