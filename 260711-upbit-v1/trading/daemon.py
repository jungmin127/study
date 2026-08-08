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
