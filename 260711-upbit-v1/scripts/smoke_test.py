"""
수동 통합 스모크 테스트.
Run: python scripts/smoke_test.py
"""
from datetime import datetime, timedelta, timezone

import backtrader as bt

from upbit_data_service import get_candles
from engine.cache import run_backtest_cached


class SmaCrossOnce(bt.Strategy):
    params = (("period", 5),)

    def __init__(self):
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.period)

    def next(self):
        if not self.position and self.data.close[0] > self.sma[0]:
            self.buy()


def main() -> None:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=30)

    print(f"[1/2] get_candles(KRW-BTC, days, {start.date()} ~ {end.date()})")
    df = get_candles("KRW-BTC", "days", start, end)
    print(f"  받아온 캔들 수: {len(df)}")
    print(df.head(3))
    print(df.tail(3))

    risk_config = {
        "initial_capital": 10_000_000,
        "commission_rate": 0.0005,
        "position_sizing": "percent",
        "position_size": 100,
    }

    print("[2/2] run_backtest_cached() 첫 실행 (miss 예상)")
    first = run_backtest_cached(
        df=df, strategy_cls=SmaCrossOnce, risk_config=risk_config,
        market="KRW-BTC", timeframe="days", start=start, end=end,
        strategy_params={"period": 5},
    )
    print(f"  from_cache={first['from_cache']}, final_value={first['final_value']}, "
          f"trades={len(first['trades'])}")

    print("[2/2] run_backtest_cached() 두 번째 실행 (hit 예상)")
    second = run_backtest_cached(
        df=df, strategy_cls=SmaCrossOnce, risk_config=risk_config,
        market="KRW-BTC", timeframe="days", start=start, end=end,
        strategy_params={"period": 5},
    )
    print(f"  from_cache={second['from_cache']}, final_value={second['final_value']}")

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["final_value"] == first["final_value"]
    print("스모크 테스트 통과")


if __name__ == "__main__":
    main()
