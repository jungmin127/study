from __future__ import annotations

import backtrader as bt


def create_rsi(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.RSI(data, period=period)


def create_macd_line(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.MACD(data, period_me1=fast, period_me2=slow, period_signal=signal)


def create_macd_signal(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.MACD(data, period_me1=fast, period_me2=slow, period_signal=signal)


def create_stoch_k(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    return bt.indicators.Stochastic(data, period=k_period, period_dfast=d_period)


def create_stoch_d(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    return bt.indicators.Stochastic(data, period=k_period, period_dfast=d_period)


def create_cci(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.CCI(data, period=period)


def create_williams_r(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.WilliamsR(data, period=period)


def create_momentum_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 5))
    return bt.indicators.ROC100(data, period=period)


def create_macd_ppo(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.PPO(data, period1=fast, period2=slow, period_signal=signal)


def create_macd_ppo_signal(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.PPO(data, period1=fast, period2=slow, period_signal=signal)
