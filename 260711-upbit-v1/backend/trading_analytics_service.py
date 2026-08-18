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

from datetime import datetime, timedelta

import pandas as pd

import trading.db as trading_db
import trading.risk_manager as risk_manager
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


def _zero_filled_last_30_days(
    pnl_by_date: dict[str, float], *, today: str | None = None,
) -> list[dict]:
    """pnl_by_date(YYYY-MM-DD 키)를 오늘(KST) 포함 최근 30일로 0-채움한 배열로 바꾼다.
    그래프가 청산 없는 날도 막대(0)로 표시할 수 있도록 daily_performance에 행이 없는
    날짜도 항목을 만든다. today를 넘기면 그 날짜를 기준으로 30일 창을 만든다(테스트용,
    기본은 실제 오늘)."""
    anchor = datetime.strptime(today or risk_manager.today_kst(), "%Y-%m-%d").date()
    return [
        {
            "date": (anchor - timedelta(days=offset)).isoformat(),
            "pnl": round(pnl_by_date.get((anchor - timedelta(days=offset)).isoformat(), 0.0), 4),
        }
        for offset in range(29, -1, -1)
    ]


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


def _twr_pct(closed_positions: list[dict], baseline: float, adjustments: list[dict]) -> float:
    """자본 조정 시점을 경계로 거래를 구간으로 나눠 구간수익률을 복리로 연결한다
    (시간가중수익률). 조정 이력이 없으면 결과는 (cumulative_pnl / baseline * 100)과
    수학적으로 동일하다. adjustments는 adjusted_at 오름차순이어야 한다
    (trading_db.list_capital_adjustments가 이미 그 순서로 반환)."""
    if not adjustments:
        pnl = sum(p["realized_pnl"] for p in closed_positions)
        return (pnl / baseline * 100.0) if baseline else 0.0

    positions_sorted = sorted(closed_positions, key=lambda p: p["exit_time"])
    factor = 1.0
    seg_start_capital = baseline
    cursor = 0
    for adj in adjustments:
        seg_pnl = 0.0
        while cursor < len(positions_sorted) and positions_sorted[cursor]["exit_time"] < adj["adjusted_at"]:
            seg_pnl += positions_sorted[cursor]["realized_pnl"]
            cursor += 1
        if seg_start_capital:
            factor *= 1 + seg_pnl / seg_start_capital
        seg_start_capital = adj["new_capital"]

    seg_pnl = sum(p["realized_pnl"] for p in positions_sorted[cursor:])
    if seg_start_capital:
        factor *= 1 + seg_pnl / seg_start_capital
    return (factor - 1) * 100.0


def _strategy_metrics(strategy: dict) -> dict:
    closed = trading_db.list_closed_positions(strategy["id"])
    daily_rows = trading_db.list_daily_performance(strategy["id"])
    adjustments = trading_db.list_capital_adjustments(strategy["id"])
    baseline = _strategy_baseline_capital(strategy, daily_rows)

    cumulative_pnl = sum(p["realized_pnl"] for p in closed)
    cumulative_pnl_pct = _twr_pct(closed, baseline, adjustments)
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
            "daily_pnl_30d": _zero_filled_last_30_days({}),
            "daily": [],
        }

    strategy_cards = []
    for strategy in strategies:
        m = _strategy_metrics(strategy)
        strategy_cards.append({
            "id": strategy["id"],
            "market": strategy["market"],
            "timeframe": strategy["timeframe"],
            "status": strategy["status"],
            "cumulative_pnl": round(m["cumulative_pnl"], 4),
            "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
            "trade_count": len(m["closed_positions"]),
        })

    # 계좌 전체 합산은 코인 단위 합산(_market_metrics)과 공식이 완전히 같다 —
    # market으로 거르지 않은 전체 승인 전략 리스트를 넘기면 그대로 계좌 합산이 된다.
    agg = _market_metrics(strategies)
    equity_curve = [
        {"trading_date": d["trading_date"], "value": d["cumulative"]} for d in agg["daily"]
    ]

    return {
        "cumulative_pnl": round(agg["cumulative_pnl"], 4),
        "cumulative_pnl_pct": round(agg["cumulative_pnl_pct"], 4),
        "mdd_pct": round(agg["mdd_pct"], 4),
        "win_rate_pct": round(agg["win_rate_pct"], 4),
        "equity_curve": equity_curve,
        "strategies": strategy_cards,
        "daily_pnl_30d": agg["daily_pnl_30d"],
        "daily": agg["daily"],
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


def _market_metrics(strategies: list[dict]) -> dict:
    """여러 live_strategy 행(같은 market, 서로 다른 timeframe·세대 포함)을 하나로 합친
    지표. 코인 단위 매매일지(달력/그래프 포함)를 위해 _strategy_metrics를 전략별로 구해
    날짜별 realized_pnl을 합산한 뒤, baseline부터 날짜순으로 누적하며 그날의 수익률(%)까지
    함께 계산한다 — daily_performance에 이미 저장된 realized_pnl_pct는 전략 단위 기준이라
    코인 합산 관점에서는 재계산이 필요하다.
    cumulative_pnl_pct는 각 전략의 TWR 보정된 cumulative_pnl_pct를 baseline으로
    가중평균한다 — 자본 조정 이력이 없는 흔한 경우엔 sum(pnl)/sum(baseline)과
    대수적으로 동일하다(가중치가 전부 baseline이고 pct_i == pnl_i/baseline_i*100일 때)."""
    total_baseline = 0.0
    weighted_pct_sum = 0.0
    all_closed: list[dict] = []
    pnl_by_date: dict[str, float] = {}
    for strategy in strategies:
        m = _strategy_metrics(strategy)
        total_baseline += m["baseline"]
        weighted_pct_sum += m["cumulative_pnl_pct"] * m["baseline"]
        all_closed.extend(m["closed_positions"])
        for row in m["daily_rows"]:
            pnl_by_date[row["trading_date"]] = pnl_by_date.get(row["trading_date"], 0.0) + row["realized_pnl"]

    daily: list[dict] = []
    running = total_baseline
    for trading_date in sorted(pnl_by_date):
        day_pnl = pnl_by_date[trading_date]
        day_pct = (day_pnl / running * 100.0) if running else 0.0
        running += day_pnl
        daily.append({
            "trading_date": trading_date,
            "pnl": round(day_pnl, 4),
            "pnl_pct": round(day_pct, 4),
            "cumulative": round(running, 4),
        })

    cumulative_pnl = sum(p["realized_pnl"] for p in all_closed)
    cumulative_pnl_pct = (weighted_pct_sum / total_baseline) if total_baseline else 0.0
    mdd_series = [total_baseline] + [d["cumulative"] for d in daily]

    return {
        "closed_positions": all_closed,
        "cumulative_pnl": cumulative_pnl,
        "cumulative_pnl_pct": cumulative_pnl_pct,
        "mdd_pct": _mdd_pct(mdd_series),
        "win_rate_pct": _win_rate_pct(all_closed),
        "daily": daily,
        "daily_pnl_30d": _zero_filled_last_30_days(pnl_by_date),
    }


def get_market_journal(market: str) -> dict | None:
    """이 마켓(코인)에 대해 지금까지 만들어진 모든 승인된 live_strategy(타임프레임·
    중지 후 재시작한 세대 전부 포함)를 하나로 합쳐 보여준다 — 개별 전략 단위 화면은
    없고 코인 단위가 유일한 조회 단위다(사용자 결정: 코인만으로 합침)."""
    strategies = [
        s for s in trading_db.list_live_strategies()
        if s["market"] == market and s["approved_at"] is not None
    ]
    if not strategies:
        return None

    m = _market_metrics(strategies)

    orders: list[dict] = []
    for strategy in strategies:
        orders.extend(trading_db.list_orders_for_strategy(strategy["id"]))
    slippages = [o["slippage_pct"] for o in orders if o["slippage_pct"] is not None]
    avg_slippage_pct = round(sum(slippages) / len(slippages), 4) if slippages else None
    max_slippage_pct = round(max(slippages, key=abs), 4) if slippages else None

    trade_log = sorted(
        (
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
        ),
        key=lambda t: t["entry_time"],
    )

    # list_live_strategies()는 created_at DESC라 strategies[0]가 가장 최근 세대 —
    # 백테스트 비교는 "지금 쓰는 전략"과 비교하는 게 의미 있으므로 그 기준을 쓴다.
    latest_strategy = strategies[0]

    return {
        "market": market,
        "timeframes": sorted({s["timeframe"] for s in strategies}),
        "statuses": sorted({s["status"] for s in strategies}),
        "cumulative_pnl": round(m["cumulative_pnl"], 4),
        "cumulative_pnl_pct": round(m["cumulative_pnl_pct"], 4),
        "mdd_pct": round(m["mdd_pct"], 4),
        "win_rate_pct": round(m["win_rate_pct"], 4),
        "avg_slippage_pct": avg_slippage_pct,
        "max_slippage_pct": max_slippage_pct,
        "trade_count": len(m["closed_positions"]),
        "backtest_comparison": _backtest_comparison(latest_strategy, m),
        "trade_log": trade_log,
        "daily": m["daily"],
        "daily_pnl_30d": m["daily_pnl_30d"],
    }
