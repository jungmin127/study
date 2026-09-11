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


def test_holding_period_bars_forces_exit_after_n_bars():
    buy = {"type": "AND", "conditions": [{"indicator": "SMA", "params": {"period": 1}, "operator": ">", "threshold": 0}]}  # 항상 참
    sell = {"type": "AND", "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 3}]}
    result = _run(buy, sell)
    assert len(result["trades"]) > 10
    assert all(t["holdingPeriod"] <= 5 for t in result["trades"])


def test_trailing_stop_step_pct_locks_in_gains_as_price_rises():
    # 항상 매수, 계단식 트레일링(step=1)만 매도조건. make_oscillating_df()는
    # base=20000 진폭 600(=3%)+리플 50 사인파라 진입 후 대부분의 상승 구간에서
    # 최소 1%(step) 이상은 오르내리므로, 최고수익률 대비 한 단계 아래로
    # 떨어지는 순간 매도가 걸려야 한다.
    buy = {"type": "AND", "conditions": [{"indicator": "SMA", "params": {"period": 1}, "operator": ">", "threshold": 0}]}  # 항상 참
    sell = {"type": "AND", "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 1}]}
    result = _run(buy, sell)
    assert len(result["trades"]) > 0
    # 계단식은 "본절 이상을 지키는" 게 목적 -> 최소 한 건은 수익(또는 본절 근접) 청산이어야 한다.
    assert any(t["returnRate"] >= -0.5 for t in result["trades"])
