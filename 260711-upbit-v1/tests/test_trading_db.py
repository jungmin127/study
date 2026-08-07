import sqlite3

import pytest

import trading.db as db_module


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    return db_module


def test_connect_creates_all_seven_tables(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {row[0] for row in rows}
    finally:
        conn.close()

    assert table_names == set(db.TABLE_NAMES)


def test_connect_creates_file_at_db_path(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert not db.DB_PATH.exists()
    db._connect().close()
    assert db.DB_PATH.exists()


def test_connect_is_idempotent(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    db._connect().close()
    db._connect().close()
    db._connect().close()  # 여러 번 호출해도 에러 없어야 함


def test_connect_enables_wal_mode(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert mode.lower() == "wal"


def test_foreign_keys_are_enforced(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO positions (id, live_strategy_id, market) VALUES (?, ?, ?)",
                ("pos-1", "nonexistent-strategy-id", "KRW-BTC"),
            )
    finally:
        conn.close()


def test_live_strategies_columns(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(live_strategies)")}
    finally:
        conn.close()

    assert columns == {
        "id", "source_run_id", "market", "timeframe", "buy_conditions_json",
        "sell_conditions_json", "risk_config_json", "current_capital", "status",
        "last_processed_candle_time", "created_at", "approved_at", "started_at",
        "stopped_at",
    }


def test_circuit_breaker_state_and_daily_performance_are_per_strategy(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    conn = db._connect()
    try:
        cb_columns = {row[1] for row in conn.execute("PRAGMA table_info(circuit_breaker_state)")}
        dp_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_performance)")}
    finally:
        conn.close()

    assert "live_strategy_id" in cb_columns
    assert "live_strategy_id" in dp_columns


from tests.trading_db_fixtures import insert_live_strategy


def test_get_live_strategy_returns_row_as_dict(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, market="KRW-ETH")

    result = db.get_live_strategy(strategy_id)

    assert result["id"] == strategy_id
    assert result["market"] == "KRW-ETH"
    assert result["status"] == "running"


def test_get_live_strategy_returns_none_when_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.get_live_strategy("nonexistent-id") is None


def test_update_live_strategy_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    db.update_live_strategy_status(strategy_id, "paused")

    assert db.get_live_strategy(strategy_id)["status"] == "paused"


def test_update_live_strategy_capital(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, current_capital=100000.0)

    db.update_live_strategy_capital(strategy_id, 105320.5)

    assert db.get_live_strategy(strategy_id)["current_capital"] == 105320.5


def test_update_live_strategy_last_candle(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.update_live_strategy_last_candle(strategy_id, "2026-08-07T10:00:00+00:00")

    assert db.get_live_strategy(strategy_id)["last_processed_candle_time"] == "2026-08-07T10:00:00+00:00"


def test_insert_position_and_get_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    open_position = db.get_open_position(strategy_id)
    assert open_position["id"] == position_id
    assert open_position["status"] == "open"
    assert open_position["entry_price"] == 100_000_000.0
    assert open_position["entry_qty"] == 0.01
    assert open_position["entry_time"] is not None


def test_get_open_position_returns_none_when_no_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_open_position(strategy_id) is None


def test_get_position_by_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = db.get_position(position_id)

    assert result["id"] == position_id
    assert result["live_strategy_id"] == strategy_id


def test_close_position_row_updates_fields_and_leaves_open_position_none(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.close_position_row(position_id, 101_000_000.0, 0.01, 9500.0, 0.95, "signal")

    closed = db.get_position(position_id)
    assert closed["status"] == "closed"
    assert closed["exit_price"] == 101_000_000.0
    assert closed["exit_qty"] == 0.01
    assert closed["realized_pnl"] == 9500.0
    assert closed["realized_pnl_pct"] == 0.95
    assert closed["close_reason"] == "signal"
    assert closed["exit_time"] is not None
    assert db.get_open_position(strategy_id) is None


def test_close_position_row_raises_when_position_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        db.close_position_row("nonexistent-id", 1.0, 1.0, 0.0, 0.0, "signal")


def test_close_position_row_raises_on_double_close(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    db.close_position_row(position_id, 101_000_000.0, 0.01, 9500.0, 0.95, "signal")

    with pytest.raises(ValueError):
        db.close_position_row(position_id, 102_000_000.0, 0.01, 19500.0, 1.95, "signal")

    # 두 번째 호출이 실패했으므로 첫 번째 호출의 값이 그대로 유지돼야 함
    closed = db.get_position(position_id)
    assert closed["exit_price"] == 101_000_000.0
    assert closed["realized_pnl"] == 9500.0


def test_circuit_breaker_state_upsert_then_get(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_circuit_breaker_state(strategy_id, "2026-08-07", 2, 0)
    result = db.get_circuit_breaker_state(strategy_id)

    assert result["trading_date"] == "2026-08-07"
    assert result["consecutive_losses"] == 2
    assert result["tripped"] == 0
    assert result["tripped_reason"] is None


def test_circuit_breaker_state_upsert_overwrites_existing_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_circuit_breaker_state(strategy_id, "2026-08-07", 1, 0)
    db.upsert_circuit_breaker_state(
        strategy_id, "2026-08-07", 3, 1, "consecutive_loss_limit", "2026-08-07T12:00:00+00:00",
    )

    result = db.get_circuit_breaker_state(strategy_id)
    assert result["consecutive_losses"] == 3
    assert result["tripped"] == 1
    assert result["tripped_reason"] == "consecutive_loss_limit"


def test_get_circuit_breaker_state_returns_none_when_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_circuit_breaker_state(strategy_id) is None


def test_daily_performance_upsert_then_get(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_daily_performance(
        strategy_id, "2026-08-07", 5000.0, 5.0, 1, 1, 0, 100_000.0, 105_000.0,
    )
    result = db.get_daily_performance(strategy_id, "2026-08-07")

    assert result["realized_pnl"] == 5000.0
    assert result["realized_pnl_pct"] == 5.0
    assert result["trade_count"] == 1
    assert result["win_count"] == 1
    assert result["loss_count"] == 0
    assert result["starting_balance"] == 100_000.0
    assert result["ending_balance"] == 105_000.0


def test_daily_performance_upsert_preserves_starting_balance_on_second_call(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    db.upsert_daily_performance(strategy_id, "2026-08-07", 5000.0, 5.0, 1, 1, 0, 100_000.0, 105_000.0)
    db.upsert_daily_performance(strategy_id, "2026-08-07", 3000.0, 3.0, 2, 1, 1, 999_999.0, 103_000.0)

    result = db.get_daily_performance(strategy_id, "2026-08-07")
    assert result["starting_balance"] == 100_000.0  # 두 번째 호출의 999_999.0으로 덮어써지지 않음
    assert result["ending_balance"] == 103_000.0
    assert result["trade_count"] == 2


def test_get_daily_performance_returns_none_when_not_found(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_daily_performance(strategy_id, "2026-08-07") is None
