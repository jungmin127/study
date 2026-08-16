import pytest

from engine.trend_segments import _legs_from_pivots, _zigzag_pivot_indices, _merge_sideways_runs, _run_to_segment


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
