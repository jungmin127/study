"""
engine/indicators/market.py

대상 코인 자체가 아니라 시장 전체 추세를 반영하는 지표. engine.runner의
PandasDataWithExtra가 채워주는 self.data.extra 라인(백엔드가 KRW-BTC 종가를 병합해 넣는다)
에서 계산한다.
"""
from __future__ import annotations

import backtrader as bt


def create_market_trend(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 10))
    market_close = data.extra
    sma = bt.indicators.SMA(market_close, period=period)
    return market_close - sma
