import sqlite3

import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from scripts import backfill_entry_fee as bf


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _seed_closed_trade(dbm, strategy_id, *, entry_fee_on_order=500.0, exit_time_kst_date="2026-08-10"):
    order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(order_id, "uuid-1", 100_000_000.0, 0.01, entry_fee_on_order, 0.0, "done")

    position_id = dbm.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    # entry_fee 없이(구버전) 계산된 realized_pnl을 그대로 흉내낸다: exit-fee만 차감된 값
    old_realized_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 505.0
    old_pct = old_realized_pnl / (100_000_000.0 * 0.01) * 100
    dbm.close_position_row(position_id, 101_000_000.0, 0.01, old_realized_pnl, old_pct, "signal")

    # exit_time을 UTC로 직접 덮어써 KST 날짜 변환이 결정적이도록 고정(2026-08-10 09:00 UTC = 18:00 KST)
    conn = sqlite3.connect(dbm.DB_PATH)
    conn.execute(
        "UPDATE positions SET exit_time = ? WHERE id = ?",
        (f"{exit_time_kst_date} 09:00:00", position_id),
    )
    conn.commit()
    conn.close()

    dbm.upsert_daily_performance(
        strategy_id, exit_time_kst_date, old_realized_pnl, old_pct, 1, 1, 0,
        100_000_000.0 * 0.01, 101_000_000.0 * 0.01 - 505.0,
    )
    return position_id, old_realized_pnl


def test_backfill_apply_corrects_realized_pnl_and_daily_performance(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 500.0
    assert position["realized_pnl"] == old_pnl - 500.0

    daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert daily["realized_pnl"] == old_pnl - 500.0


def test_backfill_dry_run_does_not_modify_db(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)

    bf.run(apply=False)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 0.0
    assert position["realized_pnl"] == old_pnl


def test_backfill_skips_strategy_when_order_and_position_counts_mismatch(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)
    # 매수 주문을 하나 더 추가해 개수 불일치를 만든다(매칭 불확실 케이스)
    extra_order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(extra_order_id, "uuid-2", 100_000_000.0, 0.01, 500.0, 0.0, "done")

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 0.0
    assert position["realized_pnl"] == old_pnl


def test_backfill_fills_entry_fee_only_for_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(order_id, "uuid-1", 100_000_000.0, 0.01, 500.0, 0.0, "done")
    position_id = dbm.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 500.0
    assert position["status"] == "open"
    assert position["realized_pnl"] is None
