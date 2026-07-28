"""
engine/indicators/market.py

대상 코인 자체가 아니라 다른 마켓과의 관계를 반영하는 지표. engine.runner의
build_data_feed_class가 채워주는 self.data.btc_close / self.data.usdt_close 라인
(백엔드가 KRW-BTC·KRW-USDT 종가를 병합해 넣는다)에서 계산한다.
"""
from __future__ import annotations

import statistics

import backtrader as bt


def create_market_trend(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 10))
    market_close = data.btc_close
    sma = bt.indicators.SMA(market_close, period=period)
    return market_close - sma


class RollingCorrelation(bt.Indicator):
    """두 종가 라인의 봉 대비 등락률(bt.indicators.ROC100, period=1)을 최근 period봉 모아
    Pearson 상관계수를 계산한다. 등락률·롤링 윈도우 방식은
    docs/superpowers/specs/2026-07-27-strategy-source-classification.md의 "상관계수 계산
    방법론" 절에서 합의된 정의를 그대로 따른다."""

    lines = ("corr",)
    params = (("period", 20),)

    def __init__(self) -> None:
        self.roc_a = bt.indicators.ROC100(self.data0, period=1)
        self.roc_b = bt.indicators.ROC100(self.data1, period=1)
        self.addminperiod(self.p.period + 1)

    def next(self) -> None:
        xs = [self.roc_a[-i] for i in range(self.p.period)]
        ys = [self.roc_b[-i] for i in range(self.p.period)]
        try:
            self.lines.corr[0] = statistics.correlation(xs, ys)
        except statistics.StatisticsError:
            # 두 윈도우 중 하나라도 분산이 0이면(예: 페그/스테이블코인 마켓이
            # period+1봉 동안 완전히 flat) 상관계수가 수학적으로 정의되지 않는다.
            # "상관 신호 없음"으로 간주해 0.0을 반환한다 — NaN은 이후 비교 조건을
            # 모두 False로 만들고, 예외를 그대로 두면 백테스트 전체가 중단된다.
            self.lines.corr[0] = 0.0


def create_btc_correlation(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return RollingCorrelation(data.close, data.btc_close, period=period)


def create_usdt_correlation(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return RollingCorrelation(data.close, data.usdt_close, period=period)
