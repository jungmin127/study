import backtrader as bt

from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df
from engine.runner import PandasDataWithExtra, run_backtest


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
        if name == "MARKET_TREND":
            continue  # extra 데이터 라인이 필요 — test_market_trend_matches_manual_close_minus_sma_of_extra_line 참고
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
