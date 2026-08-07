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
