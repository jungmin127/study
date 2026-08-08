"""
trading/reconciler.py

거래소 실제 상태(잔고/미체결·종료주문)와 내부 DB를 대조해 외부(수동) 개입을 감지하고
자동으로 self-heal한다. 데몬 시작 시 1회(hydrate_state) + 러닝 중 주기적으로
(check_manual_intervention) 호출되는 공유 파이프라인 구조(설계 스펙 결정1). 스스로
타이머/루프를 갖지 않는다 — 언제 호출할지는 daemon.py(⑤-4b)의 몫이다. trading.upbit_client
+ trading.db + trading.position_manager + trading.risk_manager만 의존. engine/ 미의존.
"""
from __future__ import annotations

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
