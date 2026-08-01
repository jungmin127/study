from scripts.grid_search import build_condition_grid


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
