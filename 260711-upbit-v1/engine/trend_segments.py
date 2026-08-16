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


SIDEWAYS_LEG_CAP_RATIO = 1.5


def _merge_sideways_runs(legs: list[dict], threshold_pct: float) -> list[list[dict]]:
    """연속된 레그를 훑으며 묶음 시작가 대비 누적 순변화율이 threshold_pct 미만인
    동안 계속 묶는다(순방향 진행 없이 등락만 반복되는 구간 = 횡보 후보). 단, 레그
    개별 크기가 threshold_pct * SIDEWAYS_LEG_CAP_RATIO 이상이면 강한 단일 돌파로
    보고 흡수하지 않는다."""
    if not legs:
        return []
    cap_pct = threshold_pct * SIDEWAYS_LEG_CAP_RATIO
    runs: list[list[dict]] = [[legs[0]]]
    for leg in legs[1:]:
        run = runs[-1]
        run_start_price = run[0]["start_price"]
        candidate_net_pct = abs((leg["end_price"] - run_start_price) / run_start_price * 100)
        if abs(leg["return_pct"]) < cap_pct and candidate_net_pct < threshold_pct:
            run.append(leg)
        else:
            runs.append([leg])
    return runs


def _classify_return(return_pct: float, threshold_pct: float) -> str:
    if return_pct >= threshold_pct:
        return "up"
    if return_pct <= -threshold_pct:
        return "down"
    return "sideways"


def _run_to_segment(run: list[dict], threshold_pct: float) -> dict:
    start_price = run[0]["start_price"]
    end_price = run[-1]["end_price"]
    return_pct = (end_price - start_price) / start_price * 100
    return {
        "start_idx": run[0]["start_idx"], "end_idx": run[-1]["end_idx"],
        "start_date": run[0]["start_date"], "end_date": run[-1]["end_date"],
        "start_price": start_price, "end_price": end_price,
        "return_pct": return_pct, "trend": _classify_return(return_pct, threshold_pct),
    }


MIN_SEGMENT_DAYS = 14


def _combine_segments(a: dict, b: dict, threshold_pct: float) -> dict:
    first, second = (a, b) if a["start_idx"] <= b["start_idx"] else (b, a)
    start_price = first["start_price"]
    end_price = second["end_price"]
    return_pct = (end_price - start_price) / start_price * 100
    return {
        "start_idx": first["start_idx"], "end_idx": second["end_idx"],
        "start_date": first["start_date"], "end_date": second["end_date"],
        "start_price": start_price, "end_price": end_price,
        "return_pct": return_pct, "trend": _classify_return(return_pct, threshold_pct),
    }


def _absorb_short_segments(segments: list[dict], threshold_pct: float) -> list[dict]:
    """MIN_SEGMENT_DAYS 미만인 구간을 이웃 구간에 흡수한다(다음 구간 우선, 마지막
    구간이면 이전 구간). 흡수로 합쳐진 구간이 다시 짧으면 재귀적으로 계속 흡수된다."""
    segments = list(segments)
    changed = True
    while changed and len(segments) > 1:
        changed = False
        for i, seg in enumerate(segments):
            days = (seg["end_date"] - seg["start_date"]).days
            if days >= MIN_SEGMENT_DAYS:
                continue
            neighbor_i = i + 1 if i < len(segments) - 1 else i - 1
            lo, hi = sorted((i, neighbor_i))
            merged = _combine_segments(segments[lo], segments[hi], threshold_pct)
            segments = segments[:lo] + [merged] + segments[hi + 1:]
            changed = True
            break
    return segments
