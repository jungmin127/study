from engine.sweep import DEFAULT_RISK_CONFIG
from scripts.grid_search import build_condition_grid, compute_grid_results
from tests.signal_fixtures import make_oscillating_df


def test_build_condition_grid_combo_counts():
    buy_conditions, sell_conditions = build_condition_grid()
    assert len(buy_conditions) == 45
    assert len(sell_conditions) == 57


def test_build_condition_grid_uses_k_period_for_stochastics():
    buy_conditions, sell_conditions = build_condition_grid()
    for indicator in ("STOCH_K", "STOCH_D"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert len(blocks) == 9
        assert all("k_period" in b["params"] for b in blocks)
        assert all("period" not in b["params"] for b in blocks)
        sell_blocks = [b for b in sell_conditions if b["indicator"] == indicator]
        assert all("k_period" in b["params"] for b in sell_blocks)


def test_build_condition_grid_uses_period_for_non_stochastic_oscillators():
    buy_conditions, _ = build_condition_grid()
    for indicator in ("RSI", "CCI", "WILLIAMS_R"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert len(blocks) == 9
        assert {b["params"]["period"] for b in blocks} == {10, 14, 20}


def test_build_condition_grid_rsi_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    rsi_buy = [b for b in buy_conditions if b["indicator"] == "RSI"]
    rsi_sell = [b for b in sell_conditions if b["indicator"] == "RSI"]
    assert {b["threshold"] for b in rsi_buy} == {20, 30, 40}
    assert all(b["operator"] == "<" for b in rsi_buy)
    assert {b["threshold"] for b in rsi_sell} == {60, 70, 80}
    assert all(b["operator"] == ">" for b in rsi_sell)


def test_build_condition_grid_sell_only_indicators():
    _, sell_conditions = build_condition_grid()
    stop_loss = [b for b in sell_conditions if b["indicator"] == "STOP_LOSS_PCT"]
    take_profit = [b for b in sell_conditions if b["indicator"] == "TAKE_PROFIT_PCT"]
    holding = [b for b in sell_conditions if b["indicator"] == "HOLDING_PERIOD_BARS"]
    assert len(stop_loss) == 4 and {b["threshold"] for b in stop_loss} == {-3, -5, -7, -10}
    assert all(b["operator"] == "<=" and b["params"] == {} for b in stop_loss)
    assert len(take_profit) == 4 and {b["threshold"] for b in take_profit} == {5, 10, 15, 20}
    assert all(b["operator"] == ">=" and b["params"] == {} for b in take_profit)
    assert len(holding) == 4 and {b["threshold"] for b in holding} == {5, 10, 20, 40}
    assert all(b["operator"] == ">=" and b["params"] == {} for b in holding)


def test_compute_grid_results_runs_every_combo():
    df = make_oscillating_df(n=200)
    buy_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        {"indicator": "CCI", "params": {"period": 20}, "operator": "<", "threshold": -100},
    ]
    sell_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
    ]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)

    assert len(results) == 4
    for r in results:
        assert set(r.keys()) == {"return_pct", "buy_block", "sell_block", "trades", "final_value"}
        assert isinstance(r["trades"], list)
        assert isinstance(r["return_pct"], float)


def test_compute_grid_results_pairs_every_buy_with_every_sell():
    df = make_oscillating_df(n=200)
    buy_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
    ]
    sell_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "RSI", "params": {"period": 20}, "operator": ">", "threshold": 80},
    ]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)

    assert len(results) == 2
    assert results[0]["buy_block"] == buy_conditions[0]
    assert {r["sell_block"]["threshold"] for r in results} == {70, 80}
