"""
trading/order_executor.py

signal_engine.evaluate_signals() 결과를 받아 실제 업비트 주문(매수/매도)을 실행한다.
market/limit/limit_timeout/market_capped 4가지 실행모드를 지원하고, 서킷브레이커 확인 →
enter()/exit() 호출 → signals.resulting_order_id 갱신까지 handle_signal_result()가 한 번에
처리한다(설계 스펙 결정3). trading.upbit_client(async REST) + trading.position_manager +
trading.risk_manager를 엮는 이 서브플랜의 유일한 모듈. engine/ 미의존.
"""
from __future__ import annotations

import json
import math

import httpx

import trading.db as db
import trading.position_manager as position_manager
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


async def _run_market(
    order_id: str, market: str, side: str, capital: float | None, volume: float,
    expected_price: float, *, client: httpx.AsyncClient | None = None,
) -> dict:
    if side == "bid":
        resp = await _create_order_with_retry(
            market, "bid", "price", order_id=order_id, price=str(capital), client=client,
        )
    else:
        resp = await _create_order_with_retry(
            market, "ask", "market", order_id=order_id, volume=str(volume), client=client,
        )
    fill = await _fetch_fill(resp["uuid"], client=client)
    db.update_order_filled(
        order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
        _slippage_pct(fill["filled_price"], expected_price), "done",
    )
    return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
            "filled_volume": fill["executed_volume"], "fee": fill["fee"]}


async def _run_limit(
    order_id: str, market: str, side: str, price: float, volume: float,
    *, client: httpx.AsyncClient | None = None,
) -> dict:
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id, price=str(price), volume=str(volume), client=client,
    )
    db.update_order_filled(order_id, resp["uuid"], None, None, None, None, "wait")
    return {"order_id": order_id, "status": "wait", "filled_price": None, "filled_volume": None, "fee": None}


async def enter(
    strategy: dict, capital: float, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    if position_manager.get_open_position(strategy["id"]) is not None:
        raise ValueError(f"이미 오픈 포지션이 있습니다: {strategy['id']}")

    risk_config = json.loads(strategy["risk_config_json"])
    mode = risk_config["order_execution_mode"]
    market = strategy["market"]
    price = round_to_tick(expected_price)
    volume = _floor_volume(capital / price)

    order_id = db.insert_order(strategy["id"], None, market, "bid", mode, price, volume, expected_price)

    if dry_run:
        db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
        result = {"order_id": order_id, "status": "done", "filled_price": price,
                   "filled_volume": volume, "fee": 0.0}
    elif mode == "market":
        result = await _run_market(order_id, market, "bid", capital, volume, expected_price, client=client)
    elif mode == "limit":
        result = await _run_limit(order_id, market, "bid", price, volume, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")

    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    position_manager.open_position(strategy["id"], market, result["filled_price"], result["filled_volume"])
    return db.get_order_by_id(result["order_id"])


async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    if position is None:
        raise ValueError("오픈 포지션이 없습니다")

    risk_config = json.loads(strategy["risk_config_json"])
    mode = risk_config["order_execution_mode"]
    market = strategy["market"]
    price = round_to_tick(expected_price)
    volume = position["entry_qty"]

    order_id = db.insert_order(strategy["id"], position["id"], market, "ask", mode, price, volume, expected_price)

    if dry_run:
        db.update_order_filled(order_id, None, price, volume, 0.0, 0.0, "done")
        result = {"order_id": order_id, "status": "done", "filled_price": price,
                   "filled_volume": volume, "fee": 0.0}
    elif mode == "market":
        result = await _run_market(order_id, market, "ask", None, volume, expected_price, client=client)
    elif mode == "limit":
        result = await _run_limit(order_id, market, "ask", price, volume, client=client)
    else:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")

    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    close_result = position_manager.close_position(
        position["id"], result["filled_price"], result["filled_volume"], result["fee"], "signal",
    )
    order = db.get_order_by_id(result["order_id"])
    order.update(close_result)
    return order
