import pytest

from engine.trend_segments import _legs_from_pivots, _zigzag_pivot_indices


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
