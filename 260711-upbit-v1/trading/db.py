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
    stopped_at          TEXT
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
