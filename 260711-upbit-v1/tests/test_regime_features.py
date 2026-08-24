from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.regime_features import volume_confirm


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
