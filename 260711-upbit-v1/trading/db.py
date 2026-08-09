"""
trading/db.py

라이브 트레이딩 전용 SQLite DB(trading.db). 백테스트 캐시(data/backtest_results.db)와
완전히 분리된 별도 파일이며(스펙 결정 4), 실거래 데이터의 참조무결성이 중요해 외래키
제약을 켠다(캐시 전용인 engine/cache.py는 켜지 않았음).
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trading.db"

TABLE_NAMES = (
    "live_strategies",
    "positions",
    "orders",
    "signals",
    "daily_performance",
    "circuit_breaker_state",
    "manual_intervention_events",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_strategies (
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
);

CREATE TABLE IF NOT EXISTS positions (
    id               TEXT PRIMARY KEY,
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
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
);

CREATE TABLE IF NOT EXISTS orders (
    id                TEXT PRIMARY KEY,
    upbit_uuid        TEXT UNIQUE,
    live_strategy_id  TEXT REFERENCES live_strategies(id),
    position_id       TEXT REFERENCES positions(id),
    replaces_order_id TEXT REFERENCES orders(id),
    market            TEXT NOT NULL,
    side              TEXT NOT NULL,
    order_type        TEXT NOT NULL,
    requested_price   REAL,
    requested_volume  REAL,
    filled_price      REAL,
    filled_volume     REAL,
    fee               REAL,
    expected_price    REAL,
    slippage_pct      REAL,
    status            TEXT NOT NULL,
    is_external       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id                      TEXT PRIMARY KEY,
    live_strategy_id        TEXT NOT NULL REFERENCES live_strategies(id),
    signal_type             TEXT NOT NULL,
    candle_time             TEXT NOT NULL,
    indicator_snapshot_json TEXT,
    resulting_order_id      TEXT REFERENCES orders(id),
    skip_reason             TEXT,
    triggered_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_performance (
    trading_date     TEXT NOT NULL,
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
    realized_pnl     REAL NOT NULL DEFAULT 0,
    realized_pnl_pct REAL NOT NULL DEFAULT 0,
    trade_count      INTEGER NOT NULL DEFAULT 0,
    win_count        INTEGER NOT NULL DEFAULT 0,
    loss_count       INTEGER NOT NULL DEFAULT 0,
    starting_balance REAL,
    ending_balance   REAL,
    max_drawdown_pct REAL,
    PRIMARY KEY (trading_date, live_strategy_id)
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    live_strategy_id   TEXT PRIMARY KEY REFERENCES live_strategies(id),
    trading_date       TEXT NOT NULL,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    tripped            INTEGER NOT NULL DEFAULT 0,
    tripped_reason     TEXT,
    tripped_at         TEXT,
    resumed_at         TEXT
);

CREATE TABLE IF NOT EXISTS manual_intervention_events (
    id             TEXT PRIMARY KEY,
    detected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    market         TEXT,
    description    TEXT NOT NULL,
    action_taken   TEXT NOT NULL,
    resolved_at    TEXT
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def get_live_strategy(live_strategy_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM live_strategies WHERE id = ?", (live_strategy_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_live_strategy_status(live_strategy_id: str, status: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET status = ? WHERE id = ?", (status, live_strategy_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_live_strategy_capital(live_strategy_id: str, current_capital: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET current_capital = ? WHERE id = ?",
            (current_capital, live_strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_live_strategy_last_candle(live_strategy_id: str, candle_time: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET last_processed_candle_time = ? WHERE id = ?",
            (candle_time, live_strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_position(live_strategy_id: str, market: str, entry_price: float, entry_qty: float) -> str:
    position_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO positions "
            "(id, live_strategy_id, market, status, entry_price, entry_qty, entry_time) "
            "VALUES (?, ?, ?, 'open', ?, ?, datetime('now'))",
            (position_id, live_strategy_id, market, entry_price, entry_qty),
        )
        conn.commit()
    finally:
        conn.close()
    return position_id


def get_position(position_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def accumulate_stale_resolution(position_id: str, qty: float, proceeds: float, fee: float) -> None:
    """잔여 미체결 매도 주문 정리로 확인된 수량/대금/수수료를 포지션에 즉시 누적한다
    (⑤-4c 백로그 수정 Important #1/#3/#4 — 이 정보가 그 틱의 지역 변수에만 머물면 예외로
    끊기거나 다음 틱에서 사라진다). exit_for_risk()가 잔여 주문 하나를 정리할 때마다
    호출한다."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET stale_resolved_qty = stale_resolved_qty + ?, "
            "stale_resolved_proceeds = stale_resolved_proceeds + ?, "
            "stale_resolved_fee = stale_resolved_fee + ? WHERE id = ?",
            (qty, proceeds, fee, position_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_position_row(
    position_id: str, exit_price: float, exit_qty: float,
    realized_pnl: float, realized_pnl_pct: float, close_reason: str,
) -> None:
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE positions SET status='closed', exit_price=?, exit_qty=?, "
            "exit_time=datetime('now'), realized_pnl=?, realized_pnl_pct=?, close_reason=? "
            "WHERE id=? AND status='open'",
            (exit_price, exit_qty, realized_pnl, realized_pnl_pct, close_reason, position_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"포지션을 찾을 수 없거나 이미 종료된 상태입니다: {position_id}")
        conn.commit()
    finally:
        conn.close()


def get_open_position(live_strategy_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM positions WHERE live_strategy_id = ? AND status = 'open'",
            (live_strategy_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_circuit_breaker_state(live_strategy_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM circuit_breaker_state WHERE live_strategy_id = ?",
            (live_strategy_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_circuit_breaker_state(
    live_strategy_id: str, trading_date: str, consecutive_losses: int, tripped: int,
    tripped_reason: str | None = None, tripped_at: str | None = None, resumed_at: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO circuit_breaker_state "
            "(live_strategy_id, trading_date, consecutive_losses, tripped, tripped_reason, "
            "tripped_at, resumed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(live_strategy_id) DO UPDATE SET "
            "trading_date=excluded.trading_date, "
            "consecutive_losses=excluded.consecutive_losses, "
            "tripped=excluded.tripped, tripped_reason=excluded.tripped_reason, "
            "tripped_at=excluded.tripped_at, resumed_at=excluded.resumed_at",
            (live_strategy_id, trading_date, consecutive_losses, tripped, tripped_reason,
             tripped_at, resumed_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_performance(live_strategy_id: str, trading_date: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM daily_performance WHERE live_strategy_id = ? AND trading_date = ?",
            (live_strategy_id, trading_date),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_daily_performance(
    live_strategy_id: str, trading_date: str, realized_pnl: float, realized_pnl_pct: float,
    trade_count: int, win_count: int, loss_count: int,
    starting_balance: float, ending_balance: float,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO daily_performance "
            "(trading_date, live_strategy_id, realized_pnl, realized_pnl_pct, trade_count, "
            "win_count, loss_count, starting_balance, ending_balance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trading_date, live_strategy_id) DO UPDATE SET "
            "realized_pnl=excluded.realized_pnl, realized_pnl_pct=excluded.realized_pnl_pct, "
            "trade_count=excluded.trade_count, win_count=excluded.win_count, "
            "loss_count=excluded.loss_count, ending_balance=excluded.ending_balance",
            (trading_date, live_strategy_id, realized_pnl, realized_pnl_pct, trade_count,
             win_count, loss_count, starting_balance, ending_balance),
        )
        conn.commit()
    finally:
        conn.close()


def insert_signal(
    live_strategy_id: str, signal_type: str, candle_time: str,
    indicator_snapshot_json: str, skip_reason: str | None = None,
) -> str:
    signal_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO signals "
            "(id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason),
        )
        conn.commit()
    finally:
        conn.close()
    return signal_id


def insert_order(
    live_strategy_id: str, position_id: str | None, market: str, side: str, order_type: str,
    requested_price: float | None, requested_volume: float | None, expected_price: float | None,
    *, replaces_order_id: str | None = None,
) -> str:
    order_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO orders "
            "(id, live_strategy_id, position_id, replaces_order_id, market, side, order_type, "
            "requested_price, requested_volume, expected_price, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'wait')",
            (order_id, live_strategy_id, position_id, replaces_order_id, market, side, order_type,
             requested_price, requested_volume, expected_price),
        )
        conn.commit()
    finally:
        conn.close()
    return order_id


def update_order_filled(
    order_id: str, upbit_uuid: str | None, filled_price: float | None,
    filled_volume: float | None, fee: float | None, slippage_pct: float | None, status: str,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE orders SET upbit_uuid=?, filled_price=?, filled_volume=?, fee=?, "
            "slippage_pct=?, status=?, updated_at=datetime('now') WHERE id=?",
            (upbit_uuid, filled_price, filled_volume, fee, slippage_pct, status, order_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_order_by_id(order_id: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_signal_result(
    signal_id: str, resulting_order_id: str | None, skip_reason: str | None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE signals SET resulting_order_id=?, skip_reason=? WHERE id=?",
            (resulting_order_id, skip_reason, signal_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_live_strategy_baseline_qty(live_strategy_id: str, baseline_qty: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE live_strategies SET baseline_qty = ? WHERE id = ?",
            (baseline_qty, live_strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_order_by_upbit_uuid(upbit_uuid: str) -> dict | None:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM orders WHERE upbit_uuid = ?", (upbit_uuid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_external_order(
    live_strategy_id: str, position_id: str | None, market: str, side: str,
    order_type: str, upbit_uuid: str, filled_price: float | None,
    filled_volume: float | None, fee: float | None, status: str,
) -> str:
    order_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO orders "
            "(id, upbit_uuid, live_strategy_id, position_id, market, side, order_type, "
            "filled_price, filled_volume, fee, status, is_external, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))",
            (order_id, upbit_uuid, live_strategy_id, position_id, market, side, order_type,
             filled_price, filled_volume, fee, status),
        )
        conn.commit()
    finally:
        conn.close()
    return order_id


def insert_manual_intervention_event(market: str, description: str, action_taken: str) -> str:
    event_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO manual_intervention_events (id, market, description, action_taken) "
            "VALUES (?, ?, ?, ?)",
            (event_id, market, description, action_taken),
        )
        conn.commit()
    finally:
        conn.close()
    return event_id


def list_wait_orders(live_strategy_id: str, order_type: str | None = None) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        if order_type is not None:
            rows = conn.execute(
                "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait' "
                "AND order_type = ?",
                (live_strategy_id, order_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait'",
                (live_strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def adjust_position_qty(
    position_id: str, new_qty: float, new_entry_price: float | None = None,
) -> None:
    conn = _connect()
    try:
        if new_entry_price is not None:
            conn.execute(
                "UPDATE positions SET entry_qty = ?, entry_price = ? WHERE id = ? AND status = 'open'",
                (new_qty, new_entry_price, position_id),
            )
        else:
            conn.execute(
                "UPDATE positions SET entry_qty = ? WHERE id = ? AND status = 'open'",
                (new_qty, position_id),
            )
        conn.commit()
    finally:
        conn.close()


def list_active_strategies() -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM live_strategies WHERE status IN ('running', 'paused')"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
