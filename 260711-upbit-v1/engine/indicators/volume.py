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
