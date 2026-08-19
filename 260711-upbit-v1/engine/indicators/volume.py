from __future__ import annotations

import statistics
from collections import deque

import backtrader as bt


class OBV(bt.Indicator):
    """On Balance Volume — 종가가 전일 대비 상승하면 +거래량, 하락하면 -거래량을 누적."""

    lines = ("obv",)
    plotinfo = dict(subplot=True)

    def __init__(self) -> None:
        self.addminperiod(2)

    def nextstart(self) -> None:
        # 첫 next() 시점엔 obv[-1](이전 봉)이 한 번도 채워진 적 없어 NaN이므로,
        # obv[-1]을 참조하지 않고 0을 기준선으로 삼아 첫 델타를 직접 계산한다.
        if self.data.close[0] > self.data.close[-1]:
            self.lines.obv[0] = self.data.volume[0]
        elif self.data.close[0] < self.data.close[-1]:
            self.lines.obv[0] = -self.data.volume[0]
        else:
            self.lines.obv[0] = 0.0

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


def create_volume(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    """봉의 원시 거래량(수량) — TRADE_VALUE와 동일한 위상으로, 조건식에서 직접 쓸 수
    있게 data.volume 라인을 그대로 노출한다."""
    return data.volume


class VolumeRatio(bt.Indicator):
    """이번 봉 거래량이 자체 이동평균(period봉) 대비 몇 % 높거나 낮은지를 나타낸
    정규화 버전. TradeValueRatio와 동일한 패턴을 거래량(수량)에 적용한다."""

    lines = ("pct",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.volume, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.data.volume[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_volume_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return VolumeRatio(data, period=period)


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


class TradeValueRatio(bt.Indicator):
    """이번 봉 거래대금이 자체 이동평균(period봉) 대비 몇 % 높거나 낮은지를 나타낸
    정규화 버전. 코인마다 다른 거래대금 스케일을 제거한다."""

    lines = ("pct",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.sma = bt.indicators.SMA(self.data.trade_value, period=self.p.period)

    def next(self) -> None:
        sma_val = self.sma[0]
        self.lines.pct[0] = (self.data.trade_value[0] - sma_val) / sma_val * 100 if sma_val else 0.0


def create_trade_value_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return TradeValueRatio(data, period=period)


class VolumeBarVPIN(bt.Indicator):
    """거래량 버킷(volume bar) 기반 VPIN. Easley/López de Prado/O'Hara의 Bulk Volume
    Classification(BVC, 2012)을 따른다 — 틱 단위 매수/매도 라벨이 필요 없고, 캔들
    종가·거래량만으로 버킷별 매수/매도 비율을 확률적으로 추정한다."""

    lines = ("vpin",)
    params = (("period", 20),)

    def __init__(self) -> None:
        period = self.p.period
        self._recent_volumes: deque = deque(maxlen=period)
        self._bucket_cum_volume = 0.0
        self._last_bucket_close: float | None = None
        self._bucket_deltas: deque = deque(maxlen=period)
        self._bucket_imbalance_ratios: deque = deque(maxlen=period)

    def _accumulate(self) -> None:
        """이번 봉을 현재 버킷에 누적하고, 목표 거래량에 도달했으면 버킷을 완성해
        BVC로 매수/매도 불균형 비율을 계산·기록한다. next()/nextstart() 공통 로직."""
        period = self.p.period
        self._recent_volumes.append(self.data.volume[0])
        self._bucket_cum_volume += self.data.volume[0]

        target = (
            statistics.mean(self._recent_volumes)
            if len(self._recent_volumes) == period
            else None
        )
        if target is None or self._bucket_cum_volume < target:
            return

        bucket_close = self.data.close[0]
        bucket_volume = self._bucket_cum_volume

        if self._last_bucket_close is not None:
            delta = bucket_close - self._last_bucket_close
            self._bucket_deltas.append(delta)
            sigma = statistics.stdev(self._bucket_deltas) if len(self._bucket_deltas) >= 2 else 0.0
            z = delta / sigma if sigma > 0 else 0.0
            buy_ratio = statistics.NormalDist().cdf(z)
            buy_volume = bucket_volume * buy_ratio
            sell_volume = bucket_volume - buy_volume
            imbalance_ratio = abs(buy_volume - sell_volume) / bucket_volume if bucket_volume else 0.0
            self._bucket_imbalance_ratios.append(imbalance_ratio)

        self._last_bucket_close = bucket_close
        self._bucket_cum_volume = 0.0

    def nextstart(self) -> None:
        # next()가 이 지표에 대해 처음 호출되는 시점에도 이 지표 자신의 vpin 라인은 아직
        # 한 번도 기록된 적이 없어 vpin[-1]이 정의돼 있지 않다 — OBV의 nextstart()와 같은
        # 이유로, 첫 호출만 [-1] 참조 없이 따로 처리한다.
        self._accumulate()
        if len(self._bucket_imbalance_ratios) == self.p.period:
            self.lines.vpin[0] = statistics.mean(self._bucket_imbalance_ratios)
        else:
            self.lines.vpin[0] = float("nan")

    def next(self) -> None:
        self._accumulate()
        if len(self._bucket_imbalance_ratios) == self.p.period:
            self.lines.vpin[0] = statistics.mean(self._bucket_imbalance_ratios)
        else:
            self.lines.vpin[0] = self.lines.vpin[-1]


def create_vpin(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return VolumeBarVPIN(data, period=period)
