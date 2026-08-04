"""
engine/cache.py

전략 실행 결과를 SQLite에 캐싱해 동일 조건 재실행을 피한다.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import warnings
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

_SCHEMA += """
CREATE TABLE IF NOT EXISTS segment_classification (
    market TEXT PRIMARY KEY,
    korean_name TEXT NOT NULL,
    segment TEXT NOT NULL,
    trade_value_24h REAL,
    volatility_30d REAL,
    trade_value_percentile REAL,
    volatility_percentile REAL,
    is_caution INTEGER NOT NULL,
    computed_at TEXT NOT NULL
);
"""

_SCHEMA += """
CREATE TABLE IF NOT EXISTS grid_search_jobs (
    id             TEXT PRIMARY KEY,
    market         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    capital        REAL NOT NULL,
    start          TEXT NOT NULL,
    end            TEXT NOT NULL,
    top_n          INTEGER NOT NULL,
    status         TEXT NOT NULL,
    total_combos   INTEGER,
    done_combos    INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    elapsed_sec    REAL,
    error_message  TEXT,
    result_json    TEXT
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
        attrs = vars(obj)
        primitives = {k: v for k, v in attrs.items() if isinstance(v, _JSON_PRIMITIVES)}
        # signals.py의 관례: 생성자 설정값은 public 속성, setup()이 붙이는
        # backtrader 지표 등 런타임 상태는 `_`로 시작하는 private 속성이다.
        # private 속성이 걸러지는 건 정상이므로 경고하지 않는다 — public인데도
        # 원시 타입이 아니어서 걸러진 경우만, 캐시 키가 실수로 설정값 차이를
        # 구분하지 못하는 상황일 수 있어 경고한다.
        dropped_config = sorted(
            k for k in set(attrs) - set(primitives) if not k.startswith("_")
        )
        if dropped_config:
            warnings.warn(
                f"{type(obj).__name__}의 비-원시 타입 속성 {dropped_config}이(가) 캐시 키에서 "
                "제외되었습니다. list/dict가 아닌 원시 타입으로 바꾸지 않는 한 캐시 키가 "
                "그 값의 차이를 구분하지 못해 잘못된 캐시 hit이 발생할 수 있습니다.",
                stacklevel=2,
            )
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
    # backtest_runs에 title/description 컬럼을 추가하는 경량 마이그레이션.
    # CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블의 컬럼을 추가해주지 않으므로,
    # 기존 DB 파일에도 안전하게 적용되도록 ALTER TABLE을 시도하고 이미 있으면 무시한다.
    for column in ("title", "description"):
        try:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    return conn


def load_result(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT res.final_value, res.sharpe, res.max_drawdown, res.equity_curve_json, res.trades_json, "
            "       r.market, r.timeframe, r.start, r.end, r.risk_config_json "
            "FROM backtest_results res "
            "JOIN backtest_runs r ON r.id = res.run_id "
            "WHERE res.run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    (final_value, sharpe, max_drawdown, equity_curve_json, trades_json,
     market, timeframe, start, end, risk_config_json) = row
    risk_config = json.loads(risk_config_json)
    initial_capital = risk_config.get("initial_capital")
    commission_rate = risk_config.get("commission_rate", 0.0005)
    return {
        "final_value": final_value,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "equity_curve": json.loads(equity_curve_json),
        "trades": json.loads(trades_json),
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "initial_capital": initial_capital,
        "commission_rate": commission_rate,
        "from_cache": True,
    }


def get_run_config(run_id: str) -> dict | None:
    """run_id로 저장된 실행 설정(시장/봉타입/시작일/조건식/리스크설정 등)을 반환한다.
    "최신 데이터로 갱신" 기능처럼 같은 조건으로 end만 바꿔 재실행할 때 쓴다."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT strategy_name, market, timeframe, start, risk_config_json, params_json, title, description "
            "FROM backtest_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    strategy_name, market, timeframe, start, risk_config_json, params_json, title, description = row
    params = json.loads(params_json)
    return {
        "strategy_name": strategy_name,
        "market": market,
        "timeframe": timeframe,
        "start": start,
        "risk_config": json.loads(risk_config_json),
        "buy_conditions": params["buy_conditions"],
        "sell_conditions": params["sell_conditions"],
        "title": title,
        "description": description,
    }


def delete_backtest_run(run_id: str) -> bool:
    """run_id에 해당하는 백테스트 결과를 삭제한다. 삭제된 행이 있었으면 True를 반환한다."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM backtest_results WHERE run_id = ?", (run_id,))
        cur = conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


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
    title: str | None = None,
    description: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_runs "
            "(id, strategy_name, params_json, market, timeframe, start, end, risk_config_json, created_at, title, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)",
            (
                run_id,
                strategy_name,
                json.dumps(strategy_params, sort_keys=True, default=_json_default),
                market,
                timeframe,
                start.isoformat(),
                end.isoformat(),
                json.dumps(risk_config, sort_keys=True),
                title,
                description,
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
    title: str | None = None,
    description: str | None = None,
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
        title=title,
        description=description,
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


def list_backtest_runs(strategy_name: str = "ConditionTreeStrategy", limit: int = 1000) -> list[dict]:
    """온디맨드 조건식 실행(홈 화면) 결과만 최신순으로 반환한다.

    strategy_name으로 필터링해 run_sweep()이 남기는 SignalStrategy 기반 행(히트맵/랭킹
    전용)은 섞이지 않게 한다 — 두 시스템은 의도적으로 분리되어 있다.

    initial_capital/commission_rate/trades/buy_conditions/sell_conditions는 클라이언트
    응답용이 아니라 backend/main.py가 미청산 포지션 실시간 재평가 계산에 쓰는 내부 필드다."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT r.id, r.title, r.description, r.market, r.timeframe, r.start, r.end, "
            "       r.created_at, r.risk_config_json, r.params_json, "
            "       res.final_value, res.sharpe, res.max_drawdown, res.trades_json "
            "FROM backtest_runs r "
            "JOIN backtest_results res ON res.run_id = r.id "
            "WHERE r.strategy_name = ? "
            # created_at은 초 단위라 같은 초에 여러 건이 저장되면 순서가 불안정해질 수 있어,
            # 삽입 순서를 그대로 보존하는 rowid를 보조 정렬 기준으로 둔다.
            "ORDER BY r.created_at DESC, r.rowid DESC "
            "LIMIT ?",
            (strategy_name, limit),
        ).fetchall()
    finally:
        conn.close()

    runs: list[dict] = []
    for row in rows:
        (run_id, title, description, market, timeframe, start, end,
         created_at, risk_config_json, params_json,
         final_value, sharpe, max_drawdown, trades_json) = row
        risk_config = json.loads(risk_config_json)
        initial_capital = risk_config.get("initial_capital")
        commission_rate = risk_config.get("commission_rate", 0.0005)
        return_rate = (
            (final_value - initial_capital) / initial_capital * 100
            if initial_capital else None
        )
        params = json.loads(params_json)
        runs.append({
            "run_id": run_id,
            "title": title,
            "description": description,
            "market": market,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "created_at": created_at,
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "trades": json.loads(trades_json),
            "buy_conditions": params["buy_conditions"],
            "sell_conditions": params["sell_conditions"],
        })
    return runs


def save_segment_classification(rows: list[dict]) -> None:
    """세그먼트(규모) 분류 결과를 저장한다. 배치 실행마다 테이블을 통째로 교체한다
    (과거 분류 이력은 보관하지 않고 항상 최신 1회분만 유지)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM segment_classification")
        conn.executemany(
            "INSERT INTO segment_classification "
            "(market, korean_name, segment, trade_value_24h, volatility_30d, "
            " trade_value_percentile, volatility_percentile, is_caution, computed_at) "
            "VALUES (:market, :korean_name, :segment, :trade_value_24h, :volatility_30d, "
            " :trade_value_percentile, :volatility_percentile, :is_caution, :computed_at)",
            [{**r, "is_caution": int(r["is_caution"])} for r in rows],
        )
        conn.commit()
    finally:
        conn.close()


def list_segment_classification() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT market, korean_name, segment, trade_value_24h, volatility_30d, "
            "       trade_value_percentile, volatility_percentile, is_caution, computed_at "
            "FROM segment_classification "
            "ORDER BY trade_value_24h DESC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "market": r[0],
            "korean_name": r[1],
            "segment": r[2],
            "trade_value_24h": r[3],
            "volatility_30d": r[4],
            "trade_value_percentile": r[5],
            "volatility_percentile": r[6],
            "is_caution": bool(r[7]),
            "computed_at": r[8],
        }
        for r in rows
    ]


def create_grid_search_job(
    job_id: str, market: str, timeframe: str, capital: float,
    start: str, end: str, top_n: int,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO grid_search_jobs "
            "(id, market, timeframe, capital, start, end, top_n, status, done_combos, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 0, datetime('now'))",
            (job_id, market, timeframe, capital, start, end, top_n),
        )
        conn.commit()
    finally:
        conn.close()


def update_grid_search_job_progress(job_id: str, done_combos: int, total_combos: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE grid_search_jobs SET done_combos = ?, total_combos = ? WHERE id = ?",
            (done_combos, total_combos, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def finish_grid_search_job(
    job_id: str,
    status: str,
    elapsed_sec: float | None = None,
    result_json: str | None = None,
    error_message: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE grid_search_jobs "
            "SET status = ?, finished_at = datetime('now'), elapsed_sec = ?, "
            "    result_json = ?, error_message = ? "
            "WHERE id = ?",
            (status, elapsed_sec, result_json, error_message, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def remove_grid_search_result(job_id: str, run_id: str) -> bool:
    """job_id의 저장된 결과 목록(result_json)에서 run_id 항목을 제거한다.
    제거된 항목이 있었으면 True를 반환한다."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT result_json FROM grid_search_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[0] is None:
            return False
        results = json.loads(row[0])
        filtered = [r for r in results if r.get("run_id") != run_id]
        if len(filtered) == len(results):
            return False
        conn.execute(
            "UPDATE grid_search_jobs SET result_json = ? WHERE id = ?",
            (json.dumps(filtered), job_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _row_to_grid_search_job_dict(row: tuple) -> dict:
    (job_id, market, timeframe, capital, start, end, top_n, status,
     total_combos, done_combos, started_at, finished_at, elapsed_sec,
     error_message, result_json) = row
    return {
        "id": job_id,
        "market": market,
        "timeframe": timeframe,
        "capital": capital,
        "start": start,
        "end": end,
        "top_n": top_n,
        "status": status,
        "total_combos": total_combos,
        "done_combos": done_combos,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_sec": elapsed_sec,
        "error_message": error_message,
        "result_json": json.loads(result_json) if result_json else None,
    }


def get_grid_search_job(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, market, timeframe, capital, start, end, top_n, status, "
            "       total_combos, done_combos, started_at, finished_at, elapsed_sec, "
            "       error_message, result_json "
            "FROM grid_search_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_grid_search_job_dict(row) if row else None


def list_grid_search_jobs() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, market, timeframe, capital, start, end, top_n, status, "
            "       total_combos, done_combos, started_at, finished_at, elapsed_sec, "
            "       error_message, result_json "
            "FROM grid_search_jobs ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_grid_search_job_dict(r) for r in rows]
