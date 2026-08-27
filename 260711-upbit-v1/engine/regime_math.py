"""
engine/regime_math.py

장세 판별 ML 파이프라인이 쓰는 순수 수학/시간프레임 헬퍼. 원래
engine/regime_detector.py(규칙기반 판별기)의 일부였으나, 규칙기반 시스템이
E 작업(2026-08-28)으로 삭제되면서 ML이 실제로 의존하는 이 부분만 이 모듈로
옮겨졌다. 설계 문서: docs/superpowers/specs/2026-08-28-regime-rule-based-removal-design.md
"""
from __future__ import annotations

import pandas as pd

from upbit_data_service import timeframe_duration

HALF_LIFE_DAYS = 1.0
N_MULTIPLIER = 2.5


def half_life_bars_for_timeframe(timeframe: str) -> float:
    """전략의 timeframe(예: 'minutes60', 'days')에서 HALF_LIFE_DAYS에 해당하는 봉 수를
    환산한다. 타임프레임이 달라도 체감 반응속도가 동일하게 유지된다."""
    bar_seconds = timeframe_duration(timeframe).total_seconds()
    return HALF_LIFE_DAYS * 86400.0 / bar_seconds


def _ewm_std_series(returns: pd.Series, half_life_bars: float) -> pd.Series:
    """수익률의 지수가중 표준편차 시계열(변동성 계산 전용) — ewm_volatility의 구현
    디테일."""
    return returns.ewm(halflife=half_life_bars).std()


def ewm_volatility(returns: pd.Series, half_life_bars: float) -> float:
    """수익률의 지수가중 표준편차(가장 최근 값) — 변동성 정규화용.
    분자(모멘텀=EWMA 평균)와 분모가 서로 다른 통계량이어야 score가 카테고리 대표값
    ±2.0(급상승/급하락)에 실제로 도달할 수 있다(EWMA 절댓값평균을 쓰면 삼각부등식으로
    score가 [-1, 1]에 갇히는 버그가 있었다 — 규칙기반 판별기 시절 Task 3 최종리뷰에서
    발견)."""
    return float(_ewm_std_series(returns, half_life_bars).iloc[-1])
