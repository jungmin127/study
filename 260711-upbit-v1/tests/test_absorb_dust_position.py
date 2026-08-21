import json

import pytest

import trading.db as db
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client
from tests.trading_db_fixtures import insert_live_strategy
from scripts import absorb_dust_position as adp


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _strategy_row(dbm, *, baseline_qty=0.0, current_capital=1_000_000.0):
    strategy_id = insert_live_strategy(
        dbm, market="KRW-BTC", current_capital=current_capital,
        risk_config_json=json.dumps({"order_execution_mode": "market"}),
    )
    dbm.update_live_strategy_baseline_qty(strategy_id, baseline_qty)
    return dbm.get_live_strategy(strategy_id)


def _account(balance, avg_buy_price="0"):
    return {"currency": "BTC", "balance": str(balance), "locked": "0",
            "avg_buy_price": avg_buy_price}


async def test_run_closes_dust_position_and_absorbs_baseline_without_touching_capital(
    monkeypatch, tmp_path,
):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm, baseline_qty=0.0, current_capital=1_000_000.0)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)  # ≈4,500원

    async def fake_get_accounts(*, client=None):
        return [_account(0.00009)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=False)

    assert position_manager.get_open_position(strategy["id"]) is None
    updated = dbm.get_live_strategy(strategy["id"])
    assert updated["baseline_qty"] == pytest.approx(0.00009)
    assert updated["current_capital"] == pytest.approx(1_000_000.0)  # 자본 불변
    conn = dbm._connect()
    try:
        rows = conn.execute(
            "SELECT action_taken FROM manual_intervention_events"
        ).fetchall()
    finally:
        conn.close()
    assert any(row[0] == "dust_absorbed_manual_cleanup" for row in rows)


async def test_run_dry_run_does_not_modify_anything(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)

    async def fake_get_accounts(*, client=None):
        return [_account(0.00009)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=False, force=False)

    assert position_manager.get_open_position(strategy["id"]) is not None
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == pytest.approx(0.0)


async def test_run_aborts_when_actual_balance_does_not_match_position(monkeypatch, tmp_path):
    """포지션 수량과 실제 잔고가 크게 다르면(더 큰 진짜 포지션과 혼동될 위험) 자동
    처리를 거부하고 사람이 직접 확인하게 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.00009)

    async def fake_get_accounts(*, client=None):
        return [_account(0.05)]  # 포지션(0.00009)과 실제 잔고(0.05)가 크게 다름

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=False)

    assert position_manager.get_open_position(strategy["id"]) is not None
    assert dbm.get_live_strategy(strategy["id"])["baseline_qty"] == pytest.approx(0.0)


async def test_run_refuses_without_force_when_value_at_or_above_min_order_amount(
    monkeypatch, tmp_path,
):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.001)  # 5만원

    async def fake_get_accounts(*, client=None):
        return [_account(0.001)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=False)

    assert position_manager.get_open_position(strategy["id"]) is not None


async def test_run_with_force_closes_position_above_min_order_amount(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy = _strategy_row(dbm)
    position_manager.open_position(strategy["id"], "KRW-BTC", 50_000_000.0, 0.001)

    async def fake_get_accounts(*, client=None):
        return [_account(0.001)]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    await adp.run(strategy["id"], apply=True, force=True)

    assert position_manager.get_open_position(strategy["id"]) is None
