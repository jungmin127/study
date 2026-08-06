from engine.condition_tree import (
    AUX_MARKET_INDICATORS,
    apply_operator,
    collect_blocks,
    eval_group,
    eval_group_values,
    find_unknown_indicators,
    get_indicator_value,
    indicator_key,
    is_empty,
    max_required_period,
    required_aux_markets,
)
from engine.indicators import INDICATOR_FACTORY
from engine.runner import AUX_MARKET_LINE_NAME
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


def test_all_aux_market_indicators_have_a_line_name_mapping():
    """AUX_MARKET_INDICATORS에 새 마켓 코드가 추가되면 engine.runner.AUX_MARKET_LINE_NAME에도
    같은 코드가 있어야 한다. 누락되면 backend/main.py의 AUX_MARKET_LINE_NAME[aux_market] 조회가
    처리되지 않은 KeyError로 500 에러가 된다."""
    assert set(AUX_MARKET_INDICATORS.values()) <= set(AUX_MARKET_LINE_NAME.keys())


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


def test_required_aux_markets_returns_usdt_when_korea_premium_present():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}],
    }
    assert required_aux_markets(tree) == {"KRW-USDT"}


def test_eval_group_values_matches_apply_operator_when_all_known():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {"indicator": "CCI", "params": {}, "operator": "<", "threshold": -120},
        ],
    }
    values = {
        indicator_key("RSI", {"period": 14}): 25.0,
        indicator_key("CCI", {}): -150.0,
    }
    assert eval_group_values(tree, values) is True

    values[indicator_key("CCI", {})] = -50.0
    assert eval_group_values(tree, values) is False


def test_eval_group_values_drops_unknown_leaf_in_and():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
        ],
    }
    # FUNDING_RATE 키가 아예 없음 = unknown -> RSI 조건만으로 판단
    values = {indicator_key("RSI", {"period": 14}): 25.0}
    assert eval_group_values(tree, values) is True

    values = {indicator_key("RSI", {"period": 14}): 40.0}
    assert eval_group_values(tree, values) is False


def test_eval_group_values_drops_unknown_leaf_in_or():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 54},
            {"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": 0.01},
        ],
    }
    values = {indicator_key("RSI", {"period": 14}): 60.0}
    assert eval_group_values(tree, values) is True

    values = {indicator_key("RSI", {"period": 14}): 10.0}
    assert eval_group_values(tree, values) is False


def test_eval_group_values_none_value_is_treated_as_unknown():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31}],
    }
    # 키는 있지만 값이 None(지표 계산 실패) -> unknown -> 결과도 None
    values = {indicator_key("RSI", {"period": 14}): None}
    assert eval_group_values(tree, values) is None


def test_eval_group_values_returns_none_when_all_leaves_unknown():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
            {"indicator": "KOREA_PREMIUM", "params": {}, "operator": "<", "threshold": 2.0},
        ],
    }
    assert eval_group_values(tree, {}) is None


def test_eval_group_values_propagates_unknown_nested_group():
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {
                "type": "OR",
                "conditions": [
                    {"indicator": "FUNDING_RATE", "params": {}, "operator": "<", "threshold": -0.01},
                    {"indicator": "KOREA_PREMIUM", "params": {}, "operator": "<", "threshold": 2.0},
                ],
            },
        ],
    }
    values = {indicator_key("RSI", {"period": 14}): 25.0}
    # 중첩 OR 그룹 전체가 unknown -> 상위 AND에서 제외 -> RSI만으로 판단
    assert eval_group_values(tree, values) is True


def test_eval_group_values_position_relative_indicators_unaffected_by_unknown_handling():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<", "threshold": -5.0}],
    }
    # 포지션 없음(position_return_pct=None) -> False (unknown이 아니라 기존 eval_group과 동일한 "false")
    assert eval_group_values(tree, {}, position_return_pct=None) is False
    assert eval_group_values(tree, {}, position_return_pct=-6.0) is True


def test_eval_group_values_holding_period_bars_false_without_position():
    # HOLDING_PERIOD_BARS도 STOP_LOSS_PCT와 동일하게 position_holding_bars=None이면
    # unknown이 아니라 False로 처리된다(eval_group()과 동일한 동작).
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "HOLDING_PERIOD_BARS", "params": {}, "operator": ">", "threshold": 10}],
    }
    assert eval_group_values(tree, {}, position_holding_bars=None) is False


def test_eval_group_values_empty_conditions_returns_false_not_none():
    assert eval_group_values({"type": "AND", "conditions": []}, {}) is False


def test_eval_group_values_empty_nested_subgroup_is_structural_false_not_unknown():
    # 빈 OR 하위그룹은 eval_group_values(하위그룹) 자체가 False를 반환한다(unknown인 None이 아님).
    # 따라서 상위 AND 그룹에 "알 수 없어서 제외"가 아니라 "확실히 거짓"으로 포함되어 AND를 False로 만든다.
    # 이는 자식이 전부 unknown이라 None을 반환하고 상위에서 제외되는 경우와는 다르다.
    tree = {
        "type": "AND",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31},
            {"type": "OR", "conditions": []},
        ],
    }
    values = {indicator_key("RSI", {"period": 14}): 25.0}
    assert eval_group_values(tree, values) is False


def test_eval_group_values_nan_value_is_treated_as_unknown():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 31}],
    }
    # 키는 있지만 값이 NaN(pandas/numpy의 결측치 표현) -> unknown -> 결과도 None
    values = {indicator_key("RSI", {"period": 14}): float("nan")}
    assert eval_group_values(tree, values) is None


def test_eval_group_values_drops_nan_leaf_in_or():
    tree = {
        "type": "OR",
        "conditions": [
            {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 54},
            {"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": 0.01},
        ],
    }
    values = {
        indicator_key("RSI", {"period": 14}): 60.0,
        indicator_key("FUNDING_RATE", {}): float("nan"),
    }
    assert eval_group_values(tree, values) is True

    values = {
        indicator_key("RSI", {"period": 14}): 10.0,
        indicator_key("FUNDING_RATE", {}): float("nan"),
    }
    assert eval_group_values(tree, values) is False
