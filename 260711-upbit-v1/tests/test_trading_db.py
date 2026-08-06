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
