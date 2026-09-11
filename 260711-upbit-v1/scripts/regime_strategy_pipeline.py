"""
scripts/regime_strategy_pipeline.py

코인 하나를 지정하면 하락/횡보/상승 장세 각각에 대해 최근 구간을 찾아 grid
search로 전략을 발굴하고, 손절/익절 OR조건을 부착한 뒤 regime_strategy_library에
매핑까지 자동으로 수행한다. 라이브 전략 생성/자동스왑 토글은 범위 밖 — 사용자가
/strategy-library에서 최종 확인 후 수동으로 진행한다. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-regime-strategy-auto-pipeline-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
     --market KRW-ETH --history-start 2026-01-01
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

TIMEFRAME = "minutes60"


def min_trades_for_days(period_days: float) -> int:
    """기간이 길수록 요구 거래횟수도 늘어난다(5일당 1회, 최소 3회)."""
    return max(3, math.ceil(period_days / 5))


def adjust_window(seg: dict, min_days: int, history_start: datetime) -> tuple[datetime, datetime]:
    """세그먼트 길이가 min_days 미만이면 start를 당겨 채운다. end는 항상 세그먼트의
    end 그대로 두고(사용자가 지정한 장세가 "끝난 시점"은 그대로 유지), history_start
    보다 앞으로는 당기지 않는다(요청한 데이터 범위 밖으로 나가지 않기 위함)."""
    end = datetime.fromisoformat(seg["end"])
    start = datetime.fromisoformat(seg["start"])
    min_start = end - timedelta(days=min_days)
    if start > min_start:
        start = max(min_start, history_start)
    return start, end
