"""
trading/position_manager.py

전략별 포지션 추적 + 복리 자금관리(스펙 결정 7). trading/db.py의 CRUD 함수만 사용하는
순수 모듈 — 계좌 잔고 조회(업비트 API)는 호출자(승인 API, daemon)의 몫이라 이 모듈은
잔고를 파라미터로 받는다(서브플랜⑤-1 설계 스펙 결정 1). engine/upbit_client 미의존.
"""
from __future__ import annotations

import trading.db as db


def calculate_initial_capital(risk_config: dict, available_balance: float) -> float:
    """position_sizing_mode('fixed'|'percent')에 따라 최초 진입 자금을 계산하고,
    max_position_per_market 상한으로 클램프한다(설계 스펙 결정 7 — 승인 시 1회만 호출).
    percent 모드의 position_sizing_value는 퍼센트 숫자(예: 10 = 10%)다."""
    mode = risk_config["position_sizing_mode"]
    if mode == "fixed":
        capital = float(risk_config["position_sizing_value"])
    elif mode == "percent":
        capital = available_balance * float(risk_config["position_sizing_value"]) / 100
    else:
        raise ValueError(f"지원하지 않는 position_sizing_mode: {mode}")

    max_position = risk_config.get("max_position_per_market")
    if max_position is not None:
        capital = min(capital, float(max_position))
    return capital


def open_position(
    live_strategy_id: str, market: str, entry_price: float, entry_qty: float,
    entry_fee: float = 0.0,
) -> str:
    return db.insert_position(live_strategy_id, market, entry_price, entry_qty, entry_fee)


def get_open_position(live_strategy_id: str) -> dict | None:
    return db.get_open_position(live_strategy_id)


def close_position(
    position_id: str, exit_price: float, exit_qty: float, fee: float, close_reason: str,
) -> dict:
    """포지션을 청산한다. realized_pnl/realized_pnl_pct를 계산해 positions 행을 갱신하고,
    live_strategies.current_capital을 (exit_price*exit_qty - fee)로 갱신한다(복리, 설계
    스펙 결정 7 — 수수료 차감 후 실현금액이 그대로 다음 진입 자금). 반환값은 호출자가
    risk_manager.record_trade_result()에 그대로 넘길 수 있는 형태다.

    realized_pnl은 진입 수수료(entry_fee, positions 행에 저장된 값)와 청산 수수료(fee
    인자)를 모두 차감한다 — capital_after는 매도 실수령액 기준이라 entry_fee와 무관하지만,
    손익 지표는 실제 매수 시 지불한 총 현금(entry_price*entry_qty + entry_fee)을 원가로
    써야 정확하다."""
    position = db.get_position(position_id)
    if position is None:
        raise ValueError(f"포지션을 찾을 수 없습니다: {position_id}")

    entry_price = position["entry_price"]
    entry_qty = position["entry_qty"]
    entry_fee = position["entry_fee"] or 0.0
    live_strategy_id = position["live_strategy_id"]

    realized_pnl = (exit_price * exit_qty) - (entry_price * entry_qty) - entry_fee - fee
    realized_pnl_pct = realized_pnl / (entry_price * entry_qty) * 100
    capital_after = exit_price * exit_qty - fee

    db.close_position_row(position_id, exit_price, exit_qty, realized_pnl, realized_pnl_pct, close_reason)
    db.update_live_strategy_capital(live_strategy_id, capital_after)

    return {
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "capital_after": capital_after,
    }
