"""
trading/order_executor.py

signal_engine.evaluate_signals() 결과를 받아 실제 업비트 주문(매수/매도)을 실행한다.
market/limit/limit_timeout/market_capped 4가지 실행모드를 지원하고, 서킷브레이커 확인 →
enter()/exit() 호출 → signals.resulting_order_id 갱신까지 handle_signal_result()가 한 번에
처리한다(설계 스펙 결정3). trading.upbit_client(async REST) + trading.position_manager +
trading.risk_manager를 엮는 이 서브플랜의 유일한 모듈. engine/ 미의존.
"""
from __future__ import annotations

import math

import httpx

import trading.upbit_client as upbit_client

# 업비트 원화마켓 주문가격단위(2026-08 기준, docs.upbit.com/kr/docs/krw-market-info).
# orders/chance 응답의 price_unit은 deprecated라 쓰지 않는다(설계 스펙 결정1) — 업비트가
# 이 표를 바꾸면(2023/2024년 실제 변경 이력 있음) 수동으로 갱신해야 한다.
_TICK_TABLE: list[tuple[float, float]] = [
    (1_000_000, 1000),
    (500_000, 500),
    (100_000, 100),
    (50_000, 50),
    (10_000, 10),
    (5_000, 5),
    (100, 1),
    (10, 0.1),
    (1, 0.01),
    (0.1, 0.001),
    (0.01, 0.0001),
    (0.001, 0.00001),
    (0.0001, 0.000001),
    (0.00001, 0.0000001),
    (0, 0.00000001),
]


def round_to_tick(price: float) -> float:
    for threshold, tick in _TICK_TABLE:
        if price >= threshold:
            return round(round(price / tick) * tick, 8)
    return price


def _floor_volume(volume: float) -> float:
    return math.floor(volume * 1e8) / 1e8


async def _create_order_with_retry(
    market: str, side: str, ord_type: str, *, order_id: str,
    volume: str | None = None, price: str | None = None, time_in_force: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """create_order()가 네트워크 에러/타임아웃으로 응답을 못 받으면 identifier로 재조회해
    실제로 주문이 들어갔는지 확인한 뒤에만 1회 재시도한다(설계 스펙 결정5, 이중주문 방지)."""
    try:
        return await upbit_client.create_order(
            market, side, ord_type, volume=volume, price=price,
            time_in_force=time_in_force, identifier=order_id, client=client,
        )
    except (httpx.TransportError, httpx.TimeoutException):
        try:
            return await upbit_client.get_order(identifier=order_id, client=client)
        except httpx.HTTPStatusError:
            return await upbit_client.create_order(
                market, side, ord_type, volume=volume, price=price,
                time_in_force=time_in_force, identifier=order_id, client=client,
            )


async def _fetch_fill(upbit_uuid: str, *, client: httpx.AsyncClient | None = None) -> dict:
    """get_order()로 체결 결과를 조회한다. 평균체결가는 trades[].funds 합계 ÷
    executed_volume으로 계산한다(업비트 공식 문서 기준)."""
    resp = await upbit_client.get_order(uuid=upbit_uuid, client=client)
    executed_volume = float(resp["executed_volume"])
    filled_price = (
        sum(float(t["funds"]) for t in resp["trades"]) / executed_volume
        if executed_volume > 0 else None
    )
    return {
        "state": resp["state"],
        "executed_volume": executed_volume,
        "remaining_volume": float(resp["remaining_volume"]),
        "filled_price": filled_price,
        "fee": float(resp["paid_fee"]),
    }


def _slippage_pct(filled_price: float, expected_price: float) -> float:
    return (filled_price - expected_price) / expected_price * 100
