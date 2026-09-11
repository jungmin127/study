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

from backend.regime_adx_service import compute_adx_regime_history
from scripts.grid_search import dedup_top_results

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


def select_target_segments(market: str, history_start: datetime) -> dict[str, dict | None]:
    """라벨(하락/횡보/상승)별 history_start 이후 시작하는 가장 최근 세그먼트를
    고른다. 해당 라벨의 세그먼트가 없으면 None."""
    history = compute_adx_regime_history(market, TIMEFRAME)
    by_label: dict[str, dict] = {}
    for seg in history["segments"]:
        if datetime.fromisoformat(seg["start"]) < history_start:
            continue
        label = seg["label"]
        if label not in by_label or seg["end"] > by_label[label]["end"]:
            by_label[label] = seg
    return {label: by_label.get(label) for label in ("하락", "횡보", "상승")}


def augment_with_tp_sl(sell_group: dict, stop_loss_pct: float, take_profit_pct: float) -> dict:
    """원래 매도조건에 손절/익절 OR조건을 얹는다. STOP_LOSS_PCT/TAKE_PROFIT_PCT는
    포지션 진입가 대비 수익률로 평가되는 기존 조건트리 지표(engine/condition_tree.py의
    POSITION_RELATIVE_INDICATORS)라 그대로 재사용한다."""
    return {
        "type": "OR",
        "conditions": [
            sell_group,
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": stop_loss_pct},
            {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": take_profit_pct},
        ],
    }


def top_candidates(results: list[dict], min_trades: int, pool_size: int) -> list[dict]:
    """거래횟수 미달 결과를 먼저 버리고, 남은 것에 기존 dedup_top_results를 적용해
    수익률 내림차순 상위 pool_size개를 돌려준다."""
    filtered = [r for r in results if len(r["trades"]) >= min_trades]
    return dedup_top_results(filtered, pool_size)
