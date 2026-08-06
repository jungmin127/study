"""라이브 지표(trading/live_indicators.py, pandas)와 백테스트 지표
(engine/indicators/*.py, backtrader)가 같은 값을 내는지 검증하는 골든테스트 공용 하네스."""
from __future__ import annotations

import backtrader as bt

from engine.condition_tree import get_indicator_value
from engine.indicators import INDICATOR_FACTORY
from tests.signal_fixtures import make_oscillating_df


def run_backtrader_probe(indicator: str, params: dict) -> list[float]:
    """make_oscillating_df() 전체 구간에 대해 backtrader로 indicator를 계산해, next()가
    호출된 매 봉의 값을 리스트로 반환한다(워밍업 구간은 backtrader의 minperiod 로직에
    따라 애초에 리스트에 포함되지 않는다). 라이브(pandas) 지표 함수의 골든테스트
    기준값으로 쓴다."""
    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _Probe(bt.Strategy):
        def __init__(self) -> None:
            create_fn = INDICATOR_FACTORY[indicator]
            self.probe = create_fn(self.data, **params)
            self.seen_values: list[float] = []

        def next(self) -> None:
            self.seen_values.append(get_indicator_value(indicator, self.probe))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_Probe)
    results = cerebro.run()
    return results[0].seen_values


def assert_matches_backtrader(indicator: str, params: dict, pandas_series, tol: float = 1e-6) -> None:
    """pandas_series(라이브 지표 함수가 make_oscillating_df() 전체에 대해 계산한 결과)의
    마지막 값이, 같은 지표·같은 파라미터로 backtrader를 돌린 결과의 마지막 값과 tol 오차
    내로 일치하는지 검증한다. 라이브 엔진은 매 캔들 마감 시 항상 '지금까지의 마지막 값'만
    쓰므로, 이 골든테스트도 마지막 값 비교면 충분하다."""
    bt_values = run_backtrader_probe(indicator, params)
    pandas_last = pandas_series.iloc[-1]
    bt_last = bt_values[-1]
    assert abs(pandas_last - bt_last) < tol, (
        f"{indicator}({params}) 불일치: pandas={pandas_last!r} vs backtrader={bt_last!r} "
        f"(오차={abs(pandas_last - bt_last)!r})"
    )
