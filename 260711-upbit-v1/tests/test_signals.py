from engine.runner import run_backtest
from engine.strategies import SignalStrategy
from signals import MacdCrossSignal, RsiZoneSignal, SmaCrossSignal
from tests.signal_fixtures import make_oscillating_df

RISK_CONFIG = {"initial_capital": 10000, "commission_rate": 0.001, "position_sizing": "percent", "position_size": 100}


def test_macd_cross_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [MacdCrossSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_rsi_zone_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [RsiZoneSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_sma_cross_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [SmaCrossSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])
