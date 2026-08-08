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
