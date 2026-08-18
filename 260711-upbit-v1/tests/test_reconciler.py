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

    synced = await reconciler.sync_pending_limit_orders(strategy)

    assert len(synced) == 1
    assert synced[0]["id"] == order_id
    assert synced[0]["filled_price"] == 100.0
    assert synced[0]["filled_volume"] == 1.0
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

    synced = await reconciler.sync_pending_limit_orders(strategy)

    assert synced == []
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

    synced = await reconciler.sync_pending_limit_orders(strategy)

    assert synced == []
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


async def test_detect_external_orders_sets_manual_pause_so_auto_resume_cannot_revert(
    monkeypatch, tmp_path,
):
    """코드 리뷰 Critical 발견 — reconciler가 외부주문을 발견해 정지시키면 manual_pause=1도
    함께 세워야, signal_engine.py의 B그룹 자동재개 가드가 다음 poll에서 이 정지를 조용히
    되돌리지 못한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-manual-pause"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await reconciler._detect_external_orders(strategy)

    assert dbm.get_live_strategy(strategy["id"])["manual_pause"] == 1


async def test_detect_external_orders_multiple_orders_write_single_intervention_event(
    monkeypatch, tmp_path,
):
    """Minor 발견 — 한 사이클에 외부주문 N건이 발견돼도 상태갱신/개입이벤트는 한 사건당
    한 번만 기록해야 한다(주문마다 반복 기록하면 같은 사건이 N번 중복된다)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-a"}, {"uuid": "ext-uuid-b"}]

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

    assert len(found) == 2
    conn = dbm._connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM manual_intervention_events").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


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


async def test_detect_external_orders_ignores_closed_order_that_predates_strategy_start(
    monkeypatch, tmp_path,
):
    """실제 사고 재현 — DB 리셋 이후로 로컬 orders 테이블 기억에서 빠진 몇 주 전 실전
    테스트 주문이, 새 전략을 만들 때마다 매번 "외부주문"으로 오인돼 정지시켰다. 이미
    종결(done)됐고 전략 시작보다 훨씬 이전에 발생한 주문은 무시해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(
        dbm, baseline_qty=0.0, manual_intervention_policy="all_stop",
        started_at="2026-08-16 02:11:43",
    )

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return [{"uuid": "stale-uuid"}]

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "side": "ask", "ord_type": "limit",
                "created_at": "2026-08-12T05:00:36+09:00",
                "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert found == []
    assert dbm.get_order_by_upbit_uuid("stale-uuid") is None
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"


async def test_detect_external_orders_still_flags_closed_order_after_strategy_start(
    monkeypatch, tmp_path,
):
    """나이 필터가 과하게 넓어져 진짜 동시 개입까지 숨기면 안 된다 — 전략 시작 이후
    발생한 종결 주문은 여전히 외부주문으로 잡혀 정지해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(
        dbm, baseline_qty=0.0, manual_intervention_policy="all_stop",
        started_at="2026-08-16 02:11:43",
    )

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return [{"uuid": "fresh-external-uuid"}]

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "side": "ask", "ord_type": "limit",
                "created_at": "2026-08-16T02:12:07+00:00",
                "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert found[0]["upbit_uuid"] == "fresh-external-uuid"
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_detect_external_orders_still_flags_open_order_that_predates_strategy_start(
    monkeypatch, tmp_path,
):
    """아직 열려있는(wait) 주문은 나이와 무관하게 계속 추적해야 한다 — 나중에 체결/취소
    되며 baseline 캡처 이후의 실제 잔고를 바꿀 수 있기 때문이다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(
        dbm, baseline_qty=0.0, manual_intervention_policy="all_stop",
        started_at="2026-08-16 02:11:43",
    )

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "old-still-open-uuid"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "created_at": "2026-08-12T05:00:36+09:00",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert found[0]["upbit_uuid"] == "old-still-open-uuid"


async def test_detect_external_orders_flags_closed_order_when_strategy_started_at_missing(
    monkeypatch, tmp_path,
):
    """started_at을 모르면(예상 밖 상태) 안전한 쪽으로 기울여 기존 동작대로 계속
    추적한다 — 필터가 잘못된 침묵을 만들지 않도록."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")
    assert strategy["started_at"] is None

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return [{"uuid": "unknown-start-uuid"}]

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "done", "side": "ask", "ord_type": "limit",
                "created_at": "2026-08-12T05:00:36+09:00",
                "executed_volume": "1.0", "remaining_volume": "0",
                "paid_fee": "0", "trades": [{"funds": "100.0"}]}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    found = await reconciler._detect_external_orders(strategy)

    assert len(found) == 1
    assert found[0]["upbit_uuid"] == "unknown-start-uuid"


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
    assert position["entry_fee"] == 500.0


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
    assert dbm.get_live_strategy(strategy["id"])["manual_pause"] == 1


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
    assert dbm.get_live_strategy(strategy["id"])["manual_pause"] == 1
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.005)


async def test_reconcile_position_own_fills_mixed_side_does_not_pause(monkeypatch, tmp_path):
    """코드 리뷰 Minor 발견 — mixed_side/topup 가드로 정밀 self-heal은 못 타지만, 그
    잔고변화가 own_fills(우리 자신이 추적하는 주문)만으로 전부 설명되면 external_orders가
    전혀 없으므로 사람이 확인해야 할 수동개입이 아니다. 수량 self-heal은 하되 정지/기록은
    걸지 않아야 한다 — 그러지 않으면 own_fills-only는 정지시키지 않는다는 규칙(설명된
    변화 분기)과 모순된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_get_accounts(*, client=None):
        # 매수 0.01 - 매도 0.005 = 순증 0.005, 전부 own_fills
        return [_account(0.005, avg_buy_price="50500000")]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    own_fills = [
        {"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0},
        {"side": "ask", "filled_volume": 0.005, "filled_price": 51_000_000.0, "fee": 250.0},
    ]
    result = await reconciler._reconcile_position(strategy, [], own_fills=own_fills)

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": False}
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"
    assert dbm.get_live_strategy(strategy["id"])["manual_pause"] == 0
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.005)
    conn = dbm._connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM manual_intervention_events").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


async def test_reconcile_position_mixed_side_with_external_order_still_pauses(monkeypatch, tmp_path):
    """own_fills-only 예외가 진짜 외부주문이 섞인 mixed_side까지 조용히 지나치면 안 된다 —
    external_orders가 하나라도 있으면 종전대로 정지시켜야 한다(결정5 준수)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_get_accounts(*, client=None):
        return [_account(0.005)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    own_fills = [{"side": "ask", "filled_volume": 0.005, "filled_price": 51_000_000.0, "fee": 250.0}]
    result = await reconciler._reconcile_position(strategy, external_orders, own_fills=own_fills)

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": True}
    assert dbm.get_live_strategy(strategy["id"])["manual_pause"] == 1


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


async def test_reconcile_position_treats_topup_while_open_as_unexplained(
    monkeypatch, tmp_path,
):
    """최종 브랜치 리뷰 Important 3(사용자 확정) — 포지션이 열려 있는 상태에서 매칭된
    외부 매수주문으로 순매수(top-up)가 발생해도, 사용자 개인자금이 전략 자금에 조용히
    섞여 들어가는 걸 막기 위해 "정밀 self-heal"이 아니라 "설명 안 됨"으로 처리한다 —
    수량만 실제 잔고로 보정하고 entry_price는 건드리지 않으며, 정책과 무관하게
    강제 정지된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")
    position_manager.open_position(strategy["id"], "KRW-BTC", 40_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0.02)]  # 0.01 보유 + 외부매수 0.01 = 0.02

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [{"side": "bid", "filled_volume": 0.01, "filled_price": 60_000_000.0, "fee": 300.0}]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result["action"] == "unexplained"
    assert result["paused"] is True
    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == pytest.approx(0.02)
    assert position["entry_price"] == 40_000_000.0  # 원가는 건드리지 않음


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


async def test_check_manual_intervention_forwards_own_fills_to_reconcile_position(monkeypatch, tmp_path):
    """C1 — daemon이 넘긴 own_fills가 공개 진입점(check_manual_intervention)을 거쳐
    _reconcile_position까지 도달해 정밀 self-heal에 실제로 쓰이는지 검증한다. own_fills만
    으로 잔고 변화가 전부 설명되면 정책이 all_stop이어도 수동개입으로 처리되면 안 된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_accounts(*, client=None):
        return [_account(0.01)]

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    own_fills = [{"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler.check_manual_intervention(strategy, own_fills=own_fills)

    assert result == {"balance_mismatch": True, "action": "opened", "paused": False}
    position = position_manager.get_open_position(strategy["id"])
    assert position["entry_qty"] == 0.01
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"
    conn = dbm._connect()
    try:
        rows = conn.execute("SELECT COUNT(*) FROM manual_intervention_events").fetchone()
    finally:
        conn.close()
    assert rows[0] == 0


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


async def test_detect_external_orders_skips_cycle_when_own_order_in_flight(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 Critical 3 — order_executor가 방금 낸 주문은 체결/타임아웃 확인이
    끝나야 upbit_uuid가 기록된다. 그 사이 창(status='wait', upbit_uuid IS NULL)에
    reconciler가 돌면 그 주문이 거래소 목록엔 "새 주문"으로 보여 외부주문으로 오인되고,
    나중에 order_executor가 같은 uuid를 기록하려 할 때 UNIQUE 제약 위반이 난다. 그런
    미확정 주문이 있으면 이번 사이클은 아예 감지를 건너뛴다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)
    dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)
    calls = {"count": 0}

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        calls["count"] += 1
        return [{"uuid": "some-uuid"}]

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)

    found = await reconciler._detect_external_orders(strategy)

    assert found == []
    assert calls["count"] == 0


async def test_detect_external_orders_ignores_stale_in_flight_order(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 재검토 Important 1 — 미확정(upbit_uuid IS NULL) 주문이라도 그
    전략의 order_timeout_sec + 여유분보다 오래됐으면 더는 "방금 낸 주문"으로 보지 않는다.
    나이 제한이 없으면 주문 실패 후 정리되지 않은 행이나 limit_timeout의 의도적으로
    'wait'인 채 남는 잔량 전환 행 때문에 그 전략의 외부주문 감지가 영구히 멈춘다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)
    order_id = dbm.insert_order(strategy["id"], None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)
    conn = dbm._connect()
    try:
        conn.execute(
            "UPDATE orders SET created_at = datetime('now', '-1 hour') WHERE id = ?",
            (order_id,),
        )
        conn.commit()
    finally:
        conn.close()
    calls = {"count": 0}

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        calls["count"] += 1
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)

    found = await reconciler._detect_external_orders(strategy)

    assert found == []
    assert calls["count"] == 1  # 나이 제한을 넘겼으니 감지 로직이 정상적으로 실행됐다


async def test_reconcile_position_own_fills_alone_do_not_trigger_manual_intervention(
    monkeypatch, tmp_path,
):
    """최종 브랜치 리뷰 Critical 1 — 오프라인 중 체결된 우리 자신의 plain limit 주문
    (own_fills)만으로 잔고 변화가 전부 설명되면, 이건 수동개입이 아니다. paused는 항상
    False여야 하고 manual_intervention_events도 기록되면 안 된다(정책이 all_stop이어도)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="all_stop")

    async def fake_get_accounts(*, client=None):
        return [_account(0.01)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    own_fills = [{"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0}]
    result = await reconciler._reconcile_position(strategy, [], own_fills=own_fills)

    assert result == {"balance_mismatch": True, "action": "opened", "paused": False}
    assert dbm.get_live_strategy(strategy["id"])["status"] == "running"
    conn = dbm._connect()
    try:
        rows = conn.execute("SELECT COUNT(*) FROM manual_intervention_events").fetchone()
    finally:
        conn.close()
    assert rows[0] == 0


async def test_run_reconcile_pipeline_rereads_strategy_row_not_stale_dict(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 Critical 2 — 호출자가 baseline_qty를 캡처하기 전의 오래된
    strategy dict를 그대로 들고 있다가 넘겨도, 파이프라인은 DB에서 최신 행을 다시 읽어야
    한다. 그렇지 않으면 baseline 격리(결정9)가 무력화된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.05)
    stale_strategy = dict(strategy)
    stale_strategy["baseline_qty"] = None  # 캡처 이전 시점의 오래된 스냅샷을 흉내낸다

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_accounts(*, client=None):
        return [_account(0.05)]  # baseline과 정확히 일치 — 봇 자신의 포지션은 0

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._run_reconcile_pipeline(stale_strategy)

    assert result == {"balance_mismatch": False, "action": "none", "paused": False}


async def test_detect_external_orders_unrecognized_policy_still_pauses(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 Important 4 — manual_intervention_policy에 오타/미지원 값이 들어와도
    안전한 쪽(정지)으로 기울여야 한다. "acknowledge_and_continue"를 명시적으로 고른
    경우에만 계속 진행한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="some_typo")

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return [{"uuid": "ext-uuid-typo"}]

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    async def fake_get_order(*, uuid=None, identifier=None, client=None):
        return {"state": "wait", "side": "bid", "ord_type": "limit",
                "executed_volume": "0", "remaining_volume": "1.0",
                "paid_fee": "0", "trades": []}

    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)
    monkeypatch.setattr(upbit_client, "get_order", fake_get_order)

    await reconciler._detect_external_orders(strategy)

    assert dbm.get_live_strategy(strategy["id"])["status"] == "paused"


async def test_self_heal_unexplained_skips_open_when_avg_buy_price_is_zero(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 Important 6 — avg_buy_price가 0(입금/에어드롭 등으로 업비트가
    원가를 추적하지 못한 코인)이면 entry_price=0으로 포지션을 열지 않는다 — 그렇게 열면
    나중에 정상 청산 시 ZeroDivisionError로 죽는다. 정지는 여전히 걸린다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0)

    async def fake_get_accounts(*, client=None):
        return [_account(0.02, avg_buy_price="0")]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler._reconcile_position(strategy, [])

    assert result == {"balance_mismatch": True, "action": "unexplained", "paused": True}
    assert position_manager.get_open_position(strategy["id"]) is None


async def test_hydrate_state_first_call_excludes_existing_position_from_baseline(
    monkeypatch, tmp_path,
):
    """최종 브랜치 리뷰 Important 7 — 스키마가 나중에 기존에 매매 중이던 전략에 추가되는
    등, baseline_qty가 없는 채로 오픈 포지션이 이미 존재하는 상태에서 첫 hydrate_state가
    불리면, 그 포지션 수량까지 baseline으로 흡수해버려서 다음 사이클에 포지션이 통째로
    사라진다. 오픈 포지션 수량은 baseline 계산에서 제외해야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.01)

    async def fake_get_accounts(*, client=None):
        return [_account(0.03)]  # 봇 포지션 0.01 + 승인 전부터 보유하던 0.02

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    result = await reconciler.hydrate_state(strategy)

    assert result["baseline_captured"] is True
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == pytest.approx(0.02)


async def test_reconcile_position_mixed_side_treated_as_unexplained(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 Important 1(사용자 확정) — 한 틱 안에 매수/매도 외부주문이 동시에
    매칭되면(설계 스펙 에러 처리 절), 순매매량이 실제 잔고차와 정확히 맞아떨어져도
    "설명 안 됨"으로 처리한다. 방향이 섞인 자동 매칭은 오탐 위험이 크다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, manual_intervention_policy="acknowledge_and_continue")

    async def fake_get_accounts(*, client=None):
        return [_account(0.005)]  # 매수 0.01 - 매도 0.005 = 순증 0.005

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    external_orders = [
        {"side": "bid", "filled_volume": 0.01, "filled_price": 50_000_000.0, "fee": 500.0},
        {"side": "ask", "filled_volume": 0.005, "filled_price": 51_000_000.0, "fee": 250.0},
    ]
    result = await reconciler._reconcile_position(strategy, external_orders)

    assert result["action"] == "unexplained"
    assert result["paused"] is True


async def test_hydrate_state_rereads_strategy_row_not_stale_dict(monkeypatch, tmp_path):
    """최종 브랜치 리뷰 재검토 Important 2 — _run_reconcile_pipeline은 최신 행을 다시
    읽지만, hydrate_state 자신의 baseline_qty is None 판단은 그 재읽기보다 먼저 일어난다.
    호출자가 baseline이 이미 잡힌 뒤의 오래된 dict(baseline_qty=None)를 재사용해도,
    hydrate_state가 스스로 최신 행을 다시 읽지 않으면 이미 잡아둔 baseline을 다시
    캡처해버려 그 사이 생긴 실제 잔고변화를 baseline으로 흡수해버린다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.05)
    stale_strategy = dict(strategy)
    stale_strategy["baseline_qty"] = None  # baseline 캡처 이전 시점의 오래된 스냅샷

    async def fake_get_accounts(*, client=None):
        return [_account(0.08)]  # 그 사이 사용자가 0.03을 추가로 수동매수했다고 가정

    async def fake_list_open_orders(*, market=None, states=None, client=None):
        return []

    async def fake_list_closed_orders(*, market=None, states=None, client=None):
        return []

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)
    monkeypatch.setattr(upbit_client, "list_open_orders", fake_list_open_orders)
    monkeypatch.setattr(upbit_client, "list_closed_orders", fake_list_closed_orders)

    result = await reconciler.hydrate_state(stale_strategy)

    assert result["baseline_captured"] is False
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == 0.05  # 재캡처되지 않음
    assert result["balance_mismatch"] is True  # 0.03 증가가 정상적으로 불일치로 감지됨
