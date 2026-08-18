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


def test_backfill_handles_manual_unexplained_closed_position_without_crashing(monkeypatch, tmp_path):
    """trading/reconciler.py의 _self_heal_unexplained()는 가격 근거가 없는 잔고 소멸을
    db.close_position_row(position_id, None, qty, None, None, "manual_unexplained")로 닫는다
    — realized_pnl이 NULL인 청산 포지션은 이 코드베이스에서 실제로 설계된 상태다. 이런
    포지션이 섞여 있어도 스크립트가 죽지 않고, entry_fee만 채운 뒤 realized_pnl 재계산과
    daily_performance 집계 대상에서는 제외해야 한다(애초에 record_trade_result()를 거친
    적이 없어 trade_count에 포함된 적도 없으므로)."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    # 정상 청산 포지션(비교 기준)
    normal_position_id, old_pnl = _seed_closed_trade(dbm, strategy_id)

    # manual_unexplained 청산 포지션 — exit_price/realized_pnl/realized_pnl_pct 전부 NULL
    order_id = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 50_000_000.0,
    )
    dbm.update_order_filled(order_id, "uuid-2", 50_000_000.0, 0.005, 250.0, 0.0, "done")
    unexplained_position_id = dbm.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.005)
    dbm.close_position_row(unexplained_position_id, None, 0.005, None, None, "manual_unexplained")

    # created_at/entry_time을 명시적으로 벌려 매칭 순서를 결정적으로 만든다(같은 초에
    # 만들어지면 정렬이 안정적이라는 보장이 없음).
    conn = sqlite3.connect(dbm.DB_PATH)
    order_ids = [
        row[0] for row in conn.execute(
            "SELECT id FROM orders WHERE live_strategy_id = ? ORDER BY rowid ASC", (strategy_id,),
        ).fetchall()
    ]
    for i, oid in enumerate(order_ids):
        conn.execute("UPDATE orders SET created_at = ? WHERE id = ?", (f"2026-08-10 08:0{i}:00", oid))
    position_ids = [
        row[0] for row in conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? ORDER BY rowid ASC", (strategy_id,),
        ).fetchall()
    ]
    for i, pid in enumerate(position_ids):
        conn.execute("UPDATE positions SET entry_time = ? WHERE id = ?", (f"2026-08-10 07:0{i}:00", pid))
    conn.commit()
    conn.close()

    bf.run(apply=True)

    normal_position = dbm.get_position(normal_position_id)
    assert normal_position["entry_fee"] == 500.0
    assert normal_position["realized_pnl"] == old_pnl - 500.0

    unexplained_position = dbm.get_position(unexplained_position_id)
    assert unexplained_position["entry_fee"] == 250.0
    assert unexplained_position["realized_pnl"] is None

    daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert daily["realized_pnl"] == old_pnl - 500.0


def test_backfill_skips_daily_performance_overwrite_when_trade_count_mismatches(monkeypatch, tmp_path):
    """daily_performance 행은 원래 record_trade_result()가 청산 시점에 기록한 trade_count를
    갖고 있다. 재계산 시 같은 (전략, 날짜)에서 실제로 발견되는 청산 건수가 그 trade_count와
    다르면(가장 그럴듯한 원인: KST 자정 근처에서 두 clock 판단이 어긋난 경우) 그 불일치
    자체가 신뢰할 수 없다는 신호이므로, 조용히 다른 숫자로 덮어쓰지 말고 경고만 남기고
    그 날짜의 행은 건드리지 않아야 한다."""
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    # trade_count=1로 기록된 daily_performance 행 — 정상적으로 거래 1건만 안 상태를 흉내낸다.
    position_id_1, old_pnl_1 = _seed_closed_trade(dbm, strategy_id, exit_time_kst_date="2026-08-10")

    # 같은 KST 날짜(2026-08-10)에 두 번째 청산 포지션을 추가해, 재계산이 실제로 찾는
    # 거래 수(2건)가 daily_performance 행이 알던 거래 수(1건)보다 많아지게 만든다.
    order_id_2 = dbm.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0,
    )
    dbm.update_order_filled(order_id_2, "uuid-2", 100_000_000.0, 0.01, 500.0, 0.0, "done")
    position_id_2 = dbm.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    old_pnl_2 = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 505.0
    old_pct_2 = old_pnl_2 / (100_000_000.0 * 0.01) * 100
    dbm.close_position_row(position_id_2, 101_000_000.0, 0.01, old_pnl_2, old_pct_2, "signal")

    conn = sqlite3.connect(dbm.DB_PATH)
    conn.execute(
        "UPDATE positions SET exit_time = ? WHERE id = ?",
        ("2026-08-10 10:00:00", position_id_2),
    )
    conn.commit()
    conn.close()

    before_daily = dbm.get_daily_performance(strategy_id, "2026-08-10")

    bf.run(apply=True)

    # 포지션 자체의 entry_fee/realized_pnl 보정은 daily_performance 검증과 별개 단계라서
    # 정상적으로 이뤄진다.
    p1 = dbm.get_position(position_id_1)
    p2 = dbm.get_position(position_id_2)
    assert p1["entry_fee"] == 500.0
    assert p1["realized_pnl"] == old_pnl_1 - 500.0
    assert p2["entry_fee"] == 500.0
    assert p2["realized_pnl"] == old_pnl_2 - 500.0

    # 하지만 daily_performance 행은 trade_count(1) != 재계산에서 찾은 거래 수(2)라서
    # 건드리지 않고 그대로 남아야 한다.
    after_daily = dbm.get_daily_performance(strategy_id, "2026-08-10")
    assert after_daily == before_daily
