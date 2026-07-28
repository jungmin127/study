"""
engine/indicators/sentiment.py

시장 심리 계열 지표 — 코인 자체가 아니라 외부 데이터 소스(공포/탐욕 지수 등)에서 값을 가져온다.
engine.runner의 build_data_feed_class가 채워주는 self.data.fear_greed_value 라인(백엔드가
external_data_service.get_fear_greed_cmc로 조회한 값을 병합한다)을 그대로 반환한다.
"""
from __future__ import annotations

import backtrader as bt


def create_fear_greed_cmc(data: bt.feeds.PandasData, **params) -> bt.LineBuffer:
    return data.fear_greed_value
