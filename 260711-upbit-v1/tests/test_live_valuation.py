from engine.live_valuation import has_revaluable_open_trade, revalue_open_trades


def test_has_revaluable_open_trade_true_when_forceclosed_with_size():
    trades = [{"forceClosed": True, "size": 1.0, "entryPrice": 100.0, "pnl": 10.0}]
    assert has_revaluable_open_trade(trades) is True


def test_has_revaluable_open_trade_false_when_size_missing():
    trades = [{"forceClosed": True, "entryPrice": 100.0, "pnl": 10.0}]
    assert has_revaluable_open_trade(trades) is False


def test_has_revaluable_open_trade_false_when_not_forceclosed():
    trades = [{"forceClosed": False, "size": 1.0, "entryPrice": 100.0, "pnl": 10.0}]
    assert has_revaluable_open_trade(trades) is False


def test_has_revaluable_open_trade_false_for_empty_list():
    assert has_revaluable_open_trade([]) is False


def test_revalue_open_trades_recomputes_pnl_and_return_rate():
    trades = [{
        "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
        "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
        "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
    }]
    updated, delta = revalue_open_trades(
        trades, live_price=120.0, live_time="2026-01-15T00:00:00", commission_rate=0.001,
    )

    entry_commission = 100.0 * 100.0 * 0.001
    exit_commission = 120.0 * 100.0 * 0.001
    expected_pnl = round((120.0 - 100.0) * 100.0 - entry_commission - exit_commission, 4)
    expected_return_rate = round(expected_pnl / (100.0 * 100.0) * 100, 4)

    assert updated[0]["pnl"] == expected_pnl
    assert updated[0]["returnRate"] == expected_return_rate
    assert updated[0]["exitPrice"] == 120.0
    assert updated[0]["exitTime"] == "2026-01-15T00:00:00"
    assert updated[0]["holdingPeriod"] == 9  # 갱신 안 함(알려진 제약)
    assert delta == round(expected_pnl - 500.0, 4)


def test_revalue_open_trades_ignores_trade_without_size():
    trades = [{"entryPrice": 100.0, "exitPrice": 105.0, "pnl": 500.0, "forceClosed": True}]
    updated, delta = revalue_open_trades(
        trades, live_price=120.0, live_time="2026-01-15T00:00:00", commission_rate=0.001,
    )
    assert updated == trades
    assert delta == 0.0


def test_revalue_open_trades_ignores_non_forceclosed_trade():
    trades = [{"entryPrice": 100.0, "exitPrice": 105.0, "pnl": 500.0, "forceClosed": False, "size": 100.0}]
    updated, delta = revalue_open_trades(
        trades, live_price=120.0, live_time="2026-01-15T00:00:00", commission_rate=0.001,
    )
    assert updated == trades
    assert delta == 0.0
