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


LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
}

__all__ = ["LIVE_INDICATOR_FACTORY"]
