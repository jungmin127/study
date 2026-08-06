"""
trading/live_indicators.py

라이브 트레이딩용 지표 계산 — pandas 기반. engine/indicators/*.py(backtrader 기반,
백테스트 전용)와 값이 일치하도록 골든테스트로 검증한다(스펙 결정 1). A그룹(대상 마켓
OHLCV만으로 계산되는 지표 33개)만 다룬다 — B그룹(외부데이터·보조마켓 의존 6개)은 별도
서브플랜에서 추가한다(스펙 결정 2).

각 함수는 engine/indicators/*.py의 동명 함수와 1:1 대응하며, bt.feeds.PandasData 대신
OHLCV 컬럼(open/high/low/close/volume, 일부는 trade_value)을 가진 pandas.DataFrame을
받아 같은 이름의 pandas.Series(워밍업 구간 NaN)를 반환한다. LIVE_INDICATOR_FACTORY
레지스트리는 engine.indicators.INDICATOR_FACTORY와 같은 패턴이다.
"""
from __future__ import annotations

import statistics
from collections import deque

import numpy as np
import pandas as pd


def create_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    return df["close"].rolling(period).mean()


def create_ema(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    return df["close"].ewm(span=period, adjust=False, min_periods=period).mean()


def create_wma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    weights = np.arange(1, period + 1)
    return df["close"].rolling(period).apply(
        lambda window: np.dot(window, weights) / weights.sum(), raw=True
    )


def create_rsi(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def create_macd_line(df: pd.DataFrame, **params) -> pd.Series:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    return ema_fast - ema_slow


def create_macd_signal(df: pd.DataFrame, **params) -> pd.Series:
    signal = int(params.get("signal", 9))
    macd_line = create_macd_line(df, **params)
    return macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()


def create_macd_ppo(df: pd.DataFrame, **params) -> pd.Series:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    return (ema_fast - ema_slow) / ema_slow * 100


def create_macd_ppo_signal(df: pd.DataFrame, **params) -> pd.Series:
    signal = int(params.get("signal", 9))
    ppo = create_macd_ppo(df, **params)
    return ppo.ewm(span=signal, adjust=False, min_periods=signal).mean()


def create_stoch_k(df: pd.DataFrame, **params) -> pd.Series:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    lowest_low = df["low"].rolling(k_period).min()
    highest_high = df["high"].rolling(k_period).max()
    fast_k = 100 * (df["close"] - lowest_low) / (highest_high - lowest_low)
    # backtrader의 Stochastic(StochasticFast가 아님)은 %K 자체가 이미 period_dfast로
    # 스무딩된 값을 노출한다 — fast_k를 그대로 쓰면 안 됨.
    return fast_k.rolling(d_period).mean()


def create_stoch_d(df: pd.DataFrame, **params) -> pd.Series:
    slow_k = create_stoch_k(df, **params)
    # period_dslow는 backtrader 기본값 3으로 고정(이 프로젝트의 STOCH 팩토리가
    # 파라미터화하지 않음, engine/indicators/momentum.py 참고).
    return slow_k.rolling(3).mean()


def create_cci(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_mean = tp.rolling(period).mean()
    # backtrader의 MeanDev는 각 시점의 |tp-tpmean|을 먼저 전체 시계열로 만든 뒤 그
    # 절대편차 시계열을 다시 이동평균한다 — 각 윈도우 내부에서 자기 평균을 새로 구해
    # 편차를 재는 것과 다르다(둘은 값이 다르다, 반드시 이 순서를 지킬 것).
    mean_dev = (tp - tp_mean).abs().rolling(period).mean()
    return (tp - tp_mean) / (0.015 * mean_dev)


def create_williams_r(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return -100 * (hh - df["close"]) / (hh - ll)


def create_momentum_pct(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 5))
    close = df["close"]
    return (close - close.shift(period)) / close.shift(period) * 100


def create_atr(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    prev_close = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def create_atr_pct(df: pd.DataFrame, **params) -> pd.Series:
    atr = create_atr(df, **params)
    return atr / df["close"] * 100


def create_bb_middle(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["close"].rolling(period).mean()


def create_bb_upper(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    mid = create_bb_middle(df, **params)
    std = df["close"].rolling(period).std(ddof=0)
    return mid + 2 * std


def create_bb_lower(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    mid = create_bb_middle(df, **params)
    std = df["close"].rolling(period).std(ddof=0)
    return mid - 2 * std


def create_bb_percent_b(df: pd.DataFrame, **params) -> pd.Series:
    upper = create_bb_upper(df, **params)
    lower = create_bb_lower(df, **params)
    return (df["close"] - lower) / (upper - lower)


def create_obv(df: pd.DataFrame, **params) -> pd.Series:
    direction = np.sign(df["close"].diff())
    return (direction * df["volume"]).fillna(0).cumsum()


def create_volume_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["volume"].rolling(period).mean()


def create_trade_value(df: pd.DataFrame, **params) -> pd.Series:
    return df["trade_value"]


def create_trade_value_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["trade_value"].rolling(period).mean()


LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "MOMENTUM_PCT": create_momentum_pct,
    "ATR": create_atr,
    "ATR_PCT": create_atr_pct,
    "BB_upper": create_bb_upper,
    "BB_lower": create_bb_lower,
    "BB_middle": create_bb_middle,
    "BB_PERCENT_B": create_bb_percent_b,
    "OBV": create_obv,
    "VOLUME_SMA": create_volume_sma,
    "TRADE_VALUE": create_trade_value,
    "TRADE_VALUE_SMA": create_trade_value_sma,
}

__all__ = ["LIVE_INDICATOR_FACTORY"]
