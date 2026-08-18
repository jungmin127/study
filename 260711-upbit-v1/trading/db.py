"""
trading/db.py

라이브 트레이딩 전용 SQLite DB(trading.db). 백테스트 캐시(data/backtest_results.db)와
완전히 분리된 별도 파일이며(스펙 결정 4), 실거래 데이터의 참조무결성이 중요해 외래키
제약을 켠다(캐시 전용인 engine/cache.py는 켜지 않았음).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
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
    "capital_adjustments",
)

_initialized_paths: set[Path] = set()

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
    manual_pause        INTEGER NOT NULL DEFAULT 0,
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
    entry_fee        REAL NOT NULL DEFAULT 0,
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
    triggered_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(live_strategy_id, signal_type, candle_time)
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

CREATE TABLE IF NOT EXISTS capital_adjustments (
    id                TEXT PRIMARY KEY,
    live_strategy_id  TEXT NOT NULL REFERENCES live_strategies(id),
    adjusted_at       TEXT NOT NULL DEFAULT (datetime('now')),
    previous_capital  REAL NOT NULL,
    new_capital       REAL NOT NULL,
    delta             REAL NOT NULL
);
"""


def _assert_signals_unique_constraint_present(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 signals 테이블에 새 UNIQUE 제약을
    추가하지 못한다 — 이 DB 파일이 UNIQUE(live_strategy_id, signal_type, candle_time)
    추가 이전에 만들어졌다면 insert_signal()의 idempotent 보장이 조용히 무효화된다.
    개발 단계 무마이그레이션 정책(마이그레이션 대신 DB 파일 재생성) 위반을 시작
    시점에 크게 실패시켜 조용한 무결성 붕괴를 막는다."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
    ).fetchone() is not None
    if not table_exists:
        return
    indexes = conn.execute("PRAGMA index_list('signals')").fetchall()
    if any(row[3] == "u" for row in indexes):
        return
    raise RuntimeError(
        "signals 테이블에 UNIQUE(live_strategy_id, signal_type, candle_time) 제약이 "
        "없습니다 — 이 DB 파일은 그 제약이 추가되기 전에 생성됐습니다. 개발 단계라 "
        "마이그레이션이 없으므로, 기존 DB 파일(예: data/trading.db)을 삭제한 뒤 다시 "
        "시작하세요."
    )


def _assert_live_strategies_manual_pause_column_present(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 live_strategies 테이블에 새 컬럼
    manual_pause를 추가하지 못한다 — 이 DB 파일이 manual_pause 추가 이전에 만들어졌다면
    수동 일시정지와 B그룹 지표 장애로 인한 자동 일시정지를 구분할 방법이 없어져
    Fix 1(수동 일시정지가 조용히 자동 재개되는 Critical 버그)이 다시 재발한다. 개발
    단계 무마이그레이션 정책(마이그레이션 대신 DB 파일 재생성) 위반을 시작 시점에 크게
    실패시켜 조용한 무결성 붕괴를 막는다(_assert_signals_unique_constraint_present와
    동일 패턴)."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='live_strategies'"
    ).fetchone() is not None
    if not table_exists:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info('live_strategies')")}
    if "manual_pause" in columns:
        return
    raise RuntimeError(
        "live_strategies 테이블에 manual_pause 컬럼이 없습니다 — 이 DB 파일은 그 컬럼이 "
        "추가되기 전에 생성됐습니다. 개발 단계라 마이그레이션이 없으므로, 기존 DB 파일"
        "(예: data/trading.db)을 삭제한 뒤 다시 시작하세요."
    )


def _ensure_positions_entry_fee_column(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 이미 존재하는 positions 테이블에 새 컬럼 entry_fee를
    추가하지 못한다. 다른 컬럼들은 "DB 파일을 지우고 다시 시작하라"는 assert로 크게
    실패시키지만(무마이그레이션 정책), entry_fee는 지금 AWS에서 실거래 중인 프로덕션
    DB에 적용해야 해서 파일을 지울 수 없다 — 컬럼이 없으면 실제 ALTER TABLE로 추가한다
    (기존 행은 DEFAULT 0으로 채워짐, 이후 백필 스크립트가 정확한 값으로 갱신한다)."""
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone() is not None
    if not table_exists:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info('positions')")}
    if "entry_fee" in columns:
        return
    conn.execute("ALTER TABLE positions ADD COLUMN entry_fee REAL NOT NULL DEFAULT 0")
    conn.commit()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if DB_PATH not in _initialized_paths:
        conn.executescript(_SCHEMA)
        _assert_signals_unique_constraint_present(conn)
        _assert_live_strategies_manual_pause_column_present(conn)
        _ensure_positions_entry_fee_column(conn)
        _initialized_paths.add(DB_PATH)
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


def update_live_strategy_status(
    live_strategy_id: str, status: str, *, manual_pause: int | None = None,
) -> None:
    """manual_pause를 함께 넘기면(reconciler의 개입정지 등, 사람 확인 전엔 자동재개되면
    안 되는 경우) 그 값도 같이 기록한다 — signal_engine.py의 B그룹 자동재개 가드가
    manual_pause==1인 전략은 절대 되돌리지 않기 때문이다. 생략하면(기본 None) status만
    바꾸고 manual_pause는 건드리지 않는다(circuit breaker 트립/B그룹 자동정지처럼 원래도
    자동재개 대상인 호출부는 이 인자를 넘기지 않는다)."""
    conn = _connect()
    try:
        if manual_pause is None:
            conn.execute(
                "UPDATE live_strategies SET status = ? WHERE id = ?", (status, live_strategy_id)
            )
        else:
            conn.execute(
                "UPDATE live_strategies SET status = ?, manual_pause = ? WHERE id = ?",
                (status, manual_pause, live_strategy_id),
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


def insert_position(
    live_strategy_id: str, market: str, entry_price: float, entry_qty: float,
    entry_fee: float = 0.0,
) -> str:
    position_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO positions "
            "(id, live_strategy_id, market, status, entry_price, entry_qty, entry_fee, entry_time) "
            "VALUES (?, ?, ?, 'open', ?, ?, ?, datetime('now'))",
            (position_id, live_strategy_id, market, entry_price, entry_qty, entry_fee),
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


def update_position_entry_fee(position_id: str, entry_fee: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET entry_fee = ? WHERE id = ?",
            (entry_fee, position_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_position_realized_pnl(position_id: str, realized_pnl: float, realized_pnl_pct: float) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET realized_pnl = ?, realized_pnl_pct = ? "
            "WHERE id = ? AND status = 'closed'",
            (realized_pnl, realized_pnl_pct, position_id),
        )
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
    """signals에 (live_strategy_id, signal_type, candle_time) UNIQUE 제약이 있어, 같은
    조합으로 재호출되면(daemon이 last_processed_candle_time 갱신 전에 죽었다가 재시작해
    같은 candle을 재평가하는 경우) 새 행 대신 기존 행의 id를 그대로 반환한다 — 예외
    없이 evaluate_signals()가 계속 진행되게 한다(운영 가시성/무결성 보완)."""
    signal_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "INSERT OR IGNORE INTO signals "
            "(id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signal_id, live_strategy_id, signal_type, candle_time, indicator_snapshot_json, skip_reason),
        )
        if cursor.rowcount == 0:
            existing = conn.execute(
                "SELECT id FROM signals WHERE live_strategy_id = ? AND signal_type = ? AND candle_time = ?",
                (live_strategy_id, signal_type, candle_time),
            ).fetchone()
            signal_id = existing["id"]
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


def update_order_position_id(order_id: str, position_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE orders SET position_id = ? WHERE id = ?",
            (position_id, order_id),
        )
        conn.commit()
    finally:
        conn.close()


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


def list_wait_orders(
    live_strategy_id: str, order_type: str | None = None, position_id: str | None = None,
) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM orders WHERE live_strategy_id = ? AND status = 'wait'"
        params: list = [live_strategy_id]
        if order_type is not None:
            query += " AND order_type = ?"
            params.append(order_type)
        if position_id is not None:
            query += " AND position_id = ?"
            params.append(position_id)
        rows = conn.execute(query, params).fetchall()
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


def insert_live_strategy(
    source_run_id: str | None, market: str, timeframe: str,
    buy_conditions_json: str, sell_conditions_json: str, risk_config_json: str,
) -> str:
    live_strategy_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO live_strategies "
            "(id, source_run_id, market, timeframe, buy_conditions_json, sell_conditions_json, risk_config_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (live_strategy_id, source_run_id, market, timeframe,
             buy_conditions_json, sell_conditions_json, risk_config_json),
        )
        conn.commit()
    finally:
        conn.close()
    return live_strategy_id


def list_live_strategies() -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM live_strategies ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def approve_live_strategy(live_strategy_id: str, current_capital: float) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET status='running', current_capital=?, "
            "approved_at=datetime('now'), started_at=datetime('now') "
            "WHERE id=? AND status='draft'",
            (current_capital, live_strategy_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def pause_live_strategy_manually(live_strategy_id: str) -> bool:
    """사용자가 웹 UI에서 명시적으로 누른 일시정지(Fix 1). manual_pause=1을 함께 기록해,
    signal_engine.py의 자동 재개 로직(B그룹 지표 일시 장애로 인한 자동 일시정지 전용)이
    이 수동 일시정지를 조용히 뒤집지 못하게 한다."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET status='paused', manual_pause=1 "
            "WHERE id=? AND status='running'",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def resume_live_strategy_manually(live_strategy_id: str) -> bool:
    """수동 일시정지의 짝(Fix 1). status/manual_pause를 되돌리는 것과 같은 DB 쓰기
    경로 안에서 서킷브레이커 트립도 함께 해제한다 — 그렇지 않으면 사용자가 재개를
    눌러도 daemon이 여전히 트립 상태로 취급해 재개가 무의미해진다. tripped_reason/
    tripped_at/consecutive_losses는 감사 이력이므로 그대로 보존하고(UPSERT라 다른
    필드를 실수로 지우지 않도록 기존 값을 그대로 넘긴다 — signal_engine.py의 기존
    자동 재개 경로와 동일한 upsert-preserving-other-fields 패턴), tripped=0과
    resumed_at만 새로 채운다. 애초에 트립된 적 없는(흔한 경우) 전략은
    circuit_breaker_state 행이 아예 없거나 tripped=0이라 이 분기를 건드리지 않는다."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE live_strategies SET status='running', manual_pause=0 "
            "WHERE id=? AND status='paused'",
            (live_strategy_id,),
        )
        conn.commit()
        resumed = cursor.rowcount > 0
    finally:
        conn.close()

    if resumed:
        cb_state = get_circuit_breaker_state(live_strategy_id)
        if cb_state is not None and cb_state["tripped"] == 1:
            upsert_circuit_breaker_state(
                live_strategy_id, cb_state["trading_date"], cb_state["consecutive_losses"],
                0, cb_state["tripped_reason"], cb_state["tripped_at"],
                datetime.now(timezone.utc).isoformat(),
            )

    return resumed


def stop_live_strategy_if_no_open_position(live_strategy_id: str) -> bool:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        open_position = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? AND status = 'open'",
            (live_strategy_id,),
        ).fetchone()
        if open_position is not None:
            return False
        cursor = conn.execute(
            "UPDATE live_strategies SET status='stopped', stopped_at=datetime('now') "
            "WHERE id=? AND status IN ('draft','running','paused')",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
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


def insert_capital_adjustment(live_strategy_id: str, previous_capital: float, new_capital: float) -> str:
    adjustment_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO capital_adjustments "
            "(id, live_strategy_id, previous_capital, new_capital, delta) "
            "VALUES (?, ?, ?, ?, ?)",
            (adjustment_id, live_strategy_id, previous_capital, new_capital, new_capital - previous_capital),
        )
        conn.commit()
    finally:
        conn.close()
    return adjustment_id


def list_capital_adjustments(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM capital_adjustments WHERE live_strategy_id = ? "
            "ORDER BY adjusted_at ASC, rowid ASC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def adjust_live_strategy_capital_if_no_open_position(
    live_strategy_id: str, previous_capital: float, new_capital: float,
) -> bool:
    """포지션 없음 확인과 자본 조정 기록+갱신을 한 커넥션(단일 트랜잭션)으로 묶는다 —
    stop_live_strategy_if_no_open_position()과 동일 패턴. 별도 프로세스인 트레이딩
    데몬이 포지션 확인 이후 갱신 이전 사이에 끼어들어 포지션을 여는 경쟁을 막는다.
    포지션이 있으면 아무것도 쓰지 않고 False를 반환한다."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        open_position = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? AND status = 'open'",
            (live_strategy_id,),
        ).fetchone()
        if open_position is not None:
            return False

        adjustment_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO capital_adjustments "
            "(id, live_strategy_id, previous_capital, new_capital, delta) "
            "VALUES (?, ?, ?, ?, ?)",
            (adjustment_id, live_strategy_id, previous_capital, new_capital, new_capital - previous_capital),
        )
        conn.execute(
            "UPDATE live_strategies SET current_capital = ? WHERE id = ?",
            (new_capital, live_strategy_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_live_strategy(live_strategy_id: str) -> bool:
    """stopped 상태의 라이브 전략을 자식 행까지 포함해 완전히 삭제한다. FK 제약
    (PRAGMA foreign_keys = ON)이 켜져 있어 부모(live_strategies)보다 자식 테이블을
    먼저 지워야 한다. 삭제 순서: signals(orders 참조) -> orders(position_id로 positions
    참조 + replaces_order_id로 같은 테이블을 자기참조하지만, 해당 전략의 orders를
    한 문장으로 전부 지우므로 자기참조로 인한 FK 위반 없음) -> positions ->
    daily_performance/circuit_breaker_state/capital_adjustments -> live_strategies.
    manual_intervention_events는 live_strategy_id를 FK로 참조하지 않으므로 건드리지
    않는다. status가 'stopped'가 아니면(또는 id가 없으면) 아무것도 지우지 않고
    False를 반환한다."""
    conn = _connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM live_strategies WHERE id = ? AND status = 'stopped'",
            (live_strategy_id,),
        ).fetchone()
        if exists is None:
            return False

        conn.execute("DELETE FROM signals WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM orders WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM positions WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM daily_performance WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM circuit_breaker_state WHERE live_strategy_id = ?", (live_strategy_id,))
        conn.execute("DELETE FROM capital_adjustments WHERE live_strategy_id = ?", (live_strategy_id,))
        cursor = conn.execute(
            "DELETE FROM live_strategies WHERE id = ? AND status = 'stopped'",
            (live_strategy_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_daily_performance(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM daily_performance WHERE live_strategy_id = ? "
            "ORDER BY trading_date ASC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_closed_positions(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM positions WHERE live_strategy_id = ? AND status = 'closed' "
            "ORDER BY entry_time DESC, rowid DESC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_orders_for_strategy(live_strategy_id: str) -> list[dict]:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM orders WHERE live_strategy_id = ? ORDER BY created_at ASC",
            (live_strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
