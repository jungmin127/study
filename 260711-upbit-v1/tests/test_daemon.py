import trading.daemon as daemon


def test_poll_interval_sec_scales_with_timeframe():
    assert daemon._poll_interval_sec("minutes1") == 5.0
    assert daemon._poll_interval_sec("minutes3") == 15.0
    assert daemon._poll_interval_sec("minutes5") == 25.0
    assert daemon._poll_interval_sec("minutes15") == 60.0  # 75초 -> 60초 상한
    assert daemon._poll_interval_sec("minutes60") == 60.0  # 300초 -> 60초 상한
    assert daemon._poll_interval_sec("minutes240") == 60.0
    assert daemon._poll_interval_sec("days") == 60.0
