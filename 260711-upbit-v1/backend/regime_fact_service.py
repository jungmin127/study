"""
backend/regime_fact_service.py

engine/regime_ml_labels.py의 compute_triple_barrier_labels()(Triple Barrier, ML
아님)로 코인별 과거 "하락/하락아님" fact 구간을 계산한다. /regime 탭의 시각화 +
그리드서치 프리필 전용. 캐싱 없음 — 요청마다 즉시 계산(실측: KRW-BTC minutes60
2024-01-01~현재 23,305봉 기준 0.57초). 설계 문서:
docs/superpowers/specs/2026-08-30-regime-fact-segment-viewer-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_labels import compute_triple_barrier_labels
from upbit_data_service import get_candles

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
# scripts/train_regime_ml.py:BARRIER_K와 동일 값(2026-08-29 select_barrier_k.py로
# 결정한 프로덕션 학습 파이프라인 상수). 이 모듈은 학습 모듈을 import하지 않으므로
# 값만 복제한다.
BARRIER_K = 6.25
# 표에 나열할 최소 지속봉수(24봉=minutes60 기준 1일). 미만인 구간은 차트에는
# 그대로 색칠되지만 표에는 나오지 않는다(바 단위 라벨이 자주 뒤집혀 실용성 저하 방지).
MIN_SEGMENT_BARS = 24


def _to_iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _same_label(a: object, b: object) -> bool:
    """NaN != NaN이 True인 파이썬 기본 비교로는 "같은 NaN 구간"을 라벨이 계속
    바뀌는 것으로 오인하게 되므로, 둘 다 NaN이면 같다고 취급한다."""
    if pd.isna(a) and pd.isna(b):
        return True
    return a == b


def compute_fact_regime_segments(market: str, timeframe: str) -> dict:
    """market의 fact 장세 라벨을 봉별 OHLCV+라벨 배열과, 최소 지속봉수 이상인
    연속 구간 목록으로 반환한다. 반환값: {market, timeframe, bars, segments}.
    bars의 각 원소는 {time, open, high, low, close, label}(label은 "하락"/
    "하락아님"/None). segments의 각 원소는 {start, end, label, bar_count}."""
    df = get_candles(market, timeframe, START, datetime.now(timezone.utc))
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    labels = compute_triple_barrier_labels(df, half_life_bars, n_bars, BARRIER_K)

    bars = [
        {
            "time": _to_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "label": None if pd.isna(label) else label,
        }
        for row, label in zip(df.itertuples(), labels)
    ]

    segments: list[dict] = []
    if len(labels) > 0:
        run_start_idx = 0
        run_label = labels.iloc[0]
        for i in range(1, len(labels) + 1):
            at_end = i == len(labels)
            cur_label = None if at_end else labels.iloc[i]
            if at_end or not _same_label(cur_label, run_label):
                bar_count = i - run_start_idx
                if pd.notna(run_label) and bar_count >= MIN_SEGMENT_BARS:
                    segments.append({
                        "start": _to_iso(df["candle_time"].iloc[run_start_idx]),
                        "end": _to_iso(df["candle_time"].iloc[i - 1]),
                        "label": run_label,
                        "bar_count": bar_count,
                    })
                run_start_idx = i
                run_label = cur_label

    return {"market": market, "timeframe": timeframe, "bars": bars, "segments": segments}
