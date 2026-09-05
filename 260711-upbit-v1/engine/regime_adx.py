"""
engine/regime_adx.py

ADX(Average Directional Index)+방향지표(+DI/-DI)로 상승/하락/횡보를
인과적으로(미래 데이터 불필요) 판별한다. Wilder 원 공식의 순수 pandas
구현 — backtrader Cerebro를 거치지 않아 과거 전체 기간 재계산과 최신
시점 계산에 동일한 함수를 쓸 수 있고, 반복 호출 메모리 누수
(docs/superpowers/references의 runner.py 메모리 누수 기록 참고) 위험이
없다. 설계 문서: docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md
"""
from __future__ import annotations

import pandas as pd

PERIOD = 14
ADX_TREND_THRESHOLD = 25.0


def compute_adx_di(df: pd.DataFrame, period: int = PERIOD) -> pd.DataFrame:
    """df는 high/low/close 컬럼을 포함해야 한다. Wilder 스무딩(alpha=1/period)으로
    ADX/plus_di/minus_di 3개 컬럼을 가진 DataFrame을 df와 같은 인덱스로 반환한다.
    앞쪽 워밍업 구간(대략 2*period봉)은 NaN이다."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0)

    alpha = 1.0 / period
    smoothed_tr = tr.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    plus_di = 100 * smoothed_plus_dm / smoothed_tr
    minus_di = 100 * smoothed_minus_dm / smoothed_tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})


def classify_regime(
    adx: float, plus_di: float, minus_di: float, threshold: float = ADX_TREND_THRESHOLD
) -> str | None:
    """단일 시점 값을 "상승"/"하락"/"횡보" 중 하나로 분류한다. adx/plus_di/minus_di
    중 하나라도 NaN이면(워밍업 구간) None을 반환한다."""
    if pd.isna(adx) or pd.isna(plus_di) or pd.isna(minus_di):
        return None
    if adx <= threshold:
        return "횡보"
    return "상승" if plus_di > minus_di else "하락"
