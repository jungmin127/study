from __future__ import annotations

import backtrader as bt


class OBV(bt.Indicator):
    """On Balance Volume — 종가가 전일 대비 상승하면 +거래량, 하락하면 -거래량을 누적."""

    lines = ("obv",)
    plotinfo = dict(subplot=True)

    def __init__(self) -> None:
        self.addminperiod(2)

    def next(self) -> None:
        if self.data.close[0] > self.data.close[-1]:
            self.lines.obv[0] = self.lines.obv[-1] + self.data.volume[0]
        elif self.data.close[0] < self.data.close[-1]:
            self.lines.obv[0] = self.lines.obv[-1] - self.data.volume[0]
        else:
            self.lines.obv[0] = self.lines.obv[-1]


def create_obv(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return OBV(data)


def create_volume_sma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.SMA(data.volume, period=period)


def create_trade_value(data: bt.feeds.PandasData, **params):
    """봉의 거래대금(KRW) — engine.runner의 build_data_feed_class가 동적으로 만들어주는
    PandasData 서브클래스가 채워주는 self.data.trade_value 라인(업비트 candle_acc_trade_price)을
    그대로 반환한다.
    거래량(수량)과 달리 가격이 반영된 값이라, 저가 잡코인의 거래량 착시 없이
    실제로 큰돈이 들어온 종목을 거를 때 쓴다."""
    return data.trade_value


def create_trade_value_sma(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.SMA(data.trade_value, period=period)
