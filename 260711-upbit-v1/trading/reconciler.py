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
from datetime import datetime, timezone

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


async def sync_pending_limit_orders(
    strategy: dict, *, client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """내부 status='wait', order_type='limit' 주문(오프라인 동안 결과를 못 받은 사용자
    선택 방치 주문, 설계 스펙 결정6)을 재조회해 조용히 동기화한다. 우리가 낸 주문이므로
    수동개입으로 기록하지 않는다. 반환값(동기화된 주문 행들)은 _reconcile_position이
    own_fills로 재사용한다 — 그렇지 않으면 이 체결이 "설명 안 되는 잔고 변화"로 오인돼
    포지션이 잘못 강제종료된다(최종 브랜치 리뷰 Critical 1)."""
    wait_orders = db.list_wait_orders(strategy["id"], order_type="limit")
    synced: list[dict] = []
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
        synced.append(db.get_order_by_id(order["id"]))
    return synced


async def hydrate_state(strategy: dict, *, client: httpx.AsyncClient | None = None) -> dict:
    """데몬 시작 시 전략 1개당 1회 호출. 최신 strategy 행을 다시 읽은 뒤(호출자가 오래된
    dict를 재사용해도 baseline_qty 판단이 stale해지지 않도록 — 최종 브랜치 리뷰 재검토에서
    발견된 Critical 2의 잔여 구멍, _run_reconcile_pipeline만 재읽기하고 이 함수 자체의
    baseline_qty 첫 판단은 놓쳤었다) 내부 wait limit 주문을 먼저 동기화(결정6)한다.
    strategy['baseline_qty']가 None이면(결정9, 이 전략의 첫 호출) 그 시점 실제 코인 잔고에서
    이미 봇이 추적 중인 오픈 포지션 수량을 뺀 값을 baseline으로 저장하고(최종 브랜치 리뷰
    Important 7 — 포지션이 있는 채로 baseline을 다시 잡으면 그 포지션이 baseline에
    흡수돼 사라진다) 불일치 검사 없이 반환한다. 이미 baseline이 있으면
    _run_reconcile_pipeline()에 이번에 동기화한 own_fills를 넘겨 수행한다."""
    strategy = db.get_live_strategy(strategy["id"]) or strategy
    synced_orders = await sync_pending_limit_orders(strategy, client=client)

    if strategy["baseline_qty"] is None:
        account = await _get_coin_account(strategy["market"], client=client)
        raw_balance = (float(account["balance"]) + float(account["locked"])) if account else 0.0
        existing_position = position_manager.get_open_position(strategy["id"])
        own_qty = existing_position["entry_qty"] if existing_position else 0.0
        baseline = max(raw_balance - own_qty, 0.0)
        db.update_live_strategy_baseline_qty(strategy["id"], baseline)
        return {"synced_wait_orders": len(synced_orders), "baseline_captured": True}

    result = await _run_reconcile_pipeline(strategy, own_fills=synced_orders, client=client)
    return {"synced_wait_orders": len(synced_orders), "baseline_captured": False, **result}


def is_recent(created_at: str, max_age_sec: float) -> bool:
    """공개 헬퍼 — daemon.py의 _run_risk_exit_loop(⑤-4c 재검토 Important 2)도 동일한
    '영구 wait 행이 가드를 무기한 막는' 문제를 겪어 이 함수를 그대로 재사용한다."""
    created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() <= max_age_sec


def _predates_strategy_start(detail: dict, status: str, strategy: dict) -> bool:
    """이미 종결된(done/cancel) 주문이 이 전략이 시작되기 전에 거래소에서 발생했다면
    "외부 개입"이 아니라 그냥 이 마켓의 오래된 주문 이력이다 — 실제 사고 사례: DB 리셋
    이후로 로컬 orders 테이블 기억에서 빠진 몇 주 전 실전 테스트 주문이, 같은 마켓에
    새 전략을 만들 때마다 매번 "방금 발견한 외부주문"으로 오인돼 전략을 정지시켰다.
    baseline_qty가 hydrate_state 시점의 실제 잔고를 그대로 캡처하므로, 그 이전에 이미
    종결된 주문의 잔고 효과는 baseline에 이미 반영돼 있어 무시해도 안전하다. 아직
    열려있는(wait) 주문은 나이와 무관하게 계속 추적 대상으로 남긴다 — 이후 체결/취소되며
    baseline 캡처 이후의 실제 잔고를 바꿀 수 있기 때문이다. started_at/created_at 중
    하나라도 없거나 파싱할 수 없으면(테스트 더블, 예상 밖 응답 형식) 안전한 쪽(기존
    동작대로 계속 추적)으로 기울여 False를 반환한다."""
    if status == "wait":
        return False
    started_at = strategy.get("started_at")
    created_at = detail.get("created_at")
    if not started_at or not created_at:
        return False
    try:
        order_time = datetime.fromisoformat(created_at).astimezone(timezone.utc)
        strategy_start = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return order_time < strategy_start


async def _detect_external_orders(
    strategy: dict, *, client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """그 마켓의 미체결+최근 종료 주문을 조회해 내부 DB에 없는 uuid만 골라 기록한다
    (설계 스펙 결정7 준비 — 여기서 찾은 주문들을 _reconcile_position이 재사용).

    우리가 방금 낸 주문이 아직 upbit_uuid를 기록하기 전(order_executor가 체결/타임아웃
    확인을 기다리는 짧은 창) 상태로 남아있으면, 그 주문이 거래소 목록에는 "새 주문"으로
    보여 외부주문으로 오인될 수 있다(최종 브랜치 리뷰 Critical 3 — 오탐 정지 + upbit_uuid
    UNIQUE 제약 위반으로 order_executor가 크래시). 그런 미확정 주문이 "최근"(전략의
    order_timeout_sec + 여유분 이내) 것이면 이번 사이클은 감지를 건너뛴다 — 다음
    사이클엔 그 주문의 uuid가 기록돼 있어 정상적으로 무시된다. 오래된 미확정 행(주문
    실패 후 정리되지 않았거나, limit_timeout 잔량 전환 주문처럼 의도적으로 'wait'인 채
    남는 행)까지 무기한 걸리게 두면 그 전략의 외부주문 감지가 영구히 멈춘다(최종
    브랜치 리뷰 재검토 Important 1) — 나이 제한으로 그 문제를 막는다."""
    risk_config = json.loads(strategy["risk_config_json"])
    in_flight_window_sec = float(risk_config.get("order_timeout_sec", 10)) + 60
    if any(
        o["upbit_uuid"] is None and is_recent(o["created_at"], in_flight_window_sec)
        for o in db.list_wait_orders(strategy["id"])
    ):
        return []

    market = strategy["market"]
    open_orders = await upbit_client.list_open_orders(market=market, client=client)
    closed_orders = await upbit_client.list_closed_orders(
        market=market, states=["done", "cancel"], client=client,
    )

    policy = risk_config.get("manual_intervention_policy", "all_stop")
    # 정책 값이 없거나 오타여도 안전한 쪽(정지)으로 기울인다 — "acknowledge_and_continue"를
    # 명시적으로 고른 경우에만 계속 진행(최종 브랜치 리뷰 Important 4, 기존엔 반대였음).
    should_pause = policy != "acknowledge_and_continue"

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
        if _predates_strategy_start(detail, status, strategy):
            continue

        order_id = db.insert_external_order(
            strategy["id"], None, market, detail["side"], detail["ord_type"], upbit_uuid,
            filled_price, executed_volume if executed_volume > 0 else None,
            float(detail["paid_fee"]), status,
        )
        found.append(db.get_order_by_id(order_id))

    # 사이클당 외부주문 N건은 한 사건이다 — 주문마다 기록/상태갱신을 반복하면 같은 사건이
    # DB에 N번 중복 쓰기된다(코드 리뷰 지적). 발견분 전체를 한 번에 요약해 한 번만 쓴다.
    if found:
        action_taken = "all_stop" if should_pause else "acknowledged_and_continued"
        uuid_list = ", ".join(o["upbit_uuid"] for o in found)
        db.insert_manual_intervention_event(
            market,
            f"내부에 없는 외부주문 {len(found)}건 발견: uuid=[{uuid_list}]",
            action_taken,
        )
        if should_pause:
            # manual_pause=1 — 사람이 원인을 확인하기 전엔 signal_engine.py의 B그룹
            # 자동재개 가드가 이 정지를 되돌리면 안 된다(코드 리뷰 Critical 발견).
            db.update_live_strategy_status(strategy["id"], "paused", manual_pause=1)

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
    buy_price: float, sell_price: float, sell_fee: float,
) -> str | None:
    """설계 스펙 결정4/7 — 매칭된 외부주문의 실제 체결가로 정밀하게 self-heal한다. 호출자
    (_reconcile_position)가 이미 "매수+매도 동시 매칭"과 "포지션 보유 중 top-up" 두
    케이스를 걸러내므로(최종 브랜치 리뷰 Important 1·3, 사용자 확정 — 둘 다 설명 안 됨
    으로 처리), 여기 도달하는 건 순수 신규진입/전량청산/부분청산(순매도)뿐이다. None을
    반환하면 "정밀하게 open으로 설명할 수 없다"는 뜻이며, 호출자는 unexplained 처리로
    넘어가야 한다(baseline_qty로 인해 actual_qty가 0 이하가 되는 경우, 음수/영 수량
    포지션을 여는 것을 방지)."""
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

    # 부분청산(순매도)만 여기 도달한다(top-up은 호출자가 이미 걸러냄) — 원가는 그대로,
    # 수량만 축소
    db.adjust_position_qty(position["id"], actual_qty)
    return "adjusted"


def _self_heal_unexplained(strategy: dict, position: dict | None, actual_qty: float, avg_buy_price: float) -> None:
    """설계 스펙 결정5 — 가격 근거가 없으므로 PnL/current_capital은 건드리지 않고
    수량만 실제 잔고에 맞춘다. 신규 포지션은 업비트가 자체 관리하는 avg_buy_price를
    근사 원가로 쓴다(정확한 매도가는 알 수 없어도, 향후 정상 청산 시 PnL 계산의 기준점은
    있어야 한다). avg_buy_price가 0 이하(입금/에어드롭 등으로 업비트가 원가를 추적하지
    못한 코인)면 아예 열지 않는다 — entry_price=0으로 열면 나중에 정상 청산 시
    ZeroDivisionError로 죽는다(최종 브랜치 리뷰 Important 6). 이 경우 코인은 추적되지
    않은 채로 남지만, 이미 "설명 안 됨"으로 정지된 상태라 사용자의 수동 확인이 필요한
    상황이라는 점은 동일하다."""
    if position is None:
        if actual_qty > _QTY_EPSILON and avg_buy_price > 0:
            position_manager.open_position(strategy["id"], strategy["market"], avg_buy_price, actual_qty)
        return

    if actual_qty <= _QTY_EPSILON:
        db.close_position_row(position["id"], None, position["entry_qty"], None, None, "manual_unexplained")
        return

    db.adjust_position_qty(position["id"], actual_qty)


async def _reconcile_position(
    strategy: dict, external_orders: list[dict], *,
    own_fills: list[dict] = (), client: httpx.AsyncClient | None = None,
) -> dict:
    """own_fills는 이번 사이클에 동기화된 "우리가 낸" 주문(hydrate_state의
    sync_pending_limit_orders 결과)이다 — 잔고 변화를 설명하는 데는 external_orders와
    똑같이 쓰이지만, 이것만으로 설명되는 변화는 수동개입이 아니므로 정책 적용/이벤트
    기록 대상에서 제외한다(최종 브랜치 리뷰 Critical 1)."""
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

    matched_orders = list(external_orders) + list(own_fills)
    done_buys = [o for o in matched_orders if o["side"] == "bid" and o["filled_volume"]]
    done_sells = [o for o in matched_orders if o["side"] == "ask" and o["filled_volume"]]
    buy_volume, buy_price, _buy_fee = _weighted_fill(done_buys)
    sell_volume, sell_price, sell_fee = _weighted_fill(done_sells)
    explained_diff = buy_volume - sell_volume

    # 정밀 self-heal(_apply_explained_change) 대상은 순수 신규진입/전량청산/순매도뿐이다.
    # 매수+매도가 한 틱에 섞여 매칭되거나(Important 1), 포지션 보유 중 순매수로 top-up
    # 되는 경우(Important 3)는 사용자 확정으로 둘 다 "설명 안 됨" 경로로 보낸다 —
    # 전자는 방향이 섞인 자동 매칭의 오탐 위험, 후자는 사용자 개인자금이 전략 자금에
    # 조용히 섞여 들어가는 걸 막기 위함.
    is_mixed_side = buy_volume > 0 and sell_volume > 0
    is_topup = position is not None and actual_qty > position["entry_qty"] + _QTY_EPSILON

    action = None
    if (
        not is_mixed_side and not is_topup
        and (buy_volume > 0 or sell_volume > 0)
        and abs(diff - explained_diff) <= _QTY_EPSILON
    ):
        action = _apply_explained_change(strategy, position, actual_qty, buy_price, sell_price, sell_fee)

    if action is not None:
        # own_fills만으로 전부 설명됐다면(external_orders가 이번에 아무것도 못 찾았다면)
        # 수동개입이 아니라 우리 자신의 정상 체결이 뒤늦게 반영된 것뿐이다 — 정책과
        # 무관하게 정지시키지 않는다.
        is_manual = bool(external_orders)
        paused = is_manual and policy != "acknowledge_and_continue"
        if paused:
            # manual_pause=1 — 사람이 원인을 확인하기 전엔 자동재개 가드가 이 정지를
            # 되돌리면 안 된다(코드 리뷰 Critical 발견, signal_engine.py 참고).
            db.update_live_strategy_status(strategy["id"], "paused", manual_pause=1)
        return {"balance_mismatch": True, "action": action, "paused": paused}

    # own_fills만으로 diff가 전부 설명되면(외부주문 없이) mixed_side/topup 가드 때문에
    # 정밀 self-heal 경로를 못 탔을 뿐, 이건 우리 자신이 추적 중인 주문이 뒤늦게 두 개
    # 이상 겹쳐 반영된 것뿐이다 — 결정5("설명 안 되는 잔고 불일치")의 대상인 진짜 미확인
    # 변화(입출금 등, 우리 주문으로 전혀 설명 안 되는 경우)와는 다르다(코드 리뷰 지적).
    # 수량 self-heal은 그대로 하되, 사람 개입이 필요한 정지/기록은 걸지 않는다.
    explained_by_own_fills_only = not external_orders and abs(diff - explained_diff) <= _QTY_EPSILON

    _self_heal_unexplained(strategy, position, actual_qty, avg_buy_price)
    if explained_by_own_fills_only:
        return {"balance_mismatch": True, "action": "unexplained", "paused": False}

    db.insert_manual_intervention_event(
        market,
        f"설명 안 되는 잔고 변화: 기대수량={internal_qty}, 실제수량={actual_qty}",
        "all_stop",
    )
    db.update_live_strategy_status(strategy["id"], "paused", manual_pause=1)
    return {"balance_mismatch": True, "action": "unexplained", "paused": True}


async def _run_reconcile_pipeline(
    strategy: dict, *, own_fills: list[dict] = (), client: httpx.AsyncClient | None = None,
) -> dict:
    """_detect_external_orders() → _reconcile_position() 순서로 실행한다. 업비트 API 실패는
    여기서 흡수하고(설계 스펙 결정8) 매매를 막지 않는다 — reconciler는 감시자이지
    트레이더가 아니다. 매 호출마다 최신 strategy 행을 다시 읽는다 — 호출자가 이전에 읽은
    stale한 dict(예: 방금 hydrate_state가 baseline_qty를 막 저장한 그 dict)를 그대로
    재사용하면 baseline 격리가 무력화된다(최종 브랜치 리뷰 Critical 2)."""
    try:
        strategy = db.get_live_strategy(strategy["id"]) or strategy
        external_orders = await _detect_external_orders(strategy, client=client)
        return await _reconcile_position(strategy, external_orders, own_fills=own_fills, client=client)
    except (httpx.HTTPError, upbit_client.UpbitRateLimitError) as exc:
        return {"error": str(exc)}


async def check_manual_intervention(
    strategy: dict, *, own_fills: list[dict] = (), client: httpx.AsyncClient | None = None,
) -> dict:
    """러닝 중 데몬이 주기적으로(15~30초, 스케줄링은 daemon.py 몫) 호출한다. own_fills는
    daemon이 같은 사이클에 sync_pending_limit_orders()로 먼저 동기화한 우리 자신의 체결
    결과를 넘길 때 쓴다(자체 체결이 수동개입으로 오인되지 않게, 최종 브랜치 리뷰 재검토
    Critical 1과 동일한 원칙)."""
    return await _run_reconcile_pipeline(strategy, own_fills=own_fills, client=client)
