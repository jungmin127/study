"""
engine/strategies.py

signals.Signal 리스트를 받아 매수는 AND, 매도는 OR로 결합해 실행하는
제네릭 backtrader 전략. 신호가 1개면 단독 전략, 여러 개면 혼합 전략이 된다
— 별도의 "단독 전략용" 클래스는 두지 않는다.
"""
from __future__ import annotations

import backtrader as bt


class SignalStrategy(bt.Strategy):
    params = (("signals", []),)

    def __init__(self):
        self.signals = list(self.p.signals)
        for signal in self.signals:
            signal.setup(self)

    def next(self):
        if not self.position:
            if self.signals and all(s.should_buy(self) for s in self.signals):
                self.buy()
        else:
            if any(s.should_sell(self) for s in self.signals):
                self.sell()
