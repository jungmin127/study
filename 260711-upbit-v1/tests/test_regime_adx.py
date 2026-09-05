"""
tests/test_regime_adx.py

engine.regime_adx의 ADX/+DI/-DI 계산(compute_adx_di)과 3-라벨 분류
(classify_regime)를 검증한다. compute_adx_di는 backtrader의
DirectionalMovementIndex(같은 Wilder 공식의 검증된 구현)를 golden-test
오라클로 써서 별도 참조 계산 없이 교차검증한다.
"""
from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest

from engine.regime_adx import ADX_TREND_THRESHOLD, PERIOD, classify_regime, compute_adx_di
from tests.signal_fixtures import make_oscillating_df


def test_compute_adx_di_matches_backtrader_directional_movement_index():
    df = make_oscillating_df()
    result = compute_adx_di(df)

    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _Probe(bt.Strategy):
        def __init__(self):
            self.dmi = bt.indicators.DirectionalMovementIndex(self.data, period=PERIOD)
            self.seen: list[tuple[float, float, float]] = []

        def next(self):
            self.seen.append((self.dmi.adx[0], self.dmi.plusDI[0], self.dmi.minusDI[0]))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_Probe)
    bt_seen = cerebro.run()[0].seen

    bt_adx = [v[0] for v in bt_seen]
    bt_plus = [v[1] for v in bt_seen]
    bt_minus = [v[2] for v in bt_seen]
    pandas_adx = result["adx"].dropna().tolist()
    pandas_plus = result["plus_di"].dropna().tolist()
    pandas_minus = result["minus_di"].dropna().tolist()

    tail = 50
    for bt_v, pd_v in zip(bt_adx[-tail:], pandas_adx[-tail:]):
        assert abs(bt_v - pd_v) < 0.5, f"adx mismatch: bt={bt_v} pandas={pd_v}"
    for bt_v, pd_v in zip(bt_plus[-tail:], pandas_plus[-tail:]):
        assert abs(bt_v - pd_v) < 0.5, f"plus_di mismatch: bt={bt_v} pandas={pd_v}"
    for bt_v, pd_v in zip(bt_minus[-tail:], pandas_minus[-tail:]):
        assert abs(bt_v - pd_v) < 0.5, f"minus_di mismatch: bt={bt_v} pandas={pd_v}"


def test_compute_adx_di_warmup_region_is_nan():
    # Wilder 원 공식: TR/DM은 bar 2부터 존재(직전 봉 필요), 1차 스무딩이
    # PERIOD개, 2차(ADX) 스무딩이 다시 PERIOD개를 요구하므로 첫 유효값은
    # 0-index 기준 2*PERIOD - 1(=27)에서 나온다(2*PERIOD가 아님). 이는
    # backtrader golden test로 교차검증된 compute_adx_di의 실제 동작과 일치한다.
    df = make_oscillating_df(n=50)
    result = compute_adx_di(df)
    assert result["adx"].iloc[0:2 * PERIOD - 1].isna().all()
    assert not pd.isna(result["adx"].iloc[2 * PERIOD - 1])


def test_classify_regime_returns_none_when_adx_is_nan():
    assert classify_regime(float("nan"), 20.0, 10.0) is None


def test_classify_regime_returns_sideways_at_and_below_threshold():
    assert classify_regime(ADX_TREND_THRESHOLD, 30.0, 10.0) == "횡보"
    assert classify_regime(10.0, 30.0, 10.0) == "횡보"


def test_classify_regime_returns_uptrend_when_plus_di_dominates_above_threshold():
    assert classify_regime(30.0, 25.0, 10.0) == "상승"


def test_classify_regime_returns_downtrend_when_minus_di_dominates_above_threshold():
    assert classify_regime(30.0, 10.0, 25.0) == "하락"


def test_classify_regime_handles_synthetic_pure_uptrend():
    n = 60
    prices = [100.0 + i * 2 for i in range(n)]
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
    })
    result = compute_adx_di(df)
    last = result.iloc[-1]
    assert classify_regime(last.adx, last.plus_di, last.minus_di) == "상승"


def test_classify_regime_handles_synthetic_pure_downtrend():
    n = 60
    prices = [200.0 - i * 2 for i in range(n)]
    df = pd.DataFrame({
        "candle_time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
    })
    result = compute_adx_di(df)
    last = result.iloc[-1]
    assert classify_regime(last.adx, last.plus_di, last.minus_di) == "하락"
