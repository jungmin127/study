"""
scripts/scan_candle_gaps.py

20개 학습 마켓의 minutes60 캔들에서 결측 구간(캔들 간 시간 간격이 timeframe
배수를 벗어나는 지점)을 스캔한다. docs/regime-ml-backlog.md 기술부채 항목 —
KRW-DOGE 2026-07-05 17:00~20:59 결측이 Triple Barrier 라벨링을 왜곡할 수 있음이
확인됐고, 다른 마켓에도 있는지는 미확인이었다. 설계 문서:
docs/superpowers/specs/2026-08-31-regime-ml-performance-improvement-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/scan_candle_gaps.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from engine.regime_ml_constants import TRAINING_MARKETS
from upbit_data_service import get_candles

TIMEFRAME = "minutes60"
EXPECTED_INTERVAL_HOURS = 1.0
TRAIN_START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def scan_market_gaps(df: pd.DataFrame, expected_interval_hours: float) -> list[dict]:
    """df["candle_time"]이 오름차순 정렬돼 있다고 가정(get_candles()의 보장 —
    upbit_data_service.py 참고). 연속한 두 캔들 간 간격이 expected_interval_hours를
    초과하는 지점을 전부 찾아 반환한다."""
    diffs = df["candle_time"].diff().dt.total_seconds() / 3600.0
    gap_mask = diffs > expected_interval_hours
    gaps = []
    for idx in diffs[gap_mask].index:
        gaps.append({
            "gap_start": df["candle_time"].iloc[idx - 1],
            "gap_end": df["candle_time"].iloc[idx],
            "gap_hours": float(diffs.loc[idx]),
        })
    return gaps


def main() -> None:
    end = datetime.now(timezone.utc)
    print(f"스캔 구간: {TRAIN_START.date()} ~ {end.date()}, timeframe={TIMEFRAME}\n")
    for market in TRAINING_MARKETS:
        df = get_candles(market, TIMEFRAME, TRAIN_START, end)
        gaps = scan_market_gaps(df, EXPECTED_INTERVAL_HOURS)
        total_gap_hours = sum(g["gap_hours"] for g in gaps)
        total_span_hours = (df["candle_time"].iloc[-1] - df["candle_time"].iloc[0]).total_seconds() / 3600.0
        pct = (total_gap_hours / total_span_hours * 100.0) if total_span_hours > 0 else 0.0
        print(f"{market}: 결측 {len(gaps)}건, 총 {total_gap_hours:.1f}시간({pct:.3f}% of 전체 구간)")
        for g in gaps:
            print(f"    {g['gap_start']} ~ {g['gap_end']} ({g['gap_hours']:.1f}시간)")


if __name__ == "__main__":
    main()
