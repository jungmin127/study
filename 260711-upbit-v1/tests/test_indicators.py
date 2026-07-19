import backtrader as bt

from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df
from engine.runner import run_backtest


class _ProbeStrategy(bt.Strategy):
    params = (("indicator", "RSI"), ("indicator_params", {}))

    def __init__(self):
        create_fn = INDICATOR_FACTORY[self.p.indicator]
        self.probe = create_fn(self.data, **self.p.indicator_params)
        self.seen_values: list[float] = []

    def next(self):
        self.seen_values.append(float(self.probe[0]))


def _run_probe(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_all_registered_indicators_produce_values():
    for name in INDICATOR_FACTORY:
        values = _run_probe(name, {})
        assert len(values) > 0, f"{name} 지표가 값을 하나도 생성하지 못함"


def test_sma_matches_manual_average():
    values = _run_probe("SMA", {"period": 5})
    df = make_oscillating_df()
    manual = df["close"].rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6
