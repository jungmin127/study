import pandas as pd

from engine.runner import run_backtest
from engine.strategies import SignalStrategy


class StubSignal:
    """지정된 bar 번호(len(strategy) 기준)에서만 매수/매도 신호를 낸다."""

    def __init__(self, buy_bars: set[int], sell_bars: set[int]):
        self.buy_bars = buy_bars
        self.sell_bars = sell_bars

    def setup(self, strategy) -> None:
        pass

    def should_buy(self, strategy) -> bool:
        return len(strategy) in self.buy_bars

    def should_sell(self, strategy) -> bool:
        return len(strategy) in self.sell_bars


def _make_df(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    prices = [10000 + i * 2 for i in range(n)]
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


RISK_CONFIG = {"initial_capital": 10000, "commission_rate": 0.001, "position_sizing": "percent", "position_size": 100}


def test_single_signal_buys_and_sells_at_its_own_bars():
    df = _make_df()
    signal = StubSignal(buy_bars={5}, sell_bars={10})
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [signal]})

    assert len(result["trades"]) == 1
    assert result["trades"][0]["forceClosed"] is False


def test_combined_buy_requires_all_signals_to_agree_same_bar():
    df = _make_df()
    # 매수 조건이 겹치는 bar는 8뿐 (5,8 ∩ 8,12 = {8})
    a = StubSignal(buy_bars={5, 8}, sell_bars=set())
    b = StubSignal(buy_bars={8, 12}, sell_bars=set())
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [a, b]})

    assert len(result["trades"]) == 1
    # bar 8에 진입했는지 확인 (bar 8 = 인덱스 7, candle_time 8번째 값)
    entry_time = result["trades"][0]["entryTime"]
    expected_time = df["candle_time"].iloc[8].isoformat().replace("+00:00", "")
    assert entry_time == expected_time


def test_combined_sell_fires_when_any_signal_says_sell():
    df = _make_df()
    a = StubSignal(buy_bars={5}, sell_bars={20})
    b = StubSignal(buy_bars={5}, sell_bars={15})
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [a, b]})

    assert len(result["trades"]) == 1
    # b가 더 빠른 bar(15)에 매도 신호를 내므로 그때 청산돼야 함
    exit_time = result["trades"][0]["exitTime"]
    expected_time = df["candle_time"].iloc[15].isoformat().replace("+00:00", "")
    assert exit_time == expected_time


def test_no_signals_never_trades():
    df = _make_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": []})
    assert len(result["trades"]) == 0
