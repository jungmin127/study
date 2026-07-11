import pytest

from upbit_data_service import _endpoint_for_timeframe


def test_endpoint_for_days():
    assert _endpoint_for_timeframe("days") == "https://api.upbit.com/v1/candles/days"


def test_endpoint_for_minutes():
    assert _endpoint_for_timeframe("minutes60") == "https://api.upbit.com/v1/candles/minutes/60"


def test_endpoint_for_unsupported_timeframe_raises():
    with pytest.raises(ValueError):
        _endpoint_for_timeframe("weeks")
