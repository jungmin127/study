from __future__ import annotations

import backtrader as bt


def create_bb_upper(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_bb_lower(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_bb_middle(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_atr(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.ATR(data, period=period)
