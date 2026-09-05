"""
backend/regime_adx_service.py

engine/regime_adx.py의 ADX+DI 규칙기반 판정으로 "장세 판별" 탭의 (1) 코인별
과거 전체 기간 상승/하락/횡보 구간 뷰어, (2) 메이저 코인 20개 현재 판정
오버뷰를 계산한다. 캐싱 없음 — 요청마다 즉시 계산. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from engine.regime_adx import classify_regime, compute_adx_di
from engine.regime_adx_constants import MAJOR_MARKETS
from upbit_data_service import get_candles

HISTORY_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
# 표에 나열할 최소 지속봉수(24봉=minutes60 기준 1일). 미만인 구간은 차트에는
# 그대로 색칠되지만 표에는 나오지 않는다.
MIN_SEGMENT_BARS = 24
# 오버뷰(최신 시점만 필요)용 조회 기간 — ADX(14) 워밍업(약 28봉)에 여유를
# 둔 값. 전체 히스토리를 긁을 필요 없다.
OVERVIEW_LOOKBACK_BARS = 200


def _to_iso(ts: pd.Timestamp) -> str:
    return ts.isoformat()


def _same_label(a: object, b: object) -> bool:
    """둘 다 None이면(같은 미분류 구간) 같다고 취급한다."""
    if a is None and b is None:
        return True
    return a == b


def compute_adx_regime_history(market: str, timeframe: str) -> dict:
    """market의 전체 기간(HISTORY_START~현재) ADX 장세 라벨을 봉별 OHLCV+라벨
    배열과, 최소 지속봉수 이상인 연속 구간 목록으로 반환한다. 반환값:
    {market, timeframe, bars, segments}. bars의 각 원소는
    {time, open, high, low, close, label}(label은 "상승"/"하락"/"횡보"/None).
    segments의 각 원소는 {start, end, label, bar_count}."""
    df = get_candles(market, timeframe, HISTORY_START, datetime.now(timezone.utc))
    adx_di = compute_adx_di(df)
    labels = [
        classify_regime(row.adx, row.plus_di, row.minus_di)
        for row in adx_di.itertuples()
    ]

    bars = [
        {
            "time": _to_iso(row.candle_time),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
            "label": label,
        }
        for row, label in zip(df.itertuples(), labels)
    ]

    segments: list[dict] = []
    if labels:
        run_start_idx = 0
        run_label = labels[0]
        for i in range(1, len(labels) + 1):
            at_end = i == len(labels)
            cur_label = None if at_end else labels[i]
            if at_end or not _same_label(cur_label, run_label):
                bar_count = i - run_start_idx
                if run_label is not None and bar_count >= MIN_SEGMENT_BARS:
                    segments.append({
                        "start": _to_iso(df["candle_time"].iloc[run_start_idx]),
                        "end": _to_iso(df["candle_time"].iloc[i - 1]),
                        "label": run_label,
                        "bar_count": bar_count,
                    })
                run_start_idx = i
                run_label = cur_label

    return {"market": market, "timeframe": timeframe, "bars": bars, "segments": segments}


def compute_adx_regime_overview(timeframe: str) -> list[dict]:
    """MAJOR_MARKETS 각각의 현재(최신 봉) ADX 장세 판정을 반환한다. 반환값:
    [{market, label, adx, plus_di, minus_di}, ...] (label은 "상승"/"하락"/
    "횡보"/None, 순서는 MAJOR_MARKETS와 동일)."""
    start = datetime.now(timezone.utc) - timedelta(hours=OVERVIEW_LOOKBACK_BARS)
    results = []
    for market in MAJOR_MARKETS:
        df = get_candles(market, timeframe, start, datetime.now(timezone.utc))
        adx_di = compute_adx_di(df)
        last = adx_di.iloc[-1]
        label = classify_regime(last.adx, last.plus_di, last.minus_di)
        results.append({
            "market": market,
            "label": label,
            "adx": None if pd.isna(last.adx) else float(last.adx),
            "plus_di": None if pd.isna(last.plus_di) else float(last.plus_di),
            "minus_di": None if pd.isna(last.minus_di) else float(last.minus_di),
        })
    return results
