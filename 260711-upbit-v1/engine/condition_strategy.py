"""
engine/condition_strategy.py

ConditionGroup 트리(engine/condition_tree.py) 두 개(매수/매도)를 받아 실행하는
정적 bt.Strategy. 요청마다 동적으로 클래스를 만들지 않는다 — engine/cache.py의
캐시 키가 inspect.getsource(strategy_cls)에 의존하므로, 클래스는 항상 이 모듈에
고정된 소스로 존재해야 하고 트리 내용은 backtrader params(=strategy_params)로만
달라져야 캐싱이 올바르게 동작한다.
"""
from __future__ import annotations

import backtrader as bt

from engine.condition_tree import POSITION_RELATIVE_INDICATORS, collect_blocks, eval_group, indicator_key
from engine.indicators import INDICATOR_FACTORY

_EMPTY_GROUP: dict = {"type": "AND", "conditions": []}


class ConditionTreeStrategy(bt.Strategy):
    params = (
        ("buy_conditions", None),
        ("sell_conditions", None),
    )

    def __init__(self) -> None:
        self._buy_cond: dict = self.p.buy_conditions or _EMPTY_GROUP
        self._sell_cond: dict = self.p.sell_conditions or _EMPTY_GROUP
        self._buy_inds: dict[str, bt.Indicator] = {}
        self._sell_inds: dict[str, bt.Indicator] = {}
        self._entry_bar: int | None = None

        for block in collect_blocks(self._buy_cond):
            self._ensure_indicator(self._buy_inds, block)
        for block in collect_blocks(self._sell_cond):
            self._ensure_indicator(self._sell_inds, block)

    def _ensure_indicator(self, store: dict[str, bt.Indicator], block: dict) -> None:
        if block["indicator"] in POSITION_RELATIVE_INDICATORS:
            return
        key = indicator_key(block["indicator"], block.get("params", {}))
        if key in store:
            return
        create_fn = INDICATOR_FACTORY.get(block["indicator"])
        if create_fn is None:
            raise ValueError(f"알 수 없는 지표: {block['indicator']}")
        store[key] = create_fn(self.data, **block.get("params", {}))

    def next(self) -> None:
        if not self.position:
            self._entry_bar = None
            if eval_group(self._buy_cond, self._buy_inds):
                self.buy()
        else:
            if self._entry_bar is None:
                self._entry_bar = len(self)
            entry_price = self.position.price
            position_return_pct = (
                (self.data.close[0] - entry_price) / entry_price * 100 if entry_price else None
            )
            position_holding_bars = len(self) - self._entry_bar
            if eval_group(
                self._sell_cond,
                self._sell_inds,
                position_return_pct=position_return_pct,
                position_holding_bars=position_holding_bars,
            ):
                self.sell()


__all__ = ["ConditionTreeStrategy"]
