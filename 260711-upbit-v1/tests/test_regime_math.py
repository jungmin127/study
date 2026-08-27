"""
tests/test_regime_math.py

engine/regime_math.py — half_life_bars_for_timeframe/ewm_volatility 검증.
engine/regime_detector.py(규칙기반, E 작업으로 삭제됨)에 있던 동명 함수 테스트를
그대로 옮겨왔다. 이 두 함수는 ML 파이프라인(engine/regime_ml_labels.py,
backend/regime_ml_service.py, scripts/train_regime_ml.py)이 실제로 의존한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.regime_math import ewm_volatility, half_life_bars_for_timeframe


def test_half_life_bars_for_timeframe_days_is_one():
    assert half_life_bars_for_timeframe("days") == pytest.approx(1.0)


def test_half_life_bars_for_timeframe_minutes60_is_24():
    assert half_life_bars_for_timeframe("minutes60") == pytest.approx(24.0)


def test_half_life_bars_for_timeframe_minutes15_is_96():
    assert half_life_bars_for_timeframe("minutes15") == pytest.approx(96.0)


def test_ewm_volatility_of_constant_returns_is_near_zero():
    """수익률이 일정하면 지수가중 표준편차는 0에 가까워야 한다(EWMA 절댓값평균이던
    구버전에서는 이 값이 0.01이 나왔지만, 삼각부등식으로 score가 [-1, 1]에 갇히는 버그의
    원인이었다 — 표준편차 기반으로 바뀐 지금은 변동성이 없는 시계열의 분산은 0이 맞다)."""
    returns = pd.Series([0.01] * 30)
    vol = ewm_volatility(returns, half_life_bars=5.0)
    assert vol == pytest.approx(0.0, abs=1e-9)


def test_ewm_volatility_matches_pandas_ewm_std():
    """ewm_volatility가 pandas의 지수가중 표준편차와 동일한 값을 내는지 직접 대조한다."""
    rng = np.random.default_rng(seed=42)
    returns = pd.Series(rng.normal(loc=0.0, scale=0.02, size=30))
    vol = ewm_volatility(returns, half_life_bars=5.0)
    expected = float(returns.ewm(halflife=5.0).std().iloc[-1])
    assert vol == pytest.approx(expected, rel=1e-9)
