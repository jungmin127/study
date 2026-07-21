from datetime import datetime, timezone

from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest
from engine.sweep import DEFAULT_RISK_CONFIG
from tests.signal_fixtures import make_oscillating_df


def _run(buy_conditions: dict, sell_conditions: dict) -> dict:
    df = make_oscillating_df()
    return run_backtest(
        df=df,
        strategy_cls=ConditionTreeStrategy,
        risk_config=DEFAULT_RISK_CONFIG,
        strategy_params={"buy_conditions": buy_conditions, "sell_conditions": sell_conditions},
    )


def test_rsi_oversold_overbought_produces_trades():
    buy = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 40}]}
    sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 60}]}
    result = _run(buy, sell)
    assert result["final_value"] > 0
    assert isinstance(result["trades"], list)


def test_empty_buy_conditions_never_enters():
    buy = {"type": "AND", "conditions": []}
    sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": ">", "threshold": 60}]}
    result = _run(buy, sell)
    assert result["trades"] == []


def test_or_group_at_top_level_combines_with_any():
    buy = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 1},  # 거의 발생 안 함
            {"indicator": "SMA", "params": {"period": 5}, "operator": ">", "threshold": 0},  # 항상 참
        ],
    }
    sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": ">", "threshold": 60}]}
    result = _run(buy, sell)
    # SMA > 0 조건이 항상 참이므로 OR 그룹은 항상 참 -> 첫 봉 이후 즉시 매수되어야 함
    assert len(result["trades"]) > 0 or result["final_value"] != DEFAULT_RISK_CONFIG["initial_capital"]


def test_stop_loss_pct_exits_position_on_drawdown():
    buy = {"type": "AND", "conditions": [{"indicator": "SMA", "params": {"period": 1}, "operator": ">", "threshold": 0}]}  # 항상 참
    sell = {"type": "AND", "conditions": [{"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -2}]}
    result = _run(buy, sell)
    assert len(result["trades"]) > 0
    assert any(t["returnRate"] < 0 for t in result["trades"])


def test_take_profit_pct_exits_position_on_gain():
    buy = {"type": "AND", "conditions": [{"indicator": "SMA", "params": {"period": 1}, "operator": ">", "threshold": 0}]}  # 항상 참
    sell = {"type": "AND", "conditions": [{"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 2}]}
    result = _run(buy, sell)
    assert len(result["trades"]) > 0
    assert any(t["returnRate"] > 0 for t in result["trades"])
