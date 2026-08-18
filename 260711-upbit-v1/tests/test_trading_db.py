import sqlite3

import pytest

import trading.db as db_module


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    return db_module


def test_connect_creates_all_tables(monkeypatch, tmp_path):
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


def test_connect_does_not_reexecute_schema_script_on_second_call(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        db, "_SCHEMA",
        "CREATE TABLE strict_once (id INTEGER);",  # IF NOT EXISTS 없음 — 두 번째 실행되면 에러
    )

    db._connect().close()  # 첫 호출: 정상 생성
    db._connect().close()  # 캐싱이 안 되면 sqlite3.OperationalError: table strict_once already exists


def test_connect_initializes_schema_separately_for_each_db_path(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    db._connect().close()  # 첫 경로 초기화

    other_path = tmp_path / "other" / "trading.db"
    monkeypatch.setattr(db, "DB_PATH", other_path)

    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {row[0] for row in rows}
    finally:
        conn.close()

    assert table_names == set(db.TABLE_NAMES)


def test_connect_raises_when_signals_table_predates_unique_constraint(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    # 제약 추가 이전 버전의 signals 테이블을 직접 만들어, "제약이 없는 채로 이미
    # 존재하는 DB 파일"을 흉내낸다 — _connect()는 CREATE TABLE IF NOT EXISTS라
    # 이 테이블을 그대로 두고 넘어가므로, 그 상태를 감지해서 크게 실패해야 한다.
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE signals (
            id TEXT PRIMARY KEY,
            live_strategy_id TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            candle_time TEXT NOT NULL,
            indicator_snapshot_json TEXT,
            resulting_order_id TEXT,
            skip_reason TEXT,
            triggered_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="UNIQUE"):
        db._connect()


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
        "manual_pause", "last_processed_candle_time", "created_at", "approved_at",
        "started_at", "stopped_at", "baseline_qty",
    }


def test_connect_raises_when_live_strategies_table_predates_manual_pause_column(monkeypatch, tmp_path):
    """Fix 1 — manual_pause 컬럼이 없는 기존 live_strategies 테이블(CREATE TABLE IF NOT
    EXISTS로는 컬럼이 소급 추가되지 않는다)을 흉내내, _connect()가 이를 감지해 크게
    실패해야 한다(개발 단계 무마이그레이션 정책, signals UNIQUE 가드와 동일 패턴)."""
    db = _fresh_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE live_strategies (
            id                  TEXT PRIMARY KEY,
            source_run_id       TEXT,
            market              TEXT NOT NULL,
            timeframe           TEXT NOT NULL,
            buy_conditions_json TEXT NOT NULL,
            sell_conditions_json TEXT NOT NULL,
            risk_config_json    TEXT NOT NULL,
            current_capital     REAL,
            status              TEXT NOT NULL DEFAULT 'draft',
            last_processed_candle_time TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            approved_at         TEXT,
            started_at          TEXT,
            stopped_at          TEXT,
            baseline_qty        REAL
        )
    """)
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="manual_pause"):
        db._connect()


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


def test_insert_signal_creates_row_with_null_resulting_order_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    signal_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"RSI__[(\'period\', 14)]": 25.0}',
    )

    conn = db._connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()

    assert row["live_strategy_id"] == strategy_id
    assert row["signal_type"] == "buy"
    assert row["candle_time"] == "2026-08-07T10:00:00+00:00"
    assert row["resulting_order_id"] is None
    assert row["skip_reason"] is None
    assert row["triggered_at"] is not None


def test_insert_signal_stores_skip_reason(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    signal_id = db.insert_signal(
        strategy_id, "sell", "2026-08-07T10:00:00+00:00", "{}", skip_reason="unknown",
    )

    conn = db._connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT skip_reason FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()

    assert row["skip_reason"] == "unknown"


def test_insert_signal_is_idempotent_for_same_strategy_type_and_candle_time(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    first_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"a": 1}',
    )
    second_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"a": 2}',  # 다른 snapshot이어도 무시됨
    )

    assert second_id == first_id
    conn = db._connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE live_strategy_id = ? AND signal_type = ? AND candle_time = ?",
            (strategy_id, "buy", "2026-08-07T10:00:00+00:00"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_insert_signal_allows_different_signal_types_for_same_candle(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    buy_id = db.insert_signal(strategy_id, "buy", "2026-08-07T10:00:00+00:00", "{}")
    sell_id = db.insert_signal(strategy_id, "sell", "2026-08-07T10:00:00+00:00", "{}")

    assert buy_id != sell_id


def test_insert_signal_keeps_first_row_data_on_idempotent_hit(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    first_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"a": 1}',
        skip_reason="unknown:FUNDING_RATE",
    )
    second_id = db.insert_signal(
        strategy_id, "buy", "2026-08-07T10:00:00+00:00", '{"a": 2}',
        skip_reason=None,
    )

    assert second_id == first_id
    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (first_id,)).fetchone()
    finally:
        conn.close()
    assert row["indicator_snapshot_json"] == '{"a": 1}'
    assert row["skip_reason"] == "unknown:FUNDING_RATE"


def test_insert_order_creates_wait_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    order_id = db.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", 50000000.0, 0.01, 50000000.0,
    )

    order = db.get_order_by_id(order_id)
    assert order["live_strategy_id"] == strategy_id
    assert order["position_id"] is None
    assert order["market"] == "KRW-BTC"
    assert order["side"] == "bid"
    assert order["order_type"] == "market"
    assert order["requested_price"] == 50000000.0
    assert order["requested_volume"] == 0.01
    assert order["expected_price"] == 50000000.0
    assert order["status"] == "wait"
    assert order["replaces_order_id"] is None


def test_insert_order_with_replaces_order_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    original_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)

    child_id = db.insert_order(
        strategy_id, None, "KRW-BTC", "bid", "market", None, 0.5, 100.0,
        replaces_order_id=original_id,
    )

    order = db.get_order_by_id(child_id)
    assert order["replaces_order_id"] == original_id


def test_update_order_filled_sets_fill_fields_and_updated_at(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    order_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)

    db.update_order_filled(order_id, "upbit-uuid-1", 101.0, 1.0, 0.05, 1.0, "done")

    order = db.get_order_by_id(order_id)
    assert order["upbit_uuid"] == "upbit-uuid-1"
    assert order["filled_price"] == 101.0
    assert order["filled_volume"] == 1.0
    assert order["fee"] == 0.05
    assert order["slippage_pct"] == 1.0
    assert order["status"] == "done"
    assert order["updated_at"] is not None


def test_get_order_by_id_returns_none_when_missing(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.get_order_by_id("nonexistent") is None


def test_update_signal_result_sets_resulting_order_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    signal_id = db.insert_signal(strategy_id, "buy", "2026-08-08T10:00:00+00:00", "{}")
    order_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)

    db.update_signal_result(signal_id, order_id, None)

    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()
    assert row["resulting_order_id"] == order_id
    assert row["skip_reason"] is None


def test_update_signal_result_sets_skip_reason_without_order(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    signal_id = db.insert_signal(strategy_id, "buy", "2026-08-08T10:00:00+00:00", "{}")

    db.update_signal_result(signal_id, None, "circuit_breaker_tripped")

    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    finally:
        conn.close()
    assert row["resulting_order_id"] is None
    assert row["skip_reason"] == "circuit_breaker_tripped"


def test_update_live_strategy_baseline_qty_sets_value(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    assert db.get_live_strategy(strategy_id)["baseline_qty"] is None

    db.update_live_strategy_baseline_qty(strategy_id, 0.05)

    assert db.get_live_strategy(strategy_id)["baseline_qty"] == 0.05


def test_get_order_by_upbit_uuid_returns_none_when_missing(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.get_order_by_upbit_uuid("nonexistent-uuid") is None


def test_insert_external_order_is_findable_by_upbit_uuid(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    order_id = db.insert_external_order(
        strategy_id, None, "KRW-BTC", "bid", "limit", "upbit-ext-1",
        50_000_000.0, 0.01, 500.0, "done",
    )

    found = db.get_order_by_upbit_uuid("upbit-ext-1")
    assert found["id"] == order_id
    assert found["is_external"] == 1
    assert found["status"] == "done"
    assert found["filled_price"] == 50_000_000.0
    assert found["live_strategy_id"] == strategy_id


def test_insert_manual_intervention_event_creates_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)

    event_id = db.insert_manual_intervention_event(
        "KRW-BTC", "설명 안 되는 잔고 변화", "all_stop",
    )

    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM manual_intervention_events WHERE id = ?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["market"] == "KRW-BTC"
    assert row["description"] == "설명 안 되는 잔고 변화"
    assert row["action_taken"] == "all_stop"
    assert row["detected_at"] is not None


def test_list_wait_orders_filters_by_status_and_optional_type(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    wait_limit_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    wait_market_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", 100.0, 1.0, 100.0)
    done_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "limit", 100.0, 1.0, 100.0)
    db.update_order_filled(done_id, "uuid-done", 100.0, 1.0, 0.0, 0.0, "done")

    all_wait = db.list_wait_orders(strategy_id)
    limit_only = db.list_wait_orders(strategy_id, order_type="limit")

    assert {o["id"] for o in all_wait} == {wait_limit_id, wait_market_id}
    assert {o["id"] for o in limit_only} == {wait_limit_id}


def test_list_wait_orders_filters_by_position_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_a = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    position_b = db.insert_position(strategy_id, "KRW-BTC", 51_000_000.0, 0.02)
    order_a = db.insert_order(strategy_id, position_a, "KRW-BTC", "ask", "limit", 100.0, 1.0, 100.0)
    order_b = db.insert_order(strategy_id, position_b, "KRW-BTC", "ask", "limit", 100.0, 1.0, 100.0)

    result = db.list_wait_orders(strategy_id, position_id=position_a)

    assert {o["id"] for o in result} == {order_a}
    assert order_b not in {o["id"] for o in result}


def test_adjust_position_qty_updates_open_position_only(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    db.adjust_position_qty(position_id, 0.006)

    assert db.get_position(position_id)["entry_qty"] == 0.006


def test_adjust_position_qty_with_new_entry_price_updates_both(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    db.adjust_position_qty(position_id, 0.02, 51_000_000.0)

    position = db.get_position(position_id)
    assert position["entry_qty"] == 0.02
    assert position["entry_price"] == 51_000_000.0


def test_adjust_position_qty_without_new_entry_price_leaves_price_unchanged(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    db.adjust_position_qty(position_id, 0.006)

    position = db.get_position(position_id)
    assert position["entry_qty"] == 0.006
    assert position["entry_price"] == 50_000_000.0


def test_list_active_strategies_returns_only_running_and_paused(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    running_id = insert_live_strategy(db, status="running")
    paused_id = insert_live_strategy(db, status="paused")
    insert_live_strategy(db, status="draft")
    insert_live_strategy(db, status="approved")
    insert_live_strategy(db, status="stopped")

    active = db.list_active_strategies()

    assert {s["id"] for s in active} == {running_id, paused_id}


def test_positions_have_stale_resolution_columns_defaulting_to_zero(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    position = db.get_position(position_id)

    assert position["stale_resolved_qty"] == 0
    assert position["stale_resolved_proceeds"] == 0
    assert position["stale_resolved_fee"] == 0


def test_accumulate_stale_resolution_adds_to_existing_totals(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    db.accumulate_stale_resolution(position_id, 0.004, 200_000.0, 50.0)
    db.accumulate_stale_resolution(position_id, 0.002, 100_000.0, 25.0)

    position = db.get_position(position_id)
    assert position["stale_resolved_qty"] == pytest.approx(0.006)
    assert position["stale_resolved_proceeds"] == pytest.approx(300_000.0)
    assert position["stale_resolved_fee"] == pytest.approx(75.0)


def test_accumulate_stale_resolution_does_not_affect_other_positions(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_a = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    position_b = db.insert_position(strategy_id, "KRW-BTC", 51_000_000.0, 0.02)

    db.accumulate_stale_resolution(position_a, 0.004, 200_000.0, 50.0)

    assert db.get_position(position_a)["stale_resolved_qty"] == pytest.approx(0.004)
    assert db.get_position(position_b)["stale_resolved_qty"] == 0


def test_insert_live_strategy_creates_draft_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = db.insert_live_strategy(
        source_run_id="run-1", market="KRW-BTC", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}", risk_config_json="{}",
    )

    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "draft"
    assert strategy["source_run_id"] == "run-1"
    assert strategy["market"] == "KRW-BTC"
    assert strategy["timeframe"] == "minutes60"
    assert strategy["current_capital"] is None
    assert strategy["approved_at"] is None


def test_insert_live_strategy_allows_null_source_run_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = db.insert_live_strategy(
        source_run_id=None, market="KRW-ETH", timeframe="days",
        buy_conditions_json="{}", sell_conditions_json="{}", risk_config_json="{}",
    )

    strategy = db.get_live_strategy(strategy_id)
    assert strategy["source_run_id"] is None


def test_list_live_strategies_returns_all_statuses_newest_first(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    older_id = insert_live_strategy(db, status="stopped")
    newer_id = db.insert_live_strategy(
        source_run_id=None, market="KRW-BTC", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}", risk_config_json="{}",
    )

    rows = db.list_live_strategies()

    assert [r["id"] for r in rows] == [newer_id, older_id]


def test_list_live_strategies_returns_empty_list_when_none(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    assert db.list_live_strategies() == []


def test_approve_live_strategy_transitions_draft_to_running(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft", current_capital=None)

    result = db.approve_live_strategy(strategy_id, 150000.0)

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "running"
    assert strategy["current_capital"] == 150000.0
    assert strategy["approved_at"] is not None
    assert strategy["started_at"] is not None


def test_approve_live_strategy_returns_false_when_not_draft(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running", current_capital=100000.0)

    result = db.approve_live_strategy(strategy_id, 150000.0)

    assert result is False
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["current_capital"] == 100000.0
    assert strategy["approved_at"] is None


def test_pause_live_strategy_manually_sets_status_and_manual_pause_flag(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    result = db.pause_live_strategy_manually(strategy_id)

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "paused"
    assert strategy["manual_pause"] == 1


def test_pause_live_strategy_manually_refuses_when_not_running(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft")

    result = db.pause_live_strategy_manually(strategy_id)

    assert result is False
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "draft"
    assert strategy["manual_pause"] == 0


def test_resume_live_strategy_manually_sets_status_and_clears_manual_pause_flag(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="paused", manual_pause=1)

    result = db.resume_live_strategy_manually(strategy_id)

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "running"
    assert strategy["manual_pause"] == 0


def test_resume_live_strategy_manually_refuses_when_not_paused(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    result = db.resume_live_strategy_manually(strategy_id)

    assert result is False
    assert db.get_live_strategy(strategy_id)["status"] == "running"


def test_resume_live_strategy_manually_clears_tripped_circuit_breaker_preserving_audit_fields(
    monkeypatch, tmp_path,
):
    """Fix 1 — 수동 재개는 서킷브레이커 트립도 함께 해제해야 하지만(그렇지 않으면
    daemon이 다음 신호 평가에서 여전히 트립 상태로 취급해 재개가 무의미해진다),
    tripped_reason/tripped_at/consecutive_losses 같은 감사 이력은 지우면 안 된다."""
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="paused", manual_pause=1)
    db.upsert_circuit_breaker_state(
        strategy_id, "2026-08-11", 3, 1, "daily_loss_limit", "2026-08-11T01:00:00+00:00",
    )

    result = db.resume_live_strategy_manually(strategy_id)

    assert result is True
    assert db.get_live_strategy(strategy_id)["status"] == "running"
    cb_state = db.get_circuit_breaker_state(strategy_id)
    assert cb_state["tripped"] == 0
    assert cb_state["tripped_reason"] == "daily_loss_limit"
    assert cb_state["tripped_at"] == "2026-08-11T01:00:00+00:00"
    assert cb_state["consecutive_losses"] == 3
    assert cb_state["trading_date"] == "2026-08-11"
    assert cb_state["resumed_at"] is not None


def test_resume_live_strategy_manually_does_not_touch_circuit_breaker_when_not_tripped(
    monkeypatch, tmp_path,
):
    """트립되지 않은(흔한 경우) 수동 일시정지 재개는 circuit_breaker_state가 아예
    없거나 tripped=0인 행을 새로 만들거나 건드리지 않아야 한다."""
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="paused", manual_pause=1)

    result = db.resume_live_strategy_manually(strategy_id)

    assert result is True
    assert db.get_circuit_breaker_state(strategy_id) is None


def test_stop_live_strategy_if_no_open_position_stops_when_no_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    result = db.stop_live_strategy_if_no_open_position(strategy_id)

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["status"] == "stopped"
    assert strategy["stopped_at"] is not None


def test_stop_live_strategy_if_no_open_position_refuses_when_position_open(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    result = db.stop_live_strategy_if_no_open_position(strategy_id)

    assert result is False
    assert db.get_live_strategy(strategy_id)["status"] == "running"


def test_stop_live_strategy_if_no_open_position_allows_stopping_after_position_closed(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    db.close_position_row(position_id, 51_000_000.0, 0.01, 10000.0, 2.0, "signal")

    result = db.stop_live_strategy_if_no_open_position(strategy_id)

    assert result is True
    assert db.get_live_strategy(strategy_id)["status"] == "stopped"


def test_list_daily_performance_returns_rows_ordered_by_date(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    db.upsert_daily_performance(
        strategy_id, "2026-08-12", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0,
    )
    db.upsert_daily_performance(
        strategy_id, "2026-08-10", -500.0, -0.5, 1, 0, 1, 100_500.0, 100_000.0,
    )

    rows = db.list_daily_performance(strategy_id)

    assert [r["trading_date"] for r in rows] == ["2026-08-10", "2026-08-12"]


def test_list_daily_performance_scoped_to_strategy(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_a = insert_live_strategy(db)
    strategy_b = insert_live_strategy(db)
    db.upsert_daily_performance(
        strategy_a, "2026-08-10", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0,
    )
    db.upsert_daily_performance(
        strategy_b, "2026-08-10", -500.0, -0.5, 1, 0, 1, 200_000.0, 199_500.0,
    )

    rows = db.list_daily_performance(strategy_a)

    assert len(rows) == 1
    assert rows[0]["live_strategy_id"] == strategy_a


def test_list_closed_positions_excludes_open_and_orders_newest_first(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    open_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    older_id = db.insert_position(strategy_id, "KRW-BTC", 49_000_000.0, 0.01)
    newer_id = db.insert_position(strategy_id, "KRW-BTC", 51_000_000.0, 0.01)
    db.close_position_row(older_id, 49_500_000.0, 0.01, 5000.0, 1.0, "take_profit")
    db.close_position_row(newer_id, 51_500_000.0, 0.01, 5000.0, 1.0, "take_profit")

    rows = db.list_closed_positions(strategy_id)

    assert [r["id"] for r in rows] == [newer_id, older_id]
    assert open_id not in [r["id"] for r in rows]


def test_list_orders_for_strategy_returns_all_orders(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-BTC", "bid", "market", None, 0.01, 50_000_000.0,
    )
    db.update_order_filled(order_id, "uuid-1", 50_010_000.0, 0.01, 25.0, 0.02, "done")

    rows = db.list_orders_for_strategy(strategy_id)

    assert len(rows) == 1
    assert rows[0]["slippage_pct"] == 0.02


def test_delete_live_strategy_removes_strategy_and_child_rows(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="stopped")
    position_id = db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-BTC", "buy", "market", None, None, 50_000_000.0,
    )
    db.insert_signal(strategy_id, "buy", "2026-08-17T00:00:00", "{}")
    db.upsert_daily_performance(
        strategy_id, "2026-08-17", 0.0, 0.0, 0, 0, 0, 100000.0, 100000.0,
    )
    db.upsert_circuit_breaker_state(strategy_id, "2026-08-17", 0, 0)
    db.insert_capital_adjustment(strategy_id, 100000.0, 200000.0)

    deleted = db.delete_live_strategy(strategy_id)

    assert deleted is True
    assert db.get_live_strategy(strategy_id) is None
    assert db.get_position(position_id) is None
    assert db.get_order_by_id(order_id) is None
    assert db.get_circuit_breaker_state(strategy_id) is None
    assert db.get_daily_performance(strategy_id, "2026-08-17") is None
    assert db.list_capital_adjustments(strategy_id) == []


def test_delete_live_strategy_rejects_non_stopped_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")

    deleted = db.delete_live_strategy(strategy_id)

    assert deleted is False
    assert db.get_live_strategy(strategy_id) is not None


def test_delete_live_strategy_returns_false_for_missing_id(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)

    assert db.delete_live_strategy("does-not-exist") is False


def test_insert_capital_adjustment_persists_fields(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    adjustment_id = db.insert_capital_adjustment(strategy_id, 500_000.0, 800_000.0)

    rows = db.list_capital_adjustments(strategy_id)
    assert len(rows) == 1
    assert rows[0]["id"] == adjustment_id
    assert rows[0]["previous_capital"] == 500_000.0
    assert rows[0]["new_capital"] == 800_000.0
    assert rows[0]["delta"] == 300_000.0


def test_list_capital_adjustments_orders_ascending_by_adjusted_at(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    first_id = db.insert_capital_adjustment(strategy_id, 500_000.0, 1_000_000.0)
    second_id = db.insert_capital_adjustment(strategy_id, 1_000_000.0, 800_000.0)

    conn = db._connect()
    try:
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-01 09:00:00' WHERE id = ?",
            (first_id,),
        )
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-02 09:00:00' WHERE id = ?",
            (second_id,),
        )
        conn.commit()
    finally:
        conn.close()

    rows = db.list_capital_adjustments(strategy_id)

    assert [r["id"] for r in rows] == [first_id, second_id]


def test_list_capital_adjustments_returns_empty_for_strategy_with_none(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    assert db.list_capital_adjustments(strategy_id) == []


def test_connect_adds_entry_fee_column_to_existing_positions_table(monkeypatch, tmp_path):
    """entry_fee는 실거래 중인 프로덕션 DB에 적용해야 해서, 다른 컬럼들의 "크게 실패시키는
    assert" 패턴과 달리 실제 ALTER TABLE로 추가한다(무마이그레이션 정책의 유일한 예외)."""
    db = _fresh_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE positions (
            id               TEXT PRIMARY KEY,
            live_strategy_id TEXT NOT NULL,
            market           TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'open',
            entry_price      REAL,
            entry_qty        REAL,
            entry_time       TEXT,
            exit_price       REAL,
            exit_qty         REAL,
            exit_time        TEXT,
            realized_pnl     REAL,
            realized_pnl_pct REAL,
            close_reason     TEXT,
            stale_resolved_qty      REAL NOT NULL DEFAULT 0,
            stale_resolved_proceeds REAL NOT NULL DEFAULT 0,
            stale_resolved_fee      REAL NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("INSERT INTO positions (id, live_strategy_id, market) VALUES ('p1', 's1', 'KRW-BTC')")
    conn.commit()
    conn.close()

    db._connect()

    conn = sqlite3.connect(db.DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
        row = conn.execute("SELECT entry_fee FROM positions WHERE id = 'p1'").fetchone()
    finally:
        conn.close()
    assert "entry_fee" in columns
    assert row[0] == 0


def test_insert_position_stores_entry_fee(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01, entry_fee=500.0)

    position = db.get_position(position_id)
    assert position["entry_fee"] == 500.0


def test_insert_position_defaults_entry_fee_to_zero(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)

    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    assert db.get_position(position_id)["entry_fee"] == 0.0


def test_update_position_entry_fee_updates_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_position_entry_fee(position_id, 777.0)

    assert db.get_position(position_id)["entry_fee"] == 777.0


def test_update_position_realized_pnl_updates_closed_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)
    db.close_position_row(position_id, 101_000_000.0, 0.01, 9500.0, 0.95, "signal")

    db.update_position_realized_pnl(position_id, 9000.0, 0.9)

    closed = db.get_position(position_id)
    assert closed["realized_pnl"] == 9000.0
    assert closed["realized_pnl_pct"] == 0.9


def test_update_position_realized_pnl_ignores_open_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_position_realized_pnl(position_id, 9000.0, 0.9)  # status='open'이라 아무 효과 없어야 함

    position = db.get_position(position_id)
    assert position["realized_pnl"] is None


def test_update_order_position_id_links_order_to_position(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db)
    order_id = db.insert_order(strategy_id, None, "KRW-BTC", "bid", "market", None, None, 100_000_000.0)
    position_id = db.insert_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    db.update_order_position_id(order_id, position_id)

    assert db.get_order_by_id(order_id)["position_id"] == position_id


def test_replace_live_strategy_strategy_updates_fields_and_preserves_others(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        db,
        source_run_id="old-run",
        market="KRW-BTC",
        timeframe="minutes60",
        buy_conditions_json='{"old": true}',
        sell_conditions_json='{"old": true}',
        risk_config_json='{"position_sizing_value": 100000}',
        current_capital=500000.0,
        status="running",
    )
    db.update_live_strategy_last_candle(strategy_id, "2026-08-17T00:00:00")
    db.update_live_strategy_baseline_qty(strategy_id, 0.05)

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is True
    strategy = db.get_live_strategy(strategy_id)
    assert strategy["source_run_id"] == "new-run"
    assert strategy["timeframe"] == "minutes30"
    assert strategy["buy_conditions_json"] == '{"new": true}'
    assert strategy["sell_conditions_json"] == '{"new": true}'
    assert strategy["last_processed_candle_time"] is None
    # market/자본/자금관리/기존 보유량 판단 기준은 그대로 유지되어야 한다
    assert strategy["market"] == "KRW-BTC"
    assert strategy["current_capital"] == 500000.0
    assert strategy["risk_config_json"] == '{"position_sizing_value": 100000}'
    assert strategy["baseline_qty"] == 0.05


def test_replace_live_strategy_strategy_refuses_when_position_open(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running", timeframe="minutes60")
    db.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is False
    assert db.get_live_strategy(strategy_id)["timeframe"] == "minutes60"


def test_replace_live_strategy_strategy_refuses_draft_status(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft", timeframe="minutes60")

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is False
    assert db.get_live_strategy(strategy_id)["timeframe"] == "minutes60"


def test_replace_live_strategy_strategy_resets_tripped_circuit_breaker(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="running")
    db.upsert_circuit_breaker_state(
        strategy_id, "2026-08-18", 3, 1,
        tripped_reason="일일 손실 한도 초과", tripped_at="2026-08-18T05:00:00",
    )

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is True
    cb_state = db.get_circuit_breaker_state(strategy_id)
    assert cb_state["tripped"] == 0
    assert cb_state["consecutive_losses"] == 0
    assert cb_state["tripped_reason"] is None
    assert cb_state["tripped_at"] is None


def test_replace_live_strategy_strategy_noop_when_no_circuit_breaker_row(monkeypatch, tmp_path):
    db = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="stopped")

    result = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id="new-run",
        timeframe="minutes30",
        buy_conditions_json='{"new": true}',
        sell_conditions_json='{"new": true}',
    )

    assert result is True
    assert db.get_circuit_breaker_state(strategy_id) is None
