import pytest
from datetime import date, timedelta
import pandas as pd

from engine.trend_segments import _legs_from_pivots, _zigzag_pivot_indices, _merge_sideways_runs, _run_to_segment, _absorb_short_segments, _combine_segments, PATTERN_LABELS, _classify_half, compute_trend_segments


def test_zigzag_pivot_indices_finds_expected_swing_points():
    closes = [100, 105, 110, 115, 120, 118, 114, 108, 102, 96, 90, 95, 100, 108, 115, 122, 130]

    pivots = _zigzag_pivot_indices(closes, threshold_pct=10.0)

    assert pivots == [0, 4, 10, 16]


def test_zigzag_pivot_indices_handles_flat_series():
    closes = [100.0] * 10

    pivots = _zigzag_pivot_indices(closes, threshold_pct=10.0)

    assert pivots == [0, 9]


def test_zigzag_pivot_indices_handles_single_point():
    assert _zigzag_pivot_indices([100.0], threshold_pct=10.0) == [0]


def test_legs_from_pivots_computes_return_pct():
    closes = [100, 105, 110, 115, 120, 118, 114, 108, 102, 96, 90, 95, 100, 108, 115, 122, 130]
    dates = list(range(len(closes)))  # 실제로는 pandas Timestamp지만 테스트에서는 정수로 대체 가능
    pivots = [0, 4, 10, 16]

    legs = _legs_from_pivots(closes, dates, pivots)

    assert len(legs) == 3
    assert legs[0]["start_idx"] == 0 and legs[0]["end_idx"] == 4
    assert legs[0]["return_pct"] == pytest.approx(20.0)
    assert legs[1]["return_pct"] == pytest.approx(-25.0)
    assert legs[2]["return_pct"] == pytest.approx((130 - 90) / 90 * 100)


def _leg(start_price, end_price, start_idx=0, end_idx=1):
    return {
        "start_idx": start_idx, "end_idx": end_idx,
        "start_date": start_idx, "end_date": end_idx,
        "start_price": start_price, "end_price": end_price,
        "return_pct": (end_price - start_price) / start_price * 100,
    }


def test_merge_sideways_runs_merges_low_net_change_legs():
    # leg0: 100->115(+15%), leg1: 115->104(약 -9.57%) → 누적 순변화 4% < threshold(10%) → 병합
    # leg2: 104->116(약 +11.5%) → 누적 순변화 16% >= threshold(10%) → 새 구간
    legs = [
        _leg(100, 115, 0, 1),
        _leg(115, 104, 1, 2),
        _leg(104, 116, 2, 3),
    ]

    runs = _merge_sideways_runs(legs, threshold_pct=10.0)

    assert len(runs) == 2
    assert len(runs[0]) == 2
    assert len(runs[1]) == 1


def test_merge_sideways_runs_does_not_absorb_leg_above_cap():
    # cap = threshold(10) * 1.5 = 15. leg1의 크기(20%)가 cap 이상이면 흡수하지 않고 분리.
    legs = [
        _leg(100, 104, 0, 1),   # +4%
        _leg(104, 124.8, 1, 2),  # +20% (cap 이상)
    ]

    runs = _merge_sideways_runs(legs, threshold_pct=10.0)

    assert len(runs) == 2
    assert len(runs[0]) == 1
    assert len(runs[1]) == 1


def test_run_to_segment_classifies_up_down_sideways_by_threshold():
    up_run = [_leg(100, 120, 0, 1)]
    down_run = [_leg(100, 80, 0, 1)]
    sideways_run = [_leg(100, 104, 0, 1)]

    assert _run_to_segment(up_run, threshold_pct=10.0)["trend"] == "up"
    assert _run_to_segment(down_run, threshold_pct=10.0)["trend"] == "down"
    assert _run_to_segment(sideways_run, threshold_pct=10.0)["trend"] == "sideways"


def _segment(start_day, end_day, start_price, end_price, trend, start_idx=0, end_idx=1):
    return {
        "start_idx": start_idx, "end_idx": end_idx,
        "start_date": date(2026, 1, 1) + timedelta(days=start_day),
        "end_date": date(2026, 1, 1) + timedelta(days=end_day),
        "start_price": start_price, "end_price": end_price,
        "return_pct": (end_price - start_price) / start_price * 100,
        "trend": trend,
    }


def test_combine_segments_recomputes_trend_over_full_range():
    a = _segment(0, 5, 100, 130, "up", start_idx=0, end_idx=5)      # 5일, 30%
    b = _segment(5, 8, 130, 133, "sideways", start_idx=5, end_idx=8)  # 3일, ~2.3%

    combined = _combine_segments(a, b, threshold_pct=10.0)

    assert combined["start_idx"] == 0 and combined["end_idx"] == 8
    assert combined["trend"] == "up"
    assert combined["return_pct"] == pytest.approx(33.0)


def test_absorb_short_segments_merges_into_following_neighbor():
    # 가운데 구간(3일)이 MIN_SEGMENT_DAYS(14) 미만 → 다음 구간에 흡수되어야 한다.
    segments = [
        _segment(0, 20, 100, 130, "up", 0, 20),
        _segment(20, 23, 130, 132, "sideways", 20, 23),
        _segment(23, 50, 132, 90, "down", 23, 50),
    ]

    result = _absorb_short_segments(segments, threshold_pct=10.0)

    assert len(result) == 2
    assert result[0]["end_idx"] == 20
    assert result[1]["start_idx"] == 20 and result[1]["end_idx"] == 50


def test_absorb_short_segments_merges_last_into_previous():
    segments = [
        _segment(0, 20, 100, 130, "up", 0, 20),
        _segment(20, 25, 130, 132, "sideways", 20, 25),  # 5일, 마지막 구간
    ]

    result = _absorb_short_segments(segments, threshold_pct=10.0)

    assert len(result) == 1
    assert result[0]["start_idx"] == 0 and result[0]["end_idx"] == 25


def test_absorb_short_segments_keeps_single_segment_untouched():
    segments = [_segment(0, 5, 100, 101, "sideways", 0, 5)]
    result = _absorb_short_segments(segments, threshold_pct=10.0)
    assert result == segments


def test_absorb_short_segments_cascades_through_multiple_merges():
    # 두 개의 연속된 짧은 구간(각 5일)이 순차적으로 흡수되어 최종적으로 하나로
    # 합쳐져야 한다(1회 병합 후에도 여전히 14일 미만이면 다시 흡수).
    segments = [
        _segment(0, 20, 100, 130, "up", 0, 20),
        _segment(20, 25, 130, 132, "sideways", 20, 25),
        _segment(25, 30, 132, 134, "sideways", 25, 30),
        _segment(30, 60, 134, 90, "down", 30, 60),
    ]

    result = _absorb_short_segments(segments, threshold_pct=10.0)

    assert len(result) == 2
    assert result[0]["start_idx"] == 0 and result[0]["end_idx"] == 20
    assert result[1]["start_idx"] == 20 and result[1]["end_idx"] == 60


def test_classify_half_uses_half_threshold():
    closes = [100.0, 110.0, 96.0]
    # idx0->idx1: +10% (threshold=10, half_threshold=5 → up)
    assert _classify_half(closes, 0, 1, threshold_pct=10.0) == "up"
    # idx1->idx2: 약 -12.7% (half_threshold=5 → down)
    assert _classify_half(closes, 1, 2, threshold_pct=10.0) == "down"
    # idx0->idx0: 변화 없음 → sideways
    assert _classify_half(closes, 0, 0, threshold_pct=10.0) == "sideways"


def test_pattern_labels_cover_all_nine_combinations():
    trends = ["up", "down", "sideways"]
    for first in trends:
        for second in trends:
            assert (first, second) in PATTERN_LABELS

    assert PATTERN_LABELS[("up", "up")] == "지속형 상승"
    assert PATTERN_LABELS[("up", "sideways")] == "상승 후 둔화"
    assert PATTERN_LABELS[("sideways", "sideways")] == "지속형 횡보"


def test_compute_trend_segments_end_to_end_with_synthetic_series():
    closes = [100, 105, 110, 115, 120, 118, 114, 108, 102, 96, 90, 95, 100, 108, 115, 122, 130]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({"candle_time": dates, "close": closes})

    segments = compute_trend_segments(df, threshold_pct=10.0)

    assert len(segments) >= 1
    for seg in segments:
        assert seg["trend"] in ("up", "down", "sideways")
        assert seg["pattern_label"] == PATTERN_LABELS[(seg["first_half_trend"], seg["second_half_trend"])]
        assert seg["start_date"] < seg["end_date"]


def test_compute_trend_segments_returns_empty_list_for_empty_df():
    df = pd.DataFrame({"candle_time": pd.Series([], dtype="datetime64[ns, UTC]"), "close": pd.Series([], dtype=float)})
    assert compute_trend_segments(df, threshold_pct=10.0) == []
