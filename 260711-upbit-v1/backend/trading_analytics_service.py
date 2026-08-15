"""
backend/trading_analytics_service.py

매매일지 대시보드(3단계 분석 대시보드)용 집계 로직. positions/daily_performance/orders를
조회 시점에 집계할 뿐 새 테이블을 두지 않는다(설계 스펙의 "중복 저장 안 함" 원칙).
main.py의 journal 엔드포인트가 이 모듈의 함수만 호출한다.

계좌 전체 집계는 잔고(ending_balance)가 아니라 일별 손익(realized_pnl) flow를 날짜별로
합산해서 누적한다 — 전략이 stopped된 뒤에도 그 전략이 과거에 낸 손익은 계좌 합산에서
사라지지 않는다(잔고를 그대로 합산하면 stopped 이후 그 전략의 daily_performance 행이
더 안 생겨서 사라지는 문제가 있었다).
"""
from __future__ import annotations

import pandas as pd

import trading.db as trading_db
from engine.cache import load_result
from engine.metrics import calculate_metrics

MIN_SAMPLE_SIZE = 10


def _mdd_pct(values: list[float]) -> float:
    if not values:
        return 0.0
    series = pd.Series(values, dtype=float)
    cummax = series.cummax()
    drawdown = pd.Series(0.0, index=series.index)
    nonzero = cummax != 0
    drawdown[nonzero] = (series[nonzero] - cummax[nonzero]) / cummax[nonzero] * 100.0
    return float(drawdown.min())


def _win_rate_pct(positions: list[dict]) -> float:
    if not positions:
        return 0.0
    wins = sum(1 for p in positions if p["realized_pnl"] >= 0)
    return wins / len(positions) * 100.0


def _strategy_baseline_capital(strategy: dict, daily_rows: list[dict]) -> float:
    """전략의 원금 근사치. 청산된 거래가 있으면 첫 daily_performance 행의
    starting_balance(첫 거래 직전 자본금)를, 아직 없으면 현재 current_capital(승인 시점
    원금에서 아직 바뀌지 않은 값)을 쓴다."""
    if daily_rows:
        return daily_rows[0]["starting_balance"] or 0.0
    return strategy["current_capital"] or 0.0


def _strategy_metrics(strategy: dict) -> dict:
    closed = trading_db.list_closed_positions(strategy["id"])
    daily_rows = trading_db.list_daily_performance(strategy["id"])
    baseline = _strategy_baseline_capital(strategy, daily_rows)

    cumulative_pnl = sum(p["realized_pnl"] for p in closed)
    cumulative_pnl_pct = (cumulative_pnl / baseline * 100.0) if baseline else 0.0
    mdd_pct = _mdd_pct([row["ending_balance"] for row in daily_rows])
    win_rate_pct = _win_rate_pct(closed)

    return {
        "closed_positions": closed,
        "daily_rows": daily_rows,
        "baseline": baseline,
        "cumulative_pnl": cumulative_pnl,
        "cumulative_pnl_pct": cumulative_pnl_pct,
        "mdd_pct": mdd_pct,
        "win_rate_pct": win_rate_pct,
    }


def get_journal_summary() -> dict:
    strategies = [s for s in trading_db.list_live_strategies() if s["approved_at"] is not None]

    if not strategies:
        return {
            "cumulative_pnl": 0.0, "cumulative_pnl_pct": 0.0, "mdd_pct": 0.0,
            "win_rate_pct": 0.0, "equity_curve": [], "strategies": [],
        }

    strategy_cards = []
    pnl_by_date: dict[str, float] = {}
    total_baseline = 0.0
    all_closed: list[dict] = []

    for strategy in strategies:
        m = _strategy_metrics(strategy)
        total_baseline += m["baseline"]
        all_closed.extend(m["closed_positions"])
        for row in m["daily_rows"]:
            pnl_by_date[row["trading_date"]] = (
                pnl_by_date.get(row["trading_date"], 0.0) + row["realized_pnl"]
            )
        strategy_cards.append({
            "id": strategy["id"],
            "market": strategy["market"],
            "timeframe": strategy["timeframe"],
            "status": strategy["status"],
            "cumulative_pnl": round(m["cumulative_pnl"], 4),
            "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
            "trade_count": len(m["closed_positions"]),
        })

    equity_curve = []
    running = total_baseline
    for trading_date in sorted(pnl_by_date):
        running += pnl_by_date[trading_date]
        equity_curve.append({"trading_date": trading_date, "value": round(running, 4)})

    cumulative_pnl = sum(p["realized_pnl"] for p in all_closed)
    cumulative_pnl_pct = (cumulative_pnl / total_baseline * 100.0) if total_baseline else 0.0
    mdd_series = [total_baseline] + [e["value"] for e in equity_curve]

    return {
        "cumulative_pnl": round(cumulative_pnl, 4),
        "cumulative_pnl_pct": round(cumulative_pnl_pct, 4),
        "mdd_pct": round(_mdd_pct(mdd_series), 4),
        "win_rate_pct": round(_win_rate_pct(all_closed), 4),
        "equity_curve": equity_curve,
        "strategies": strategy_cards,
    }


def _backtest_comparison(strategy: dict, m: dict) -> dict | None:
    source_run_id = strategy["source_run_id"]
    if not source_run_id:
        return None
    result = load_result(source_run_id)
    if result is None:
        return None

    bt_metrics = calculate_metrics(
        equity_curve=result["equity_curve"], trades=result["trades"],
        initial_capital=result["initial_capital"], df=pd.DataFrame(),
        timeframe=result["timeframe"],
    )
    bt_trades = result["trades"]
    bt_avg_return_pct = (
        sum(t["returnRate"] for t in bt_trades) / len(bt_trades) if bt_trades else 0.0
    )

    live_positions = m["closed_positions"]
    live_avg_return_pct = (
        sum(p["realized_pnl_pct"] for p in live_positions) / len(live_positions)
        if live_positions else 0.0
    )

    return {
        "backtest": {
            "win_rate_pct": round(bt_metrics["win_rate"], 4),
            "avg_return_pct": round(bt_avg_return_pct, 4),
            "mdd_pct": round(bt_metrics["mdd"], 4),
            "trade_count": bt_metrics["total_trades"],
        },
        "live": {
            "win_rate_pct": round(m["win_rate_pct"], 4),
            "avg_return_pct": round(live_avg_return_pct, 4),
            "mdd_pct": round(m["mdd_pct"], 4),
            "trade_count": len(live_positions),
        },
        "sample_size_warning": len(live_positions) < MIN_SAMPLE_SIZE,
    }


def get_strategy_journal(strategy_id: str) -> dict | None:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None or strategy["approved_at"] is None:
        return None

    m = _strategy_metrics(strategy)
    orders = trading_db.list_orders_for_strategy(strategy_id)
    slippages = [o["slippage_pct"] for o in orders if o["slippage_pct"] is not None]
    avg_slippage_pct = round(sum(slippages) / len(slippages), 4) if slippages else None
    max_slippage_pct = round(max(slippages, key=abs), 4) if slippages else None

    trade_log = [
        {
            "position_id": p["id"],
            "entry_time": p["entry_time"],
            "entry_price": p["entry_price"],
            "entry_qty": p["entry_qty"],
            "exit_time": p["exit_time"],
            "exit_price": p["exit_price"],
            "exit_qty": p["exit_qty"],
            "realized_pnl": p["realized_pnl"],
            "realized_pnl_pct": p["realized_pnl_pct"],
            "close_reason": p["close_reason"],
        }
        for p in m["closed_positions"]
    ]

    return {
        "id": strategy["id"],
        "market": strategy["market"],
        "timeframe": strategy["timeframe"],
        "status": strategy["status"],
        "cumulative_pnl": round(m["cumulative_pnl"], 4),
        "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
        "mdd_pct": round(m["mdd_pct"], 4),
        "win_rate_pct": round(m["win_rate_pct"], 4),
        "avg_slippage_pct": avg_slippage_pct,
        "max_slippage_pct": max_slippage_pct,
        "trade_count": len(m["closed_positions"]),
        "backtest_comparison": _backtest_comparison(strategy, m),
        "trade_log": trade_log,
    }
