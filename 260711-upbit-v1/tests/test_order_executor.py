import trading.order_executor as order_executor


def test_round_to_tick_boundaries():
    assert order_executor.round_to_tick(2_500_000) == 2_500_000  # 1,000,000원 이상 → 1,000원 단위
    assert order_executor.round_to_tick(2_500_400) == 2_500_000
    assert order_executor.round_to_tick(999_760) == 1_000_000  # 500,000~1,000,000 → 500원 단위이므로 반올림 값 확인
    assert order_executor.round_to_tick(150_030) == 150_000  # 100,000~500,000 → 100원 단위
    assert order_executor.round_to_tick(9_998) == 10_000  # 5,000~10,000 → 5원 단위, 반올림
    assert order_executor.round_to_tick(4_500) == 4_500  # 1,000~5,000 → 1원 단위
    assert order_executor.round_to_tick(55) == 55.0  # 10~100 → 0.1원 단위
    assert order_executor.round_to_tick(5.678) == 5.68  # 1~10 → 0.01원 단위


def test_floor_volume_truncates_to_eight_decimals():
    assert order_executor._floor_volume(0.123456789) == 0.12345678
    assert order_executor._floor_volume(1.0) == 1.0
