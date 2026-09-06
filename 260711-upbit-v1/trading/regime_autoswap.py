"""
trading/regime_autoswap.py

daemon 장세 자동 스왑 루프(4단계)의 판정+실행 로직. daemon.py는 10분마다
process_autoswap_tick()만 호출하는 얇은 래퍼다. backend/main.py의 수동
"전략 교체" 엔드포인트도 determine_target_regime()을 재사용해 auto_swap_enabled인
전략의 active_regime을 수동 교체 시점에 stamp한다 — 자동 스위치를 켜둔 채
수동 개입해도 automation이 다음 실제 장세변화 전까지 되돌리지 않게 하기 위함
(설계 스펙 docs/superpowers/specs_v2/2026-09-06-regime-daemon-autoswap-design.md
"수동 개입 연동" 절 참고).

engine.regime_adx(순수 pandas, backtrader 미사용)를 직접 import한다 —
daemon.py의 "engine/ 미의존" 원칙은 무거운 backtrader/lightgbm 의존성 회피가
취지이므로 이 가벼운 모듈은 예외로 둔다(설계 문서 결정, 사용자 승인).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import trading.db as db
from engine.regime_adx import classify_regime, compute_adx_di
from upbit_data_service import get_candles

logger = logging.getLogger(__name__)

REGIME_TIMEFRAME = "minutes60"
CONFIRM_BAR_COUNT = 3
FALLBACK_REGIME = "기본"
# ADX(14) 워밍업(약 28봉)에 여유를 둔 값 — backend/regime_adx_service.py의
# OVERVIEW_LOOKBACK_BARS와 동일한 근거(수렴 확보).
LOOKBACK_HOURS = 200


def determine_target_regime(market: str) -> str:
    """market의 1시간봉 기준 현재 확정 장세를 반환한다. 최근 CONFIRM_BAR_COUNT개
    봉의 라벨이 전부 같고 None이 아니면 그 라벨을, 아니면(끈백질 방지 조건
    미충족/데이터부족/미분류) FALLBACK_REGIME("기본")을 반환한다. 항상 4개
    라벨 중 하나를 반환한다(None을 반환하지 않음 — 호출부가 라이브러리의
    "기본" 매핑으로 항상 폴백할 수 있게 하기 위함)."""
    now = datetime.now(timezone.utc)
    df = get_candles(market, REGIME_TIMEFRAME, now - timedelta(hours=LOOKBACK_HOURS), now)
    if len(df) < CONFIRM_BAR_COUNT:
        return FALLBACK_REGIME

    adx_di = compute_adx_di(df)
    recent_labels = [
        classify_regime(row.adx, row.plus_di, row.minus_di)
        for row in adx_di.tail(CONFIRM_BAR_COUNT).itertuples()
    ]
    first = recent_labels[0]
    if first is not None and all(label == first for label in recent_labels):
        return first
    return FALLBACK_REGIME


def process_autoswap_tick() -> None:
    """auto_swap_enabled=1인 모든 활성(running/paused) 라이브 전략을 순회하며
    장세변화를 감지하고 필요하면 교체한다. 전략 단위로 예외를 흡수해 한
    전략의 실패가 나머지 전략 처리를 막지 않게 한다(daemon.py의 기존
    '예외는 로그만 남기고 다음 틱 재시도' 원칙과 동일)."""
    for strategy in db.list_active_strategies():
        if not strategy["auto_swap_enabled"]:
            continue
        try:
            _process_one(strategy)
        except Exception:
            logger.exception("자동스왑 처리 중 예외 발생: strategy_id=%s", strategy["id"])


def _process_one(strategy: dict) -> None:
    strategy_id = strategy["id"]
    market = strategy["market"]
    active_regime = strategy["active_regime"]

    target_regime = determine_target_regime(market)
    if target_regime == active_regime:
        return  # 이미 동기화됨(자동으로 맞췄든 사용자가 수동으로 맞췄든 무관)

    mapping = next(
        (m for m in db.list_regime_strategy_mappings()
         if m["market"] == market and m["regime"] == target_regime),
        None,
    )
    if mapping is None:
        db.insert_regime_swap_log(
            strategy_id, market, "swap_skipped_no_mapping",
            active_regime, target_regime,
            detail=f"{market}/{target_regime} 슬롯이 라이브러리에 없음",
        )
        return

    replaced = db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=mapping["source_run_id"],
        timeframe=mapping["timeframe"],
        buy_conditions_json=mapping["buy_conditions_json"],
        sell_conditions_json=mapping["sell_conditions_json"],
    )
    if not replaced:
        db.insert_regime_swap_log(
            strategy_id, market, "swap_skipped_open_position",
            active_regime, target_regime,
            detail="오픈 포지션(또는 체결 대기중 매수 주문)이 있어 교체 보류 — 다음 틱 재시도",
        )
        return

    db.set_active_regime(strategy_id, target_regime)
    db.insert_regime_swap_log(
        strategy_id, market, "swap_success",
        active_regime, target_regime,
        detail=f"source_run_id={mapping['source_run_id']}",
    )
