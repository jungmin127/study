from tests.live_indicator_fixtures import assert_matches_backtrader
from tests.signal_fixtures import make_oscillating_df
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    create_macd_line,
    create_macd_ppo,
    create_macd_ppo_signal,
    create_macd_signal,
    create_rsi,
)


def test_rsi_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("RSI", {"period": 14}, create_rsi(df, period=14))


def test_macd_line_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_line", params, create_macd_line(df, **params))


def test_macd_signal_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_signal", params, create_macd_signal(df, **params))


def test_macd_ppo_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_PPO", params, create_macd_ppo(df, **params))


def test_macd_ppo_signal_matches_backtrader():
    df = make_oscillating_df()
    params = {"fast": 12, "slow": 26, "signal": 9}
    assert_matches_backtrader("MACD_PPO_signal", params, create_macd_ppo_signal(df, **params))


def test_macd_ppo_param_mapping_actually_changes_output():
    df = make_oscillating_df()
    default = create_macd_ppo(df, fast=12, slow=26, signal=9)
    different = create_macd_ppo(df, fast=5, slow=10, signal=3)
    assert default.iloc[-1] != different.iloc[-1]


def test_rsi_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_rsi(df, period=14)
    assert result.iloc[:14].isna().all()
    assert result.iloc[14:].notna().all()


def test_macd_line_warmup_is_nan_before_slow_ema_ready():
    df = make_oscillating_df()
    result = create_macd_line(df, fast=12, slow=26)
    assert result.iloc[:25].isna().all()
    assert result.iloc[25:].notna().all()


def test_macd_signal_warmup_is_nan_before_signal_ema_ready():
    df = make_oscillating_df()
    result = create_macd_signal(df, fast=12, slow=26, signal=9)
    assert result.iloc[:33].isna().all()
    assert result.iloc[33:].notna().all()


def test_live_indicator_factory_registers_momentum_part1():
    assert LIVE_INDICATOR_FACTORY["RSI"] is create_rsi
    assert LIVE_INDICATOR_FACTORY["MACD_line"] is create_macd_line
    assert LIVE_INDICATOR_FACTORY["MACD_signal"] is create_macd_signal
    assert LIVE_INDICATOR_FACTORY["MACD_PPO"] is create_macd_ppo
    assert LIVE_INDICATOR_FACTORY["MACD_PPO_signal"] is create_macd_ppo_signal


from trading.live_indicators import (
    create_cci,
    create_momentum_pct,
    create_stoch_d,
    create_stoch_k,
    create_williams_r,
)


def test_stoch_k_matches_backtrader():
    df = make_oscillating_df()
    params = {"k_period": 14, "d_period": 3}
    assert_matches_backtrader("STOCH_K", params, create_stoch_k(df, **params))


def test_stoch_d_matches_backtrader():
    df = make_oscillating_df()
    params = {"k_period": 14, "d_period": 3}
    assert_matches_backtrader("STOCH_D", params, create_stoch_d(df, **params))


def test_cci_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("CCI", {"period": 20}, create_cci(df, period=20))


def test_williams_r_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("WILLIAMS_R", {"period": 14}, create_williams_r(df, period=14))


def test_momentum_pct_matches_backtrader():
    df = make_oscillating_df()
    assert_matches_backtrader("MOMENTUM_PCT", {"period": 5}, create_momentum_pct(df, period=5))


def test_live_indicator_factory_registers_momentum_part2():
    from trading.live_indicators import LIVE_INDICATOR_FACTORY
    assert LIVE_INDICATOR_FACTORY["STOCH_K"] is create_stoch_k
    assert LIVE_INDICATOR_FACTORY["STOCH_D"] is create_stoch_d
    assert LIVE_INDICATOR_FACTORY["CCI"] is create_cci
    assert LIVE_INDICATOR_FACTORY["WILLIAMS_R"] is create_williams_r
    assert LIVE_INDICATOR_FACTORY["MOMENTUM_PCT"] is create_momentum_pct


def test_cci_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_cci(df, period=20)
    assert result.iloc[:38].isna().all()
    assert result.iloc[38:].notna().all()


def test_williams_r_warmup_is_nan_before_period_bars():
    df = make_oscillating_df()
    result = create_williams_r(df, period=14)
    assert result.iloc[:13].isna().all()
    assert result.iloc[13:].notna().all()
