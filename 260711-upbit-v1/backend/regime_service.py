"""
backend/regime_service.py

engine.regime_detector.compute_regime_probs_series()의 결과를 마켓 단위로 평가한다.
scripts/regime_backtest.py(CLI)와 GET /api/v1/regime/backtest(웹 API)가 이 함수를
공유한다 — 계산 로직이 두 곳으로 갈라지면 스케일 버그가 재발할 위험이 있다(과거 실제로
2번 발생: 판별스코어-실현수익률 스케일 불일치, 부호없는 확신도로 상관계수 계산). 설계
문서: docs/superpowers/specs/2026-08-23-regime-detector-web-dashboard-design.md
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

N_MULTIPLIER = 2.5


def _to_utc_iso(value: datetime) -> str:
    """candle_time이 tz 정보 없이 UTC 값만 담고 있을 수 있어, API 응답에 넘기기 전에 항상
    UTC 오프셋을 명시한다. backend/main.py의 동명 헬퍼와 같은 이유로 존재하되, 문자열이
    아니라 pandas Timestamp를 직접 받는다(pandas Timestamp도 datetime 서브클래스라
    replace/isoformat을 그대로 쓸 수 있다) — backend.main이 backend.regime_service를
    import하므로, 반대 방향으로 backend.main의 헬퍼를 가져오면 순환참조가 생긴다."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def evaluate_market(market: str, timeframe: str, start: datetime, end: datetime) -> dict:
    """market 하나에 대해 봉별 예측 카테고리 시계열 + confusion matrix + 상관계수 +
    실제분포를 반환한다. "적중" 판정의 정규화(봉당 스케일 맞추기)는
    docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md
    "정정(Task 5 최종리뷰, 2026-08-23)" 문단 참고.

    반환값:
      half_life_bars, n_bars: 이 timeframe에 대해 실제로 쓰인 값(디버깅/표시용)
      candles: [{time, open, high, low, close, predicted_category}, ...] — 워밍업 미달
        구간은 predicted_category가 None. 마지막 n_bars 구간도 "정답"을 매길 미래 데이터가
        없어 confusion/actual_totals 집계에서는 빠지지만, candles에는 예측값이 그대로 담긴다.
      confusion: {예측카테고리: {실제카테고리: 건수}}
      actual_totals: {실제카테고리: 건수}
      correlation: 확률벡터 기댓값과 정규화된 실현수익률의 상관계수(샘플 2건 미만이면 None)
    """
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)

    df = get_candles(market, timeframe, start, end)
    closes = df["close"]
    returns = closes.pct_change(fill_method=None)
    regime_series = compute_regime_probs_series(df, half_life_bars)

    labels = list(CATEGORY_REFERENCE_SCORES.keys())
    confusion: dict[str, dict[str, int]] = {p: {a: 0 for a in labels} for p in labels}
    actual_totals: dict[str, int] = {a: 0 for a in labels}
    expected_scores: list[float] = []
    normalized_realized_values: list[float] = []

    candles: list[dict] = []
    for i, row in enumerate(df.itertuples()):
        probs = regime_series[i]
        predicted_category = max(probs, key=probs.get) if probs is not None else None
        candles.append({
            "time": _to_utc_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "predicted_category": predicted_category,
        })

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
        normalized_realized = future_returns.mean() / realized_volatility
        actual = classify_score_to_category(normalized_realized)

        confusion[predicted][actual] += 1
        actual_totals[actual] += 1
        expected_score = sum(probs[label] * CATEGORY_REFERENCE_SCORES[label] for label in probs)
        expected_scores.append(expected_score)
        normalized_realized_values.append(normalized_realized)

    correlation: float | None = None
    if len(expected_scores) >= 2:
        computed = float(np.corrcoef(expected_scores, normalized_realized_values)[0, 1])
        if not math.isnan(computed):
            correlation = computed

    return {
        "half_life_bars": half_life_bars,
        "n_bars": n_bars,
        "candles": candles,
        "confusion": confusion,
        "actual_totals": actual_totals,
        "correlation": correlation,
    }
