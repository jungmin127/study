"""
engine/cache.py

전략 실행 결과를 SQLite에 캐싱해 동일 조건 재실행을 피한다.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import backtrader as bt

from engine.runner import run_backtest

DB_PATH = Path(__file__).parent.parent / "data" / "backtest_results.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    params_json TEXT NOT NULL,
    market TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    risk_config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_results (
    run_id TEXT PRIMARY KEY REFERENCES backtest_runs(id),
    final_value REAL,
    sharpe REAL,
    max_drawdown REAL,
    equity_curve_json TEXT NOT NULL,
    trades_json TEXT NOT NULL
);
"""

_SCHEMA += """
CREATE TABLE IF NOT EXISTS sweep_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    signal_set_name TEXT NOT NULL,
    is_combined INTEGER NOT NULL,
    market TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    return_rate REAL,
    sharpe REAL,
    max_drawdown REAL,
    swept_at TEXT NOT NULL
);
"""


_JSON_PRIMITIVES = (str, int, float, bool, type(None))


def _json_default(obj):
    """JSON으로 직렬화할 수 없는 객체(예: signals.py의 Signal 구현체)를
    안정적인 표현으로 변환한다. str(obj)의 기본 repr은 메모리 주소를 포함해
    프로세스마다 달라지므로, 클래스명 + 원시 타입 인스턴스 속성으로 대체해
    같은 설정이면 항상 같은 캐시 키가 나오도록 한다.

    원시 타입이 아닌 속성(예: setup() 이후 신호에 붙는 backtrader 지표 객체)은
    캐시 키에서 제외한다 — 그런 런타임 상태는 신호의 "설정"이 아니고, 종종
    자기 자신을 참조하는 순환 구조라 재귀 직렬화 시 오류가 난다."""
    if hasattr(obj, "__dict__"):
        primitives = {
            k: v for k, v in vars(obj).items() if isinstance(v, _JSON_PRIMITIVES)
        }
        return {"__class__": type(obj).__name__, **primitives}
    return str(obj)


def compute_cache_key(
    strategy_cls: type,
    strategy_params: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    risk_config: dict,
) -> str:
    payload = {
        "strategy_source": inspect.getsource(strategy_cls),
        "strategy_params": strategy_params,
        "market": market,
        "timeframe": timeframe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "risk_config": risk_config,
    }
    canonical = json.dumps(payload, sort_keys=True, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT final_value, sharpe, max_drawdown, equity_curve_json, trades_json "
            "FROM backtest_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    final_value, sharpe, max_drawdown, equity_curve_json, trades_json = row
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "from_cache": True,
    }


def save_result(
    run_id: str,
    strategy_name: str,
    strategy_params: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    risk_config: dict,
    result: dict,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_runs "
            "(id, strategy_name, params_json, market, timeframe, start, end, risk_config_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id,
                strategy_name,
                json.dumps(strategy_params, sort_keys=True, default=_json_default),
                market,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                json.dumps(risk_config, sort_keys=True),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO backtest_results "
            "(run_id, final_value, sharpe, max_drawdown, equity_curve_json, trades_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result["final_value"],
                result["sharpe"],
                result["max_drawdown"],
                json.dumps(result["equity_curve"]),
                json.dumps(result["trades"]),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def run_backtest_cached(
    df: pd.DataFrame,
    strategy_cls: type[bt.Strategy],
    risk_config: dict,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    strategy_params: dict | None = None,
) -> dict:
    """
    캐시된 백테스트 결과를 반환하거나 새로 실행해 저장한다.

    주의: 캐시 키는 df의 실제 내용을 포함하지 않고 (strategy_source, params, market, timeframe, start, end, risk_config)
    으로만 생성된다. end가 과거의 완료된 시간이면 안전하지만, end가 현재 근처로 고정되고 캔들 마감 후
    재실행되면 get_candles()는 더 많은 바를 반환하면서도 캐시 키는 동일하게 유지되어 오래된 결과가 반환될 수 있다.
    """
    strategy_params = strategy_params or {}
    run_id = compute_cache_key(
        strategy_cls, strategy_params, market, timeframe, start, end, risk_config
    )

    cached = load_result(run_id)
    if cached is not None:
        cached["run_id"] = run_id
        return cached

    result = run_backtest(df, strategy_cls, risk_config, strategy_params)
    save_result(
        run_id=run_id,
        strategy_name=strategy_cls.__name__,
        strategy_params=strategy_params,
        market=market,
        timeframe=timeframe,
        start=start,
        end=end,
        risk_config=risk_config,
        result=result,
    )
    result["from_cache"] = False
    result["run_id"] = run_id
    return result


def save_sweep_result(
    run_id: str,
    signal_set_name: str,
    is_combined: bool,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    return_rate: float | None,
    sharpe: float | None,
    max_drawdown: float | None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sweep_history "
            "(run_id, signal_set_name, is_combined, market, timeframe, start, end, "
            " return_rate, sharpe, max_drawdown, swept_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                run_id, signal_set_name, int(is_combined), market, timeframe,
                start.isoformat(), end.isoformat(), return_rate, sharpe, max_drawdown,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_sweep_dict(row: tuple) -> dict:
    (run_id, signal_set_name, is_combined, market, timeframe, start, end,
     return_rate, sharpe, max_drawdown, swept_at) = row
    return {
        "run_id": run_id,
        "signal_set_name": signal_set_name,
        "is_combined": bool(is_combined),
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "return_rate": return_rate,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "swept_at": swept_at,
    }


def list_latest_sweep_results() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, signal_set_name, is_combined, market, timeframe, start, end, "
            "       return_rate, sharpe, max_drawdown, swept_at "
            "FROM sweep_history "
            "WHERE id IN ("
            "  SELECT MAX(id) FROM sweep_history "
            "  GROUP BY signal_set_name, is_combined, market, timeframe"
            ") "
            "ORDER BY signal_set_name, market, timeframe"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_sweep_dict(r) for r in rows]


def list_combined_ranking() -> list[dict]:
    return sorted(
        (r for r in list_latest_sweep_results() if r["is_combined"]),
        key=lambda r: (r["return_rate"] if r["return_rate"] is not None else float("-inf")),
        reverse=True,
    )


def list_sweep_history(
    signal_set_name: str, market: str, timeframe: str, is_combined: bool
) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, signal_set_name, is_combined, market, timeframe, start, end, "
            "       return_rate, sharpe, max_drawdown, swept_at "
            "FROM sweep_history "
            "WHERE signal_set_name = ? AND market = ? AND timeframe = ? AND is_combined = ? "
            "ORDER BY swept_at",
            (signal_set_name, market, timeframe, int(is_combined)),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_sweep_dict(r) for r in rows]


def list_distinct_combos() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT signal_set_name, is_combined, market, timeframe FROM sweep_history "
            "ORDER BY signal_set_name, market, timeframe"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"signal_set_name": r[0], "is_combined": bool(r[1]), "market": r[2], "timeframe": r[3]}
        for r in rows
    ]
