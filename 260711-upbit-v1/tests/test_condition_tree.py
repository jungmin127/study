from engine.condition_tree import (
    apply_operator,
    collect_blocks,
    eval_group,
    find_unknown_indicators,
    get_indicator_value,
    is_empty,
    max_required_period,
    required_aux_markets,
)
from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df


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


def test_required_aux_markets_returns_btc_when_market_trend_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 10}, "operator": "<", "threshold": 0}],
    }
    assert required_aux_markets(tree) == {"KRW-BTC"}


def test_required_aux_markets_empty_when_absent():
    tree = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}]}
    assert required_aux_markets(tree) == set()


def test_required_aux_markets_checks_nested_groups():
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
    assert required_aux_markets(tree) == {"KRW-BTC"}


def test_required_aux_markets_returns_both_btc_and_usdt_when_both_correlations_present():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "BTC_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.5},
            {"indicator": "USDT_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.5},
        ],
    }
    assert required_aux_markets(tree) == {"KRW-BTC", "KRW-USDT"}


def test_required_aux_markets_dedupes_when_market_trend_and_btc_correlation_both_need_btc():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "MARKET_TREND", "params": {"period": 10}, "operator": "<", "threshold": 0},
            {"indicator": "BTC_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.5},
        ],
    }
    assert required_aux_markets(tree) == {"KRW-BTC"}


def test_get_indicator_value_dispatches_pivot_sublines():
    import backtrader as bt

    class _FakeLine:
        def __init__(self, value):
            self.value = value

        def __getitem__(self, idx):
            return self.value

    class _FakePivot:
        def __init__(self):
            self.lines = type('obj', (object,), {
                'p': _FakeLine(105.0),
                'r1': _FakeLine(110.0),
                's1': _FakeLine(100.0),
            })()

    obj = _FakePivot()
    assert get_indicator_value("PIVOT_P", obj) == 105.0
    assert get_indicator_value("PIVOT_R1", obj) == 110.0
    assert get_indicator_value("PIVOT_S1", obj) == 100.0


def test_pivot_factories_produce_dispatch_compatible_objects():
    """Regression test: ensure factory functions return PivotPoints instances
    that work with get_indicator_value() in the real integration path."""
    import backtrader as bt

    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    captured = {}

    class _Probe(bt.Strategy):
        def __init__(self):
            self.p_obj = INDICATOR_FACTORY["PIVOT_P"](self.data)
            self.r1_obj = INDICATOR_FACTORY["PIVOT_R1"](self.data)
            self.s1_obj = INDICATOR_FACTORY["PIVOT_S1"](self.data)

        def next(self):
            captured["p"] = get_indicator_value("PIVOT_P", self.p_obj)
            captured["r1"] = get_indicator_value("PIVOT_R1", self.r1_obj)
            captured["s1"] = get_indicator_value("PIVOT_S1", self.s1_obj)

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_Probe)
    cerebro.run()

    assert isinstance(captured["p"], float)
    assert isinstance(captured["r1"], float)
    assert isinstance(captured["s1"], float)
