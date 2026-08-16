"""
engine/trend_segments.py

코인별 일봉 이력을 ZigZag 스윙 기반으로 상승/하락/횡보 구간으로 분류하고,
각 구간을 전반/후반으로 나눠 9패턴으로 라벨링한다. 설계 문서:
docs/superpowers/specs/2026-08-16-trend-segment-analysis-design.md
"""
from __future__ import annotations


def _zigzag_pivot_indices(closes: list[float], threshold_pct: float) -> list[int]:
    """종가 배열에서 ZigZag 스윙 고점/저점의 인덱스를 확정 순서대로 반환한다.
    항상 첫 인덱스(0)로 시작하고 마지막 인덱스로 끝난다."""
    n = len(closes)
    if n <= 1:
        return list(range(n))

    pivots = [0]
    anchor_price = closes[0]
    direction: str | None = None
    max_idx, max_price = 0, closes[0]
    min_idx, min_price = 0, closes[0]
    ext_idx, ext_price = 0, closes[0]

    for i in range(1, n):
        price = closes[i]

        if direction is None:
            if price > max_price:
                max_price, max_idx = price, i
            if price < min_price:
                min_price, min_idx = price, i
            up_pct = (max_price - anchor_price) / anchor_price * 100
            down_pct = (anchor_price - min_price) / anchor_price * 100
            if up_pct >= threshold_pct and up_pct >= down_pct:
                direction = "up"
                ext_idx, ext_price = max_idx, max_price
            elif down_pct >= threshold_pct:
                direction = "down"
                ext_idx, ext_price = min_idx, min_price
            continue

        if direction == "up":
            if price >= ext_price:
                ext_price, ext_idx = price, i
                continue
            retrace_pct = (ext_price - price) / ext_price * 100
            if retrace_pct >= threshold_pct:
                pivots.append(ext_idx)
                anchor_price = ext_price
                direction = "down"
                ext_price, ext_idx = price, i
        else:
            if price <= ext_price:
                ext_price, ext_idx = price, i
                continue
            retrace_pct = (price - ext_price) / ext_price * 100
            if retrace_pct >= threshold_pct:
                pivots.append(ext_idx)
                anchor_price = ext_price
                direction = "up"
                ext_price, ext_idx = price, i

    if pivots[-1] != n - 1:
        pivots.append(n - 1)
    return pivots


def _legs_from_pivots(closes: list[float], dates: list, pivots: list[int]) -> list[dict]:
    """확정된 스윙 인덱스 사이사이를 상승/하락 레그로 변환한다."""
    legs = []
    for a, b in zip(pivots, pivots[1:]):
        start_price = closes[a]
        end_price = closes[b]
        legs.append({
            "start_idx": a, "end_idx": b,
            "start_date": dates[a], "end_date": dates[b],
            "start_price": start_price, "end_price": end_price,
            "return_pct": (end_price - start_price) / start_price * 100,
        })
    return legs
