from datetime import datetime, timezone

import backtrader as bt
import pandas as pd

from engine.runner import _build_forced_close_trade, run_backtest


class BuyAndHoldOnce(bt.Strategy):
    def __init__(self):
        self.bought = False

    def next(self):
        if not self.bought and len(self) == 5:
            self.buy()
            self.bought = True


def _make_synthetic_df(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    # 봉 사이 등락폭을 작게(바 대비 0.05%) 유지해야 한다 — FractionalPercentSizer는
    # 신호 발생 시점(종가)의 가격으로 매수 수량을 계산하지만 실제 체결은 다음 봉의 시가에서
    # 이뤄진다. 등락폭이 사이저의 버퍼(0.5%)+수수료를 넘으면 체결 시점에 현금이 모자라
    # Margin으로 주문이 거부된다 — 100 대신 10000을 기준가로, +1 대신 +5를 스텝으로 사용해
    # 봉 사이 변동을 충분히 작게 유지한다.
    prices = [10000 + i * 5 for i in range(n)]
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1.0] * n,
        }
    )


def test_run_backtest_buy_and_hold_once():
    df = _make_synthetic_df()
    result = run_backtest(
        df=df,
        strategy_cls=BuyAndHoldOnce,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
    )

    assert result["final_value"] > 10000
    assert len(result["equity_curve"]) == 30
    assert len(result["trades"]) == 1
    assert result["trades"][0]["forceClosed"] is True
    assert "sharpe" in result
    assert "max_drawdown" in result
    assert result["trades"][0]["size"] > 0


def test_forced_close_trade_deducts_entry_and_exit_commission():
    trade = _build_forced_close_trade(
        entry_time="2026-01-01T00:00:00", entry_price=100.0, size=2.0,
        baropen=0, last_close=110.0, last_dt="2026-01-10T00:00:00",
        total_bars=10, commission_rate=0.01,
    )
    pnl_gross = (110.0 - 100.0) * 2.0
    entry_commission = 100.0 * 2.0 * 0.01
    exit_commission = 110.0 * 2.0 * 0.01
    expected_pnl = pnl_gross - entry_commission - exit_commission
    assert trade["pnl"] == round(expected_pnl, 4)
    assert trade["forceClosed"] is True
    assert trade["holdingPeriod"] == 9
    assert trade["entryPrice"] == 100.0
    assert trade["exitPrice"] == 110.0
    assert trade["size"] == 2.0


def test_run_backtest_with_extra_column_exposes_data_extra_line():
    df = _make_synthetic_df()
    df["market_close"] = [50000 + i * 10 for i in range(len(df))]

    captured: list[float] = []

    class _CapturesExtraLine(bt.Strategy):
        def next(self):
            captured.append(float(self.data.extra[0]))

    run_backtest(
        df=df,
        strategy_cls=_CapturesExtraLine,
        risk_config={
            "initial_capital": 10000,
            "commission_rate": 0.001,
            "position_sizing": "percent",
            "position_size": 100,
        },
        extra_column="market_close",
    )

    assert captured[0] == 50000.0
    assert captured[-1] == 50000 + (len(df) - 1) * 10
