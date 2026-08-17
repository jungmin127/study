"""
engine/metrics.py

백테스트 결과(equity_curve, trades)로부터 12종 성과 지표를 계산.
C:\\Users\\jungm\\project\\backtesting_1의 backend/app/engine/metrics.py를 참고해
포팅했다. 원본과 다른 점: monthly_returns(월별 수익률)는 이번 요청 범위 밖이라 제외.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# 타임프레임별 1 bar당 분(minute) 수
_TIMEFRAME_MINUTES: dict[str, float] = {
    "minutes1": 1,
    "minutes3": 3,
    "minutes5": 5,
    "minutes15": 15,
    "minutes30": 30,
    "minutes60": 60,
    "minutes240": 240,
    "days": 1440,
}

VALID_TIMEFRAMES: frozenset[str] = frozenset(_TIMEFRAME_MINUTES)


def bars_to_days(bars: int | float, timeframe: str) -> float:
    """bar 수를 일(day) 단위로 변환."""
    minutes_per_bar = _TIMEFRAME_MINUTES.get(timeframe, 1440)
    return bars * minutes_per_bar / 1440.0


def calculate_metrics(
    equity_curve: list[dict],
    trades: list[dict],
    initial_capital: float,
    df: pd.DataFrame,
    timeframe: str = "days",
) -> dict:
    """
    성과 지표 계산.

    Args:
        equity_curve: [{'timestamp': str, 'value': float}, ...]
        trades: [{'entryTime', 'exitTime', 'entryPrice', 'exitPrice',
                  'returnRate', 'holdingPeriod', 'pnl'}, ...]
        initial_capital: 초기 자본
        df: OHLCV DataFrame (buy_and_hold_return 계산용)

    Returns:
        {total_return, cagr, mdd, sharpe_ratio, sortino_ratio, calmar_ratio,
         win_rate, profit_factor, avg_holding_period, max_consecutive_loss,
         buy_and_hold_return, total_trades}
    """
    if not equity_curve:
        return _empty_metrics()

    values = pd.Series([float(e["value"]) for e in equity_curve])
    final_val = float(values.iloc[-1])

    total_return = _safe_div(final_val - initial_capital, initial_capital) * 100.0

    try:
        t0 = pd.Timestamp(equity_curve[0]["timestamp"])
        t1 = pd.Timestamp(equity_curve[-1]["timestamp"])
        days = max((t1 - t0).days, 1)
    except Exception:
        days = 1
    ratio = final_val / initial_capital if initial_capital > 0 else 1.0
    try:
        cagr = (ratio ** (365.0 / days) - 1.0) * 100.0 if ratio > 0 else 0.0
    except OverflowError:
        # 매우 짧은 기간(days) 대비 극단적인 ratio가 결합되면(예: 미청산 포지션이
        # 크게 다른 현재가로 재평가된 직후) 지수 연산 결과가 float 범위를 넘을 수 있다.
        cagr = 0.0

    cummax = values.cummax()
    drawdown = (values - cummax) / cummax * 100.0
    mdd = float(drawdown.min()) if not drawdown.empty else 0.0

    period_returns = values.pct_change().dropna()
    sharpe_ratio = _sharpe(period_returns)
    sortino_ratio = _sortino(period_returns)
    calmar_ratio = _safe_div(cagr, abs(mdd)) if mdd != 0 else 0.0

    total_trades = len(trades)
    win_rate = 0.0
    profit_factor = 0.0
    avg_holding_period = 0.0
    max_consecutive_loss = 0
    top_trade_contribution_pct_value: float | None = None

    if trades:
        pnls = [float(t.get("pnl", 0.0)) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = _safe_div(gross_profit, gross_loss) if gross_loss > 0 else 999.0
        if wins and gross_profit > 0:
            top_trade_contribution_pct_value = max(wins) / gross_profit * 100.0

        holding_periods = [bars_to_days(int(t.get("holdingPeriod", 0)), timeframe) for t in trades]
        avg_holding_period = float(np.mean(holding_periods)) if holding_periods else 0.0

        max_consecutive_loss = _max_consecutive_loss(pnls)

    buy_and_hold_return = 0.0
    if not df.empty and "close" in df.columns:
        first_close = float(df["close"].iloc[0])
        last_close = float(df["close"].iloc[-1])
        if first_close > 0:
            buy_and_hold_return = (last_close - first_close) / first_close * 100.0

    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "mdd": round(mdd, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "sortino_ratio": round(sortino_ratio, 4),
        "calmar_ratio": round(calmar_ratio, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_holding_period": round(avg_holding_period, 2),
        "max_consecutive_loss": max_consecutive_loss,
        "buy_and_hold_return": round(buy_and_hold_return, 4),
        "top_trade_contribution_pct": (
            round(top_trade_contribution_pct_value, 4)
            if top_trade_contribution_pct_value is not None
            else None
        ),
        "total_trades": total_trades,
    }


def _empty_metrics() -> dict:
    return {
        "total_return": 0.0, "cagr": 0.0, "mdd": 0.0,
        "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "calmar_ratio": 0.0,
        "win_rate": 0.0, "profit_factor": 0.0, "avg_holding_period": 0.0,
        "max_consecutive_loss": 0, "buy_and_hold_return": 0.0,
        "top_trade_contribution_pct": None, "total_trades": 0,
    }


def _safe_div(a: float, b: float) -> float:
    return a / b if b != 0 else 0.0


def _sharpe(period_returns: pd.Series) -> float:
    if period_returns.empty or period_returns.std() == 0:
        return 0.0
    return float(period_returns.mean() / period_returns.std() * math.sqrt(252))


def _sortino(period_returns: pd.Series) -> float:
    if period_returns.empty:
        return 0.0
    neg = period_returns[period_returns < 0]
    if neg.empty or neg.std() == 0:
        return 0.0
    return float(period_returns.mean() / neg.std() * math.sqrt(252))


def _max_consecutive_loss(pnls: list[float]) -> int:
    max_consec = 0
    current = 0
    for p in pnls:
        if p <= 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0
    return max_consec


def top_trade_contribution_pct(trades: list[dict]) -> float | None:
    """총 이익(gross profit) 중 가장 큰 단일 거래의 pnl이 차지하는 비중(%).
    이긴 거래가 없으면 None. 분모를 총수익률이 아니라 gross_profit으로 잡아,
    전략이 순손실이어도 '이긴 거래들 중 쏠림 정도'를 안정적으로 보여준다."""
    wins = [float(t.get("pnl", 0.0)) for t in trades]
    wins = [p for p in wins if p > 0]
    if not wins:
        return None
    gross_profit = sum(wins)
    return round(max(wins) / gross_profit * 100.0, 4) if gross_profit > 0 else None


__all__ = ["calculate_metrics", "bars_to_days", "top_trade_contribution_pct"]
