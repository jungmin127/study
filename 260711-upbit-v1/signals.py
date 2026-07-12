"""
signals.py

백테스팅에 사용할 지표 기반 신호. 각 신호는 Signal 프로토콜을 구현하며,
서로의 존재를 모르는 완전히 독립된 단위다. SignalStrategy가 신호 리스트를
받아 매수는 AND, 매도는 OR로 결합해 실행한다.

should_buy/should_sell은 "지금 막 교차했는가"가 아니라 "지금 이 상태에
있는가"를 기준으로 정의한다 — 여러 신호를 AND로 묶었을 때 서로 다른
지표가 정확히 같은 봉에서 동시에 교차할 확률은 매우 낮아, 이벤트 기준으로
정의하면 혼합 전략이 사실상 거래를 하지 않게 된다(프로토타이핑으로 확인).

새 신호 추가 절차:
1. Signal 프로토콜(setup/should_buy/should_sell)을 구현하는 클래스를 작성.
2. SIGNAL_REGISTRY에 한 줄 등록.
그 외 engine/strategies.py, engine/sweep.py, 백엔드/프론트엔드는 수정할 필요 없다.
"""
from __future__ import annotations

from typing import Protocol

import backtrader as bt


class Signal(Protocol):
    def setup(self, strategy: bt.Strategy) -> None: ...
    def should_buy(self, strategy: bt.Strategy) -> bool: ...
    def should_sell(self, strategy: bt.Strategy) -> bool: ...


SIGNAL_REGISTRY: dict[str, Signal] = {}
