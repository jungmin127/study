"""
trading/db.py

라이브 트레이딩 전용 SQLite DB(trading.db). 백테스트 캐시(data/backtest_results.db)와
완전히 분리된 별도 파일이며(스펙 결정 4), 실거래 데이터의 참조무결성이 중요해 외래키
제약을 켠다(캐시 전용인 engine/cache.py는 켜지 않았음).
"""
from __future__ import annotations

import sqlite3
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
