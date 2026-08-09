"""
trading/daemon.py

라이브 트레이딩 상주 프로세스 진입점(서브플랜⑤-4b). 승인된 전략(status IN
('running','paused'))을 전략별 asyncio 태스크로 동시에 처리한다 — 각 태스크는 봉타임에
비례한 주기로 signal_engine -> order_executor를 돌리고, 그 안에서 reconciler(수동개입
감지)와 서킷브레이커 판정까지 순차적으로 실행해 동시성 충돌을 원천 차단한다(설계 스펙
결정3). 실시간 손절/익절(ticker 기반, ⑤-4c)은 전략별 개별 ticker WS 연결
(_run_risk_exit_loop)로 처리하며, _run_strategy_loop와 전략별 asyncio.Lock을 공유해
주문실행이 겹치지 않게 한다. trading.db + trading.signal_engine +
trading.order_executor + trading.position_manager + trading.reconciler +
trading.risk_manager + trading.upbit_ws + upbit_data_service만 의존. engine/ 미의존.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import trading.db as db
import trading.order_executor as order_executor
import trading.position_manager as position_manager
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
import trading.upbit_ws as upbit_ws
import upbit_data_service

logger = logging.getLogger(__name__)

_TASK_REFRESH_INTERVAL_SEC = 20
_RECONCILE_INTERVAL_SEC = 20
_NTP_CHECK_INTERVAL_SEC = 600
_NTP_DRIFT_THRESHOLD_SEC = 0.5
_MIN_POLL_INTERVAL_SEC = 5.0
_MAX_POLL_INTERVAL_SEC = 60.0
# exit_for_risk()가 "slippage_exceeded"(FOK 취소, 대기 주문을 남기지 않음)를 반환하면
# list_wait_orders() 가드는 무력하다 — 다음 tick도 여전히 같은 breach라서 즉시 재시도돼
# 거래소에 tick 주기(초당 여러 번)로 스팸성 주문을 계속 낸다. 이 쿨다운으로 재시도
# 간격을 최소 이만큼 벌린다(최종 브랜치 리뷰 Critical 1).
_RISK_EXIT_RETRY_COOLDOWN_SEC = 30


def _poll_interval_sec(timeframe: str) -> float:
    """봉타임에 비례한 폴링 주기(설계 스펙 결정4). 1분봉=5초, 3분봉=15초, 15분봉
    이상은 전부 60초 상한."""
    duration_sec = upbit_data_service.timeframe_duration(timeframe).total_seconds()
    return max(_MIN_POLL_INTERVAL_SEC, min(_MAX_POLL_INTERVAL_SEC, duration_sec // 12))


async def _run_strategy_loop(strategy_id: str, lock: asyncio.Lock | None = None) -> None:
    """전략 하나를 담당하는 유일한 태스크(설계 스펙 결정3). hydrate_state() 1회 →
    무한루프(새 봉 처리 → 매도체결 시 서킷브레이커 판정 → 20초마다 reconciler 2종
    호출 → 봉타임 비례 sleep). status가 running/paused가 아니게 되면 스스로 종료한다.
    신호처리와 reconcile은 서로 독립된 try/except다 — 신호처리가 매 틱 죽어도 reconcile
    워치독은 계속 돌아야 하고, 그 반대도 마찬가지다(최종 브랜치 리뷰 Important 3).
    예외는 로그만 남기고 다음 틱에 재시도(결정8). lock은 order_executor.enter()/exit()가
    실제로 실행되는 구간(신호처리의 handle_signal_result 호출 + reconcile 블록 전체)을
    감싸 _run_risk_exit_loop(⑤-4c)의 ticker 트리거 청산과 겹치지 않게 한다(⑤-4c 설계
    스펙 결정4). lock이 None이면(기존 호출부와의 하위호환) 새 Lock을 만든다 — 아무도
    공유하지 않으므로 사실상 no-op."""
    if lock is None:
        lock = asyncio.Lock()
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
    try:
        await reconciler.hydrate_state(strategy)
    except Exception:
        # hydrate_state는 아래 while 루프의 try/except 밖에 있어 여기서 직접 흡수해야
        # 한다 — 그러지 않으면 일시적 네트워크 장애로 태스크 자체가 로그 한 줄 없이
        # 죽는다(최종 브랜치 리뷰 Important 1). 재시도 루프는 만들지 않는다 — 다음
        # 태스크셋 매니저 스캔 주기에 이 전략이 다시 태스크로 떠서 재시도된다.
        logger.exception("hydrate_state 실패로 전략 태스크 시작 중단: strategy_id=%s", strategy_id)
        return
    # -inf로 시작해 hydrate_state() 직후 첫 틱에서 반드시 한 번 재확인하게 만든다
    # (time.monotonic()으로 초기화하면 테스트에서 그 함수를 상수로 monkeypatch했을 때
    # now - last_reconcile이 항상 0이 되어 재확인 주기 경과를 절대 감지 못 한다).
    last_reconcile = float("-inf")

    while True:
        try:
            current = db.get_live_strategy(strategy_id)
        except Exception:
            # 조회 실패 시 이전 strategy로 이번 틱을 마저 진행한다 — 그러지 않으면
            # 일시적 DB 오류로 태스크 자체가 로그 한 줄 없이 죽는다(코드 리뷰 지적).
            logger.exception("전략 상태 재조회 중 예외 발생: strategy_id=%s", strategy_id)
            current = strategy
        if current is None or current["status"] not in ("running", "paused"):
            return
        strategy = current

        try:
            result = await asyncio.to_thread(signal_engine.evaluate_signals, strategy_id)
            if result["new_candle"]:
                async with lock:
                    action_result = await order_executor.handle_signal_result(strategy_id, result)
                    if action_result["sell_action"] == "exited":
                        risk_config = json.loads(strategy["risk_config_json"])
                        risk_manager.check_circuit_breaker(strategy_id, risk_config)
        except Exception:
            logger.exception("전략 신호처리 중 예외 발생: strategy_id=%s", strategy_id)

        now = time.monotonic()
        if now - last_reconcile >= _RECONCILE_INTERVAL_SEC:
            # 시도 직전에 갱신한다 — 실패해도 다음 틱마다(5~60초) 재시도하지 않고
            # 20초 상한을 지키게 하기 위함이다(M3).
            last_reconcile = now
            try:
                async with lock:
                    strategy = db.get_live_strategy(strategy_id) or strategy
                    synced = await reconciler.sync_pending_limit_orders(strategy)
                    await reconciler.check_manual_intervention(strategy, own_fills=synced)
            except Exception:
                logger.exception("전략 reconcile 중 예외 발생: strategy_id=%s", strategy_id)

        await asyncio.sleep(_poll_interval_sec(strategy["timeframe"]))


async def _run_risk_exit_loop(strategy_id: str, lock: asyncio.Lock | None = None) -> None:
    """전략 하나의 ticker 기반 실시간 손절/익절 전용 태스크(⑤-4c 설계 스펙). 시작 시
    sell_conditions_json에 STOP_LOSS_PCT/TAKE_PROFIT_PCT가 없으면 WS 연결 없이 즉시
    반환한다(결정7 — 위험조건 없는 전략까지 연결을 열 이유가 없음). 있으면 해당 마켓의
    ticker를 구독해(결정3) 매 tick마다 position_return_pct를 계산하고 독립 안전망으로
    평가(결정1). 위반이 감지돼도 곧바로 주문을 내지 않고 세 가드를 순서대로 통과해야만
    트리거한다: ① list_wait_orders()로 이미 진행 중인 청산 시도(리밋 주문이 아직
    열려있는 "pending")가 있으면 건너뛴다, ② _RISK_EXIT_RETRY_COOLDOWN_SEC 안에 이미
    시도했으면(대기 주문을 남기지 않는 "slippage_exceeded" 포함) 건너뛴다(둘 다 최종
    브랜치 리뷰 Critical 1 — 매 tick 재시도로 같은 breach에 실주문이 반복 발사되는 걸
    막는다), ③ 재조회한 fresh_strategy의 status가 "running"이 아니면(예: reconciler가
    잔고 불일치로 paused 전환) 건너뛴다 — paused는 데몬이 자신의 포지션 기록을 신뢰할 수
    없다고 판단한 상태라 그 위에서 실주문 청산을 내는 게 더 위험하다(Important 3, 사용자
    결정). 포지션 조회/임계치 판정/세 가드는 모두 lock 밖에서 수행한다 — 매 tick 락을
    잡으면 아무 것도 안 걸릴 때조차 _run_strategy_loop의 주문실행 구간과 불필요하게
    경합하고, _run_strategy_loop가 락을 오래 쥐고 있는 동안 blocking돼 있다가 몇 초 뒤
    stale한 trade_price로 판단할 수 있다(Important 2). lock은 "트리거하기로 결정한"
    이후에만 잡고(결정4), 그 안에서 포지션을 다시 한번 fresh하게 읽어(결정8 — 밖에서 읽은
    건 락을 기다리는 동안 stale해질 수 있음) exit_for_risk() 호출(결정5), 청산 성공
    시에만 check_circuit_breaker()까지 호출(결정7 재사용). tick 처리 예외는 로그만
    남기고 다음 tick에 계속(⑤-4b 결정8과 동일 원칙) — 이와 별개로 stream_ticker() 자체가
    async for 문에서 예외를 던지는 경우(예: 핸드셰이크 TimeoutError)는 그 안쪽
    try/except가 잡지 못해 태스크 전체가 로그 없이 죽으므로, async for 전체를 감싸는
    바깥 try/except를 하나 더 둔다(Important 5)."""
    if lock is None:
        lock = asyncio.Lock()
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
    sell_conditions = json.loads(strategy["sell_conditions_json"])
    if not signal_engine.has_risk_exit_conditions(sell_conditions):
        return

    market = strategy["market"]
    # -inf로 시작해 첫 breach에서는 반드시 트리거를 시도하게 만든다(last_reconcile과
    # 동일한 이유 — time.monotonic()으로 초기화하면 그 함수가 monkeypatch된 테스트에서
    # now - last_risk_exit_attempt가 항상 0이 돼 쿨다운 경과를 절대 감지 못 한다).
    last_risk_exit_attempt = float("-inf")
    try:
        async for tick in upbit_ws.stream_ticker([market]):
            try:
                # ticker 채널에서 다른 타입의 프레임이 오면 trade_price가 없어 KeyError가
                # 나고, 그게 매번 아래 except에 잡혀 전체 스택트레이스를 로그에 쏟는다.
                # 조용히 건너뛴다(트리비얼 지적사항).
                if tick.get("type") != "ticker" or tick.get("trade_price") is None:
                    continue
                trade_price = tick["trade_price"]
                position = position_manager.get_open_position(strategy_id)
                if position is None:
                    continue
                position_return_pct = (
                    (trade_price - position["entry_price"]) / position["entry_price"] * 100
                )
                matched = signal_engine.matched_risk_exit_indicator(sell_conditions, position_return_pct)
                if matched is None:
                    continue
                # 가드 ① — 이미 청산 시도가 진행 중(대기 주문 존재)이면 건너뛴다.
                if db.list_wait_orders(strategy_id):
                    continue
                # 가드 ② — 쿨다운 안이면 건너뛴다.
                now = time.monotonic()
                if now - last_risk_exit_attempt < _RISK_EXIT_RETRY_COOLDOWN_SEC:
                    continue
                fresh_strategy = db.get_live_strategy(strategy_id)
                if fresh_strategy is None:
                    continue
                # 가드 ③ — paused 등 running이 아니면 건너뛴다(Important 3).
                if fresh_strategy["status"] != "running":
                    continue
                # 시도 직전에 갱신한다 — exit_for_risk()가 예외를 던지거나
                # slippage_exceeded로 끝나도 다음 tick에 즉시 재시도하지 않고 쿨다운
                # 상한을 지키게 하기 위함이다(_run_strategy_loop의 last_reconcile과
                # 동일 패턴, M3 참고).
                last_risk_exit_attempt = now
                async with lock:
                    fresh_position = position_manager.get_open_position(strategy_id)
                    if fresh_position is None:
                        continue
                    result = await order_executor.exit_for_risk(
                        fresh_strategy, fresh_position, trade_price, matched.lower(),
                    )
                    if result["action"] == "exited":
                        risk_config = json.loads(fresh_strategy["risk_config_json"])
                        risk_manager.check_circuit_breaker(strategy_id, risk_config)
            except Exception:
                logger.exception("실시간 손절/익절 처리 중 예외 발생: strategy_id=%s", strategy_id)
    except Exception:
        # async for 문 자체(즉 stream_ticker()가 tick을 만들어내는 도중)에서 예외가 나면
        # 위 안쪽 try/except는 관여하지 않는다 — 여기서 흡수하지 않으면 태스크가 로그
        # 한 줄 없이 죽고, 다음 _task_set_manager_loop 스캔 주기(20초)에야 재생성된다
        # (Important 5).
        logger.exception("실시간 손절/익절 ticker 스트림 자체에서 예외 발생: strategy_id=%s", strategy_id)


async def _task_set_manager_loop() -> None:
    """20초마다 db.list_active_strategies()를 다시 조회해 태스크 집합을 갱신한다
    (설계 스펙 결정2). 새 전략 -> create_task(_run_strategy_loop) +
    create_task(_run_risk_exit_loop)(⑤-4c), 더 이상 대상 아님 -> 두 태스크 다
    task.cancel(). 전략당 asyncio.Lock을 하나 만들어 두 태스크에 동일 객체로 넘겨
    주문실행을 직렬화한다(⑤-4c 설계 스펙 결정4). 재시작 없이 새로 승인된 전략을
    자동으로 픽업한다."""
    tasks: dict[str, asyncio.Task] = {}
    risk_tasks: dict[str, asyncio.Task] = {}
    locks: dict[str, asyncio.Lock] = {}
    while True:
        try:
            active_ids = {s["id"] for s in db.list_active_strategies()}

            for strategy_id in active_ids:
                if strategy_id not in locks:
                    locks[strategy_id] = asyncio.Lock()
                if strategy_id not in tasks or tasks[strategy_id].done():
                    tasks[strategy_id] = asyncio.create_task(
                        _run_strategy_loop(strategy_id, locks[strategy_id])
                    )
                if strategy_id not in risk_tasks or risk_tasks[strategy_id].done():
                    risk_tasks[strategy_id] = asyncio.create_task(
                        _run_risk_exit_loop(strategy_id, locks[strategy_id])
                    )

            for strategy_id in list(tasks):
                if strategy_id not in active_ids:
                    tasks[strategy_id].cancel()
                    del tasks[strategy_id]
            for strategy_id in list(risk_tasks):
                if strategy_id not in active_ids:
                    risk_tasks[strategy_id].cancel()
                    del risk_tasks[strategy_id]
            for strategy_id in list(locks):
                if strategy_id not in active_ids:
                    del locks[strategy_id]
        except Exception:
            # 이 루프가 죽으면 새 전략을 영영 못 집는다(설계 스펙 '에러 처리' 절) —
            # 로그만 남기고 다음 스캔 주기에 재시도한다(코드 리뷰 지적).
            logger.exception("태스크셋 스캔 중 예외 발생")

        await asyncio.sleep(_TASK_REFRESH_INTERVAL_SEC)


async def _run_ntp_check_loop() -> None:
    """시작 직후 1회 + 10분마다 로컬 시각과 업비트 서버 시각의 오차를 확인한다
    (설계 스펙 결정10). 임계치(500ms) 초과 시 로그만 남긴다 — 자동조치는 2단계
    텔레그램 이후."""
    while True:
        try:
            offset = await asyncio.to_thread(upbit_data_service.get_server_time_offset_sec)
            if abs(offset) > _NTP_DRIFT_THRESHOLD_SEC:
                logger.warning("로컬 시각이 업비트 서버와 %.3f초 어긋남", offset)
        except Exception:
            logger.exception("NTP 드리프트 체크 실패")
        await asyncio.sleep(_NTP_CHECK_INTERVAL_SEC)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await asyncio.gather(_task_set_manager_loop(), _run_ntp_check_loop())


if __name__ == "__main__":
    asyncio.run(main())
