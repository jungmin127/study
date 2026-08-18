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

    daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert daily["realized_pnl"] == old_pnl


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


def test_backfill_apply_does_not_double_correct_already_migrated_position(monkeypatch, tmp_path):
    """entry_fee가 이미 채워진 청산 포지션은(직전 실행에서 이미 백필됐거나, 버그 수정
    이후 코드로 진입/청산되어 close_position()이 이미 entry_fee를 반영한 경우) realized_pnl을
    다시 차감하면 안 된다 — 재실행/이미-올바른-데이터에 대해 이중 차감 방지."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(order_id, "uuid-1", 100_000_000.0, 0.01, 500.0, 0.0, "done")

    position_id = dbm.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01, entry_fee=500.0)
    # 이미 entry_fee가 반영되어 올바르게 계산된 realized_pnl(진입+청산 수수료 모두 차감됨)
    already_correct_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 505.0 - 500.0
    already_correct_pct = already_correct_pnl / (100_000_000.0 * 0.01) * 100
    dbm.close_position_row(
        position_id, 101_000_000.0, 0.01, already_correct_pnl, already_correct_pct, "signal",
    )

    conn = sqlite3.connect(dbm.DB_PATH)
    conn.execute(
        "UPDATE positions SET exit_time = ? WHERE id = ?",
        ("2026-08-10 09:00:00", position_id),
    )
    conn.commit()
    conn.close()

    dbm.upsert_daily_performance(
        strategy_id, "2026-08-10", already_correct_pnl, already_correct_pct, 1, 1, 0,
        100_000_000.0 * 0.01, 101_000_000.0 * 0.01 - 505.0,
    )

    bf.run(apply=True)

    position = dbm.get_position(position_id)
    assert position["entry_fee"] == 500.0
    assert position["realized_pnl"] == already_correct_pnl

    daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert daily["realized_pnl"] == already_correct_pnl


def test_recompute_daily_performance_preview_uses_corrected_pnl_not_stale_db_value(monkeypatch, tmp_path):
    """드라이런에서도 daily_performance 미리보기가 (아직 DB에 쓰이지 않은) 보정된
    realized_pnl을 반영해야 한다 — 재조회한 DB의 예전 값을 그대로 합산하면 안 된다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)
    corrected_pnl = old_pnl - 500.0

    preview = bf._recompute_daily_performance(
        strategy_id, apply=False, corrected_pnl_by_position={position_id: corrected_pnl},
    )

    assert preview["2026-08-10"] == corrected_pnl

    # 드라이런이므로 DB 자체는 여전히 예전 값 그대로여야 한다
    daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert daily["realized_pnl"] == old_pnl


def test_exit_kst_date_crosses_midnight_boundary():
    """09:00 UTC(같은 날 18:00 KST)만 테스트하면 +9시간 변환을 아예 빼먹어도 통과한다.
    자정을 넘기는 케이스(16:00 UTC -> 다음날 01:00 KST)로 실제 타임존 변환을 검증한다."""
    assert bf._exit_kst_date("2026-08-10 16:00:00") == "2026-08-11"
    assert bf._exit_kst_date("2026-08-10 14:59:59") == "2026-08-10"
