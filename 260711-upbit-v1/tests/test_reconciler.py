import json

import httpx
import pytest

import trading.db as db
import trading.reconciler as reconciler
import trading.upbit_client as upbit_client
from tests.trading_db_fixtures import insert_live_strategy


def test_coin_currency_extracts_ticker_from_market():
    assert reconciler._coin_currency("KRW-BTC") == "BTC"
    assert reconciler._coin_currency("KRW-ETH") == "ETH"


async def test_get_coin_account_returns_matching_currency(monkeypatch):
    async def fake_get_accounts(*, client=None):
        return [
            {"currency": "KRW", "balance": "100000.0", "locked": "0", "avg_buy_price": "0"},
            {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "49000000"},
        ]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    account = await reconciler._get_coin_account("KRW-BTC")

    assert account["currency"] == "BTC"
    assert account["balance"] == "0.01"


async def test_get_coin_account_returns_none_when_not_held(monkeypatch):
    async def fake_get_accounts(*, client=None):
        return [{"currency": "KRW", "balance": "100000.0", "locked": "0", "avg_buy_price": "0"}]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    assert await reconciler._get_coin_account("KRW-BTC") is None


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _strategy_row(dbm, *, baseline_qty=None, manual_intervention_policy="all_stop", **overrides):
    risk_config = {
        "order_execution_mode": "market",
        "max_position_per_market": 1_000_000.0,
        "manual_intervention_policy": manual_intervention_policy,
    }
    strategy_id = insert_live_strategy(
        dbm, market="KRW-BTC", current_capital=1_000_000.0,
        risk_config_json=json.dumps(risk_config), **overrides,
    )
    if baseline_qty is not None:
        dbm.update_live_strategy_baseline_qty(strategy_id, baseline_qty)
    return dbm.get_live_strategy(strategy_id)


async def test_sync_pending_limit_orders_updates_filled_order(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    order_id = dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    dbm.update_order_filled(order_id, "uuid-limit-1", None, None, None, None, "wait")

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "50.0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    synced = await reconciler._sync_pending_limit_orders(strategy)

    assert synced == 1
    order = dbm.get_order_by_id(order_id)
    assert order["status"] == "done"
    assert order["filled_price"] == 100.0
    assert order["filled_volume"] == 1.0


async def test_sync_pending_limit_orders_skips_orders_still_waiting(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    order_id = dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    dbm.update_order_filled(order_id, "uuid-limit-2", None, None, None, None, "wait")

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    synced = await reconciler._sync_pending_limit_orders(strategy)

    assert synced == 0
    assert dbm.get_order_by_id(order_id)["status"] == "wait"


async def test_sync_pending_limit_orders_ignores_non_limit_wait_orders(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)
    calls = {"count": 0}

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["count"] += 1
        return {"state": "done", "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    synced = await reconciler._sync_pending_limit_orders(strategy)

    assert synced == 0
    assert calls["count"] == 0


async def test_hydrate_state_captures_baseline_on_first_call(monkeypatch, tmp_path):
    """설계 스펙 결정9 — 전략 시작 전부터 보유하던 코인(사용자 사례: 기존 BTC 보유)을
    첫 hydrate_state 호출에서 baseline으로 격리하고, 그 호출은 불일치 검사를 건너뛴다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    assert strategy["baseline_qty"] is None

    async def fake_get_accounts(*, client=None):
        return [{"currency": "BTC", "balance": "0.05", "locked": "0", "avg_buy_price": "50000000"}]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.hydrate_state(strategy)

    assert result["baseline_captured"] is True
    assert result["synced_wait_orders"] == 0
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == 0.05
    assert dbm.get_live_strategy(strategy["id"])["status"] != "paused"


async def test_detect_external_orders_finds_new_order_all_stop(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-1"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert found[0]["upbit_uuid"] == "ext-uuid-1"
    assert found[0]["is_external"] == 1
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_detect_external_orders_acknowledge_and_continue_keeps_running(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(
        dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue",
    )

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-2"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"


async def test_detect_external_orders_ignores_already_known_uuid(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)
    dbm.insert_external_order(
        strategy["id"], None, "KRW-BTC", "bid", "limit", "known-uuid",
        100.0, 1.0, 0.0, "done",
    )
    calls = {"get_order": 0}

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return [{"uuid": "known-uuid"}]

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        calls["get_order"] += 1
        return {"state": "done", "side": "bid", "ord_type": "limit",
                "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert found == []
    assert calls["get_order"] == 0


import trading.position_manager as position_manager


def _account(balance, locked="0", avg_buy_price="0"):
    return {"currency": "BTC", "balance": str(balance), "locked": locked,
            "avg_buy_price": avg_buy_price}


async def test_reconcile_position_no_mismatch_returns_none_action(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": False, "action": "none", "paused": False}


async def test_reconcile_position_opens_from_matched_external_buy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")

    async def fake_get_accounts(*, client=None):
        return [_account(0.01)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result == {"balance_mismatch": True, "action": "opened", "paused": False}
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == 0.01
    assert position["entry_price"] == 50_000_000.0


async def test_reconcile_position_closes_from_matched_external_sell(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "ask", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result == {"balance_mismatch": True, "action": "closed", "paused": True}
    assert position_manager.get_open_position(strategy["id"]) is None
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_reconcile_position_adjusts_qty_on_partial_external_sell(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)
    capital_before = dbm.get_live_strategy(strategy["id"])["current_capital"]

    async def fake_get_accounts(*, client=None):
        return [_account(0.006)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "ask", "filled_volume": 0.004, "filled_price": 50_000_000.0, "fee": 200.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result["action"] == "adjusted"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.006)
    assert dbm.get_live_strategy(strategy["id"])["current_capital"] == capital_before


async def test_reconcile_position_unexplained_forces_paused_regardless_of_policy(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")
    position_manager.open_position(strategy["id"], "KRW-BTC", 49_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0.005)]  # 절반만 남았는데 설명할 외부주문이 없음

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": True}
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.005)


async def test_reconcile_position_negative_actual_qty_from_baseline_does_not_open_position(
    monkeypatch, tmp_path,
):
    """Finding 1 — baseline_qty>0(봇 시작 전부터 보유하던 코인)인 상태에서 사용자가
    그 보유분 일부를 수동 매도하면 actual_qty(raw_balance - baseline_qty)가 음수가 될 수
    있다. 그 diff가 외부 매도주문과 정확히 매칭되더라도, position이 None인 상태에서
    음수/0 수량으로 포지션을 여는 것은 금지되어야 한다 — entry_qty<0, entry_price=0.0인
    자기영속(self-perpetuating) 포지션은 이후 청산 시 position_manager.close_position()에서
    ZeroDivisionError를 일으킨다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(
        dbm, baseline_qty=0.02, manual_intervention_policy="acknowledge_and_continue",
    )

    async def fake_get_accounts(*, client=None):
        return [_account(0.01)]  # raw_balance=0.01, baseline=0.02 -> actual_qty=-0.01

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "ask", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": True}
    assert position_manager.get_open_position(strategy["id"]) is None
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"
    conn = dbm._connect()
    try:
        rows = conn.execute(
            "SELECT description FROM manual_intervention_events"
        ).fetchall()
    finally:
        conn.close()
    assert any("설명 안 되는 잔고 변화" in row[0] for row in rows)


async def test_reconcile_position_blends_entry_price_on_partial_external_buy_topup(
    monkeypatch, tmp_path,
):
    """Finding 2 — 포지션이 열려 있는 상태에서 매칭된 외부 매수주문으로 순매수(top-up)가
    발생하면, entry_qty만 갱신하고 entry_price를 그대로 두면 원가가 과소평가된다.
    거래량가중평균(volume-weighted average)으로 entry_price도 재계산해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")
    position_manager.open_position(strategy["id"], "KRW-BTC", 40_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0.02)]  # 0.01 보유 + 외부매수 0.01 = 0.02

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "bid", "filled_volume": 0.01, "filled_price": 60_000_000.0, "fee": 300.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result["action"] == "adjusted"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.02)
    # 가중평균 원가 = (40M*0.01 + 60M*0.01) / 0.02 = 50,000,000
    assert position["entry_price"] == pytest.approx(50_000_000.0)


async def test_reconcile_position_unexplained_open_uses_avg_buy_price(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0.02, avg_buy_price="48000000")]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result["action"] == "unexplained"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_price"] == 48_000_000.0
    assert position["entry_qty"] == pytest.approx(0.02)


async def test_run_reconcile_pipeline_absorbs_api_errors(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def failing_list_open_orders(*, market=None, states=None, client=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(upbit_client, "list_open_orders", failing_list_open_orders)

    result = await reconciler._run_reconcile_pipeline(strategy)

    assert "error" in result
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"


async def test_check_manual_intervention_runs_pipeline_directly(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.check_manual_intervention(strategy)

    assert result == {"balance_mismatch": False, "action": "none", "paused": False}


async def test_hydrate_state_runs_pipeline_when_baseline_already_set(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_accounts(*, client=None):
        return [_account(0)]

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.hydrate_state(strategy)

    assert result["baseline_captured"] is False
    assert result["balance_mismatch"] is False
    assert result["synced_wait_orders"] == 0
