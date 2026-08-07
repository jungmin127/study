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


def open_position(live_strategy_id: str, market: str, entry_price: float, entry_qty: float) -> str:
    return db.insert_position(live_strategy_id, market, entry_price, entry_qty)


def get_open_position(live_strategy_id: str) -> dict | None:
    return db.get_open_position(live_strategy_id)
