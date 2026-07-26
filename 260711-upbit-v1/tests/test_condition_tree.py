from engine.condition_tree import (
    apply_operator,
    collect_blocks,
    eval_group,
    find_unknown_indicators,
    is_empty,
    max_required_period,
    requires_market_data,
)


def test_collect_blocks_flattens_nested_groups():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
            {
                "type": "OR",
                "conditions": [
                    {"indicator": "MACD_line", "params": {}, "operator": ">", "threshold": 0},
                    {"indicator": "SMA", "params": {"period": 20}, "operator": ">", "threshold": 100},
                ],
            },
        ],
    }
    blocks = collect_blocks(tree)
    assert len(blocks) == 3
    assert {b["indicator"] for b in blocks} == {"RSI", "MACD_line", "SMA"}


def test_apply_operator_all_variants():
    assert apply_operator(10, ">", 5) is True
    assert apply_operator(10, "<", 5) is False
    assert apply_operator(5, ">=", 5) is True
    assert apply_operator(5, "<=", 5) is True
    assert apply_operator(5, "==", 5) is True


def test_find_unknown_indicators_detects_bad_key():
    tree = {"type": "AND", "conditions": [{"indicator": "NOPE", "params": {}, "operator": ">", "threshold": 0}]}
    assert find_unknown_indicators(tree) == ["NOPE"]


def test_is_empty_true_for_no_conditions():
    assert is_empty({"type": "AND", "conditions": []}) is True
    assert is_empty({"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": "<", "threshold": 1}]}) is False


def test_max_required_period_takes_largest_param_value():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "SMA", "params": {"period": 200}, "operator": ">", "threshold": 0},
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        ],
    }
    assert max_required_period(tree) == 200


def test_eval_group_evaluates_stop_loss_pct_against_position_return():
    tree = {"type": "AND", "conditions": [{"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5}]}
    assert eval_group(tree, {}, position_return_pct=-6) is True
    assert eval_group(tree, {}, position_return_pct=-3) is False


def test_eval_group_position_relative_indicator_false_without_position():
    tree = {"type": "AND", "conditions": [{"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10}]}
    assert eval_group(tree, {}) is False


def test_find_unknown_indicators_allows_position_relative_indicators():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5},
            {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
        ],
    }
    assert find_unknown_indicators(tree) == []


def test_eval_group_evaluates_holding_period_bars_against_position_state():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5}],
    }
    assert eval_group(tree, {}, position_holding_bars=5) is True
    assert eval_group(tree, {}, position_holding_bars=4) is False


def test_eval_group_holding_period_bars_false_without_position():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5}],
    }
    assert eval_group(tree, {}) is False


def test_find_unknown_indicators_allows_holding_period_bars():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">=", "threshold": 5}],
    }
    assert find_unknown_indicators(tree) == []


def test_requires_market_data_true_when_market_trend_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 10}, "operator": "<", "threshold": 0}],
    }
    assert requires_market_data(tree) is True


def test_requires_market_data_false_when_market_trend_absent():
    tree = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}]}
    assert requires_market_data(tree) is False


def test_requires_market_data_checks_nested_groups():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30},
            {
                "type": "AND",
                "conditions": [
                    {"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}
                ],
            },
        ],
    }
    assert requires_market_data(tree) is True
