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
# exit_for_risk()가 "slippage_exceeded"(FOK 취소, 대기 주문을 남기지 않음)를 반환하거나
# 그냥 "exited"로 성공해도, 다음 tick에도 가격이 같은 임계치 근방이면 즉시 재시도돼
# 거래소에 tick 주기(초당 여러 번)로 취소+재주문 스팸을 계속 낸다(5라운드부터 트리거
# 자체를 막는 가드가 없어 이 쿨다운이 그 재시도 빈도를 제한하는 유일한 장치다). 이
# 쿨다운으로 재시도 간격을 최소 이만큼 벌린다(최종 브랜치 리뷰 Critical 1).
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
    평가(결정1). 포지션 조회(비신선)/임계치 판정/쿨다운 사전필터는 lock 밖에서 수행한다
    — 매 tick 락을 잡으면 아무 것도 안 걸릴 때조차 _run_strategy_loop의 주문실행 구간과
    불필요하게 경합한다(Important 2). 하지만 "트리거하기로 결정"한 뒤에는(결정4) lock을
    잡은 안쪽에서 그 결정이 의존하는 상태(전략 status/포지션)를 전부 다시 fresh하게
    읽는다 — 락을 기다리는 동안 _run_strategy_loop가 이 전략을 paused로 돌릴 수 있어
    (reconciler), 밖에서 읽은 스냅샷은 락을 얻은 시점엔 이미 stale할 수 있다(재검토
    Important 1 — pause 우회가 실제로 재현됨. 예전엔 status 재조회를 락 밖에서 했었다).
    lock 안에서 통과해야만 exit_for_risk()를 호출하는 가드는 이제 하나뿐이다 —
    fresh_strategy의 status가 "running"이 아니면 건너뛴다. paused는 데몬이 자신의
    포지션 기록을 신뢰할 수 없다고 판단한 상태라 그 위에서 실주문 청산을 내는 게 더
    위험하다(Important 3, 사용자 결정).

    5라운드 구조적 수정 — 이전엔(1~4라운드) 여기 두 번째 가드로 list_wait_orders() 중
    side=="ask"이고 order_timeout_sec+60초 이내로 최근에 생성된 행이 있으면 트리거
    자체를 건너뛰었다(reconciler._detect_external_orders와 동일한 is_recent + 나이창
    패턴 재사용). 그런데 그 나이창이 지나도 candle 트리거가 남긴 진짜 미체결
    order_execution_mode='limit' 매도가 거래소에 여전히 열려있을 수 있어, 그 상태에서
    exit_for_risk()가 강제로 market 매도를 내면 두 주문이 같은 코인 잔고를 두고
    충돌한다 — 지금까지는 "Upbit가 잔고 부족으로 뒤의 주문을 거부할 것"이라는 검증되지
    않은 가정에 기대 왔다(사용자가 지적한 잔여 위험, 4라운드 문서가 "2차 방어선"이라
    불렀던 부분). 이 라운드부터 exit_for_risk() 자신이 exit() 호출 전에 남아있는 ask
    wait 행을 upbit_client.cancel_order()로 먼저 정리해 그 충돌 가능성을 구조적으로
    없앤다(order_executor.py의 exit_for_risk() docstring 5라운드 문단 참고) — 그래서
    이 루프 쪽의 나이 필터 가드는 더 이상 정합성을 위해 필요하지 않다. 트리거하기로
    결정했으면 그냥 exit_for_risk()를 부르고, 충돌 정리는 그 함수에 맡긴다 — 최악의
    경우도 "취소 + 재주문 한 사이클"일 뿐 중복/충돌 주문이 아니다. 이 가드를 지우고도
    쿨다운(_RISK_EXIT_RETRY_COOLDOWN_SEC)은 그대로 남긴다 — 이건 정합성 가드가 아니라
    처리량 제어다. 손절/익절 임계치 바로 위에서 가격이 tick마다(초당 여러 번) 오르내리면
    쿨다운 없이는 매 tick 취소+재주문이 나가 수수료/슬리피지가 무의미하게 반복
    소모된다(exit_for_risk()가 slippage_exceeded로 끝나 대기 주문을 남기지 않는 경우엔
    특히 이 반복이 막을 길이 없었다 — 최종 브랜치 리뷰 Critical 1) — 순수 로컬 태스크
    상태(공유되지 않음)라 lock 밖에서 사전필터로 먼저 걸러도 stale-state 문제가 없다.
    exit_for_risk()가 실제로
    성공(action=="exited")하면 쿨다운을 다시 -inf로 리셋한다 — 그러지 않으면 포지션을
    완전히 닫은 뒤 곧바로 재진입한 새 포지션의 정당한 손절/익절까지 남은 쿨다운 창
    안에서 억눌린다(번들 수정). "트리거하기로 결정"한 시점의 position_return_pct/
    matched_risk_exit_indicator는 lock 획득 전(밖) 스냅샷 기준이다 — lock 안에서
    fresh_position을 다시 읽은 뒤에는 그 fresh 값으로 breach 여부를 다시 계산해서
    exit_for_risk()를 부른다(3라운드 M3) — 현재 프로덕션 경로 중 lock 보유 중에
    포지션의 원가(entry_price)가 바뀌는 경로는 없어 당장 재현 가능한 버그는 아니지만,
    이 함수가 이미 두 라운드 연속으로 "lock 밖 스냅샷으로 실주문을 낸다"류 버그를
    겪었으므로 같은 유형을 구조적으로 막아둔다. last_risk_exit_attempt에 기록하는
    now는 lock을 얻은 직후 다시 읽는다(3라운드 M2) — lock 대기가 길어지면 밖에서 읽은
    now가 실제 재시도 시각보다 과거라 쿨다운이 그만큼 짧아지는 걸 막기 위함이다(쿨다운
    사전필터 자체는 여전히 lock 밖의 now로 판단 — 락을 매 tick 다툴 필요가 없다는 원래
    의도는 그대로 유지). 청산 성공 시에만 check_circuit_breaker()까지 호출(결정7
    재사용). tick 처리 예외는 로그만 남기고 다음 tick에 계속(⑤-4b 결정8과 동일 원칙)
    — 이와 별개로 stream_ticker() 자체가 async for 문에서 예외를 던지는 경우(예:
    핸드셰이크 TimeoutError)는 그 안쪽 try/except가 잡지 못해 태스크 전체가 로그 없이
    죽으므로, async for 전체를 감싸는 바깥 try/except를 하나 더 둔다(Important 5)."""
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
                # 쿨다운 사전필터 — 로컬 태스크 변수만 보므로 lock 밖에서 걸러도 안전하다
                # (다른 태스크가 이 값을 바꿀 수 없음). 락을 매 tick 불필요하게 다투지
                # 않기 위한 저비용 최적화.
                now = time.monotonic()
                if now - last_risk_exit_attempt < _RISK_EXIT_RETRY_COOLDOWN_SEC:
                    continue
                async with lock:
                    # "트리거하기로 결정"한 뒤부터 exit_for_risk() 호출 직전까지, 가드가
                    # 의존하는 모든 상태를 lock 안에서 fresh하게 다시 읽는다(재검토
                    # Important 1) — 밖에서 읽은 값은 lock을 기다리는 동안 stale해질 수
                    # 있다.
                    fresh_strategy = db.get_live_strategy(strategy_id)
                    if fresh_strategy is None:
                        continue
                    # 가드 ① — paused 등 running이 아니면 건너뛴다(Important 3).
                    if fresh_strategy["status"] != "running":
                        continue
                    risk_config = json.loads(fresh_strategy["risk_config_json"])
                    # 5라운드 — 진행 중인 청산 주문(side=='ask' wait 행) 유무로 트리거
                    # 자체를 건너뛰던 옛 가드 ②(list_wait_orders + is_recent 나이 필터)를
                    # 제거했다. exit_for_risk()가 이제 그 우려를 스스로 해소한다 —
                    # exit() 호출 전에 자기 손으로 남아있는 ask wait 행을 거래소에서
                    # cancel_order()로 먼저 지운다(order_executor.py docstring 5라운드
                    # 문단 참고). 즉 "먼저 확인하고 막을지 말지 판단"이 아니라 "일단
                    # 트리거하고, 부르는 쪽(exit_for_risk)이 충돌을 능동적으로 치운다"로
                    # 책임이 옮겨갔다 — 이 루프가 다시 list_wait_orders를 조회해 막는 건
                    # 이제 이중 방어가 아니라 그냥 불필요한 조회다(가장 나쁜 경우도 취소
                    # +재주문 한 사이클일 뿐 중복/충돌 주문이 아니다). 쿨다운은 그대로
                    # 남긴다 — 손절/익절 임계치 바로 위에서 가격이 tick마다 오르내리면
                    # 매 tick 취소+재주문이 나가 수수료/슬리피지가 반복 소모되므로, 그
                    # 무의미한 재시도 spam을 막는 역할은 여전히 필요하다.
                    fresh_position = position_manager.get_open_position(strategy_id)
                    if fresh_position is None:
                        continue
                    # 3라운드 M3 — lock 밖에서 계산한 matched(사전 스냅샷)를 그대로 쓰지
                    # 않고, 방금 다시 읽은 fresh_position 기준으로 breach를 재계산한다.
                    # 더 이상 breach가 아니면(원가가 바뀌어 손절/익절 조건을 벗어났다면)
                    # 실주문을 내지 않고 건너뛴다.
                    fresh_position_return_pct = (
                        (trade_price - fresh_position["entry_price"]) / fresh_position["entry_price"] * 100
                    )
                    fresh_matched = signal_engine.matched_risk_exit_indicator(
                        sell_conditions, fresh_position_return_pct,
                    )
                    if fresh_matched is None:
                        continue
                    # 3라운드 M2 — lock을 얻은 직후 다시 읽는다. lock 대기가 길면 밖에서
                    # 읽은 now가 실제 재시도 시각보다 과거라 쿨다운이 그만큼 짧아진다.
                    # (쿨다운 사전필터 자체는 여전히 락 밖의 now로 판단 — 락을 매 tick
                    # 다투지 않는다는 원래 의도는 그대로 유지.)
                    now = time.monotonic()
                    # 시도 직전에 갱신한다 — exit_for_risk()가 예외를 던지거나
                    # slippage_exceeded로 끝나도 다음 tick에 즉시 재시도하지 않고 쿨다운
                    # 상한을 지키게 하기 위함이다(_run_strategy_loop의 last_reconcile과
                    # 동일 패턴). 가드 ①에 막혀 실제 시도조차 못 한 tick은 쿨다운을
                    # 소모하지 않는다 — 상태가 정상으로 돌아오자마자 곧바로 재시도할 수
                    # 있어야 한다.
                    last_risk_exit_attempt = now
                    result = await order_executor.exit_for_risk(
                        fresh_strategy, fresh_position, trade_price, fresh_matched.lower(),
                    )
                    if result["action"] == "exited":
                        # 포지션을 성공적으로 닫았으면 쿨다운을 리셋한다 — 그러지 않으면
                        # 새 포지션이 쿨다운 창 안에 재진입해도 정당한 손절/익절이 남은
                        # 시간만큼 억눌린다(번들 수정).
                        last_risk_exit_attempt = float("-inf")
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
