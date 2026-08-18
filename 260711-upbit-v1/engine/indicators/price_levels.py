"""
engine/indicators/price_levels.py

가격대(지지/저항) 계열 지표 — 피보나치 되돌림, Pivot Points, Volume Profile(VPVR).
모두 캔들의 고가/저가/거래량만으로 계산되는 순수 파생 지표라 보조 마켓·틱 데이터가 필요 없다.
"""
from __future__ import annotations

from collections import deque

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


class PivotPoints(bt.Indicator):
    """직전 1봉의 고가·저가·종가로 계산하는 표준 Pivot Point 기준선(P)/저항선(R1)/지지선(S1)."""

    lines = ("p", "r1", "s1")

    def __init__(self) -> None:
        self.addminperiod(2)

    def next(self) -> None:
        prev_high = self.data.high[-1]
        prev_low = self.data.low[-1]
        prev_close = self.data.close[-1]
        pivot = (prev_high + prev_low + prev_close) / 3.0
        self.lines.p[0] = pivot
        self.lines.r1[0] = pivot * 2 - prev_low
        self.lines.s1[0] = pivot * 2 - prev_high


def create_pivot_p(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return PivotPoints(data)


def create_pivot_r1(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return PivotPoints(data)


def create_pivot_s1(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    return PivotPoints(data)


NUM_BINS = 24
VALUE_AREA_PCT = 0.7


class VolumeProfile(bt.Indicator):
    """최근 period개 봉의 고가-저가 범위에 거래량을 겹치는 비율만큼 분배해 가격대별 거래량
    분포(Volume Profile)를 만들고, 그 분포에서 POC(거래량 최다 가격대)와 Value Area
    상단/하단(VAH/VAL)을 뽑아낸다. 틱 데이터 없이 캔들만으로 계산하는 근사치다."""

    lines = ("poc", "vah", "val")
    params = (("period", 50),)

    def __init__(self) -> None:
        period = self.p.period
        self._highs: deque = deque(maxlen=period)
        self._lows: deque = deque(maxlen=period)
        self._volumes: deque = deque(maxlen=period)

    def next(self) -> None:
        self._highs.append(self.data.high[0])
        self._lows.append(self.data.low[0])
        self._volumes.append(self.data.volume[0])

        if len(self._highs) < self.p.period:
            self.lines.poc[0] = float("nan")
            self.lines.vah[0] = float("nan")
            self.lines.val[0] = float("nan")
            return

        window_high = max(self._highs)
        window_low = min(self._lows)

        if window_high == window_low:
            self.lines.poc[0] = window_high
            self.lines.vah[0] = window_high
            self.lines.val[0] = window_high
            return

        bin_width = (window_high - window_low) / NUM_BINS
        bin_volumes = [0.0] * NUM_BINS

        for h, l, v in zip(self._highs, self._lows, self._volumes):
            if h == l:
                idx = min(int((h - window_low) / bin_width), NUM_BINS - 1)
                bin_volumes[idx] += v
                continue
            for i in range(NUM_BINS):
                bin_bottom = window_low + i * bin_width
                bin_top = bin_bottom + bin_width
                overlap = min(h, bin_top) - max(l, bin_bottom)
                if overlap > 0:
                    bin_volumes[i] += v * (overlap / (h - l))

        total_volume = sum(bin_volumes)
        poc_idx = max(range(NUM_BINS), key=lambda i: bin_volumes[i])
        poc_price = window_low + (poc_idx + 0.5) * bin_width

        lo = hi = poc_idx
        accumulated = bin_volumes[poc_idx]
        target = total_volume * VALUE_AREA_PCT
        while accumulated < target and (lo > 0 or hi < NUM_BINS - 1):
            expand_lo = bin_volumes[lo - 1] if lo > 0 else -1.0
            expand_hi = bin_volumes[hi + 1] if hi < NUM_BINS - 1 else -1.0
            if expand_hi >= expand_lo:
                hi += 1
                accumulated += expand_hi
            else:
                lo -= 1
                accumulated += expand_lo

        self.lines.poc[0] = poc_price
        self.lines.vah[0] = window_low + (hi + 1) * bin_width
        self.lines.val[0] = window_low + lo * bin_width


def create_vpvr_poc(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


def create_vpvr_vah(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


def create_vpvr_val(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 50))
    return VolumeProfile(data, period=period)


class FibPct(bt.Indicator):
    """피보나치 되돌림 레벨 대비 종가의 이격을 %로 나타낸 정규화 버전.
    FIB_382/500/618과 같은 레벨 계산에, 종가와의 이격도만 추가로 계산한다."""

    lines = ("pct",)
    params = (("period", 20), ("ratio", 0.382))

    def __init__(self) -> None:
        hh = bt.indicators.Highest(self.data.high, period=self.p.period)
        ll = bt.indicators.Lowest(self.data.low, period=self.p.period)
        self.level = hh - (hh - ll) * self.p.ratio

    def next(self) -> None:
        level = self.level[0]
        self.lines.pct[0] = (self.data.close[0] - level) / level * 100 if level else 0.0


def create_fib_382_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return FibPct(data, period=period, ratio=0.382)


def create_fib_500_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return FibPct(data, period=period, ratio=0.5)


def create_fib_618_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return FibPct(data, period=period, ratio=0.618)
