"""
trading/order_executor.py

signal_engine.evaluate_signals() 결과를 받아 실제 업비트 주문(매수/매도)을 실행한다.
market/limit/limit_timeout/market_capped 4가지 실행모드를 지원하고, 서킷브레이커 확인 →
enter()/exit() 호출 → signals.resulting_order_id 갱신까지 handle_signal_result()가 한 번에
처리한다(설계 스펙 결정3). trading.upbit_client(async REST) + trading.position_manager +
trading.risk_manager를 엮는 이 서브플랜의 유일한 모듈. engine/ 미의존.
"""
from __future__ import annotations

import asyncio
import json
import math

import httpx

import trading.db as db
import trading.position_manager as position_manager
import trading.risk_manager as risk_manager
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


def _capped_price(expected_price: float, side: str, max_slippage_pct: float) -> float:
    """market_capped 모드가 실제로 내는 주문가(허용 슬리피지 상한/하한가)."""
    sign = 1 if side == "bid" else -1
    return round_to_tick(expected_price * (1 + sign * max_slippage_pct / 100))


async def _await_settlement(
    upbit_uuid: str, *, timeout: float = 3.0, interval: float = 0.2,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """체결 상태가 확정(done/cancel)될 때까지 짧은 간격으로 폴링한다. 업비트는 주문을
    비동기로 체결하므로, create_order() 직후 즉시 조회하면 아직 wait 상태일 수 있다.
    timeout 안에 확정되지 않으면 마지막으로 조회한 상태를 그대로 반환한다(호출자가 그
    상태를 판단)."""
    elapsed = 0.0
    fill = await _fetch_fill(upbit_uuid, client=client)
    while fill["state"] not in ("done", "cancel") and elapsed < timeout:
        await asyncio.sleep(interval)
        elapsed += interval
        fill = await _fetch_fill(upbit_uuid, client=client)
    return fill


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
    fill = await _await_settlement(resp["uuid"], client=client)
    # 폴링 타임아웃까지 확정되지 않았으면 wait으로 보고한다. 무조건 "done"으로 기록하면
    # 미체결/부분체결 주문이 전량체결로 둔갑한다(최종리뷰 Critical #1).
    status = fill["state"] if fill["state"] in ("done", "cancel") else "wait"
    slippage = (
        _slippage_pct(fill["filled_price"], expected_price)
        if fill["filled_price"] is not None else None
    )
    db.update_order_filled(
        order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
        slippage, status,
    )
    return {"order_id": order_id, "status": status, "filled_price": fill["filled_price"],
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


async def _run_limit_timeout(
    order_id: str, live_strategy_id: str, position_id: str | None, market: str, side: str,
    price: float, volume: float, expected_price: float, timeout_sec: float,
    *, client: httpx.AsyncClient | None = None,
) -> dict:
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id, price=str(price), volume=str(volume), client=client,
    )
    await asyncio.sleep(timeout_sec)
    fill = await _fetch_fill(resp["uuid"], client=client)

    if fill["state"] == "done":
        db.update_order_filled(
            order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
            _slippage_pct(fill["filled_price"], expected_price), "done",
        )
        return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
                "filled_volume": fill["executed_volume"], "fee": fill["fee"]}

    await upbit_client.cancel_order(uuid=resp["uuid"], client=client)
    first_volume = fill["executed_volume"]
    first_funds = fill["filled_price"] * first_volume if first_volume else 0.0
    first_fee = fill["fee"]
    db.update_order_filled(order_id, resp["uuid"], fill["filled_price"], first_volume, first_fee, None, "cancel")

    remaining_volume = fill["remaining_volume"]
    market_order_id = db.insert_order(
        live_strategy_id, position_id, market, side, "market", None, remaining_volume, expected_price,
        replaces_order_id=order_id,
    )
    if side == "bid":
        market_resp = await _create_order_with_retry(
            market, "bid", "price", order_id=market_order_id,
            price=str(round_to_tick(expected_price) * remaining_volume), client=client,
        )
    else:
        market_resp = await _create_order_with_retry(
            market, "ask", "market", order_id=market_order_id, volume=str(remaining_volume), client=client,
        )
    second_fill = await _await_settlement(market_resp["uuid"], client=client)

    total_volume = first_volume + second_fill["executed_volume"]
    total_funds = first_funds + second_fill["filled_price"] * second_fill["executed_volume"]
    total_fee = first_fee + second_fill["fee"]
    avg_price = total_funds / total_volume
    db.update_order_filled(
        market_order_id, market_resp["uuid"], avg_price, total_volume, total_fee,
        _slippage_pct(avg_price, expected_price), "done",
    )
    return {"order_id": market_order_id, "status": "done", "filled_price": avg_price,
            "filled_volume": total_volume, "fee": total_fee}


async def _run_market_capped(
    order_id: str, market: str, side: str, expected_price: float, volume: float,
    max_slippage_pct: float, *, capital: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    capped_price = _capped_price(expected_price, side, max_slippage_pct)
    if side == "bid":
        # 실제 주문가는 expected_price가 아니라 더 불리한 capped_price다. 호출자가 넘긴
        # expected_price 기준 수량을 그대로 쓰면 capped_price × volume이 clamp된 capital을
        # max_slippage_pct%만큼 초과한다(최종리뷰 Critical #2).
        volume = _floor_volume(capital / capped_price)
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id,
        price=str(capped_price), volume=str(volume), time_in_force="fok", client=client,
    )
    fill = await _await_settlement(resp["uuid"], client=client)
    if fill["state"] != "done" or fill["executed_volume"] == 0:
        db.update_order_filled(order_id, resp["uuid"], None, None, None, None, "cancel")
        return {"order_id": order_id, "status": "cancel", "filled_price": None,
                "filled_volume": None, "fee": None}

    db.update_order_filled(
        order_id, resp["uuid"], fill["filled_price"], fill["executed_volume"], fill["fee"],
        _slippage_pct(fill["filled_price"], expected_price), "done",
    )
    return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
            "filled_volume": fill["executed_volume"], "fee": fill["fee"]}


async def enter(
    strategy: dict, capital: float, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    if position_manager.get_open_position(strategy["id"]) is not None:
        raise ValueError(f"이미 오픈 포지션이 있습니다: {strategy['id']}")

    risk_config = json.loads(strategy["risk_config_json"])
    mode = risk_config["order_execution_mode"]
    market = strategy["market"]
    # market_capped는 expected_price가 아니라 슬리피지 상한가로 주문하므로, orders 행의
    # requested_price/requested_volume도 그 실제 주문가 기준이어야 한다.
    price = (
        _capped_price(expected_price, "bid", risk_config["max_slippage_pct"])
        if mode == "market_capped" else round_to_tick(expected_price)
    )
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
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], None, market, "bid", price, volume, expected_price,
            timeout_sec, client=client,
        )
    elif mode == "market_capped":
        result = await _run_market_capped(
            order_id, market, "bid", expected_price, volume, risk_config["max_slippage_pct"],
            capital=capital, client=client,
        )
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
    price = (
        _capped_price(expected_price, "ask", risk_config["max_slippage_pct"])
        if mode == "market_capped" else round_to_tick(expected_price)
    )
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
    elif mode == "limit_timeout":
        timeout_sec = risk_config.get("order_timeout_sec", 10)
        result = await _run_limit_timeout(
            order_id, strategy["id"], position["id"], market, "ask", price, volume, expected_price,
            timeout_sec, client=client,
        )
    elif mode == "market_capped":
        result = await _run_market_capped(
            order_id, market, "ask", expected_price, volume, risk_config["max_slippage_pct"],
            capital=None, client=client,  # 매도는 보유수량 전량이라 capital 기반 재계산 불필요
        )
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


async def handle_signal_result(
    strategy_id: str, signal_result: dict, *, dry_run: bool = False,
) -> dict:
    result = {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    if signal_result["paused"]:
        return result

    strategy = db.get_live_strategy(strategy_id)
    risk_config = json.loads(strategy["risk_config_json"])
    position = position_manager.get_open_position(strategy_id)
    expected_price = signal_result["latest_close"]

    if signal_result["buy_signal"] is True and position is None:
        if risk_manager.is_circuit_tripped_today(strategy_id):
            db.update_signal_result(signal_result["buy_signal_id"], None, "circuit_breaker_tripped")
            result["buy_action"] = "skipped_circuit_breaker"
        else:
            capital = min(strategy["current_capital"], risk_config["max_position_per_market"])
            order = await enter(strategy, capital, expected_price, dry_run=dry_run)
            result["buy_order_id"] = order["id"]
            if order["status"] == "done":
                db.update_signal_result(signal_result["buy_signal_id"], order["id"], None)
                result["buy_action"] = "entered"
            elif order["status"] == "cancel":
                db.update_signal_result(signal_result["buy_signal_id"], order["id"], "slippage_exceeded")
                result["buy_action"] = "slippage_exceeded"
            else:
                db.update_signal_result(signal_result["buy_signal_id"], order["id"], None)
                result["buy_action"] = "pending"

    if signal_result["sell_signal"] is True and position is not None:
        order = await exit(strategy, position, expected_price, dry_run=dry_run)
        result["sell_order_id"] = order["id"]
        if order["status"] == "done":
            db.update_signal_result(signal_result["sell_signal_id"], order["id"], None)
            result["sell_action"] = "exited"
            risk_manager.record_trade_result(strategy_id, order["realized_pnl"], order["capital_after"])
        elif order["status"] == "cancel":
            db.update_signal_result(signal_result["sell_signal_id"], order["id"], "slippage_exceeded")
            result["sell_action"] = "slippage_exceeded"
        else:
            db.update_signal_result(signal_result["sell_signal_id"], order["id"], None)
            result["sell_action"] = "pending"

    return result
