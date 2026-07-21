from engine.condition_tree import (
    apply_operator,
    collect_blocks,
    eval_group,
    find_unknown_indicators,
    is_empty,
    max_required_period,
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
