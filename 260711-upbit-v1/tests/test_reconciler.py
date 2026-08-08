import json

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
