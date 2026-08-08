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
    예외는 로그만 남기고 다음 틱에 재시도(결정8)."""
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        return
    await reconciler.hydrate_state(strategy)
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

            now = time.monotonic()
            if now - last_reconcile >= _RECONCILE_INTERVAL_SEC:
                strategy = db.get_live_strategy(strategy_id) or strategy
                await reconciler.check_manual_intervention(strategy)
                await reconciler.sync_pending_limit_orders(strategy)
                last_reconcile = now
        except Exception:
            logger.exception("전략 처리 중 예외 발생: strategy_id=%s", strategy_id)

        await asyncio.sleep(_poll_interval_sec(strategy["timeframe"]))
