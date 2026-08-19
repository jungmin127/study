"""테스트: FIB류, PIVOT류, VPVR류 라이브 지표 — pandas와 backtrader 값 일치 검증."""
import pandas as pd
import pytest

from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_fib_382,
    create_fib_382_pct,
    create_fib_500,
    create_fib_500_pct,
    create_fib_618,
    create_fib_618_pct,
    create_pivot_p,
    create_pivot_p_pct,
    create_pivot_r1,
    create_pivot_r1_pct,
    create_pivot_s1,
    create_pivot_s1_pct,
)


def test_fib_382_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_382", {"period": 20}, create_fib_382(df, period=20))


def test_fib_500_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_500", {"period": 20}, create_fib_500(df, period=20))


def test_fib_618_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_618", {"period": 20}, create_fib_618(df, period=20))


def test_pivot_p_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_P", {}, create_pivot_p(df))


def test_pivot_r1_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_R1", {}, create_pivot_r1(df))


def test_pivot_s1_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_S1", {}, create_pivot_s1(df))


def test_pivot_p_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_P_PCT", {}, create_pivot_p_pct(df))


def test_pivot_r1_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_R1_PCT", {}, create_pivot_r1_pct(df))


def test_pivot_s1_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("PIVOT_S1_PCT", {}, create_pivot_s1_pct(df))


def test_live_indicator_factory_registers_pivot_pct():
    assert LIVE_INDICATOR_FACTORY["PIVOT_P_PCT"] is create_pivot_p_pct
    assert LIVE_INDICATOR_FACTORY["PIVOT_R1_PCT"] is create_pivot_r1_pct
    assert LIVE_INDICATOR_FACTORY["PIVOT_S1_PCT"] is create_pivot_s1_pct


def test_fib_382_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_382_PCT", {"period": 20}, create_fib_382_pct(df, period=20))


def test_fib_500_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_500_PCT", {"period": 20}, create_fib_500_pct(df, period=20))


def test_fib_618_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("FIB_618_PCT", {"period": 20}, create_fib_618_pct(df, period=20))


def _zero_price_df():
    idx = pd.date_range("2026-01-01", periods=25, freq="h", tz="UTC")
    return pd.DataFrame({
        "candle_time": idx, "open": [0.0] * 25, "high": [0.0] * 25,
        "low": [0.0] * 25, "close": [0.0] * 25, "volume": [10.0] * 25,
    })


def test_fib_382_pct_handles_zero_level_without_crashing():
    df = _zero_price_df()
    result = create_fib_382_pct(df, period=20)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_fib_500_pct_handles_zero_level_without_crashing():
    df = _zero_price_df()
    result = create_fib_500_pct(df, period=20)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_fib_618_pct_handles_zero_level_without_crashing():
    df = _zero_price_df()
    result = create_fib_618_pct(df, period=20)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_pivot_p_pct_handles_zero_level_without_crashing():
    df = _zero_price_df()
    result = create_pivot_p_pct(df)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_pivot_r1_pct_handles_zero_level_without_crashing():
    df = _zero_price_df()
    result = create_pivot_r1_pct(df)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_pivot_s1_pct_handles_zero_level_without_crashing():
    df = _zero_price_df()
    result = create_pivot_s1_pct(df)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_live_indicator_factory_registers_fib_pct():
    assert LIVE_INDICATOR_FACTORY["FIB_382_PCT"] is create_fib_382_pct
    assert LIVE_INDICATOR_FACTORY["FIB_500_PCT"] is create_fib_500_pct
    assert LIVE_INDICATOR_FACTORY["FIB_618_PCT"] is create_fib_618_pct


def test_live_indicator_factory_registers_price_levels_part1():
    assert LIVE_INDICATOR_FACTORY["FIB_382"] is create_fib_382
    assert LIVE_INDICATOR_FACTORY["FIB_500"] is create_fib_500
    assert LIVE_INDICATOR_FACTORY["FIB_618"] is create_fib_618
    assert LIVE_INDICATOR_FACTORY["PIVOT_P"] is create_pivot_p
    assert LIVE_INDICATOR_FACTORY["PIVOT_R1"] is create_pivot_r1
    assert LIVE_INDICATOR_FACTORY["PIVOT_S1"] is create_pivot_s1


def test_fib_382_warmup_nan():
    """FIB_382(period=20)은 처음 19행이 NaN이어야 한다 (rolling window 특성)."""
    df = make_oscillating_df()
    result = create_fib_382(df, period=20)
    assert result.isna().sum() == 19, f"Expected 19 NaNs, got {result.isna().sum()}"
    assert not result.iloc[19:].isna().any(), "Expected no NaNs after index 19"


def test_pivot_p_warmup_nan():
    """PIVOT_P는 처음 1행만 NaN이어야 한다 (shift(1) 때문)."""
    df = make_oscillating_df()
    result = create_pivot_p(df)
    assert result.isna().sum() == 1, f"Expected 1 NaN, got {result.isna().sum()}"
    assert not result.iloc[1:].isna().any(), "Expected no NaNs after index 1"


from trading.live_indicators import (
    create_vpvr_poc,
    create_vpvr_poc_pct,
    create_vpvr_vah,
    create_vpvr_vah_pct,
    create_vpvr_val,
    create_vpvr_val_pct,
)


def test_vpvr_poc_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_POC", {"period": 50}, create_vpvr_poc(df, period=50))


def test_vpvr_vah_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAH", {"period": 50}, create_vpvr_vah(df, period=50))


def test_vpvr_val_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAL", {"period": 50}, create_vpvr_val(df, period=50))


def test_vpvr_poc_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_POC_PCT", {"period": 50}, create_vpvr_poc_pct(df, period=50))


def test_vpvr_vah_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAH_PCT", {"period": 50}, create_vpvr_vah_pct(df, period=50))


def test_vpvr_val_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("VPVR_VAL_PCT", {"period": 50}, create_vpvr_val_pct(df, period=50))


def test_live_indicator_factory_registers_vpvr_pct():
    assert LIVE_INDICATOR_FACTORY["VPVR_POC_PCT"] is create_vpvr_poc_pct
    assert LIVE_INDICATOR_FACTORY["VPVR_VAH_PCT"] is create_vpvr_vah_pct
    assert LIVE_INDICATOR_FACTORY["VPVR_VAL_PCT"] is create_vpvr_val_pct


def test_vpvr_matches_hand_traced_bin_distribution():
    # engine/indicators/price_levels.py의 VolumeProfile을 검증한 것과 동일한 손 계산
    # 시퀀스(tests/test_indicators.py::test_vpvr_matches_hand_traced_bin_distribution
    # 참고). NUM_BINS를 4로 좁혀서 손 계산 가능하게 만든다.
    import pandas as pd

    import trading.live_indicators as live_indicators

    highs = [2.5, 10.0, 5.0]
    lows = [0.0, 7.5, 2.5]
    volumes = [100, 10, 5]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": closes, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })

    original_num_bins = live_indicators.NUM_BINS
    live_indicators.NUM_BINS = 4
    try:
        poc = live_indicators.create_vpvr_poc(df, period=3)
        vah = live_indicators.create_vpvr_vah(df, period=3)
        val = live_indicators.create_vpvr_val(df, period=3)
    finally:
        live_indicators.NUM_BINS = original_num_bins

    assert poc.iloc[-1] == pytest.approx(1.25)
    assert vah.iloc[-1] == pytest.approx(2.5)
    assert val.iloc[-1] == pytest.approx(0.0)


def test_vpvr_handles_completely_flat_window_without_dividing_by_zero():
    highs = [100.0, 100.0, 100.0]
    lows = [100.0, 100.0, 100.0]
    volumes = [10, 10, 10]
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": highs, "high": highs, "low": lows,
        "close": highs, "volume": volumes,
    })
    poc = create_vpvr_poc(df, period=3)
    vah = create_vpvr_vah(df, period=3)
    val = create_vpvr_val(df, period=3)
    assert poc.iloc[-1] == pytest.approx(100.0)
    assert vah.iloc[-1] == pytest.approx(100.0)
    assert val.iloc[-1] == pytest.approx(100.0)


def test_vpvr_pct_handles_zero_level_without_crashing():
    # POC/VAH/VAL이 전부 0인 극단 케이스(가격 0에서 완전히 flat한 구간) — inf/NaN 없이
    # 0.0을 반환해야 한다.
    highs = [0.0, 0.0, 0.0]
    lows = [0.0, 0.0, 0.0]
    volumes = [10, 10, 10]
    idx = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx, "open": highs, "high": highs, "low": lows,
        "close": highs, "volume": volumes,
    })
    poc_pct = create_vpvr_poc_pct(df, period=3)
    vah_pct = create_vpvr_vah_pct(df, period=3)
    val_pct = create_vpvr_val_pct(df, period=3)
    assert poc_pct.iloc[-1] == pytest.approx(0.0)
    assert vah_pct.iloc[-1] == pytest.approx(0.0)
    assert val_pct.iloc[-1] == pytest.approx(0.0)


def test_live_indicator_factory_registers_vpvr():
    assert LIVE_INDICATOR_FACTORY["VPVR_POC"] is create_vpvr_poc
    assert LIVE_INDICATOR_FACTORY["VPVR_VAH"] is create_vpvr_vah
    assert LIVE_INDICATOR_FACTORY["VPVR_VAL"] is create_vpvr_val
