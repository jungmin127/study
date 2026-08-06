"""테스트: FIB류, PIVOT류 라이브 지표 — pandas와 backtrader 값 일치 검증."""
from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_fib_382,
    create_fib_500,
    create_fib_618,
    create_pivot_p,
    create_pivot_r1,
    create_pivot_s1,
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
