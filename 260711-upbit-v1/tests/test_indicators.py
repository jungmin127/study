import backtrader as bt

from engine.condition_tree import get_indicator_value
from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df
from engine.runner import PandasDataWithExtra, PandasDataWithTradeValue, run_backtest


class _ProbeStrategy(bt.Strategy):
    params = (("indicator", "RSI"), ("indicator_params", {}))

    def __init__(self):
        create_fn = INDICATOR_FACTORY[self.p.indicator]
        self.probe = create_fn(self.data, **self.p.indicator_params)
        self.seen_values: list[float] = []

    def next(self):
        self.seen_values.append(get_indicator_value(self.p.indicator, self.probe))


def _run_probe(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


_NEEDS_EXTRA_LINE = {"MARKET_TREND"}  # extra 데이터 라인이 필요 — test_market_trend_matches_manual_close_minus_sma_of_extra_line 참고
_NEEDS_TRADE_VALUE_LINE = {"TRADE_VALUE", "TRADE_VALUE_SMA"}  # trade_value 라인이 필요 — test_trade_value_* 참고


def test_all_registered_indicators_produce_values():
    for name in INDICATOR_FACTORY:
        if name in _NEEDS_EXTRA_LINE or name in _NEEDS_TRADE_VALUE_LINE:
            continue
        values = _run_probe(name, {})
        assert len(values) > 0, f"{name} 지표가 값을 하나도 생성하지 못함"


def test_sma_matches_manual_average():
    values = _run_probe("SMA", {"period": 5})
    df = make_oscillating_df()
    manual = df["close"].rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def _run_probe_with_extra(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df["market_close"] = df["close"] * 2 + 1000  # 대상 마켓과 스케일이 다른 별도 시세임을 검증하기 위해 배율을 둠
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    df_bt = df_bt.rename(columns={"market_close": "extra"})
    cerebro = bt.Cerebro()
    cerebro.adddata(
        PandasDataWithExtra(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, extra="extra",
        )
    )
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_market_trend_matches_manual_close_minus_sma_of_extra_line():
    values = _run_probe_with_extra("MARKET_TREND", {"period": 5})
    df = make_oscillating_df()
    market_close = df["close"] * 2 + 1000
    manual = (market_close - market_close.rolling(5).mean()).iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def test_momentum_pct_matches_manual_pct_change_over_period():
    values = _run_probe("MOMENTUM_PCT", {"period": 5})
    df = make_oscillating_df()
    manual = (df["close"].iloc[-1] - df["close"].iloc[-6]) / df["close"].iloc[-6] * 100
    assert abs(values[-1] - manual) < 1e-6


def _run_probe_with_trade_value(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df["trade_value"] = df["close"] * df["volume"]
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(
        PandasDataWithTradeValue(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, trade_value="trade_value",
        )
    )
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_trade_value_matches_raw_trade_value_column():
    values = _run_probe_with_trade_value("TRADE_VALUE", {})
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    assert abs(values[-1] - trade_value.iloc[-1]) < 1e-6


def test_trade_value_sma_matches_manual_average_of_trade_value():
    values = _run_probe_with_trade_value("TRADE_VALUE_SMA", {"period": 5})
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    manual = trade_value.rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def test_fib_382_matches_manual_swing_calculation():
    values = _run_probe("FIB_382", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    manual = hh - (hh - ll) * 0.382
    assert abs(values[-1] - manual) < 1e-6


def test_fib_618_matches_manual_swing_calculation():
    values = _run_probe("FIB_618", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    manual = hh - (hh - ll) * 0.618
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_p_matches_manual_prev_bar_average():
    values = _run_probe("PIVOT_P", {})
    df = make_oscillating_df()
    manual = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_r1_matches_manual_formula():
    values = _run_probe("PIVOT_R1", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    manual = pivot * 2 - df["low"].iloc[-2]
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_s1_matches_manual_formula():
    values = _run_probe("PIVOT_S1", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    manual = pivot * 2 - df["high"].iloc[-2]
    assert abs(values[-1] - manual) < 1e-6
