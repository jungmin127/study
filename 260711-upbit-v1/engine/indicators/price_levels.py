"""
engine/indicators/price_levels.py

가격대(지지/저항) 계열 지표 — 피보나치 되돌림, Pivot Points.
둘 다 캔들의 고가/저가/종가만으로 계산되는 순수 파생 지표라 보조 마켓·틱 데이터가 필요 없다.
"""
from __future__ import annotations

import backtrader as bt


def create_fib_382(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    hh = bt.indicators.Highest(data.high, period=period)
    ll = bt.indicators.Lowest(data.low, period=period)
    return hh - (hh - ll) * 0.382


def create_fib_500(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    hh = bt.indicators.Highest(data.high, period=period)
    ll = bt.indicators.Lowest(data.low, period=period)
    return hh - (hh - ll) * 0.5


def create_fib_618(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    hh = bt.indicators.Highest(data.high, period=period)
    ll = bt.indicators.Lowest(data.low, period=period)
    return hh - (hh - ll) * 0.618
