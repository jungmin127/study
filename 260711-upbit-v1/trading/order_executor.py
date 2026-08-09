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


_SUPPORTED_MODES = frozenset({"market", "limit", "limit_timeout", "market_capped"})

_BID_FEE_RATE = 0.0005  # 업비트 기본 매수 수수료율 0.05%

_MIN_ORDER_AMOUNT_KRW = 5000  # 업비트 원화마켓 최소 주문금액


def _validate_mode(mode: str, risk_config: dict) -> None:
    """orders 행을 만들기 전에 실행모드 설정을 검증한다. insert_order 뒤에서 검증하면
    잘못 설정된 전략이 status='wait' 고아 행을 영구히 남긴다(최종리뷰 Important #4).
    dry_run 경로도 이 검증을 건너뛰면 안 된다."""
    if mode not in _SUPPORTED_MODES:
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")
    if mode == "market_capped" and "max_slippage_pct" not in risk_config:
        raise ValueError("market_capped 모드는 risk_config에 max_slippage_pct가 필요합니다")


def round_to_tick(price: float) -> float:
    for threshold, tick in _TICK_TABLE:
        if price >= threshold:
            return round(round(price / tick) * tick, 8)
    return price


def _floor_volume(volume: float) -> float:
    # 이진 표현 오차 보정 후 내림한다. 보정 없이 floor하면 0.00998 * 1e8 == 997999.9999999999
    # 이라 이미 8자리인 값에서도 1사토시를 깎아먹는다(exit()의 전량매도가 먼지를 남긴다).
    return math.floor(round(volume * 1e8, 6)) / 1e8


def _fmt(value: float) -> str:
    """API 파라미터용 안전한 문자열 변환. bare str()은 작은 값에서 과학적 표기법
    (예: 6.66e-05)을 만들어 업비트가 거부하고, 부동소수점 오차로 불필요한 자리수도
    남긴다(예: 0.1+0.2 == '0.30000000000000004').

    Decimal(str(value)).normalize()는 후자를 못 고치므로(str()이 이미 오차를 문자열로
    굳혀버림) 업비트 최소 단위인 8자리 고정소수점으로 포맷한 뒤 꼬리 0을 떼는 방식을 쓴다."""
    text = f"{value:.8f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


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
            market, "bid", "price", order_id=order_id, price=_fmt(capital), client=client,
        )
    else:
        resp = await _create_order_with_retry(
            market, "ask", "market", order_id=order_id, volume=_fmt(volume), client=client,
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
        market, side, "limit", order_id=order_id, price=_fmt(price), volume=_fmt(volume), client=client,
    )
    db.update_order_filled(order_id, resp["uuid"], None, None, None, None, "wait")
    return {"order_id": order_id, "status": "wait", "filled_price": None, "filled_volume": None, "fee": None}


def _finalize_first_leg(
    order_id: str, upbit_uuid: str, fill: dict, expected_price: float,
) -> dict:
    """1차 지정가 주문의 체결만으로 주문을 확정한다(타임아웃 내 전량체결 / 잔량이
    최소주문금액 미만 / 잔량 전환 실패 — 세 경우 모두 결과 형태가 같다)."""
    volume = _floor_volume(fill["executed_volume"])
    if volume <= 0:
        db.update_order_filled(order_id, upbit_uuid, None, None, None, None, "cancel")
        return {"order_id": order_id, "status": "cancel", "filled_price": None,
                "filled_volume": None, "fee": None}

    db.update_order_filled(
        order_id, upbit_uuid, fill["filled_price"], volume, fill["fee"],
        _slippage_pct(fill["filled_price"], expected_price), "done",
    )
    return {"order_id": order_id, "status": "done", "filled_price": fill["filled_price"],
            "filled_volume": volume, "fee": fill["fee"]}


async def _run_limit_timeout(
    order_id: str, live_strategy_id: str, position_id: str | None, market: str, side: str,
    price: float, volume: float, expected_price: float, timeout_sec: float,
    *, client: httpx.AsyncClient | None = None,
) -> dict:
    resp = await _create_order_with_retry(
        market, side, "limit", order_id=order_id, price=_fmt(price), volume=_fmt(volume), client=client,
    )
    await asyncio.sleep(timeout_sec)
    fill = await _fetch_fill(resp["uuid"], client=client)

    if fill["state"] == "done":
        return _finalize_first_leg(order_id, resp["uuid"], fill, expected_price)

    try:
        await upbit_client.cancel_order(uuid=resp["uuid"], client=client)
    except httpx.HTTPStatusError:
        # 조회와 취소 사이에 전량 체결되면 업비트는 취소를 거부한다. 그 사이 실제로 확정된
        # 상태를 다시 읽어 done이면 잔량 전환 없이 확정한다(최종리뷰 Important #8-a).
        # done이 아니면 진짜 장애이므로 삼키지 않고 그대로 올린다.
        fill = await _fetch_fill(resp["uuid"], client=client)
        if fill["state"] != "done":
            raise
        return _finalize_first_leg(order_id, resp["uuid"], fill, expected_price)

    first_volume = fill["executed_volume"]
    first_funds = fill["filled_price"] * first_volume if first_volume else 0.0
    first_fee = fill["fee"]
    remaining_volume = fill["remaining_volume"]

    # 잔량 환산 기준가: timeout_sec 동안 시세가 움직였을 수 있으므로 원래의 expected_price보다
    # 방금 체결된 1차 체결가가 훨씬 신선하다. 1차가 한 주도 안 붙었을 때만 폴백한다
    # (실시간 호가 조회는 이 모듈 범위 밖 — 이후 서브플랜의 WebSocket ticker 과제).
    price_basis = fill["filled_price"] if first_volume > 0 else round_to_tick(expected_price)

    # 잔량이 업비트 최소주문금액 미만이면 전환 주문 자체가 거부되므로 시도하지 않고
    # 1차 체결만으로 확정한다(최종리뷰 Important #8-b).
    if remaining_volume * price_basis < _MIN_ORDER_AMOUNT_KRW:
        return _finalize_first_leg(order_id, resp["uuid"], fill, expected_price)

    db.update_order_filled(order_id, resp["uuid"], fill["filled_price"], first_volume, first_fee, None, "cancel")

    market_order_id = db.insert_order(
        live_strategy_id, position_id, market, side, "market", None, remaining_volume, expected_price,
        replaces_order_id=order_id,
    )
    try:
        if side == "bid":
            market_resp = await _create_order_with_retry(
                market, "bid", "price", order_id=market_order_id,
                price=_fmt(price_basis * remaining_volume), client=client,
            )
        else:
            market_resp = await _create_order_with_retry(
                market, "ask", "market", order_id=market_order_id,
                volume=_fmt(remaining_volume), client=client,
            )
        second_fill = await _await_settlement(market_resp["uuid"], client=client)
    except Exception:
        # 잔량 전환이 실패해도 1차 부분체결은 이미 실제로 보유 중인 코인이다. 예외를 그대로
        # 올리면 그 체결이 아무 데도 기록되지 않는다(최종리뷰 Important #8-d).
        # 전환 주문 행(market_order_id)은 실제 접수 여부를 알 수 없으므로 'wait'인 채로 둔다.
        if first_volume == 0:
            raise  # 보호할 부분체결이 없으면 진짜 실패다
        return _finalize_first_leg(order_id, resp["uuid"], fill, expected_price)

    # 잔량 주문이 체결 없이 취소/미체결로 확정된 경우. filled_price가 None이라 아래
    # total_funds 계산이 TypeError로 터지므로, 실패했을 때와 똑같이 1차 체결만으로 확정한다.
    if second_fill["executed_volume"] <= 0:
        return _finalize_first_leg(order_id, resp["uuid"], fill, expected_price)

    # 단순 합은 0.30000000000000004 같은 8자리 초과 값이 되므로 내림한다. avg_price도
    # 내림된 수량으로 나눠, 실제 기록되는 filled_volume과 금액 정합성을 맞춘다.
    total_volume = _floor_volume(first_volume + second_fill["executed_volume"])
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
        price=_fmt(capped_price), volume=_fmt(volume), time_in_force="fok", client=client,
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
    _validate_mode(mode, risk_config)

    # 업비트는 매수 시 주문금액+수수료를 묶으므로, capital 전액을 주문금액으로 쓰면
    # 완전복리 전략이 늘 보유 원화보다 조금 더 주문하게 된다(최종리뷰 Important #7).
    # 모든 모드의 수량/금액 계산이 이 capital에서 파생되므로 여기서 한 번만 조정한다.
    capital = capital / (1 + _BID_FEE_RATE)

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
    else:  # _validate_mode()가 이미 걸러 도달 불가 — 모드 추가 시 분기 누락 방지용 방어코드
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")

    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    position_manager.open_position(strategy["id"], market, result["filled_price"], result["filled_volume"])
    return db.get_order_by_id(result["order_id"])


async def exit(
    strategy: dict, position: dict, expected_price: float,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
    close_reason: str = "signal",
) -> dict:
    if position is None:
        raise ValueError("오픈 포지션이 없습니다")

    risk_config = json.loads(strategy["risk_config_json"])
    mode = risk_config["order_execution_mode"]
    _validate_mode(mode, risk_config)

    market = strategy["market"]
    price = (
        _capped_price(expected_price, "ask", risk_config["max_slippage_pct"])
        if mode == "market_capped" else round_to_tick(expected_price)
    )
    volume = _floor_volume(position["entry_qty"])  # 8자리 초과 정밀도는 업비트가 거부한다

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
    else:  # _validate_mode()가 이미 걸러 도달 불가 — 모드 추가 시 분기 누락 방지용 방어코드
        raise ValueError(f"지원하지 않는 order_execution_mode: {mode}")

    if result["status"] != "done":
        return db.get_order_by_id(result["order_id"])

    close_result = position_manager.close_position(
        position["id"], result["filled_price"], result["filled_volume"], result["fee"], close_reason,
    )
    order = db.get_order_by_id(result["order_id"])
    order.update(close_result)
    return order


async def exit_for_risk(
    strategy: dict, position: dict, expected_price: float, reason: str,
    *, client: httpx.AsyncClient | None = None, dry_run: bool = False,
) -> dict:
    """ticker 트리거 손절/익절 전용 진입점(⑤-4c). handle_signal_result()와 달리
    signals 테이블과 무관하다 — candle 사이클 밖에서 발생하는 이벤트라 대응되는 signal
    row가 없다. 성공 시 record_trade_result()까지 호출(handle_signal_result의 매도
    성공 분기와 동일한 부기 의무 — daemon.py의 check_circuit_breaker() 호출 전제).

    불변조건(4라운드 구조적 수정, 사용자 결정 — "슬리피지는 감수하겠습니다"): strategy에
    설정된 order_execution_mode가 무엇이든 항상 무시하고 market으로 강제한다. 1~3라운드는
    "이미 진행 중인 청산 주문이 있으면 막는다"는 가드를 order_type/나이/upbit_uuid로 점점
    정교하게 다듬으며 반복적으로 실패했다 — order_execution_mode='limit'(타임아웃 없음)
    에서는 청산 주문이 거래소에 무기한 열려 있을 수 있어, 그 행이 "정상적으로 진행 중"인지
    "영원히 안 채워질 것"인지 DB 행만 보고는 원천적으로 구별할 수 없기 때문이다(가드를
    조이면 정당한 재시도가 막히고, 풀면 중복 실주문이 나갔다). market 주문은
    _await_settlement()의 폴링 타임아웃(수 초) 안에 반드시 체결/실패로 확정되므로, 이
    함수만은 그 불확실성 자체를 구조적으로 제거한다 — 대신 슬리피지를 감수한다. 호출자의
    strategy dict는 변형하지 않는다(shallow copy 위에서 risk_config_json만 재작성)."""
    forced_risk_config = json.loads(strategy["risk_config_json"])
    forced_risk_config["order_execution_mode"] = "market"
    # market 모드는 max_slippage_pct 등 다른 risk_config 필드를 요구하지 않는다
    # (_validate_mode 참고 — market_capped만 요구) — 그 필드를 한 번도 설정한 적 없는
    # 전략을 강제로 market에 태워도 안전하다.
    forced_strategy = {**strategy, "risk_config_json": json.dumps(forced_risk_config)}

    order = await exit(
        forced_strategy, position, expected_price, client=client, dry_run=dry_run, close_reason=reason,
    )
    if order["status"] == "done":
        risk_manager.record_trade_result(strategy["id"], order["realized_pnl"], order["capital_after"])
        return {"action": "exited", "order_id": order["id"]}
    if order["status"] == "cancel":
        return {"action": "slippage_exceeded", "order_id": order["id"]}
    return {"action": "pending", "order_id": order["id"]}


async def handle_signal_result(
    strategy_id: str, signal_result: dict, *, dry_run: bool = False,
) -> dict:
    result = {"buy_action": None, "sell_action": None, "buy_order_id": None, "sell_order_id": None}

    # 새 봉이 없으면 signal_engine._no_new_candle_result()가 latest_close/*_signal_id 없는
    # 축약 dict를 주므로, paused만 보고 진행하면 KeyError로 터진다(최종리뷰 Important #3).
    if not signal_result["new_candle"] or signal_result["paused"]:
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
