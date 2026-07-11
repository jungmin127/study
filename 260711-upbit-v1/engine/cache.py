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
    canonical = json.dumps(payload, sort_keys=True, default=str)
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
                json.dumps(strategy_params, sort_keys=True),
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
    return result
