"""
trading/risk_manager.py

전략별 서킷브레이커(일일손실/연속손실) + 일별 성과 집계. trading/db.py의 CRUD 함수만
사용하는 순수 모듈. engine/upbit_client 미의존(서브플랜⑤-1 설계 스펙 결정 1).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import trading.db as db

_KST = timezone(timedelta(hours=9))


def today_kst() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d")


def record_trade_result(live_strategy_id: str, realized_pnl: float, capital_after: float) -> None:
    """포지션 청산마다 호출. daily_performance를 오늘 날짜(KST) 기준으로 upsert하고
    circuit_breaker_state.consecutive_losses를 갱신한다(이번 거래가 손실이면 +1, 아니면
    0으로 리셋 — 설계 스펙 결정 4). trading_date가 바뀌었으면 연속손실/트립 상태를 먼저
    리셋한다."""
    trading_date = today_kst()

    existing = db.get_daily_performance(live_strategy_id, trading_date)
    if existing is None:
        starting_balance = capital_after - realized_pnl
        cumulative_pnl = realized_pnl
        trade_count = 1
        win_count = 1 if realized_pnl >= 0 else 0
        loss_count = 1 if realized_pnl < 0 else 0
    else:
        starting_balance = existing["starting_balance"]
        cumulative_pnl = existing["realized_pnl"] + realized_pnl
        trade_count = existing["trade_count"] + 1
        win_count = existing["win_count"] + (1 if realized_pnl >= 0 else 0)
        loss_count = existing["loss_count"] + (1 if realized_pnl < 0 else 0)

    cumulative_pnl_pct = (cumulative_pnl / starting_balance * 100) if starting_balance else 0.0

    db.upsert_daily_performance(
        live_strategy_id, trading_date, cumulative_pnl, cumulative_pnl_pct,
        trade_count, win_count, loss_count, starting_balance, capital_after,
    )

    cb_state = db.get_circuit_breaker_state(live_strategy_id)
    if cb_state is None or cb_state["trading_date"] != trading_date:
        tripped = 0
        tripped_reason = None
        tripped_at = None
    else:
        tripped = cb_state["tripped"]
        tripped_reason = cb_state["tripped_reason"]
        tripped_at = cb_state["tripped_at"]

    prior_consecutive_losses = (
        cb_state["consecutive_losses"]
        if cb_state is not None and cb_state["trading_date"] == trading_date
        else 0
    )
    consecutive_losses = prior_consecutive_losses + 1 if realized_pnl < 0 else 0

    db.upsert_circuit_breaker_state(
        live_strategy_id, trading_date, consecutive_losses, tripped, tripped_reason, tripped_at,
    )
