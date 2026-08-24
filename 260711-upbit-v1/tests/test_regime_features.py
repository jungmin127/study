from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.regime_features import volume_confirm, pivot_levels, vpin_score, level_proximity, reversal_gate


def test_volume_confirm_neutral_when_constant():
    trade_value = pd.Series([100.0] * 30)
    result = volume_confirm(trade_value)
    assert result.iloc[-1] == pytest.approx(1.0)


def test_volume_confirm_above_one_when_volume_spikes_above_average():
    trade_value = pd.Series([100.0] * 20 + [500.0])
    result = volume_confirm(trade_value)
    assert result.iloc[-1] > 1.0


def test_volume_confirm_below_one_when_volume_drops_below_average():
    trade_value = pd.Series([100.0] * 20 + [10.0])
    result = volume_confirm(trade_value)
    assert result.iloc[-1] < 1.0


def test_volume_confirm_clipped_to_range():
    trade_value = pd.Series([100.0] * 20 + [100000.0])
    result = volume_confirm(trade_value)
    assert result.iloc[-1] == pytest.approx(1.3)


def test_volume_confirm_neutral_during_warmup():
    trade_value = pd.Series([100.0, 200.0, 50.0])
    result = volume_confirm(trade_value, period=20)
    assert result.iloc[0] == pytest.approx(1.0)
    assert result.iloc[-1] == pytest.approx(1.0)


def test_pivot_levels_first_row_is_nan():
    high = pd.Series([110.0, 112.0, 111.0])
    low = pd.Series([90.0, 95.0, 94.0])
    close = pd.Series([100.0, 105.0, 103.0])
    r1, s1 = pivot_levels(high, low, close)
    assert pd.isna(r1.iloc[0])
    assert pd.isna(s1.iloc[0])


def test_pivot_levels_uses_previous_bar_only():
    # 2번째 행(index=1)의 R1/S1은 index=0의 high/low/close로만 계산돼야 한다.
    high = pd.Series([110.0, 999.0])
    low = pd.Series([90.0, 999.0])
    close = pd.Series([100.0, 999.0])
    r1, s1 = pivot_levels(high, low, close)
    pivot = (110.0 + 90.0 + 100.0) / 3.0
    assert r1.iloc[1] == pytest.approx(pivot * 2 - 90.0)
    assert s1.iloc[1] == pytest.approx(pivot * 2 - 110.0)


def test_vpin_score_nan_during_warmup():
    volume = pd.Series([10.0] * 5)
    close = pd.Series([100.0, 101.0, 99.0, 102.0, 98.0])
    result = vpin_score(volume, close, period=20)
    assert pd.isna(result.iloc[-1])


def test_vpin_score_high_when_one_sided_trend():
    # 거래량 일정, 종가가 매 봉 꾸준히 상승 — 매수 쏠림이 강해야 한다.
    n = 60
    volume = pd.Series([10.0] * n)
    close = pd.Series([100.0 * (1.01 ** i) for i in range(n)])
    result = vpin_score(volume, close, period=10)
    assert result.iloc[-1] > 0.5


def test_vpin_score_bounded_between_zero_and_one():
    n = 60
    rng = np.random.default_rng(seed=1)
    volume = pd.Series(rng.uniform(5.0, 15.0, size=n))
    close = pd.Series(100.0 + np.cumsum(rng.normal(0.0, 1.0, size=n)))
    result = vpin_score(volume, close, period=10)
    valid = result.dropna()
    assert len(valid) > 0
    assert valid.between(0.0, 1.0).all()


def test_level_proximity_high_when_uptrend_close_to_resistance():
    close = pd.Series([100.0])
    raw_score = pd.Series([1.0])       # 상승 방향
    r1 = pd.Series([100.4])            # 저항선이 바로 위
    s1 = pd.Series([90.0])
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] > 0.5


def test_level_proximity_low_when_uptrend_far_from_resistance():
    close = pd.Series([100.0])
    raw_score = pd.Series([1.0])
    r1 = pd.Series([200.0])            # 저항선이 훨씬 위
    s1 = pd.Series([90.0])
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] == pytest.approx(0.0)


def test_level_proximity_ignores_opposite_direction_level():
    # 하락 중(raw_score<0)에는 저항선(R1) 근접은 무시하고 지지선(S1)만 본다.
    close = pd.Series([100.0])
    raw_score = pd.Series([-1.0])
    r1 = pd.Series([100.5])            # 저항선이 바로 위지만 하락 중이라 무시돼야 함
    s1 = pd.Series([200.0])            # 지지선은 훨씬 아래
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] == pytest.approx(0.0)


def test_level_proximity_zero_when_sideways():
    close = pd.Series([100.0])
    raw_score = pd.Series([0.0])
    r1 = pd.Series([100.1])
    s1 = pd.Series([99.9])
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] == pytest.approx(0.0)


def test_reversal_gate_neutral_when_no_risk():
    vpin = pd.Series([0.0])
    proximity = pd.Series([0.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(1.0)


def test_reversal_gate_dampens_when_both_high():
    vpin = pd.Series([1.0])
    proximity = pd.Series([1.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(0.3)


def test_reversal_gate_neutral_when_only_one_high():
    # VPIN만 높고 레벨 근접이 0이면 감쇠하지 않는다(둘 다 성립해야 반전위험으로 인정).
    vpin = pd.Series([1.0])
    proximity = pd.Series([0.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(1.0)


def test_reversal_gate_treats_nan_vpin_as_neutral():
    vpin = pd.Series([float("nan")])
    proximity = pd.Series([1.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(1.0)


def test_reversal_gate_never_below_floor():
    vpin = pd.Series([1.0, 1.0])
    proximity = pd.Series([1.0, 1.0])
    result = reversal_gate(vpin, proximity)
    assert (result >= 0.3).all()
