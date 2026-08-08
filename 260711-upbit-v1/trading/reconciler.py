"""
trading/reconciler.py

거래소 실제 상태(잔고/미체결·종료주문)와 내부 DB를 대조해 외부(수동) 개입을 감지하고
자동으로 self-heal한다. 데몬 시작 시 1회(hydrate_state) + 러닝 중 주기적으로
(check_manual_intervention) 호출되는 공유 파이프라인 구조(설계 스펙 결정1). 스스로
타이머/루프를 갖지 않는다 — 언제 호출할지는 daemon.py(⑤-4b)의 몫이다. trading.upbit_client
+ trading.db + trading.position_manager + trading.risk_manager만 의존. engine/ 미의존.
"""
from __future__ import annotations

import json

import httpx

import trading.db as db
import trading.position_manager as position_manager
import trading.risk_manager as risk_manager
import trading.upbit_client as upbit_client

_QTY_EPSILON = 1e-6


def _coin_currency(market: str) -> str:
    return market.split("-", 1)[1]


async def _get_coin_account(market: str, *, client: httpx.AsyncClient | None = None) -> dict | None:
    accounts = await upbit_client.get_accounts(client=client)
    currency = _coin_currency(market)
    for account in accounts:
        if account["currency"] == currency:
            return account
    return None


async def _sync_pending_limit_orders(
    strategy: dict, *, client: httpx.AsyncClient | None = None,
) -> int:
    """내부 status='wait', order_type='limit' 주문(오프라인 동안 결과를 못 받은 사용자
    선택 방치 주문, 설계 스펙 결정6)을 재조회해 조용히 동기화한다. 우리가 낸 주문이므로
    수동개입으로 기록하지 않는다."""
    wait_orders = db.list_wait_orders(strategy["id"], order_type="limit")
    synced = 0
    for order in wait_orders:
        if not order["upbit_uuid"]:
            continue
        resp = await upbit_client.get_order(uuid=order["upbit_uuid"], client=client)
        if resp["state"] == "wait":
            continue

        executed_volume = float(resp["executed_volume"])
        filled_price = (
            sum(float(t["funds"]) for t in resp["trades"]) / executed_volume
            if executed_volume > 0 else None
        )
        status = "done" if resp["state"] == "done" else "cancel"
        db.update_order_filled(
            order["id"], order["upbit_uuid"], filled_price, executed_volume,
            float(resp["paid_fee"]), None, status,
        )
        synced += 1
    return synced


async def hydrate_state(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """데몬 시작 시 전략 1개당 1회 호출. 내부 wait limit 주문을 먼저 동기화(결정6)한 뒤,
    strategy['baseline_qty']가 None이면(결정9, 이 전략의 첫 호출) 그 시점 실제 코인 잔고를
    baseline으로 저장하고 불일치 검사 없이 반환한다. 이미 baseline이 있으면
    _run_reconcile_pipeline()을 수행한다(Task8에서 연결)."""
    synced = await _sync_pending_limit_orders(strategy, client=client)

    if strategy["baseline_qty"] is None:
        account = await _get_coin_account(strategy["market"], client=client)
        baseline = (float(account["balance"]) + float(account["locked"])) if account else 0.0
        db.update_live_strategy_baseline_qty(strategy["id"], baseline)
        return {"synced_wait_orders": synced, "baseline_captured": True}

    result = await _run_reconcile_pipeline(strategy, client=client)
    return {"synced_wait_orders": synced, "baseline_captured": False, **result}


async def _detect_external_orders(
    strategy: dict, *, client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """그 마켓의 미체결+최근 종료 주문을 조회해 내부 DB에 없는 uuid만 골라 기록한다
    (설계 스펙 결정7 준비 — 여기서 찾은 주문들을 _reconcile_position이 재사용)."""
    market = strategy["market"]
    open_orders = await upbit_client.list_open_orders(market=market, client=client)
    closed_orders = await upbit_client.list_closed_orders(
        market=market, states=["done", "cancel"], client=client,
    )

    risk_config = json.loads(strategy["risk_config_json"])
    policy = risk_config.get("manual_intervention_policy", "all_stop")

    found: list[dict] = []
    for raw in open_orders + closed_orders:
        upbit_uuid = raw["uuid"]
        if db.get_order_by_upbit_uuid(upbit_uuid) is not None:
            continue

        detail = await upbit_client.get_order(uuid=upbit_uuid, client=client)
        executed_volume = float(detail["executed_volume"])
        filled_price = (
            sum(float(t["funds"]) for t in detail["trades"]) / executed_volume
            if executed_volume > 0 else None
        )
        status = "wait" if detail["state"] == "wait" else (
            "done" if detail["state"] == "done" else "cancel"
        )

        order_id = db.insert_external_order(
            strategy["id"], None, market, detail["side"], detail["ord_type"], upbit_uuid,
            filled_price, executed_volume if executed_volume > 0 else None,
            float(detail["paid_fee"]), status,
        )
        found.append(db.get_order_by_id(order_id))

        action_taken = "all_stop" if policy == "all_stop" else "acknowledged_and_continued"
        db.insert_manual_intervention_event(
            market,
            f"내부에 없는 외부주문 발견: uuid={upbit_uuid}, side={detail['side']}, "
            f"state={detail['state']}",
            action_taken,
        )
        if policy == "all_stop":
            db.update_live_strategy_status(strategy["id"], "paused")

    return found


def _weighted_fill(orders: list[dict]) -> tuple[float, float, float]:
    """반환: (총체결수량, 가중평균체결가, 총수수료). 빈 리스트면 (0, 0, 0)."""
    total_volume = sum(o["filled_volume"] for o in orders)
    total_funds = sum(o["filled_price"] * o["filled_volume"] for o in orders)
    total_fee = sum(o["fee"] or 0.0 for o in orders)
    avg_price = total_funds / total_volume if total_volume > 0 else 0.0
    return total_volume, avg_price, total_fee


def _apply_explained_change(
    strategy: dict, position: dict | None, actual_qty: float,
    buy_volume: float, buy_price: float, sell_price: float, sell_fee: float,
) -> str | None:
    """설계 스펙 결정4/7 — 매칭된 외부주문의 실제 체결가로 정밀하게 self-heal한다.
    None을 반환하면 "정밀하게 open으로 설명할 수 없다"는 뜻이며, 호출자는 unexplained
    처리로 넘어가야 한다(Finding 1 — baseline_qty로 인해 actual_qty가 0 이하가 되는
    경우, 음수/영 수량 포지션을 여는 것을 방지)."""
    if position is None:
        if actual_qty <= _QTY_EPSILON:
            return None
        position_manager.open_position(strategy["id"], strategy["market"], buy_price, actual_qty)
        return "opened"

    if actual_qty <= _QTY_EPSILON:
        close_result = position_manager.close_position(
            position["id"], sell_price, position["entry_qty"], sell_fee, "manual",
        )
        risk_manager.record_trade_result(
            strategy["id"], close_result["realized_pnl"], close_result["capital_after"],
        )
        return "closed"

    if actual_qty > position["entry_qty"] + _QTY_EPSILON:
        # 순매수(외부 매수로 top-up) — 원가를 가중평균으로 재계산(정밀 계산 원칙, Finding 2)
        old_cost = position["entry_price"] * position["entry_qty"]
        added_cost = buy_price * (actual_qty - position["entry_qty"])
        blended_price = (old_cost + added_cost) / actual_qty
        db.adjust_position_qty(position["id"], actual_qty, blended_price)
    else:
        # 순매도(부분청산) — 원가는 그대로, 수량만 축소
        db.adjust_position_qty(position["id"], actual_qty)
    return "adjusted"


def _self_heal_unexplained(strategy: dict, position: dict | None, actual_qty: float, avg_buy_price: float) -> None:
    """설계 스펙 결정5 — 가격 근거가 없으므로 PnL/current_capital은 건드리지 않고
    수량만 실제 잔고에 맞춘다. 신규 포지션은 업비트가 자체 관리하는 avg_buy_price를
    근사 원가로 쓴다(정확한 매도가는 알 수 없어도, 향후 정상 청산 시 PnL 계산의 기준점은
    있어야 한다)."""
    if position is None:
        if actual_qty > _QTY_EPSILON:
            position_manager.open_position(strategy["id"], strategy["market"], avg_buy_price, actual_qty)
        return

    if actual_qty <= _QTY_EPSILON:
        db.close_position_row(position["id"], None, position["entry_qty"], None, None, "manual_unexplained")
        return

    db.adjust_position_qty(position["id"], actual_qty)


async def _reconcile_position(
    strategy: dict, external_orders: list[dict], *, client: httpx.AsyncClient | None = None,
) -> dict:
    market = strategy["market"]
    risk_config = json.loads(strategy["risk_config_json"])
    policy = risk_config.get("manual_intervention_policy", "all_stop")

    account = await _get_coin_account(market, client=client)
    raw_balance = (float(account["balance"]) + float(account["locked"])) if account else 0.0
    avg_buy_price = float(account["avg_buy_price"]) if account and account.get("avg_buy_price") else 0.0
    baseline_qty = strategy["baseline_qty"] or 0.0
    actual_qty = raw_balance - baseline_qty

    position = position_manager.get_open_position(strategy["id"])
    internal_qty = position["entry_qty"] if position else 0.0

    diff = actual_qty - internal_qty
    if abs(diff) <= _QTY_EPSILON:
        return {"balance_mismatch": False, "action": "none", "paused": False}

    done_buys = [o for o in external_orders if o["side"] == "bid" and o["filled_volume"]]
    done_sells = [o for o in external_orders if o["side"] == "ask" and o["filled_volume"]]
    buy_volume, buy_price, _buy_fee = _weighted_fill(done_buys)
    sell_volume, sell_price, sell_fee = _weighted_fill(done_sells)
    explained_diff = buy_volume - sell_volume

    action = None
    if (buy_volume > 0 or sell_volume > 0) and abs(diff - explained_diff) <= _QTY_EPSILON:
        action = _apply_explained_change(
            strategy, position, actual_qty, buy_volume, buy_price, sell_price, sell_fee,
        )

    if action is not None:
        paused = policy == "all_stop"
        if paused:
            db.update_live_strategy_status(strategy["id"], "paused")
        return {"balance_mismatch": True, "action": action, "paused": paused}

    _self_heal_unexplained(strategy, position, actual_qty, avg_buy_price)
    db.insert_manual_intervention_event(
        market,
        f"설명 안 되는 잔고 변화: 기대수량={internal_qty}, 실제수량={actual_qty}",
        "all_stop",
    )
    db.update_live_strategy_status(strategy["id"], "paused")
    return {"balance_mismatch": True, "action": "unexplained", "paused": True}


async def _run_reconcile_pipeline(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """_detect_external_orders() → _reconcile_position() 순서로 실행한다. 업비트 API 실패는
    여기서 흡수하고(설계 스펙 결정8) 매매를 막지 않는다 — reconciler는 감시자이지
    트레이더가 아니다."""
    try:
        external_orders = await _detect_external_orders(strategy, client=client)
        return await _reconcile_position(strategy, external_orders, client=client)
    except (httpx.HTTPError, upbit_client.UpbitRateLimitError) as exc:
        return {"error": str(exc)}


async def check_manual_intervention(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """러닝 중 데몬이 주기적으로(15~30초, 스케줄링은 daemon.py 몫) 호출한다."""
    return await _run_reconcile_pipeline(strategy, client=client)
