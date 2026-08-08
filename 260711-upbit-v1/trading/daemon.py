"""
trading/daemon.py

라이브 트레이딩 상주 프로세스 진입점(서브플랜⑤-4b). 승인된 전략(status IN
('running','paused'))을 전략별 asyncio 태스크로 동시에 처리한다 — 각 태스크는 봉타임에
비례한 주기로 signal_engine -> order_executor를 돌리고, 그 안에서 reconciler(수동개입
감지)와 서킷브레이커 판정까지 순차적으로 실행해 동시성 충돌을 원천 차단한다(설계 스펙
결정3). 실시간 손절/익절(ticker 기반)은 ⑤-4c 몫이라 여기 없다. trading.db +
trading.signal_engine + trading.order_executor + trading.reconciler +
trading.risk_manager + upbit_data_service만 의존. engine/ 미의존.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import trading.db as db
import trading.order_executor as order_executor
import trading.reconciler as reconciler
import trading.risk_manager as risk_manager
import trading.signal_engine as signal_engine
import upbit_data_service

logger = logging.getLogger(__name__)

_TASK_REFRESH_INTERVAL_SEC = 20
_RECONCILE_INTERVAL_SEC = 20
_NTP_CHECK_INTERVAL_SEC = 600
_NTP_DRIFT_THRESHOLD_SEC = 0.5
_MIN_POLL_INTERVAL_SEC = 5.0
_MAX_POLL_INTERVAL_SEC = 60.0


def _poll_interval_sec(timeframe: str) -> float:
    """봉타임에 비례한 폴링 주기(설계 스펙 결정4). 1분봉=5초, 3분봉=15초, 15분봉
    이상은 전부 60초 상한."""
    duration_sec = upbit_data_service.timeframe_duration(timeframe).total_seconds()
    return max(_MIN_POLL_INTERVAL_SEC, min(_MAX_POLL_INTERVAL_SEC, duration_sec // 12))


async def _run_strategy_loop(strategy_id: str) -> None:
    """전략 하나를 담당하는 유일한 태스크(설계 스펙 결정3). hydrate_state() 1회 →
    무한루프(새 봉 처리 → 매도체결 시 서킷브레이커 판정 → 20초마다 reconciler 2종
    호출 → 봉타임 비례 sleep). status가 running/paused가 아니게 되면 스스로 종료한다.
    신호처리와 reconcile은 서로 독립된 try/except다 — 신호처리가 매 틱 죽어도 reconcile
    워치독은 계속 돌아야 하고, 그 반대도 마찬가지다(최종 브랜치 리뷰 Important 3).
    예외는 로그만 남기고 다음 틱에 재시도(결정8)."""
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
        strategy = db.get_live_strategy(strategy_id)
        if strategy is None or strategy["status"] not in ("running", "paused"):
            return

        try:
            result = await asyncio.to_thread(signal_engine.evaluate_signals, strategy_id)
            if result["new_candle"]:
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
                strategy = db.get_live_strategy(strategy_id) or strategy
                synced = await reconciler.sync_pending_limit_orders(strategy)
                await reconciler.check_manual_intervention(strategy, own_fills=synced)
            except Exception:
                logger.exception("전략 reconcile 중 예외 발생: strategy_id=%s", strategy_id)

        await asyncio.sleep(_poll_interval_sec(strategy["timeframe"]))


async def _task_set_manager_loop() -> None:
    """20초마다 db.list_active_strategies()를 다시 조회해 태스크 집합을 갱신한다
    (설계 스펙 결정2). 새 전략 -> create_task(_run_strategy_loop), 더 이상 대상
    아님 -> task.cancel(). 재시작 없이 새로 승인된 전략을 자동으로 픽업한다."""
    tasks: dict[str, asyncio.Task] = {}
    while True:
        active_ids = {s["id"] for s in db.list_active_strategies()}

        for strategy_id in active_ids:
            if strategy_id not in tasks or tasks[strategy_id].done():
                tasks[strategy_id] = asyncio.create_task(_run_strategy_loop(strategy_id))

        for strategy_id in list(tasks):
            if strategy_id not in active_ids:
                tasks[strategy_id].cancel()
                del tasks[strategy_id]

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
