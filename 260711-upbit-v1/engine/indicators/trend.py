from __future__ import annotations

import backtrader as bt


def create_sma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.SMA(data, period=period)


def create_ema(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.EMA(data, period=period)


def create_wma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.WeightedMovingAverage(data, period=period)
