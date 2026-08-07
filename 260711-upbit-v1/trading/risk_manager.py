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


def is_circuit_tripped_today(live_strategy_id: str) -> bool:
    """오늘(KST) circuit_breaker_state.tripped 여부만 판정한다 — check_circuit_breaker()의
    77-82행(리팩터 전 기준)과 같은 판정 로직이지만 부수효과가 전혀 없다(DB에 아무것도
    쓰지 않는다). check_circuit_breaker()는 트립 기록+status 변경이라는 부수효과가 있어
    "그냥 오늘 트립됐는지만 조회하고 싶다"는 용도(예: signal_engine.py의 재개 판정)에
    그대로 재사용할 수 없어서 별도로 뺐다."""
    trading_date = today_kst()
    cb_state = db.get_circuit_breaker_state(live_strategy_id)
    return cb_state is not None and cb_state["trading_date"] == trading_date and bool(cb_state["tripped"])


def check_circuit_breaker(live_strategy_id: str, risk_config: dict) -> bool:
    """오늘(KST)의 daily_performance.realized_pnl_pct와
    circuit_breaker_state.consecutive_losses를 risk_config의 한도와 비교한다. 이미
    tripped=1이면 즉시 True(판정은 is_circuit_tripped_today()에 위임 — 로직 중복 방지).
    새로 한도를 넘었으면 circuit_breaker_state.tripped=1 + tripped_reason + tripped_at을
    기록하고 live_strategies.status를 'paused'로 바꾼 뒤 True를 반환한다(설계 스펙 결정
    3 — 판정과 반응을 하나의 함수 안에서 원자적으로 처리). 한도 안이면 False."""
    if is_circuit_tripped_today(live_strategy_id):
        return True

    trading_date = today_kst()
    cb_state = db.get_circuit_breaker_state(live_strategy_id)
    is_today = cb_state is not None and cb_state["trading_date"] == trading_date

    daily = db.get_daily_performance(live_strategy_id, trading_date)
    consecutive_losses = cb_state["consecutive_losses"] if is_today else 0

    daily_loss_limit_pct = risk_config.get("daily_loss_limit_pct")
    consecutive_loss_limit = risk_config.get("consecutive_loss_limit")

    tripped_reason = None
    if (
        daily is not None
        and daily_loss_limit_pct is not None
        and daily["realized_pnl_pct"] <= daily_loss_limit_pct
    ):
        tripped_reason = "daily_loss_limit"
    elif consecutive_loss_limit is not None and consecutive_losses >= consecutive_loss_limit:
        tripped_reason = "consecutive_loss_limit"

    if tripped_reason is None:
        return False

    db.upsert_circuit_breaker_state(
        live_strategy_id, trading_date, consecutive_losses, 1, tripped_reason,
        datetime.now(_KST).isoformat(),
    )
    db.update_live_strategy_status(live_strategy_id, "paused")
    return True
